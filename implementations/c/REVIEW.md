# C entry adversarial review

## Outcome

The entry retains linked varispeed, the exact frozen primary FFmpeg graph,
`zig-48k` codecs, and the manifest build/run interface. FFmpeg performs all DSP.
The review implemented safer process cancellation, bounded diagnostics,
simpler decoded-sample validation, closed-stdio handling and RF64 auto headers.
All edits are inside `implementations/c`. No baseline or sibling entry was
built or run; no comparative benchmark or new benchmark harness was created.

## Findings and changes

1. **Cancellation could hang indefinitely; the signal handler could target a
   reaped PID (fixed).** The builder forwarded signals and then waited without
   escalation. Its `waitpid`/global-PID clearing window could let a late signal
   address a recycled process group. The handler now only records cancellation.
   The parent owns signaling, uses monotonic one-second escalation, observes
   exit with `waitid(WNOWAIT)`, cleans up the process group before reaping, and
   never signals after reaping. Event signals are blocked around exit checks
   and atomically unblocked by `pselect`, avoiding lost wakeups or a periodic
   exit-polling delay.

2. **Child diagnostics were unbounded (fixed).** Inherited stderr could emit an
   arbitrary amount of output. A nonblocking pipe now drains diagnostics while
   retaining only their last 8192 bytes. Each drain pass is bounded so a noisy
   child cannot starve cancellation. Failed exports report the retained tail
   and an explicit truncation notice. A child that closes stderr early does
   not cause a busy loop, and inherited writers do not delay direct-child exit.
   Ignoring SIGPIPE in the parent preserves cleanup when its diagnostic reader
   disappears; child SIGPIPE behavior is reset to default.

3. **Validation used buffered frame CSV and intentional child termination
   (improved).** The builder correctly checked decoded samples, not packets,
   but parsed ffprobe stdout through a fixed line buffer and terminated a
   successful probe. Buffered output could do unnecessary work before reaching
   the parent, and overlong lines could stall parser progress. Validation now
   runs FFmpeg to one PCM output frame into the null muxer, with empty-stream
   and decode-error rejection. This removes ffprobe from runtime dependencies,
   the text parser and healthy-child termination. Decoder/probe read-ahead
   still exists; this is a concrete orchestration change, not a measured speedup.
   N inputs still launch N validators and one final render. Combining validators
   in one null output was rejected during review because FFmpeg's frame-limit
   scheduling could reject a valid one-sample track beside a longer track.

4. **Closed standard descriptors could collide with owned descriptors
   (fixed).** Missing descriptors 0, 1 or 2 are filled with `/dev/null` before
   opening owned files. Spawn actions can no longer overwrite an input fd that
   happened to occupy a standard descriptor.

5. **Default RIFF size fields could overflow on long WAV exports (fixed at
   configuration level).** The seekable regular-file output is retained and
   `-rf64 auto` is enabled. Normal RIFF/data lengths are verified from file
   bytes. The >4 GiB transition was not exercised with a full-sized render.

6. **Sub-millisecond output can be reference-empty (documented boundary).**
   Nonempty 44.1 kHz clips with 1 or 10 samples, for example, can produce no
   samples through the frozen graph at 25%, 100% and 400%. The same boundary
   depends on input rate, sample count and ratio. The final render retains
   empty-output rejection and preserves an existing destination. Source
   emptiness is checked independently, before resampling. In accordance with
   `competition/CLARIFICATIONS.md`, these are reference limitations, not speed
   cases or supported header-only successes. Ordinary 5 ms and 20 ms clips are
   checked against the matched reference.

7. **MP3 chapter structures were still copied (fixed).** A chapter-bearing MP3
   retained its chapter timeline despite `-map_metadata -1`. The render now
   also sets `-map_chapters -1`. A regression check proves the source has a
   chapter and the resulting MP3 has neither chapters nor source metadata.

## File and audio audit

- The primary filter order, eight-decimal asetrate value, first-audio selection,
  track order, stereo layout, 48 kHz rate, 24-bit WAV, 320 kbps MP3 and metadata
  removal remain intact. No quality-reducing optimization was introduced.
- Exclusive publication remains an atomic `linkat`; a destination created
  during rendering is preserved. Overwrite remains atomic `renameat`. No
  output copy, audio buffer or extra publication subprocess was added.
- Input inode/device alias checks run twice. Tests insert both symlink and
  hardlink destinations during a paused render, with and without overwrite;
  source and destination contents survive. The final overwrite check and
  rename are not an atomic conditional operation. Repeated hostile directory
  swaps are not covered, but rename cannot write through a destination alias
  into the input inode. The temporary identity check also is not a sandbox
  against another writer with unrestricted directory access.
- Open input and directory descriptors survive input-path replacement and
  output-parent rename. Literal filenames, nonregular input rejection and
  `file,pipe` protocol restrictions remain in place.
- A nonzero compressed/container packet count is not accepted as proof of
  audio: a WAV with an incomplete PCM packet fails alone and in concatenation.
  A late damaged PCM packet after a decodable prefix passes preflight but is
  rejected by full-render `-xerror`, preserving the old destination.

## Verification

All results below are for the C entry only, using matched references from
that environment's FFmpeg. Long-running checks ran with `nice` in owned
`screen` sessions. Generated binaries, logs and scripts remain gitignored.

- **macOS arm64 release:** `make -C implementations/c all`; all 19 tests via
  `make -C implementations/c check`; all 11 existing tests via
  `python3 tests/integration.py implementations/c/resample`.
- **macOS sanitizers:** all 19 focused tests with
  `-O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer`.
- **Static analysis:** `clang --analyze -std=c11 -Wall -Wextra` without findings.
- **Linux arm64 Docker:** existing image `resample-cli-linux-test:latest`,
  temporary container `resample-c-fresh-review-linux`, GCC 12.2.0,
  FFmpeg/ffprobe 5.1.9, tests run as `nobody` so unwritable-parent checks are
  meaningful. Manifest release build, all 19 focused tests and all 11 existing
  integration tests passed. Schema validation and the manifest's literal run
  argv were exercised. Temporary compiler/test dependencies were installed in
  the container; the image and shared sources were not changed.
- **Audio evidence:** all 15 contract default/speed/cents combinations on mixed
  48/32 kHz tracks match reference-decoded WAV samples exactly; finite samples,
  duration/profile, distinct stereo tones, silence/impulse/seeded noise,
  WAV/MP3 order, first stream, metadata removal and chapter removal are covered. Short fixtures
  span 32/44.1/48 kHz at 25/100/400%, including 5 ms and 20 ms; nonempty results
  match reference samples and contain finalized RIFF/data lengths.
- **Failure evidence:** invalid flags/ranges, missing dependencies, empty and
  packet-bearing/no-decoded-sample input, corruption before/after preflight,
  FIFOs/directories, existing outputs, aliases, publication collision,
  descriptor-pinned renames, closed stdio, closed diagnostic reader, oversized
  stderr, validation cancellation, normal cancellation and a signal-ignoring
  child with forced termination all pass.

macOS environment: Apple clang 21.0.0 and FFmpeg/ffprobe 9.0.2. Check logs are
`review-validation.log`, `review-integration.log`, `.build/review-sanitize.log`
and `.build/linux-review.log`. These are ignored evidence, not deliverables
needed by the later harness.

## Remaining limitations

No throughput comparison, RSS measurement, >4 GiB render, x86 runtime, power-loss
recovery or exhaustive later-harness quality analysis was performed. There is
no fsync durability guarantee or SIGKILL cleanup. Hardlink publication requires
filesystem support; large input lists remain bounded by descriptor/argument
limits. Input contents must remain stable, and the output directory must not
be controlled by an adversarial concurrent writer. Reference-empty clips fail
cleanly as described above. The entry is an FFmpeg orchestration implementation,
with N+1 child startups, not a standalone DSP library or algorithm.
