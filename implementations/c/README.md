# C linked-varispeed CLI

Implements `resample-linked-v1` with the `zig-48k` profile. Pitch, speed and
duration change together. FFmpeg performs decoding, resampling, concatenation
and encoding. C implements argument parsing, validation, process management
and file publication; this is not a new resampling algorithm.

## Build and run

Requires a POSIX C11 compiler, `make`, and FFmpeg on PATH with `libmp3lame`.
The executable links only platform C/math libraries. Python 3 and ffprobe are
needed for tests, not for rendering. macOS and Linux with `/dev/fd` are tested;
Windows is unsupported.

From the repository root:

```sh
make -C implementations/c all
implementations/c/resample first.wav second.mp3 --speed 125.5 -o joined.wav
implementations/c/resample first.wav --cents -300 -o slowed.mp3
implementations/c/resample -o literal.wav -- '-leading dash.wav'
```

The manifest uses cwd `implementations/c`, build argv `["make", "all"]`, and
run prefix `["./resample"]`. Release flags are `-O3 -DNDEBUG -std=c11` with
warnings enabled, without `-march=native` or fast-math. `CC`, `CFLAGS` and
`LDLIBS` can be overridden on the make command line.

Output extensions are case insensitive. WAV is stereo 48000 Hz `pcm_s24le`;
MP3 is stereo 48000 Hz `libmp3lame` at 320 kbps. Source metadata and chapters are discarded.
Options interleave with inputs until `--`. Existing outputs require
`--overwrite`; errors print a diagnostic and exit nonzero.

## Architecture

Inputs are opened once as nonblocking, read-only regular-file descriptors.
Subprocesses receive `/dev/fd/N` aliases via `posix_spawn`, so literal names and
subsequent input pathname replacement cannot redirect the opened input. Only
`file,pipe` protocols are enabled. No shell is invoked.

Before rendering, one FFmpeg validation child per input maps its first audio
stream to PCM and a null muxer with `-frames:a 1`, `-xerror` and
`-abort_on empty_output+empty_output_stream`. A successful nonempty PCM output
proves decoded source samples, unlike a compressed packet count. Each child
exits normally after its one-frame output limit; FFmpeg probing and decoder
buffering may read ahead. The input descriptor is then rewound. Validation
neither stores whole tracks nor changes the actual render's DSP quality.

One final FFmpeg child decodes the complete inputs with `-xerror`, rejects
empty transformed output, and uses exactly this primary graph:

```text
[i:a:0]aresample=48000,asetrate=RATE,aresample=48000,
aformat=sample_fmts=fltp:channel_layouts=stereo,asetpts=N/SR/TB[ai];
... [a0][a1]...concat=n=N:v=0:a=1[out]
```

`RATE` is `48000 * ratio`, formatted to eight fractional decimal places. Tracks
use their first audio stream in supplied order. The output is a reserved,
random, mode-0600 sibling regular file. FFmpeg opens its seekable `/dev/fd/1`
alias so WAV and MP3 seek headers are finalized. WAV uses `-rf64 auto` to avoid
overflowing RIFF's size fields for large outputs; audio encoding is unchanged.

The parent holds the output directory open and uses relative `openat`,
`fstatat`, `linkat`, `renameat` and `unlinkat` operations. It verifies the
completed temporary's identity and closes it before publication. Without
overwrite, `linkat` publishes atomically and fails if a destination appeared
during rendering. With overwrite, `renameat` replaces the directory entry
atomically. Alias checks before work and immediately before publication reject
input inode/device matches. Publication never writes through a destination
symlink or hardlink. No whole-output buffer or second output copy is needed.

SIGINT, SIGTERM and SIGHUP set a cancellation flag. A signal-driven `pselect`
loop forwards cancellation to the owned child process group, escalates after
one second if necessary, and reaps the child. `waitid(WNOWAIT)` keeps the leader
PID reserved while cleaning up its group; the handler never signals a PID and
the parent never signals one after reaping. Child stderr is continuously drained
but only its final 8 KiB is retained, with a truncation notice on failure.
Closed standard descriptors and a closed diagnostic reader preserve cleanup.
Publication is the commit point: a signal delivered after publication does not
turn the completed export into a failed exit.

## Optimization and validation

The review replaced ffprobe's buffered frame-text parser and early termination
with one-frame FFmpeg validation, removing a runtime dependency and parser.
The process loop waits for signals/I/O rather than adding a polling delay to
each subprocess exit. Atomic hardlink publication retains the builder's
avoidance of an extra output copy. C memory is proportional to input count and
argument size, plus bounded diagnostics; FFmpeg maintains its own buffers.
There are still N validation children plus one render child for N inputs.
No throughput, RSS superiority or cross-entry speed claim has been measured.

```sh
make -C implementations/c check
python3 tests/integration.py implementations/c/resample
```

The fresh review passed all 19 C-focused tests and all 11 existing integration
tests on macOS arm64 (Apple clang 21.0.0, FFmpeg/ffprobe 9.0.2) and Linux arm64
in Docker (GCC 12.2.0, FFmpeg/ffprobe 5.1.9). The C-focused suite also passed
with AddressSanitizer and UndefinedBehaviorSanitizer on macOS. Clang static
analysis reported no diagnostics. See [REVIEW.md](REVIEW.md) for checks,
findings and limitations. This is focused per-entry validation, not the later
full quality or performance harness.

## Limitations

- Sub-millisecond sources can produce zero samples through the frozen graph.
  The CLI fails cleanly and preserves the destination in these reference-empty
  cases, as specified in `competition/CLARIFICATIONS.md`. It does not claim
  usable output for them. The suite separately checks these boundaries and
  ordinary 5 ms and 20 ms fixtures against nonempty references.
- Filesystems must support sibling hardlinks for non-overwrite publication.
  Unsupported publication fails without a copy fallback.
- Input contents must remain stable. Descriptors pin inodes, not immutable
  snapshots. An adversarial writer changing temporary entries or repeatedly
  swapping aliases inside the output directory is outside the concurrency
  model. The overwrite alias check and rename are separate operations; inode
  contents remain safe because rename replaces an entry, not its target.
- No `fsync` is performed. Atomic visibility does not promise crash durability;
  SIGKILL, machine crashes and power loss cannot run cleanup.
- The CLI holds one descriptor per input and needs additional child descriptors.
  OS descriptor/argument limits and `FD_SETSIZE` can limit very large lists;
  failures produce diagnostics. Only macOS and Linux arm64 were run.
- RF64 option support and ordinary finalized RIFF headers were checked; an
  actual output exceeding 4 GiB was not rendered in this review.
- Mandatory WAV/MP3 inputs and self-contained multi-stream Matroska selection
  were tested. Path-relative playlists/external-resource formats are not
  promised. FFmpeg is a required external executable, not bundled.
