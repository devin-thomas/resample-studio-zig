# Rust linked varispeed CLI

This entry implements contract `resample-linked-v1` and profile `zig-48k`:
stereo 48000 Hz, 24-bit PCM WAV or libmp3lame MP3 at 320 kbps. Pitch, speed
and duration change together. Inputs concatenate in supplied order.

## Build and run

From the repository root, change to `implementations/rust`. Build argv is
`["cargo", "build", "--release", "--locked"]`; run prefix is
`["./target/release/resample"]`. The manifest resolves both against its `cwd`.

```sh
cd /Volumes/PortableSSD/scratch/resample-hackathon-competition/implementations/rust
cargo build --release --locked
./target/release/resample input.wav --speed 73.5 -o output.wav
./target/release/resample first.wav second.mp3 --cents -300 -o joined.mp3
./target/release/resample -o literal.WAV -- "-leading-dash ' 音.wav"
```

Use `--overwrite` to replace a destination. No arguments or `--help` show usage;
`--version` identifies the entry. Output/rate options are accepted once, and
options may interleave with inputs before `--`. Numeric rates accept finite
ASCII decimals, including decimal exponents. There is no shell interpretation.

Build requirements: Rust/Cargo 1.85 or newer and the single pinned Rust crate
dependency in `Cargo.lock`, `libc` 0.2.189. Release uses LTO, one codegen unit,
stripped symbols and the toolchain's default CPU target, with no native CPU
flags. Rust 1.98.1 on arm64 macOS was used for the builder checks.

Runtime requirements: macOS or Linux, FFmpeg on PATH with the `fd` protocol,
the contract's filters and encoders, and a destination filesystem supporting
hardlinks and atomic rename. Test tooling additionally needs Python 3 and
ffprobe. FFmpeg 9.0.2 with libmp3lame was used for the builder checks. The native
CLI needs no Cargo/Rust installation after deployment; FFmpeg and its own
runtime dependencies remain required. Linux has not been exercised here.

## Architecture

Rust owns parsing, regular-file validation, process lifetime, temporary-file
reservation and publication. FFmpeg performs all decoding, resampling, linked
varispeed, concatenation and encoding. This is a native export CLI around
FFmpeg, not a new resampling algorithm or a launcher for another entry.

One FFmpeg subprocess receives every input with first audio stream selection.
Each primary track uses the exact frozen graph, with asetrate printed to eight
fractional decimal places:

```text
aresample=48000,asetrate=48000*ratio,aresample=48000,
aformat=sample_fmts=fltp:channel_layouts=stereo,asetpts=N/SR/TB
```

The tracks feed `concat=n=N:v=0:a=1`. Output metadata is disabled with
`-map_metadata -1 -map_chapters -1`, disabling both source tags and chapter
structures. No quality, rate, bit depth or bitrate reduction is used.
Input protocol whitelisting excludes network access, including references in
playlist-like inputs. Rust does not buffer decoded audio or the output.

For every input, the same FFmpeg invocation additionally maps its first
audio stream to a decoded PCM null output. Global
`-abort_on empty_output+empty_output_stream` then rejects empty decoded tracks
even when the main concatenation contains other audio. Each validation output
is capped with `-frames:a 1`: only the first decoded frame needs PCM encoding,
rather than the full track. The primary graph still decodes and checks the whole
input with `-xerror`. This avoids per-input ffprobe processes and extra decoding
passes. Decoded-source emptiness is checked separately from transformed-output
size, including for single-input exports. Empty transformed renders also fail.

## File and cancellation behavior

Inputs must open as nonempty readable local regular files. Validated handles
are inherited by FFmpeg through per-input `fd:` URLs. The parent closes its
input handles after spawning; the child retains them for decoding. Replacing
an input pathname after validation cannot redirect decoding to a different file
or a blocking FIFO. The handles pin inodes, not snapshots of their contents.
Existing output symlinks, including dangling links, count as existing destinations. Overwrite
checks compare device/inode identity to reject input aliases and repeat the
check just before publication. Replacing an unrelated output symlink replaces
the link itself, never its target.

A sibling temporary is exclusively created with mode 0600 and held open.
FFmpeg receives a duplicate of that handle as stdout and writes with its
seekable `fd` protocol. This both finalizes WAV/MP3 headers and avoids reopening
the temporary pathname for truncation. Rust checks that the pathname still
identifies the rendered inode before publishing.

Without overwrite, `hard_link` publishes the complete inode exclusively, then
the temporary name is removed. A destination created during rendering is
preserved. With overwrite, same-directory `rename` replaces the destination
atomically only after a successful render. A failed child preserves an old
destination and leaves no new final destination. No whole-output copy is used.
There is no explicit fsync or power-loss durability guarantee. Atomic
visibility and power-loss durability are different.

SIGINT, SIGTERM and SIGHUP handlers only record cancellation in a lock-free
atomic. The single-threaded supervisor signals the owned process group before
reaping its leader; the handler never targets a stale or reused PID. It resets
inherited SIGCHLD ignoring so the child remains waitable. Cancellation kills
the child, reaps it, removes the temporary, and returns `128 + signal`.
A nonblocking stderr pipe and `poll` allow cancellation even if the child closes
stderr early; the fallback poll timeout is 50 ms. Only the last 16 KiB of child
diagnostics are retained, with a truncation notice, and pipe work per iteration
is bounded so diagnostic flooding cannot starve cancellation. There is no
reader thread or additional subprocess. Input inheritance is configured in the
single-threaded parent, without a `pre_exec` callback that forces a fork path.
Input descriptors are kept above stderr, including when standard handles were
closed by the caller.
SIGKILL of the Rust parent, a crash or power loss can leave a temporary; these
cannot run cleanup, and FFmpeg may continue until it exits. Cleanup refuses to
unlink a temporary pathname whose inode was replaced. The CLI assumes trusted executable PATH and destination
directories. Concurrent hostile path/content replacement is not a file snapshot
or filesystem sandbox; callers must not mutate input content during a render.

## Optimization hypothesis

The concrete hypothesis is reduced publication time and disk traffic for large
fresh outputs: publishing a hardlink avoids a second complete output read/write
and exposes the final name only when complete. Descriptor
output also removes a temporary-path reopen. The Rust orchestrator retains only
argv, paths, metadata and the filter graph; FFmpeg owns the audio working set.
The per-input null outputs encode at most one validation frame per track,
removing full-track validation PCM conversion while preserving decoded-empty
checks. A focused framecrc check confirms one validation packet per input.
The input descriptor path also removes canonicalization plus metadata/reopen
work from the former builder design. No timing or memory comparison has been
run, and no speed or peak-memory superiority is claimed.
Process-tree measurements, including FFmpeg, are necessary before ranking.

## Focused validation

Run from this directory, with the release build already present:

```sh
cargo test --locked
cargo clippy --locked --all-targets -- -D warnings
python3 ../../tests/integration.py ./target/release/resample
python3 tests/focused.py ./target/release/resample
```

Reviewer results: 2 Rust unit tests, all 11 existing individual-entry integration
tests, and all 23 focused tests pass. Clippy reports no warnings. The real
release entrypoint handles version/help and renders all tests directly.

The focused tests compare decoded WAV samples exactly against the specified
FFmpeg pipeline at all 7 speed values and all 7 cents values in the contract,
using mixed 44.1/32 kHz mono/stereo ordered inputs. They also cover silence,
impulses, seeded noise, sweeps, a short clip, finite samples, duration, stereo
MP3 format/bitrate, literal Unicode/quote/dash filenames, empty decoded tracks
alone and in a concatenation, aliases, dangling symlinks, invalid CLI,
unwritable parents, missing dependencies, failed-child cleanup, publication
collision and all 3 handled cancellation signals. Reviewer additions cover
late aliases, input descriptor pinning across replacement by a FIFO, one-frame
validation encoding, finalized WAV RIFF/data sizes, bounded diagnostic flooding,
changed temporary cleanup, inherited SIGCHLD ignoring, and cancellation with
closed or continuously noisy stderr, closed standard input/output handles,
5/20 ms short clips, clean failure on reference-empty boundaries, and MP3
chapter/title removal in WAV and MP3 exports. The existing
tests cover tone changes, MP3 inputs/outputs, FIFO rejection and overwrite preservation.

These are correctness checks for this entry, not comparative performance tests
or the complete later quality gate. Long-track throughput/RSS, codec-delay
aligned MP3 fidelity analysis, alternative-DSP alias/passband analysis and Linux
runtime verification were not performed. The DSP is the exact FFmpeg reference;
no alternative DSP quality claim is made. Some sub-millisecond inputs produce
no samples through the frozen resampler graph and are rejected as empty renders,
even though their source contains samples. For example, a one-sample 44.1 kHz
input at default speed and a 32-sample input at 400% trigger this boundary.
The clarification in [competition/CLARIFICATIONS.md](../../competition/CLARIFICATIONS.md)
accepts clean failure on these reference-empty cases and excludes them from
speed ranking. Both 5 ms and 20 ms clips, plus 97-frame short clips, at
32/44.1/48 kHz pass the focused checks at 25/100/400%. The separate fresh-session
review is in [REVIEW.md](REVIEW.md).
