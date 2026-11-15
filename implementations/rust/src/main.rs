//! Linked varispeed orchestration; FFmpeg performs decode, DSP and encode.
#![cfg(unix)]

use std::env;
use std::ffi::{OsStr, OsString};
use std::fmt::Write as _;
use std::fs::{self, File, Metadata, OpenOptions};
use std::io::{self, Read};
use std::os::fd::{AsRawFd, FromRawFd};
use std::os::unix::fs::{MetadataExt, OpenOptionsExt};
use std::os::unix::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, ExitStatus, Stdio};
use std::sync::atomic::{AtomicI32, AtomicU64, Ordering};

const HELP: &str = "Usage: resample [INPUT ...] -o OUTPUT.wav|OUTPUT.mp3 [OPTIONS]\n\n\
  --speed PERCENT  Linked speed/pitch, 25..400 (default 100)\n\
  --cents CENTS    Linked pitch/speed, -2400..2400; exclusive with --speed\n\
  -o, --output P   Concatenate inputs in order into WAV or MP3\n\
  --overwrite      Atomically replace an existing destination\n\
  -h, --help       Show usage\n\
  --version        Show version\n\
  --               Treat remaining tokens as input filenames\n\n\
Requires FFmpeg with fd protocol and libmp3lame on PATH.\n";

type Result<T> = std::result::Result<T, String>;
static CANCELLED: AtomicI32 = AtomicI32::new(0);
static NEXT_TEMP: AtomicU64 = AtomicU64::new(0);

// The handler never signals a PID: only the supervisor, before reaping, may do so.
extern "C" fn cancel(signal: libc::c_int) {
    CANCELLED.store(signal, Ordering::SeqCst);
}

fn install_signals() -> Result<()> {
    // An inherited SIG_IGN/SA_NOCLDWAIT would auto-reap the child and invalidate
    // the supervisor's guarantee that its PID cannot be reused before wait.
    let mut child_action: libc::sigaction = unsafe { std::mem::zeroed() };
    child_action.sa_sigaction = libc::SIG_DFL;
    unsafe { libc::sigemptyset(&mut child_action.sa_mask) };
    if unsafe { libc::sigaction(libc::SIGCHLD, &child_action, std::ptr::null_mut()) } != 0 {
        return Err(format!(
            "cannot make child waitable: {}",
            io::Error::last_os_error()
        ));
    }
    for signal in [libc::SIGINT, libc::SIGTERM, libc::SIGHUP] {
        // SAFETY: sigaction is initialized completely, with a valid C handler.
        let mut action: libc::sigaction = unsafe { std::mem::zeroed() };
        action.sa_sigaction = cancel as *const () as usize;
        unsafe { libc::sigemptyset(&mut action.sa_mask) };
        if unsafe { libc::sigaction(signal, &action, std::ptr::null_mut()) } != 0 {
            return Err(format!(
                "cannot install cancellation handler: {}",
                io::Error::last_os_error()
            ));
        }
    }
    Ok(())
}

fn check_cancelled() -> Result<()> {
    let signal = CANCELLED.load(Ordering::SeqCst);
    if signal == 0 {
        Ok(())
    } else {
        Err(format!("cancelled by signal {signal}"))
    }
}

#[derive(Debug)]
struct Options {
    inputs: Vec<PathBuf>,
    output: PathBuf,
    ratio: f64,
    overwrite: bool,
}

enum Action {
    Help,
    Version,
    Render(Options),
}

// Require an ASCII decimal, optionally signed and/or with a decimal exponent.
// Rust's f64 parser alone would also accept nonfinite spellings.
fn decimal(value: &OsStr) -> Result<f64> {
    let text = value.to_str().ok_or("rate must be an ASCII decimal")?;
    let bytes = text.as_bytes();
    let mut i = usize::from(matches!(bytes.first(), Some(b'+' | b'-')));
    let mut digits = 0;
    while i < bytes.len() && bytes[i].is_ascii_digit() {
        i += 1;
        digits += 1;
    }
    if bytes.get(i) == Some(&b'.') {
        i += 1;
        while i < bytes.len() && bytes[i].is_ascii_digit() {
            i += 1;
            digits += 1;
        }
    }
    if digits == 0 {
        return Err("rate must be a finite decimal".into());
    }
    if matches!(bytes.get(i), Some(b'e' | b'E')) {
        i += 1;
        if matches!(bytes.get(i), Some(b'+' | b'-')) {
            i += 1;
        }
        let first = i;
        while i < bytes.len() && bytes[i].is_ascii_digit() {
            i += 1;
        }
        if i == first {
            return Err("rate exponent requires digits".into());
        }
    }
    if i != bytes.len() {
        return Err("rate contains trailing junk".into());
    }
    let number: f64 = text.parse().map_err(|_| "invalid decimal rate")?;
    if !number.is_finite() {
        return Err("rate must be finite".into());
    }
    Ok(number)
}

fn is_wav(path: &Path) -> bool {
    path.extension()
        .is_some_and(|e| e.eq_ignore_ascii_case("wav"))
}

fn parse(args: impl IntoIterator<Item = OsString>) -> Result<Action> {
    let mut args = args.into_iter().peekable();
    if args.peek().is_none() {
        return Ok(Action::Help);
    }
    let mut inputs = Vec::new();
    let mut output = None;
    let mut ratio = None;
    let mut overwrite = false;
    let mut filenames = false;
    let mut action = None;
    while let Some(arg) = args.next() {
        if filenames {
            inputs.push(PathBuf::from(arg));
            continue;
        }
        match arg.to_str() {
            Some("--") => filenames = true,
            Some("-h" | "--help") => action = Some(false),
            Some("--version") => action = Some(true),
            Some("--overwrite") => overwrite = true,
            Some("-o" | "--output") => {
                if output.is_some() {
                    return Err("specify the output only once".into());
                }
                let value = args
                    .next()
                    .filter(|v| !v.is_empty())
                    .ok_or("missing output path")?;
                output = Some(PathBuf::from(value));
            }
            Some("--speed" | "--cents") => {
                if ratio.is_some() {
                    return Err("choose --speed or --cents, once".into());
                }
                let value = decimal(&args.next().ok_or("missing rate value")?)?;
                ratio = Some(if arg == "--speed" {
                    if !(25.0..=400.0).contains(&value) {
                        return Err("speed must be 25..400".into());
                    }
                    value / 100.0
                } else {
                    if !(-2400.0..=2400.0).contains(&value) {
                        return Err("cents must be -2400..2400".into());
                    }
                    2.0_f64.powf(value / 1200.0)
                });
            }
            _ => {
                if arg.as_encoded_bytes().starts_with(b"-") {
                    return Err(format!(
                        "unknown option {}; use -- before leading-dash filenames",
                        arg.to_string_lossy()
                    ));
                }
                inputs.push(PathBuf::from(arg));
            }
        }
    }
    if let Some(version) = action {
        return Ok(if version {
            Action::Version
        } else {
            Action::Help
        });
    }
    if inputs.is_empty() {
        return Err("provide at least one input file".into());
    }
    let output = output.ok_or("provide -o OUTPUT.wav|OUTPUT.mp3")?;
    if !is_wav(&output)
        && !output
            .extension()
            .is_some_and(|e| e.eq_ignore_ascii_case("mp3"))
    {
        return Err("output extension must be .wav or .mp3".into());
    }
    Ok(Action::Render(Options {
        inputs,
        output,
        ratio: ratio.unwrap_or(1.0),
        overwrite,
    }))
}

fn same_file(a: &Metadata, b: &Metadata) -> bool {
    a.dev() == b.dev() && a.ino() == b.ino()
}

fn validate_output(output: &Path, inputs: &[Metadata], overwrite: bool) -> Result<()> {
    // symlink_metadata detects even dangling symlinks as existing destinations.
    match fs::symlink_metadata(output) {
        Ok(link) => {
            if !overwrite {
                return Err("output exists; use --overwrite to replace it".into());
            }
            let target = fs::metadata(output);
            if target
                .as_ref()
                .is_ok_and(|dest| inputs.iter().any(|src| same_file(src, dest)))
            {
                return Err("output aliases an input (including hardlinks/symlinks)".into());
            }
            if link.is_dir() {
                return Err("output is a directory".into());
            }
            if !link.is_file() && !link.file_type().is_symlink() {
                return Err("output must be a regular file".into());
            }
            if let Err(error) = target {
                if error.kind() != io::ErrorKind::NotFound {
                    return Err(format!("cannot inspect destination: {error}"));
                }
            }
            Ok(())
        }
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(format!("cannot inspect destination: {error}")),
    }
}

struct Temporary {
    path: PathBuf,
    file: File,
}

impl Temporary {
    fn create(parent: &Path) -> Result<Self> {
        for _ in 0..128 {
            let serial = NEXT_TEMP.fetch_add(1, Ordering::Relaxed);
            let path = parent.join(format!(
                ".resample-rust-{}-{serial}.tmp",
                std::process::id()
            ));
            match OpenOptions::new()
                .read(true)
                .write(true)
                .create_new(true)
                .mode(0o600)
                .open(&path)
            {
                Ok(file) => return Ok(Self { path, file }),
                Err(e) if e.kind() == io::ErrorKind::AlreadyExists => continue,
                Err(e) => {
                    return Err(format!(
                        "cannot create temporary output in {}: {e}",
                        parent.display()
                    ));
                }
            }
        }
        Err("cannot reserve a unique temporary output".into())
    }
}

impl Drop for Temporary {
    fn drop(&mut self) {
        // Never remove a different inode if the temporary name was replaced.
        if let Ok(named) = fs::symlink_metadata(&self.path) {
            if !self
                .file
                .metadata()
                .is_ok_and(|ours| same_file(&ours, &named))
            {
                eprintln!(
                    "resample: temporary pathname changed; leaving {} untouched",
                    self.path.display()
                );
                return;
            }
        }
        if let Err(e) = fs::remove_file(&self.path) {
            if e.kind() != io::ErrorKind::NotFound {
                eprintln!("resample: cannot remove {}: {e}", self.path.display());
            }
        }
    }
}

fn graph(count: usize, ratio: f64) -> String {
    let mut graph = String::with_capacity(count * 160);
    for i in 0..count {
        write!(graph, "[{i}:a:0]aresample=48000,asetrate={:.8},aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,asetpts=N/SR/TB[a{i}];", 48000.0 * ratio).unwrap();
    }
    for i in 0..count {
        write!(graph, "[a{i}]").unwrap();
    }
    write!(graph, "concat=n={count}:v=0:a=1[out]").unwrap();
    graph
}

const DIAGNOSTIC_LIMIT: usize = 16 * 1024;

#[derive(Default)]
struct Diagnostics {
    tail: Vec<u8>,
    truncated: bool,
}

impl Diagnostics {
    fn append(&mut self, bytes: &[u8]) {
        let excess = (self.tail.len() + bytes.len()).saturating_sub(DIAGNOSTIC_LIMIT);
        if excess > 0 {
            self.truncated = true;
            self.tail.drain(..excess.min(self.tail.len()));
        }
        self.tail
            .extend_from_slice(&bytes[bytes.len().saturating_sub(DIAGNOSTIC_LIMIT)..]);
    }

    // Limit work per iteration as well as storage: a noisy child must not starve
    // cancellation or exit checks. Never wait for a descendant to close stderr.
    fn drain(&mut self, pipe: &mut impl Read) -> io::Result<bool> {
        let mut buffer = [0_u8; 8192];
        for _ in 0..8 {
            match pipe.read(&mut buffer) {
                Ok(0) => return Ok(true),
                Ok(n) => self.append(&buffer[..n]),
                Err(e) if e.kind() == io::ErrorKind::WouldBlock => return Ok(false),
                Err(e) if e.kind() == io::ErrorKind::Interrupted => continue,
                Err(e) => return Err(e),
            }
        }
        Ok(false)
    }

    fn report(&self) {
        if self.truncated {
            eprintln!("resample: FFmpeg diagnostics truncated; last {DIAGNOSTIC_LIMIT} bytes:");
        }
        if !self.tail.is_empty() {
            eprintln!("{}", String::from_utf8_lossy(&self.tail));
        }
    }
}

// Own the unreaped child on every error path, including pipe/poll failures.
struct SupervisedChild {
    child: Child,
    reaped: bool,
}

impl SupervisedChild {
    fn kill_group(&self) {
        // The leader is still our unreaped child, so its PID cannot be reused.
        unsafe { libc::kill(-(self.child.id() as i32), libc::SIGKILL) };
    }
}

impl Drop for SupervisedChild {
    fn drop(&mut self) {
        if !self.reaped {
            self.kill_group();
            let _ = self.child.wait();
        }
    }
}

fn supervise(child: Child) -> Result<ExitStatus> {
    let mut owned = SupervisedChild {
        child,
        reaped: false,
    };
    let mut pipe = owned
        .child
        .stderr
        .take()
        .ok_or("missing FFmpeg stderr pipe")?;
    let descriptor = pipe.as_raw_fd();
    let flags = unsafe { libc::fcntl(descriptor, libc::F_GETFL) };
    if flags < 0 || unsafe { libc::fcntl(descriptor, libc::F_SETFL, flags | libc::O_NONBLOCK) } < 0
    {
        return Err(format!(
            "cannot monitor FFmpeg stderr: {}",
            io::Error::last_os_error()
        ));
    }
    let mut diagnostics = Diagnostics::default();
    let mut eof = false;
    let mut killed = false;
    let result = (|| {
        loop {
            if CANCELLED.load(Ordering::SeqCst) != 0 && !killed {
                owned.kill_group();
                killed = true;
            }
            eof |= diagnostics
                .drain(&mut pipe)
                .map_err(|e| format!("cannot read FFmpeg diagnostic: {e}"))?;
            match owned.child.try_wait() {
                Ok(Some(status)) => {
                    owned.reaped = true;
                    diagnostics
                        .drain(&mut pipe)
                        .map_err(|e| format!("cannot read FFmpeg diagnostic: {e}"))?;
                    return Ok(status);
                }
                Ok(None) => {}
                Err(e) => {
                    if e.raw_os_error() == Some(libc::ECHILD) {
                        // No longer ours: never signal a potentially reused PID.
                        owned.reaped = true;
                    }
                    return Err(format!("cannot wait for FFmpeg: {e}"));
                }
            }
            let mut event = libc::pollfd {
                fd: if eof { -1 } else { descriptor },
                events: libc::POLLIN,
                revents: 0,
            };
            // Pipe close wakes normal completion immediately. The timeout also
            // handles a child that closes stderr early or a signal just before poll.
            if unsafe { libc::poll(&mut event, 1, 50) } < 0 {
                let e = io::Error::last_os_error();
                if e.kind() != io::ErrorKind::Interrupted {
                    return Err(format!("cannot monitor FFmpeg: {e}"));
                }
            }
        }
    })();
    diagnostics.report();
    result
}

fn render(options: Options) -> Result<()> {
    install_signals()?;
    // Resolve the parent, not the destination: replacing a symlink must never
    // follow it during publication. The final name stays literal.
    let filename = options
        .output
        .file_name()
        .ok_or("output needs a filename")?;
    let parent = options
        .output
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .unwrap_or(Path::new("."));
    let parent = fs::canonicalize(parent).map_err(|e| format!("invalid output parent: {e}"))?;
    let output = parent.join(filename);
    let mut command = Command::new("ffmpeg");
    command.args([
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-xerror",
        "-abort_on",
        "empty_output+empty_output_stream",
    ]);
    let mut identities = Vec::with_capacity(options.inputs.len());
    let mut input_files = Vec::with_capacity(options.inputs.len());
    for input in &options.inputs {
        check_cancelled()?;
        // Pin the validated inode through decoding. Do not reopen the pathname
        // in FFmpeg, where a concurrent replacement could otherwise be a FIFO.
        let file = OpenOptions::new()
            .read(true)
            .custom_flags(libc::O_NONBLOCK)
            .open(input)
            .map_err(|e| format!("cannot open {}: {e}", input.display()))?;
        let opened = file
            .metadata()
            .map_err(|e| format!("cannot inspect open input: {e}"))?;
        if !opened.is_file() || opened.len() == 0 {
            return Err(format!(
                "input {} must be a nonempty local regular file",
                input.display()
            ));
        }
        // Keep inherited input handles away from stdin/stdout/stderr, even when
        // the caller closed a standard descriptor before launching this CLI.
        let file = if file.as_raw_fd() < 3 {
            let descriptor = unsafe { libc::fcntl(file.as_raw_fd(), libc::F_DUPFD, 3) };
            if descriptor < 0 {
                return Err(format!(
                    "cannot reserve input descriptor: {}",
                    io::Error::last_os_error()
                ));
            }
            unsafe { File::from_raw_fd(descriptor) }
        } else {
            file
        };
        // This single-threaded program launches exactly one child; no handler
        // spawns. Set inheritance in the parent to retain Command's native spawn
        // fast path instead of forcing fork via a pre_exec callback.
        if unsafe { libc::fcntl(file.as_raw_fd(), libc::F_SETFD, 0) } < 0 {
            return Err(format!(
                "cannot inherit input descriptor: {}",
                io::Error::last_os_error()
            ));
        }
        identities.push(opened);
        command
            .args(["-protocol_whitelist", "file,fd", "-fd"])
            .arg(file.as_raw_fd().to_string())
            .args(["-i", "fd:"]);
        input_files.push(file);
    }
    validate_output(&output, &identities, options.overwrite)?;
    let temporary = Temporary::create(&parent)?;
    command
        .arg("-filter_complex")
        .arg(graph(options.inputs.len(), options.ratio))
        .args([
            "-map",
            "[out]",
            "-map_metadata",
            "-1",
            "-map_chapters",
            "-1",
        ]);
    if is_wav(&output) {
        command.args(["-c:a", "pcm_s24le", "-f", "wav"]);
    } else {
        command.args(["-c:a", "libmp3lame", "-b:a", "320k", "-f", "mp3"]);
    }
    // fd is seekable for a regular file: WAV/MP3 headers are finalized correctly.
    // The child never opens our temporary pathname and cannot truncate a racer.
    command
        .args(["-fd", "1", "fd:"])
        .stdin(Stdio::null())
        .stderr(Stdio::piped())
        .stdout(Stdio::from(
            temporary
                .file
                .try_clone()
                .map_err(|e| format!("cannot share output handle: {e}"))?,
        ))
        .process_group(0);
    // Validate decoded source samples independently of the transformed output:
    // concat can hide an empty source, and tiny nonempty sources can transform
    // to zero samples. One encoded validation frame suffices for each source.
    for i in 0..options.inputs.len() {
        command.args([
            "-map",
            &format!("{i}:a:0"),
            "-c:a",
            "pcm_s16le",
            "-frames:a",
            "1",
            "-f",
            "null",
            "/dev/null",
        ]);
    }
    check_cancelled()?;
    let child = command.spawn().map_err(|e| {
        if e.kind() == io::ErrorKind::NotFound {
            "install FFmpeg with fd protocol and libmp3lame and add it to PATH".into()
        } else {
            format!("cannot start FFmpeg: {e}")
        }
    })?;
    // The child now owns its descriptors; the supervisor needs only identities.
    drop(input_files);
    let status = supervise(child)?;
    check_cancelled()?;
    if !status.success() {
        return Err(format!(
            "FFmpeg failed ({status}); see its diagnostic above"
        ));
    }
    let finished = temporary
        .file
        .metadata()
        .map_err(|e| format!("cannot inspect rendered output: {e}"))?;
    if finished.len() == 0 {
        return Err("FFmpeg produced no output".into());
    }
    let named = fs::symlink_metadata(&temporary.path)
        .map_err(|e| format!("temporary output disappeared: {e}"))?;
    if !same_file(&finished, &named) {
        return Err("temporary output changed during rendering".into());
    }
    validate_output(&output, &identities, options.overwrite)?;
    check_cancelled()?;
    if options.overwrite {
        fs::rename(&temporary.path, &output)
            .map_err(|e| format!("cannot atomically replace output: {e}"))?;
    } else {
        // A same-filesystem hardlink publishes the completed inode atomically,
        // failing if *any* destination appeared since validation. No output copy.
        fs::hard_link(&temporary.path, &output).map_err(|e| {
            format!("cannot publish output exclusively (destination may exist): {e}")
        })?;
    }
    Ok(())
}

fn main() {
    let result = parse(env::args_os().skip(1)).and_then(|action| match action {
        Action::Help => {
            print!("{HELP}");
            Ok(())
        }
        Action::Version => {
            println!("resample-rust 0.1.0 (resample-linked-v1)");
            Ok(())
        }
        Action::Render(options) => render(options),
    });
    if let Err(message) = result {
        eprintln!("resample: {message}");
        let signal = CANCELLED.load(Ordering::SeqCst);
        std::process::exit(if signal == 0 { 1 } else { 128 + signal });
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn args(values: &[&str]) -> Vec<OsString> {
        values.iter().map(OsString::from).collect()
    }
    #[test]
    fn strict_decimal() {
        for bad in [
            "", "nan", "NaN", "inf", "-inf", "1e999", "100x", " 100", "100 ", "0x64", ".", "1e",
            "1_00",
        ] {
            assert!(decimal(OsStr::new(bad)).is_err(), "{bad}");
        }
        for valid in ["100", "+100.0", "1e2", ".5", "-2400"] {
            assert!(decimal(OsStr::new(valid)).is_ok());
        }
    }
    #[test]
    fn parse_order_boundaries_and_duplicates() {
        let Action::Render(o) = parse(args(&[
            "a.wav", "--speed", "73.5", "-o", "out.WAV", "--", "-b.wav",
        ]))
        .unwrap() else {
            panic!()
        };
        assert_eq!(o.inputs, [PathBuf::from("a.wav"), PathBuf::from("-b.wav")]);
        assert_eq!(o.ratio, 0.735);
        for v in ["25", "400"] {
            assert!(parse(args(&["a", "-o", "x.mp3", "--speed", v])).is_ok());
        }
        for v in ["-2400", "2400"] {
            assert!(parse(args(&["a", "-o", "x.wav", "--cents", v])).is_ok());
        }
        for tail in [
            vec!["--speed", "24.9"],
            vec!["--speed", "400.1"],
            vec!["--cents", "2400.1"],
            vec!["--cents", "-2400.1"],
            vec!["--speed", "100", "--cents", "0"],
            vec!["-o", "z.wav"],
            vec!["--speed", "100", "--speed", "100"],
            vec!["--wat"],
        ] {
            let mut all = vec!["a", "-o", "x.wav"];
            all.extend(tail);
            assert!(parse(args(&all)).is_err());
        }
    }
}
