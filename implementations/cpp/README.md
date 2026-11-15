# C++ linked varispeed CLI

Implements `resample-linked-v1` and the `zig-48k` output profile. C++17 handles
arguments, local-file validation, FFmpeg process control and atomic publication.
FFmpeg performs all decoding, resampling, channel conversion, concatenation and
encoding. This entry does not implement a new resampling algorithm.

## Build and run

From this directory:

```sh
make release
./build/resample first.wav second.mp3 --speed 125.5 -o joined.wav
./build/resample first.wav --cents -300 -o slowed.mp3
./build/resample -o literal.wav -- '-leading dash.wav'
```

The manifest build argv is `["make", "release"]`; run prefix is
`["./build/resample"]`, both with cwd `implementations/cpp` relative to the
competition repository root. No shell evaluates runtime filenames or arguments.
Flags may interleave with inputs; `--` ends option parsing. Speed and cents are
exclusive and both change pitch and duration together. Independent pitch/time
processing is absent.

The release flags are `-std=c++17 -O3 -DNDEBUG -Wall -Wextra -Wpedantic`.
No `-march=native`, fast-math, LTO requirement or host CPU tuning is used.
Override `CXX` on the make command line if needed. Build dependencies are make
and a C++17 compiler; runtime dependencies are libc/libc++ or libstdc++ and
`ffmpeg` on PATH with the usual audio decoders, audio filters, `pcm_s24le` and
`libmp3lame`. `ffprobe` and Python 3.8+ are only needed for validation.
FFmpeg is resolved against the caller's PATH before changing into staging,
including relative or empty PATH entries. Relative input filenames are opened
in the caller's directory and passed as pinned `/dev/fd/N` descriptors.

Supported platforms are macOS and Linux with glibc 2.29+ (for
`posix_spawn_file_actions_addfchdir_np`) with `/dev/fd` available. macOS 26+ builds use the replacement
`posix_spawn_file_actions_addfchdir` API; older macOS deployment targets use the
older API. Darwin uses `posix_spawn_file_actions_addinherit_np` to inherit
pinned descriptors; glibc uses a spawn dup2 action. Windows and non-POSIX hosts
are not supported. Linux has not been executed in this review.

## Audio and validation architecture

The primary per-input graph is the frozen graph, with the asetrate value
formatted to eight fractional places using the classic decimal locale:

```text
[i:a:0]aresample=48000,asetrate=48000*ratio,aresample=48000,
aformat=sample_fmts=fltp:channel_layouts=stereo,asetpts=N/SR/TB[ai]
```

All labels then feed `concat=n=N:v=0:a=1[out]`. Only the first audio stream of
each input is used. Metadata is disabled with `-map_metadata -1`. WAV uses
`pcm_s24le`; MP3 uses `libmp3lame -b:a 320k`. The encoders receive stereo 48 kHz
audio. No gain normalization, fades, intentional gaps, trimming or independent
pitch/time operations are added.

`-xerror` propagates decoding/conversion errors. `-abort_on
empty_output+empty_output_stream` rejects empty streams. In addition to the
primary output, one frame of each input's decoded first audio stream maps to a
discarded `pcm_s16le` stream in a separate FFmpeg null output. These validation sinks ensure an empty
individual track fails even when another track would produce a nonempty join.
They do not change the primary filter graph or its audio format/quality. They
add first-frame PCM encoding work; include them in any later end-to-end
measurement. `-frames:a 1` limits each validation output so it cannot drive a
whole later track ahead of concat's consumption.

Inputs must open as nonempty readable regular files; directories and FIFOs are
rejected without blocking. Symlinks to regular input files are allowed. Open
input descriptors remain pinned until completion, so replacing an input pathname
with another file, symlink or FIFO cannot change the decoded source or cause a
FIFO reopen. Mutating the contents of an already-open input remains unsupported.
FFmpeg's
input protocol whitelist is `file`, preventing network protocol access through
playlist/container references. Non-audio files and valid containers without
decoded audio fail. No ffprobe subprocess is required at runtime.

## Publication and cancellation

The wrapper opens the output parent directory and creates a random private
`0700` sibling staging directory. FFmpeg changes directory there and writes a
seekable regular file named `audio`. Directory descriptors anchor publication
and cleanup even if an ancestor pathname is renamed. After FFmpeg succeeds and
closes the output:

- Without `--overwrite`, `linkat` publishes the finished inode exclusively. A
  destination created during rendering remains untouched. Readers cannot see a
  partially copied final file.
- With `--overwrite`, `renameat` atomically replaces the directory entry. The
  old destination remains visible until successful conversion.
- Input device/inode identities reject both symlink and hardlink output
  aliases, before rendering and again before publication. Existing nonregular
  destinations are rejected. Failed conversion preserves an existing output.
- SIGINT, SIGTERM and SIGHUP are forwarded to the exact FFmpeg PID. The child is
  reaped before staging cleanup; the CLI returns `128 + signal`. The exited
  child remains unreaped until forwarding is disabled, avoiding PID reuse races.
  The wrapper resets inherited SIGCHLD ignore before spawning so the child stays
  waitable. An ignored cancellation escalates to SIGKILL after a one-second grace
  period; exception paths also kill and reap the owned child.
- FFmpeg stderr is drained through a pipe, retaining and emitting only its last
  16 KiB with an explicit truncation notice. Child stdout goes to `/dev/null`.
  No diagnostic or audio buffer grows with track duration.

No-overwrite publication requires same-filesystem hard-link support. There is
no copying fallback that could expose a partial destination. No fsync is added:
atomic visibility is provided, crash/power-loss durability is not promised.
SIGKILL of the wrapper, a machine crash, or a child stuck in uninterruptible
kernel I/O may leave staging material or delay cancellation. The final overwrite
alias check and `renameat` are separate operations: portable POSIX has no
conditional rename tied to the checked inode. A hostile same-user writer can
change that destination entry between them. Rename never writes through the
racing link or changes source audio bytes, but rejection of that last-instant
alias replacement is not guaranteed. No-overwrite publication is atomically
exclusive even under a racing writer. Directory descriptors protect against
ancestor path changes; they do not prevent a same-user attacker from removing
or changing entries inside the private staging directory. No filesystem sandbox
or durability guarantee is claimed. No child or preview server is left running
by successful or handled-failure execution.

## Optimization hypothesis

The C++ wrapper keeps memory proportional to filenames/filter text, not output
duration. It launches a single FFmpeg child and streams output to disk. The
concrete hypothesis is that eliminating the baseline's final no-overwrite
full-output copy reduces disk traffic and publication latency for large WAVs.
The review also removes per-input canonical-path traversal, preallocates the
input/identity/argument vectors, and moves the full graph string into argv
instead of copying it from an initializer list. Pinned descriptors add one
retained descriptor per input, and bounded stderr adds one pipe. Atomic
no-clobber publication and cancellation handling improve safety independently
of throughput. The validation null output adds work that could
offset some savings. No comparative benchmark or speed/memory superiority
claim has been made. Later measurements must include the FFmpeg process tree.

## Focused validation

```sh
make release
python3 validate.py
python3 ../../tests/integration.py ./build/resample
```

The local checks cover strict argument parsing, literal Unicode/quote/dash
filenames, manifest cwd/run argv, WAV/MP3 profiles, metadata removal, mixed
sample rates, all fourteen specified speed/cents settings, finite samples and
exact decoded WAV equality to the specified FFmpeg reference graph. The existing
eleven integration checks add independent tone/duration/order checks, mixed MP3
input, MP3 output, invalid files and missing dependencies.

Controlled FFmpeg substitutes test publication collision, overwrite visibility,
an input alias introduced during rendering, input pathname replacement before
real FFmpeg opens the source, child failure, all three handled cancellation
signals, ignored-signal escalation, stderr EOF before child exit, and a 1 MiB
diagnostic flood. Additional tests exercise inherited SIGCHLD ignore and closed
standard descriptors. The test-only Opus fixture requires libopus encoding; it
has compressed audio packets but no decoded samples and must fail in a join. The substitutes exercise process/file behavior;
real FFmpeg runs exercise audio behavior. Tests clean up their own fixtures.
They do not invoke other entries or perform comparative timing.

Builder host: arm64 Mac Studio, macOS 27.2, Apple clang 21.0.0, FFmpeg 9.0.2
with libmp3lame. All ten local tests and all eleven existing integration tests
passed in the builder session, including the manifest's exact release run argv.
A separate streaming smoke concatenated two ten-minute silence inputs into a
1200-second, 345,600,102-byte WAV. macOS `time -l` reported 39,960,576 bytes of
maximum RSS for that invocation; this is a single-entry observation, not a
simultaneous process-tree memory measurement or comparative benchmark.
The full later contract fixture/quality/performance harness,
long-track RSS measurements, crash durability and Linux execution remain outside
this focused validation. Generated binaries, logs and test fixtures are ignored.

## Fresh review

The independent review passed all 19 focused tests and all 11 existing
integration tests after the changes above. See [REVIEW.md](REVIEW.md) for findings,
evidence, publication limits and the exact checked boundary fixtures. No other
entry was run or compared. Runtime DSP and encoding remain delegated to FFmpeg.

Ordinary short-input checks use 5 ms and 20 ms sources at 32/44.1/48 kHz and
25/100/400% speed. The frozen graph produces no samples for the separately
checked 64-frame 44.1/48 kHz inputs at 400%. Those exports fail cleanly and
preserve an existing destination; they are reference limitations described in
`competition/CLARIFICATIONS.md`, not supported output or speed results. Input
emptiness is still checked on decoded source frames, separately from this
transformed-output boundary. No resampler padding, filter changes or quality
reductions were introduced to work around it.
