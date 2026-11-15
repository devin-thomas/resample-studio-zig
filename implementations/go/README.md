# Go Resample entry

Linked varispeed CLI for `resample-linked-v1`, using the `zig-48k` output profile.
The Go program owns argument parsing, local-file validation, process lifetime,
bounded diagnostics and atomic output publication. FFmpeg performs decoding,
resampling, concatenation and encoding. This is a Go export CLI, not a new
resampling algorithm.

## Build and run

Working directory:
`/Volumes/PortableSSD/scratch/resample-hackathon-competition/implementations/go/`

Requirements: Go 1.23 or newer at build time, FFmpeg with `libmp3lame` on PATH
at runtime, and a POSIX host with `/dev/fd`, same-directory hard links and atomic
rename. This release was built and tested on macOS arm64 with Go 1.27.1 and
FFmpeg 9.0.2. Linux amd64 cross-compilation passed; its runtime was not tested.
Windows is not supported by
the inherited-descriptor input/output paths. FFmpeg must include the standard
`file` protocol and `pcm_s16le` encoder used by the decoded-sample guard.

The manifest's exact build argv is `["./build.sh"]`. From that working directory:

```sh
./build.sh
./bin/resample first.wav second.mp3 --speed 125.5 -o joined.wav
./bin/resample first.wav --cents -300 -o slowed.mp3
./bin/resample -o literal.wav -- '-leading dash.wav'
```

`build.sh` uses `go` from PATH, or the competition host's ignored
`.tools/go/bin/go` if PATH has no Go. It does not download anything. Its release
command is:

```sh
CGO_ENABLED=0 go build -trimpath -ldflags='-s -w' -o bin/resample .
```

The manifest's exact run prefix is `["./bin/resample"]`; append canonical
contract arguments directly. The release executable is
`/Volumes/PortableSSD/scratch/resample-hackathon-competition/implementations/go/bin/resample`.
Neither Go nor a shell is required when running the built CLI. FFmpeg is the
only runtime executable dependency. `ffprobe` and Python are test dependencies.

```sh
open "/Volumes/PortableSSD/scratch/resample-hackathon-competition/implementations/go/README.md"
```

## Processing and safety

All tracks use the contract pipeline, in supplied order:

```text
aresample=48000,asetrate=48000*ratio,aresample=48000,
aformat=sample_fmts=fltp:channel_layouts=stereo,asetpts=N/SR/TB
```

The `asetrate` value has eight fractional decimal places. The streams feed
`concat=n=N:v=0:a=1`. WAV uses `pcm_s24le`; MP3 uses `libmp3lame` at 320 kbps.
The first audio stream is selected. Metadata copying is disabled, and explicit
`-map_chapters -1` suppresses chapter structures independently of their tags.
There is no gain normalization or independent pitch/speed processing.

One FFmpeg process handles all inputs. An additional PCM null output per input,
limited to one frame, makes FFmpeg's `empty_output_stream` failure gate reject
a track without decoded samples even when the primary concatenation has audio.
Compressed packets alone do not prove that samples survive decoder pre-skip.
The guard shares the main input decoder and encodes at most one frame to
`pcm_s16le`, discarded through the null muxer. It adds a small per-input encoder
and simple filtergraph, without changing the primary DSP graph. `-xerror`
propagates decode failures. The input protocol whitelist is `file`, preventing
network retrieval and pipe access through local playlists. File formats that
depend on relative external files are not supported by the descriptor paths.

The parent does not buffer PCM or encoded output. Its retained diagnostic tail
is a fixed 16 KiB ring with no allocation during writes; command and file
bookkeeping scale with input count.
FFmpeg's own memory remains part of the entry's total resource use.

Inputs are opened read-only with nonblocking open before inspecting their
inode and type, so a FIFO cannot block validation. Their descriptors remain
open and are inherited by FFmpeg as `/dev/fd/4`, `/dev/fd/5`, etc. Renaming or
replacing an input pathname after validation does not change the inode decoded.
This uses one retained descriptor per input. Concurrent writes to an already
opened inode are not prevented.

The output is a randomly named, exclusively created sibling temporary. FFmpeg
inherits the already-open regular file as descriptor 3 and uses `/dev/fd/3`;
it can seek to finalize WAV headers without reopening the temporary pathname.
The child is reaped and the parent's descriptor is closed before publication.

Without `--overwrite`, an exclusive hard link publishes the finished inode in
one filesystem operation. An existing destination, including one created
during rendering, is preserved. With `--overwrite`, rename atomically replaces
the destination directory entry. Destination inode checks reject input aliases
through symlinks and hard links, including an output parent that is a symlink.
The check is repeated after rendering. A dangling destination symlink counts
as an existing output. Overwriting an unrelated symlink replaces that symlink,
not its target. Input files are never opened for writing.

SIGINT and SIGTERM cancel and reap the direct FFmpeg child, remove the temporary
and exit 130. Go's `CommandContext` uses `os.Process.Kill`, without raw-PID or
process-group signaling after reap. A two-second `WaitDelay` bounds diagnostic
pipe draining if an abnormal executable leaves a descendant holding stderr;
this does not kill arbitrary descendants. Handled child, validation and
publication failures leave no newly published output. The publication operation
is the commit point: a signal
arriving after it cannot undo a completed export. SIGKILL and power loss cannot
run cleanup. No `fsync` is performed, so atomic visibility is provided without
a power-loss durability guarantee. A filesystem that does not support hard
links fails safely; there is no copying fallback. These checks do not claim to
sandbox hostile concurrent modification of the directory tree.

## Optimization hypothesis

Exclusive hard-link publication eliminates a complete read/write copy of a
finished fresh output and avoids exposing partially copied output. A single
streaming child and bounded diagnostic retention avoid whole-track allocation
in Go. The decoded-sample guard avoids per-input probe subprocesses. Input
descriptors remove pathname reopening races and redundant input path resolution.
The fixed diagnostic ring avoids repeated slice allocation/copying on verbose
failures; the unit test measures zero allocations per write. These are concrete
architecture changes and hypotheses for later measurement;
neither speed superiority nor lower total process-tree memory has been measured.
No cross-entry benchmark was run.

## Focused validation

Run from the entry directory after building:

```sh
go test ./...
go vet ./...
python3 validate.py ./bin/resample
```

On this host, use `.tools/go/bin/go` when `go` is absent from PATH. Generated
fixtures and the locally installed toolchain stay in ignored `.validation/`
and `.tools/` directories. The release binary is ignored under `bin/`.

The reviewed release passed Go tests (also with the race detector), `go vet`,
and all 28 Python integration tests. The Python suite inherits all 11 unchanged
individual-entry tests from `../../tests/integration.py`, then adds:

- Symlink, hard-link and symlink-parent aliases, with input bytes preserved.
- Zero-byte, non-audio and zero-frame WAV inputs mixed with valid audio.
- Fractional speed and positive/negative cents duration and tone checks.
- A destination created by the child during rendering, preserved on failure.
- SIGINT/SIGTERM child cancellation/reaping and partial-child-output cleanup.
- Distinct stereo channel tones, 48 kHz/24-bit profile metadata, and decoded
  float samples identical to the contract FFmpeg reference at 125.5% speed.
- Packets that decode to no samples, including mixed-track rejection and
  preservation of an existing output.
- Input pathname replacement before decoding; destination aliases introduced
  during rendering; unrelated and dangling destination symlinks.
- Bounded noisy-child diagnostics and a descendant holding the stderr pipe.
- 5 ms and 20 ms clips at boundary/fractional speeds, a one-sample clip at
  unity rate, finalized RIFF/data sizes and the MP3 320 kbps profile.
- Mixed-rate reference equality at -300 cents, first audio stream selection,
  removed source metadata, and unwritable output parents.
- MP3 input containing a verified source title and chapter exports to both WAV
  and MP3 without chapters or source title/artist tags.

Inherited coverage includes WAV/MP3 input and output, mixed sample rates and
ordered concatenation, 25%/400% boundaries, existing outputs, corrupt input,
missing FFmpeg, literal quoted and leading-dash filenames, FIFO rejection,
help/version and invalid options. Additional Go parser checks cover duplicate
rate/output options, nondecimal numbers, trailing junk and all cents bounds.

This is focused validation, not the later harness's full sweep/noise/impulse,
alias-energy, long-track, delay-aligned MP3-quality or performance qualification.
No baseline or sibling implementation was executed or modified.

The exact FFmpeg graph can emit zero frames for submillisecond sources at
non-unity ratios (observed with one sample at 25% and 400%). Such renders fail
safely instead of publishing an empty output. Per
[the shared clarification](../../competition/CLARIFICATIONS.md), these are
reference limitations excluded from speed ranking, not supported exports.
See [REVIEW.md](REVIEW.md) for findings, evidence and remaining limits.
