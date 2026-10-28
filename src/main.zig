const std = @import("std");
const Allocator = std.mem.Allocator;

const help =
    \\Usage: resample [options] <input>... -o <output.mp3|output.wav>
    \\
    \\  --cents N       Pitch shift in cents (-2400 to 2400)
    \\  --speed N       Speed percent (25 to 400); exclusive with --cents
    \\  -o, --output P  Export path; inputs concatenate in order
    \\  --overwrite     Replace existing output
    \\  -h, --help      Show help
    \\  --version       Show version
    \\  --              Treat remaining arguments as filenames
    \\
    \\Pitch and speed change together. Requires FFmpeg on PATH.
    \\
;

const Options = struct {
    inputs: std.ArrayList([]const u8) = .empty,
    output: ?[]const u8 = null,
    rate: f64 = 1,
    overwrite: bool = false,
    action: enum { render, help, version } = .render,
};

fn rateFromCents(cents: f64) f64 {
    return std.math.pow(f64, 2, cents / 1200);
}

fn parse(allocator: Allocator, args: []const []const u8) !Options {
    var options: Options = .{};
    errdefer options.inputs.deinit(allocator);
    if (args.len == 0) {
        options.action = .help;
        return options;
    }
    var rate_set = false;
    var filenames = false;
    var i: usize = 0;
    while (i < args.len) : (i += 1) {
        const arg = args[i];
        if (!filenames and std.mem.eql(u8, arg, "--")) {
            filenames = true;
        } else if (!filenames and (std.mem.eql(u8, arg, "-h") or std.mem.eql(u8, arg, "--help"))) {
            options.action = .help;
        } else if (!filenames and std.mem.eql(u8, arg, "--version")) {
            options.action = .version;
        } else if (!filenames and std.mem.eql(u8, arg, "--overwrite")) {
            options.overwrite = true;
        } else if (!filenames and (std.mem.eql(u8, arg, "-o") or std.mem.eql(u8, arg, "--output"))) {
            if (options.output != null) return error.DuplicateOutput;
            i += 1;
            if (i == args.len or args[i].len == 0) return error.MissingOutput;
            options.output = args[i];
        } else if (!filenames and (std.mem.eql(u8, arg, "--cents") or std.mem.eql(u8, arg, "--speed"))) {
            if (rate_set) return error.ConflictingRate;
            rate_set = true;
            i += 1;
            if (i == args.len) return error.MissingRate;
            const value = std.fmt.parseFloat(f64, args[i]) catch return error.InvalidRate;
            if (!std.math.isFinite(value)) return error.InvalidRate;
            options.rate = if (std.mem.eql(u8, arg, "--cents")) rateFromCents(value) else value / 100;
            if (options.rate < 0.25 or options.rate > 4) return error.InvalidRate;
        } else if (!filenames and std.mem.startsWith(u8, arg, "-")) {
            std.debug.print("Unknown option: {s}\n", .{arg});
            return error.UnknownOption;
        } else {
            try options.inputs.append(allocator, arg);
        }
    }
    if (options.action != .render) return options;
    if (options.inputs.items.len == 0) return error.MissingInput;
    const output = options.output orelse return error.MissingOutput;
    if (!isWav(output) and !std.ascii.eqlIgnoreCase(std.fs.path.extension(output), ".mp3"))
        return error.UnsupportedOutput;
    return options;
}

fn isWav(path: []const u8) bool {
    return std.ascii.eqlIgnoreCase(std.fs.path.extension(path), ".wav");
}

fn filterGraph(allocator: Allocator, count: usize, rate: f64) ![]const u8 {
    var graph: std.ArrayList(u8) = .empty;
    errdefer graph.deinit(allocator);
    for (0..count) |i| {
        // Normalize before varispeed so mixed source sample rates behave alike.
        const segment = try std.fmt.allocPrint(allocator, "[{d}:a:0]aresample=48000,asetrate={d:.8},aresample=48000," ++
            "aformat=sample_fmts=fltp:channel_layouts=stereo,asetpts=N/SR/TB[a{d}];", .{ i, 48000 * rate, i });
        defer allocator.free(segment);
        try graph.appendSlice(allocator, segment);
    }
    for (0..count) |i| {
        const label = try std.fmt.allocPrint(allocator, "[a{d}]", .{i});
        defer allocator.free(label);
        try graph.appendSlice(allocator, label);
    }
    const concat = try std.fmt.allocPrint(allocator, "concat=n={d}:v=0:a=1[out]", .{count});
    defer allocator.free(concat);
    try graph.appendSlice(allocator, concat);
    return graph.toOwnedSlice(allocator);
}

fn removeTemporary(path: []const u8) void {
    std.fs.cwd().deleteFile(path) catch |err| {
        if (err != error.FileNotFound)
            std.debug.print("Could not remove temporary file {s}: {s}\n", .{ path, @errorName(err) });
    };
}

fn render(allocator: Allocator, options: Options) !void {
    const cwd = std.fs.cwd();
    const output = try std.fs.path.resolve(allocator, &.{options.output.?});
    defer allocator.free(output);
    const existing = cwd.realpathAlloc(allocator, output) catch |err| switch (err) {
        error.FileNotFound => null,
        else => return err,
    };
    defer if (existing) |path| allocator.free(path);
    if (existing != null and !options.overwrite) return error.OutputExists;

    var command: std.ArrayList([]const u8) = .empty;
    defer command.deinit(allocator);
    try command.appendSlice(allocator, &.{ "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-n", "-xerror" });
    var inputs: std.ArrayList([]const u8) = .empty;
    defer {
        for (inputs.items) |path| allocator.free(path);
        inputs.deinit(allocator);
    }
    for (options.inputs.items) |input| {
        const path = cwd.realpathAlloc(allocator, input) catch |err| {
            std.debug.print("Cannot read input {s}: {s}\n", .{ input, @errorName(err) });
            return error.InvalidInput;
        };
        inputs.append(allocator, path) catch |err| {
            allocator.free(path);
            return err;
        };
        if (existing) |dest| {
            if (std.mem.eql(u8, dest, path)) return error.OutputIsInput;
        }
        if ((try cwd.statFile(path)).kind != .file) return error.InvalidInput;
        try command.appendSlice(allocator, &.{ "-i", path });
    }
    const graph = try filterGraph(allocator, inputs.items.len, options.rate);
    defer allocator.free(graph);
    const temporary = try std.fmt.allocPrint(allocator, "{s}.resample-{x}.tmp", .{ output, std.crypto.random.int(u64) });
    defer allocator.free(temporary);
    defer removeTemporary(temporary);
    try command.appendSlice(allocator, &.{ "-filter_complex", graph, "-map", "[out]", "-map_metadata", "-1" });
    if (isWav(output)) {
        try command.appendSlice(allocator, &.{ "-c:a", "pcm_s24le", "-f", "wav" });
    } else {
        try command.appendSlice(allocator, &.{ "-c:a", "libmp3lame", "-b:a", "320k", "-f", "mp3" });
    }
    try command.append(allocator, temporary);
    var child = std.process.Child.init(command.items, allocator);
    child.stdin_behavior = .Ignore;
    child.stdout_behavior = .Ignore;
    child.stderr_behavior = .Inherit;
    const result = child.spawnAndWait() catch |err| switch (err) {
        error.FileNotFound => return error.FFmpegNotFound,
        else => return err,
    };
    switch (result) {
        .Exited => |code| if (code != 0) return error.FFmpegFailed,
        else => return error.FFmpegFailed,
    }
    if (options.overwrite) {
        try cwd.rename(temporary, output);
    } else {
        // Exclusive creation also protects files created while FFmpeg was running.
        const destination = cwd.createFile(output, .{ .exclusive = true }) catch |err| switch (err) {
            error.PathAlreadyExists => return error.OutputExists,
            else => return err,
        };
        defer destination.close();
        errdefer removeTemporary(output);
        const source = try cwd.openFile(temporary, .{});
        defer source.close();
        var buffer: [65536]u8 = undefined;
        while (true) {
            const size = try source.read(&buffer);
            if (size == 0) break;
            try destination.writeAll(buffer[0..size]);
        }
    }
}

fn run(allocator: Allocator) !void {
    const args = try std.process.argsAlloc(allocator);
    defer std.process.argsFree(allocator, args);
    var options = try parse(allocator, args[1..]);
    defer options.inputs.deinit(allocator);
    switch (options.action) {
        .help => try std.fs.File.stdout().writeAll(help),
        .version => try std.fs.File.stdout().writeAll("resample 0.1.0\n"),
        .render => try render(allocator, options),
    }
}

pub fn main() void {
    var arena = std.heap.ArenaAllocator.init(std.heap.page_allocator);
    defer arena.deinit();
    run(arena.allocator()) catch |err| {
        const message = switch (err) {
            error.MissingInput => "provide at least one input file",
            error.MissingOutput => "provide -o <output.mp3|output.wav>",
            error.DuplicateOutput => "specify the output only once",
            error.MissingRate, error.InvalidRate => "use --speed 25..400 or --cents -2400..2400",
            error.ConflictingRate => "choose either --speed or --cents, once",
            error.UnknownOption => "use --help for options, or -- before filenames starting with '-'",
            error.UnsupportedOutput => "output extension must be .mp3 or .wav",
            error.OutputExists => "output exists; use --overwrite to replace it",
            error.OutputIsInput => "output must differ from every input",
            error.InvalidInput => "input must be a readable local audio file",
            error.FFmpegNotFound => "install FFmpeg and add it to PATH",
            error.FFmpegFailed => "FFmpeg failed; see its diagnostic above",
            else => @errorName(err),
        };
        std.debug.print("resample: {s}\n", .{message});
        std.process.exit(1);
    };
}

test "varispeed changes pitch and duration together" {
    try std.testing.expectApproxEqAbs(@as(f64, 0.5), rateFromCents(-1200), 0.000001);
    try std.testing.expectApproxEqAbs(@as(f64, 2), rateFromCents(1200), 0.000001);
}

test "reject ambiguous and unsafe rates" {
    const a = std.testing.allocator;
    try std.testing.expectError(error.ConflictingRate, parse(a, &.{ "x.wav", "-o", "y.wav", "--cents", "0", "--speed", "100" }));
    for ([_][]const u8{ "nan", "inf", "0", "401", "-1" }) |value| {
        try std.testing.expectError(error.InvalidRate, parse(a, &.{ "x.wav", "-o", "y.wav", "--speed", value }));
    }
    try std.testing.expectError(error.UnsupportedOutput, parse(a, &.{ "x.wav", "-o", "y.ogg" }));
    try std.testing.expectError(error.MissingInput, parse(a, &.{ "-o", "y.wav" }));
    try std.testing.expectError(error.MissingOutput, parse(a, &.{"x.wav"}));
}

test "options and literal filenames preserve input order" {
    const a = std.testing.allocator;
    var options = try parse(a, &.{ "a.wav", "--speed", "50", "-o", "mix.WAV", "--", "-b.wav" });
    defer options.inputs.deinit(a);
    try std.testing.expectEqual(@as(f64, 0.5), options.rate);
    try std.testing.expectEqualStrings("a.wav", options.inputs.items[0]);
    try std.testing.expectEqualStrings("-b.wav", options.inputs.items[1]);
    try std.testing.expect(isWav(options.output.?));
}
