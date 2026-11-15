# Go entry architecture review

The fresh review found and fixed a decoded-empty-track acceptance bug, closed
input pathname validation races, bounded abnormal child-pipe waits, and removed
repeated diagnostic-buffer allocation. The entry retains linked varispeed, the
frozen main FFmpeg graph, `zig-48k` encodings, and one FFmpeg subprocess per
render. No speed superiority is claimed or measured.

## Findings and fixes

1. **High: packet presence did not establish decoded audio.** The builder's
   stream-copy null outputs accepted a compressed track whose packets decode
   to zero samples. Reproduction used a valid 769-byte Ogg/Opus file with
   pre-skip larger than its entire decoded content: FFmpeg decoded it cleanly
   to zero bytes, but the original entry returned success when concatenating
   it with ordinary audio. `main.go:280` now maps each first audio stream to a
   one-frame `pcm_s16le` null output. The existing global
   `empty_output_stream` gate therefore observes decoded frames, rather than
   compressed packets. The decoder is shared with the primary graph; the
   extra per-input simple filtergraph/PCM encoder handles at most one frame.
   Tests now reject this fixture both before and after valid audio and preserve
   an old output on failure. The embedded regression fixture needs no Opus
   encoder at test time. Zero-byte, non-audio and zero-frame WAV checks remain.

2. **Medium: validated inputs were closed and reopened by pathname.** A file
   could change between `Stat`, readability checking and FFmpeg's open. A
   replacement FIFO could also block the old validation open. `main.go:138`
   now opens with `O_RDONLY|O_NONBLOCK`, obtains type/size/inode from that
   descriptor, retains it, and passes it through `ExtraFiles` as `/dev/fd/4`
   onward. Failed validation also closes already-opened inputs. The integration
   test renames a validated WAV and replaces its old pathname with invalid
   bytes inside the FFmpeg stand-in, then execs real FFmpeg: the original
   audio still renders and the replacement remains unchanged. Alias checks
   compare destination identity with these pinned input inodes.

3. **Medium: inherited stderr could prevent `Run` from finishing.**
   `CommandContext` kills and reaps its direct child, but a descendant holding
   stderr open could leave the Go copy goroutine blocked indefinitely.
   `main.go:298` sets a two-second `WaitDelay`. A stand-in that exits after
   spawning a stderr-holding descendant now produces a handled `ErrWaitDelay`
   failure, preserves the prior output and removes the temporary. The test
   kills only that known descendant PID. The implementation deliberately uses
   Go's synchronized `os.Process.Kill` through `CommandContext`; it introduces
   no delayed raw-PID or process-group kill after reap. SIGINT and SIGTERM
   both return 130, reap the direct child, and preserve the old output in the
   integration test.

4. **Low: diagnostic retention repeatedly allocated and moved retained data.**
   The previous sliding slice kept a bounded visible length but eventually
   exhausted capacity after repeatedly advancing its beginning. `main.go:220`
   now uses a fixed 16 KiB circular array. Writes copy only incoming retained
   bytes, without moving the old tail or allocating. Ordered string assembly
   occurs only on an error. `TestBoundedDiagnosticTail` checks empty, wrapped,
   exact-boundary and oversized writes; `testing.AllocsPerRun` reports zero
   allocations per write. A child emitting two million diagnostic bytes
   produces a bounded error containing the final decoder message.

## Architecture and safety checks

- The main graph remains exactly the contract chain with an eight-place
  `asetrate` value and the same ordered `concat`. No `atempo`, independent
  pitch adjustment, fades, gain normalization, altered filter quality or
  output-profile reduction was introduced. FFmpeg owns all DSP and encoding;
  Go owns the CLI and process/file orchestration.
- The guard adds no probe subprocess. Rendering still launches one FFmpeg
  process. The stricter guard incurs a small PCM encoding/filter cost per
  input; it is required for correctness. Input pinning retains one descriptor
  per source but removes redundant pathname resolution/reopening in Go.
- No PCM or complete encoded output enters Go memory. FFmpeg memory, threads,
  decoding, DSP and encoding remain part of future process-tree measurements.
- The temporary output remains a unique sibling created exclusively and
  inherited as descriptor 3. FFmpeg writes to a seekable regular file. Tests
  inspect both RIFF and data chunk lengths, rather than relying on ffprobe's
  duration inference alone.
- Fresh publication uses an exclusive hard link, with no whole-output copy.
  A destination introduced during rendering is preserved. Overwrite uses
  rename, preserving an old output on render failure. Destination checks run
  before and after rendering and reject symlink/hard-link input aliases,
  including aliases introduced by the child and symlinked parents. Unrelated
  and dangling destination symlinks have explicit coverage. Rename replaces
  the destination entry and never opens a destination target for writing.
- The local input protocol whitelist was narrowed from `file,pipe` to `file`.
  Mandatory WAV and MP3 remain supported. Relative external-file playlists
  are outside the optional-format support offered by descriptor input paths.
- `entry.json` retains the required identity, relative cwd, build argv and run
  argv. Its dependency description now includes the decoded guard's standard
  PCM encoder and file protocol. No runtime Go installation, Python, ffprobe,
  shell or third-party Go module is needed by the built CLI.

## Verification

Reviewed and tested on macOS arm64, Go 1.27.1 from PATH and FFmpeg 9.0.2 with
libmp3lame. Commands run from this entry directory:

```sh
./build.sh
go test ./...
go test -race ./...
go vet ./...
python3 validate.py ./bin/resample
CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -trimpath -ldflags='-s -w' -o .validation/review/resample-linux-amd64 .
```

- Both Go tests, the race-detector run and vet passed. The allocation assertion
  is part of the diagnostic-ring test, not a throughput benchmark.
- All 27 Python integration tests passed: the 11 unchanged shared
  individual-entry tests plus 16 Go-specific tests. Release checks ran with
  `nice` in the owned `go-review-check` screen session.
- Distinct stereo tones at 125.5% produced exactly the same decoded stereo
  float bytes as the matched-profile FFmpeg reference. Mixed 44.1/32 kHz
  sources at -300 cents also matched the reference. First audio stream
  selection, metadata removal, stereo order, WAV 48 kHz/24-bit and MP3
  48 kHz/stereo/320 kbps were checked.
- Ordinary short fixtures used 240 and 960 mono samples at 48 kHz (5 ms and
  20 ms), at 25%, 100%, 125.5% and 400%. All produced nonempty reference audio
  and matched decoded output. A one-sample unity-rate WAV also matched.
- The one-sample, 48 kHz WAV at 25% and 400% produced zero decoded samples
  through the exact standalone reference graph. The entry failed cleanly,
  preserving the old output, as allowed by
  [CLARIFICATIONS.md](../../competition/CLARIFICATIONS.md). These are reported
  reference-empty diagnostics, not supported exports or speed results.
- The manifest's required fields/types were checked, then its exact build
  and run argv were executed successfully from the resolved cwd.
  A JSON Schema validator was not installed; no formal schema-library check
  is claimed.
- Linux amd64 cross-compilation passed and produced a static ELF executable.
  It was not run on Linux. Windows is unsupported. Go 1.23 minimum-version
  compatibility was not tested with that toolchain.

Generated executables, fixtures, logs and the existing local toolchain remain
under ignored `bin/`, `.validation/` and `.tools/`. No builder was contacted,
baseline/sibling was run, new benchmark harness was created, or comparison
timing was taken. All source/document edits are inside this Go entry; nothing
was committed, pushed or sent externally.

## Remaining limits

- This is focused qualification. The future harness must still run its full
  deterministic noise, sweep, impulse, passband/alias-energy, long-track,
  delay-aligned MP3-quality and performance gates. No end-to-end speed or
  total process-tree memory advantage has been established.
- Reference-empty submillisecond cases fail rather than produce fabricated
  audio or a header-only success; they are excluded from speed ranking.
- Atomic visibility is provided without `fsync` or power-loss durability.
  SIGKILL cannot clean up. Cancellation after the atomic publication commit
  cannot undo a completed export. A filesystem lacking sibling hard links
  fails safely without a copy fallback.
- Input pinning prevents pathname replacement from changing the source inode,
  but cannot stop another writer mutating that inode. The parent directory
  must be trusted: checks are not a sandbox against a hostile process swapping
  ancestors, temporary names or destination entries after the final check.
  Overwrite rename still never writes through an input alias. Alias rejection
  is a snapshot check; destination-entry replacement races cannot be made
  fully conditional with ordinary portable rename.
- Resource use scales with input count, including descriptors and FFmpeg's
  graphs/encoders. Native FFmpeg is the supported executable; arbitrary
  descendants of a substituted executable are not recursively terminated.

The distinction between packet-copy and decoded-frame outputs and the global
empty-output flags is documented in the official
[FFmpeg CLI documentation](https://ffmpeg.org/ffmpeg.html#Advanced-options).
The checked Go process-lifetime implementation was the installed Go 1.27.1
standard library (`os/exec` and `os`), rather than custom PID signaling.
