#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
#include <cerrno>
#include <cmath>
#include <csignal>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <filesystem>
#include <iomanip>
#include <iostream>
#include <locale>
#include <poll.h>
#include <spawn.h>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <time.h>
#include <sys/random.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>
#include <vector>

extern char **environ;
namespace fs = std::filesystem;

namespace {
volatile sig_atomic_t cancelled = 0;
volatile sig_atomic_t child_pid = 0;

void on_signal(int signal) {
    const int saved = errno;
    cancelled = signal;
    if (child_pid > 0) kill(static_cast<pid_t>(child_pid), signal);
    errno = saved;
}

[[noreturn]] void fail(const std::string &message) { throw std::runtime_error(message); }
[[noreturn]] void system_error(const std::string &message, int error = errno) {
    fail(message + ": " + std::strerror(error));
}
void check_cancelled() { if (cancelled) fail("cancelled by signal " + std::to_string(cancelled)); }

class Fd {
    int value_;
public:
    explicit Fd(int fd = -1) : value_(fd) {
        // Keep owned descriptors away from the child's standard-stream actions,
        // including when this CLI was launched with stdin/stdout/stderr closed.
        if (fd >= 0 && fd < 3) {
            value_ = fcntl(fd, F_DUPFD_CLOEXEC, 3);
            const int saved = errno;
            close(fd);
            if (value_ < 0) system_error("cannot reserve descriptor", saved);
        }
    }
    ~Fd() { if (value_ >= 0) close(value_); }
    Fd(const Fd &) = delete;
    Fd &operator=(const Fd &) = delete;
    Fd(Fd &&other) noexcept : value_(other.value_) { other.value_ = -1; }
    int get() const { return value_; }
    void close_now() { if (value_ >= 0) { close(value_); value_ = -1; } }
};

constexpr const char *usage =
    "Usage: resample [INPUT ...] [-o|--output OUTPUT.wav|OUTPUT.mp3]\n"
    "                [--speed PERCENT | --cents CENTS] [--overwrite] [-- INPUT ...]\n\n"
    "Inputs concatenate in order; pitch and speed change together.\n"
    "  --speed PERCENT   25..400 (default 100)\n"
    "  --cents CENTS     -2400..2400; exclusive with --speed\n"
    "  --overwrite       Atomically replace an existing output\n"
    "  -h, --help        Print usage\n"
    "  --version         Print version\n"
    "Requires FFmpeg with libmp3lame on PATH.\n";

struct Options {
    std::vector<std::string> inputs;
    std::string output;
    double ratio = 1;
    bool overwrite = false;
    enum { render, help, version } action = render;
};

// Decimal syntax only: reject whitespace, hexadecimal floats and partial parses.
double decimal(std::string_view text) {
    size_t i = 0;
    if (i < text.size() && (text[i] == '+' || text[i] == '-')) ++i;
    size_t digits = 0;
    while (i < text.size() && text[i] >= '0' && text[i] <= '9') { ++i; ++digits; }
    if (i < text.size() && text[i] == '.') {
        ++i;
        while (i < text.size() && text[i] >= '0' && text[i] <= '9') { ++i; ++digits; }
    }
    if (!digits) fail("rate must be a finite decimal number");
    if (i < text.size() && (text[i] == 'e' || text[i] == 'E')) {
        ++i;
        if (i < text.size() && (text[i] == '+' || text[i] == '-')) ++i;
        const auto start = i;
        while (i < text.size() && text[i] >= '0' && text[i] <= '9') ++i;
        if (i == start) fail("invalid decimal exponent");
    }
    if (i != text.size()) fail("rate must be a finite decimal number without trailing characters");
    std::istringstream stream{std::string(text)};
    stream.imbue(std::locale::classic());
    double number = 0;
    stream >> number;
    if (stream.fail() || !std::isfinite(number)) fail("rate must be a finite decimal number");
    return number;
}

std::string extension(const std::string &path) {
    auto result = fs::path(path).extension().string();
    for (char &c : result) if (c >= 'A' && c <= 'Z') c += 'a' - 'A';
    return result;
}

Options parse(int argc, char **argv) {
    Options options;
    if (argc == 1) { options.action = Options::help; return options; }
    bool literal = false, rate_set = false, output_set = false;
    for (int i = 1; i < argc; ++i) {
        const std::string arg = argv[i];
        if (!literal && arg == "--") literal = true;
        else if (!literal && (arg == "-h" || arg == "--help")) options.action = Options::help;
        else if (!literal && arg == "--version") options.action = Options::version;
        else if (!literal && arg == "--overwrite") options.overwrite = true;
        else if (!literal && (arg == "-o" || arg == "--output")) {
            if (output_set) fail("specify the output exactly once");
            output_set = true;
            if (++i == argc || !*argv[i]) fail("provide a filename after " + arg);
            options.output = argv[i];
        } else if (!literal && (arg == "--speed" || arg == "--cents")) {
            if (rate_set) fail("choose --speed or --cents exactly once");
            rate_set = true;
            if (++i == argc) fail("provide a number after " + arg);
            const double value = decimal(argv[i]);
            if (arg == "--speed") {
                if (value < 25 || value > 400) fail("--speed must be 25..400");
                options.ratio = value / 100;
            } else {
                if (value < -2400 || value > 2400) fail("--cents must be -2400..2400");
                options.ratio = std::pow(2.0, value / 1200);
            }
        } else if (!literal && !arg.empty() && arg.front() == '-') {
            fail("unknown option " + arg + "; use -- before filenames starting with '-'");
        } else options.inputs.push_back(arg);
    }
    if (options.action != Options::render) return options;
    if (options.inputs.empty()) fail("provide at least one input file");
    if (!output_set) fail("provide -o OUTPUT.wav or OUTPUT.mp3");
    if (extension(options.output) != ".wav" && extension(options.output) != ".mp3")
        fail("output extension must be .wav or .mp3");
    return options;
}

// Resolve relative PATH entries before the child changes into private staging.
std::string ffmpeg_path() {
    const char *configured = std::getenv("PATH");
    std::string search;
    if (configured) search = configured;
    else {
        const size_t length = confstr(_CS_PATH, nullptr, 0);
        if (!length) fail("install FFmpeg with libmp3lame and add it to PATH");
        search.resize(length);
        confstr(_CS_PATH, search.data(), length);
        search.resize(length - 1);
    }
    size_t start = 0;
    for (;;) {
        const size_t end = search.find(':', start);
        const std::string directory = search.substr(start, end == std::string::npos ? end : end - start);
        const fs::path candidate = fs::absolute(fs::path(directory) / "ffmpeg");
        struct stat st{};
        if (stat(candidate.c_str(), &st) == 0 && S_ISREG(st.st_mode) && access(candidate.c_str(), X_OK) == 0)
            return candidate.string();
        if (end == std::string::npos) break;
        start = end + 1;
    }
    fail("install FFmpeg with libmp3lame and add its executable to PATH");
}

struct Identity { dev_t device; ino_t inode; };
bool same(const struct stat &st, const Identity &id) { return st.st_dev == id.device && st.st_ino == id.inode; }

// A private sibling directory avoids letting FFmpeg reopen an attacker-replaced
// temporary symlink. Directory descriptors anchor cleanup and publication.
class Temporary {
    int parent_;
    std::string name_;
    Fd directory_;
    static std::string create(int parent) {
        for (int attempt = 0; attempt < 64; ++attempt) {
            unsigned char random[16];
            if (getentropy(random, sizeof(random)) != 0) system_error("cannot obtain temporary-name randomness");
            std::string name = ".resample-";
            constexpr char hex[] = "0123456789abcdef";
            for (auto byte : random) { name += hex[byte >> 4]; name += hex[byte & 15]; }
            name += ".tmp";
            if (mkdirat(parent, name.c_str(), 0700) == 0) return name;
            if (errno != EEXIST) system_error("cannot create sibling temporary directory");
        }
        fail("cannot allocate a unique temporary directory");
    }
    static int open_directory(int parent, const std::string &name) {
        const int fd = openat(parent, name.c_str(), O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
        if (fd < 0) {
            const int saved = errno;
            unlinkat(parent, name.c_str(), AT_REMOVEDIR);
            system_error("cannot open temporary directory", saved);
        }
        return fd;
    }
public:
    explicit Temporary(int parent) : parent_(parent), name_(create(parent)), directory_(open_directory(parent, name_)) {}
    ~Temporary() {
        if (unlinkat(directory_.get(), "audio", 0) != 0 && errno != ENOENT)
            std::cerr << "resample: cannot remove temporary audio: " << std::strerror(errno) << '\n';
        if (unlinkat(parent_, name_.c_str(), AT_REMOVEDIR) != 0)
            std::cerr << "resample: cannot remove temporary directory " << name_ << ": " << std::strerror(errno) << '\n';
    }
    int fd() const { return directory_.get(); }
};

class SignalBlock {
    sigset_t previous_{};
public:
    SignalBlock() {
        sigset_t set;
        sigemptyset(&set);
        for (int sig : {SIGINT, SIGTERM, SIGHUP}) sigaddset(&set, sig);
        if (sigprocmask(SIG_BLOCK, &set, &previous_) != 0) system_error("cannot block cancellation signals");
    }
    ~SignalBlock() { sigprocmask(SIG_SETMASK, &previous_, nullptr); }
};

void run_child(std::vector<std::string> &args, int cwd, const std::vector<Fd> &inputs) {
    int pipe_fds[2];
    if (pipe(pipe_fds) != 0) system_error("cannot create diagnostic pipe");
    Fd diagnostic_read(pipe_fds[0]), diagnostic_write(pipe_fds[1]);
    for (int fd : {diagnostic_read.get(), diagnostic_write.get()})
        if (fcntl(fd, F_SETFD, FD_CLOEXEC) != 0) system_error("cannot protect diagnostic descriptor");
    if (fcntl(diagnostic_read.get(), F_SETFL, O_NONBLOCK) != 0)
        system_error("cannot configure diagnostic pipe");
    std::vector<char *> argv;
    argv.reserve(args.size() + 1);
    for (auto &arg : args) argv.push_back(arg.data());
    argv.push_back(nullptr);
    posix_spawn_file_actions_t actions;
    posix_spawnattr_t attributes;
    int error = posix_spawn_file_actions_init(&actions);
    if (error) system_error("cannot initialize child file actions", error);
    struct ActionsGuard { posix_spawn_file_actions_t *p; ~ActionsGuard() { posix_spawn_file_actions_destroy(p); } } ag{&actions};
    error = posix_spawnattr_init(&attributes);
    if (error) system_error("cannot initialize child attributes", error);
    struct AttributesGuard { posix_spawnattr_t *p; ~AttributesGuard() { posix_spawnattr_destroy(p); } } atg{&attributes};
    auto checked = [](int result) { if (result) system_error("cannot configure FFmpeg process", result); };
    checked(posix_spawn_file_actions_addopen(&actions, STDIN_FILENO, "/dev/null", O_RDONLY, 0));
    checked(posix_spawn_file_actions_addopen(&actions, STDOUT_FILENO, "/dev/null", O_WRONLY, 0));
    checked(posix_spawn_file_actions_adddup2(&actions, diagnostic_write.get(), STDERR_FILENO));
    checked(posix_spawn_file_actions_addclose(&actions, diagnostic_read.get()));
    checked(posix_spawn_file_actions_addclose(&actions, diagnostic_write.get()));
    // FFmpeg reopens pinned regular-file inodes, never input pathnames.
    // Darwin needs its explicit inherit action for same-descriptor CLOEXEC.
    for (const auto &input : inputs) {
#ifdef __APPLE__
        checked(posix_spawn_file_actions_addinherit_np(&actions, input.get()));
#else
        checked(posix_spawn_file_actions_adddup2(&actions, input.get(), input.get()));
#endif
    }
#if defined(__APPLE__) && __ENVIRONMENT_MAC_OS_X_VERSION_MIN_REQUIRED__ >= 260000
    checked(posix_spawn_file_actions_addfchdir(&actions, cwd));
#else
    checked(posix_spawn_file_actions_addfchdir_np(&actions, cwd));
#endif
    sigset_t empty, defaults;
    sigemptyset(&empty);
    sigemptyset(&defaults);
    for (int sig : {SIGINT, SIGTERM, SIGHUP, SIGPIPE}) sigaddset(&defaults, sig);
    checked(posix_spawnattr_setsigmask(&attributes, &empty));
    checked(posix_spawnattr_setsigdefault(&attributes, &defaults));
    checked(posix_spawnattr_setflags(&attributes, POSIX_SPAWN_SETSIGMASK | POSIX_SPAWN_SETSIGDEF));
    pid_t pid = -1;
    {
        SignalBlock blocked;
        check_cancelled();
        error = posix_spawnp(&pid, argv[0], &actions, &attributes, argv.data(), environ);
        if (error == ENOENT) fail("install FFmpeg with libmp3lame and add it to PATH");
        if (error) system_error("cannot launch FFmpeg", error);
        child_pid = pid;
    }
    // Close the parent's writer now so child exit produces EOF promptly.
    // Keep ownership in one RAII object throughout exception paths.
    struct ChildGuard {
        pid_t pid;
        ~ChildGuard() {
            if (pid <= 0) return;
            sigset_t set, old;
            sigemptyset(&set);
            for (int sig : {SIGINT, SIGTERM, SIGHUP}) sigaddset(&set, sig);
            sigprocmask(SIG_BLOCK, &set, &old);
            // The child has not been reaped: this PID cannot belong to anyone else.
            kill(pid, SIGKILL);
            while (waitpid(pid, nullptr, 0) < 0 && errno == EINTR) {}
            child_pid = 0;
            sigprocmask(SIG_SETMASK, &old, nullptr);
        }
    } child{pid};
    diagnostic_write.close_now();
    constexpr size_t diagnostic_limit = 16384;
    std::string diagnostic;
    diagnostic.reserve(diagnostic_limit);
    bool truncated = false;
    double cancellation_deadline = 0;
    auto enforce_cancellation = [&] {
        if (!cancelled) return;
        timespec now{};
        if (clock_gettime(CLOCK_MONOTONIC, &now) != 0) system_error("cannot read cancellation clock");
        const double seconds = now.tv_sec + now.tv_nsec / 1e9;
        if (!cancellation_deadline) cancellation_deadline = seconds + 1.0;
        if (seconds >= cancellation_deadline) kill(pid, SIGKILL);
    };
    siginfo_t info{};
    bool exited = false;
    for (;;) {
        enforce_cancellation();
        pollfd descriptor{diagnostic_read.get(), POLLIN, 0};
        const int ready = poll(&descriptor, 1, cancelled ? 20 : 1000);
        if (ready < 0) {
            if (errno == EINTR) continue;
            system_error("cannot read FFmpeg diagnostics");
        }
        if (ready) {
            char buffer[8192];
            const ssize_t count = read(diagnostic_read.get(), buffer, sizeof(buffer));
            if (count == 0) break;
            if (count > 0) {
                const size_t excess = diagnostic.size() + static_cast<size_t>(count) > diagnostic_limit
                    ? diagnostic.size() + static_cast<size_t>(count) - diagnostic_limit : 0;
                if (excess) { diagnostic.erase(0, excess); truncated = true; }
                diagnostic.append(buffer, static_cast<size_t>(count));
            } else if (errno != EAGAIN && errno != EINTR) system_error("cannot read FFmpeg diagnostics");
        } else {
            // An unexpected descendant must not hold this wrapper open forever
            // merely by inheriting stderr after the actual FFmpeg child exits.
            if (waitid(P_PID, static_cast<id_t>(pid), &info, WEXITED | WNOWAIT | WNOHANG) < 0) {
                if (errno == EINTR) continue;
                system_error("cannot wait for FFmpeg");
            }
            if (info.si_pid == pid) { exited = true; break; }
        }
    }
    // Retain the exited child as a zombie until forwarding is disabled. Its
    // PID cannot be recycled underneath the async signal handler.
    while (!exited) {
        enforce_cancellation();
        info = {};
        if (waitid(P_PID, static_cast<id_t>(pid), &info,
                   WEXITED | WNOWAIT | (cancelled ? WNOHANG : 0)) < 0) {
            if (errno == EINTR) continue;
            system_error("cannot wait for FFmpeg");
        }
        exited = info.si_pid == pid;
        if (!exited) { timespec delay{0, 20000000}; nanosleep(&delay, nullptr); }
    }
    int status = 0;
    {
        SignalBlock blocked;
        child_pid = 0;
        while (waitpid(pid, &status, 0) < 0) {
            if (errno != EINTR) system_error("cannot reap FFmpeg");
        }
        child.pid = -1;
    }
    if (!diagnostic.empty()) {
        if (truncated) std::cerr << "resample: FFmpeg diagnostics truncated to last 16384 bytes\n";
        std::cerr << diagnostic;
        if (diagnostic.back() != '\n') std::cerr << '\n';
    }
    check_cancelled();
    if (WIFSIGNALED(status)) fail("FFmpeg terminated by signal " + std::to_string(WTERMSIG(status)));
    if (!WIFEXITED(status) || WEXITSTATUS(status))
        fail("FFmpeg failed" + (WIFEXITED(status) ? " (exit " + std::to_string(WEXITSTATUS(status)) + ")" : std::string{}) + "; see diagnostic above");
}

void render(const Options &options) {
    const auto destination = fs::absolute(fs::path(options.output));
    const std::string leaf = destination.filename().string();
    Fd parent(open(destination.parent_path().c_str(), O_RDONLY | O_DIRECTORY | O_CLOEXEC));
    if (parent.get() < 0) system_error("cannot open output directory");
    std::vector<Identity> identities;
    std::vector<std::string> inputs;
    std::vector<Fd> input_files;
    identities.reserve(options.inputs.size());
    inputs.reserve(options.inputs.size());
    input_files.reserve(options.inputs.size());
    for (const auto &input : options.inputs) {
        check_cancelled();
        Fd file(open(input.c_str(), O_RDONLY | O_NONBLOCK | O_CLOEXEC));
        if (file.get() < 0) system_error("cannot read input " + input);
        struct stat st{};
        if (fstat(file.get(), &st) != 0) system_error("cannot inspect input " + input);
        if (!S_ISREG(st.st_mode) || !st.st_size) fail("input must be a nonempty readable local regular audio file: " + input);
        identities.push_back({st.st_dev, st.st_ino});
        inputs.push_back("/dev/fd/" + std::to_string(file.get()));
        input_files.push_back(std::move(file));
    }
    auto validate_destination = [&] {
        struct stat st{};
        if (fstatat(parent.get(), leaf.c_str(), &st, AT_SYMLINK_NOFOLLOW) != 0) {
            if (errno == ENOENT) return;
            system_error("cannot inspect output");
        }
        if (!options.overwrite) fail("output exists; use --overwrite to replace it");
        if (!S_ISREG(st.st_mode) && !S_ISLNK(st.st_mode)) fail("existing output must be a regular file or symlink");
        if (S_ISLNK(st.st_mode) && fstatat(parent.get(), leaf.c_str(), &st, 0) != 0) {
            if (errno == ENOENT) return;
            system_error("cannot inspect output symlink target");
        }
        for (const auto &id : identities) if (same(st, id)) fail("output aliases an input; refusing replacement");
    };
    validate_destination();
    Temporary temporary(parent.get());
    std::vector<std::string> command{ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-nostdin", "-n", "-xerror", "-abort_on", "empty_output+empty_output_stream"};
    command.reserve(40 + inputs.size() * 16);
    for (const auto &input : inputs) {
        command.insert(command.end(), {"-protocol_whitelist", "file", "-i"});
        command.push_back(input);
    }
    std::ostringstream graph;
    graph.imbue(std::locale::classic());
    graph << std::fixed << std::setprecision(8);
    for (size_t i = 0; i < inputs.size(); ++i)
        graph << '[' << i << ":a:0]aresample=48000,asetrate=" << 48000 * options.ratio
              << ",aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,asetpts=N/SR/TB[a" << i << "];";
    for (size_t i = 0; i < inputs.size(); ++i) graph << "[a" << i << ']';
    graph << "concat=n=" << inputs.size() << ":v=0:a=1[out]";
    command.emplace_back("-filter_complex");
    command.push_back(graph.str()); // move the potentially large graph; do not copy an initializer-list string
    command.insert(command.end(), {"-map", "[out]", "-map_metadata", "-1", "-map_chapters", "-1"});
    if (extension(options.output) == ".wav") command.insert(command.end(), {"-c:a", "pcm_s24le", "-f", "wav"});
    else command.insert(command.end(), {"-c:a", "libmp3lame", "-b:a", "320k", "-f", "mp3"});
    command.push_back("audio");
    // These sinks prove decoded sample presence independently of primary DSP.
    // Source packet presence is insufficient (e.g. fully discarded Opus data).
    // One frame bounds the additional PCM encoding and later-track demand.
    for (size_t i = 0; i < inputs.size(); ++i)
        command.insert(command.end(), {"-map", std::to_string(i) + ":a:0", "-frames:a", "1",
            "-c:a", "pcm_s16le", "-f", "null", "/dev/null"});
    run_child(command, temporary.fd(), input_files);
    Fd audio(openat(temporary.fd(), "audio", O_RDONLY | O_NOFOLLOW | O_CLOEXEC));
    if (audio.get() < 0) system_error("FFmpeg did not create output");
    struct stat st{};
    if (fstat(audio.get(), &st) != 0) system_error("cannot inspect rendered output");
    if (!S_ISREG(st.st_mode) || st.st_size == 0) fail("FFmpeg produced no audio file");
    {
        SignalBlock blocked;
        check_cancelled();
        validate_destination();
        if (options.overwrite) {
            if (renameat(temporary.fd(), "audio", parent.get(), leaf.c_str()) != 0) system_error("cannot atomically replace output");
        } else {
            if (linkat(temporary.fd(), "audio", parent.get(), leaf.c_str(), 0) != 0) {
                if (errno == EEXIST) fail("output appeared while rendering; refusing replacement");
                system_error("cannot atomically publish output (filesystem must support hard links)");
            }
        }
    }
}
} // namespace

int main(int argc, char **argv) {
    try {
        // Ignored SIGCHLD survives exec. Restore waitable children before
        // relying on the zombie lifetime to prevent PID reuse during forwarding.
        struct sigaction child_action{};
        child_action.sa_handler = SIG_DFL;
        sigemptyset(&child_action.sa_mask);
        if (sigaction(SIGCHLD, &child_action, nullptr) != 0) system_error("cannot configure child reaping");
        struct sigaction action{};
        action.sa_handler = on_signal;
        sigemptyset(&action.sa_mask);
        for (int signal : {SIGINT, SIGTERM, SIGHUP})
            if (sigaction(signal, &action, nullptr) != 0) system_error("cannot install cancellation handler");
        const auto options = parse(argc, argv);
        if (options.action == Options::help) std::cout << usage;
        else if (options.action == Options::version) std::cout << "resample-cpp 0.1.0 (resample-linked-v1)\n";
        else render(options);
        return 0;
    } catch (const std::exception &error) {
        std::cerr << "resample: " << error.what() << '\n';
        return cancelled ? 128 + cancelled : 1;
    }
}
