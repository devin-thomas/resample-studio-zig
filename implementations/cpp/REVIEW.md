# C++ fresh architecture review

Completed against `competition/CONTRACT.md` and
`competition/CLARIFICATIONS.md`. Edits are confined to `implementations/cpp`.
No other entry was invoked or benchmarked. No commits, external notifications,
workers or new chats were created. The builder was not contacted.

## Findings and fixes

1. **Input validation had a pathname race.** The builder opened and inspected an
   input, closed it, canonicalized its name, then let FFmpeg reopen that name.
   Replacement could change the decoded source or turn a validated regular file
   into a blocking FIFO. Inputs now remain open and are explicitly inherited
   through spawn, with FFmpeg reading `/dev/fd/N`. This also removes the separate
   canonical-path traversal. Darwin needs `addinherit_np`; glibc uses spawn dup2.
   The regression pauses before real FFmpeg starts, replaces the original input
   pathname with a FIFO, resumes, and verifies identical output bytes.

2. **Cancellation could wait indefinitely; PID ownership depended on inherited
   SIGCHLD behavior.** Signals still forward only to the owned child. Ignored
   cancellation escalates to SIGKILL after a one-second grace period, both while
   draining diagnostics and while waiting after stderr EOF. An exception guard
   kills and reaps the child. Resetting inherited SIGCHLD ignore ensures exited
   children remain waitable; WNOWAIT retains the zombie until forwarding is
   disabled under the cancellation-signal mask. No signal is sent after reaping.
   Owned descriptors are kept above standard-stream numbers, including when the
   caller closed stdin/stdout. Tests cover these inherited process conditions,
   all three handled signals, and a child that ignores cancellation.

3. **FFmpeg diagnostics were unbounded.** Stderr now drains into a capped 16 KiB
   tail, emitted with an explicit truncation notice when needed. No duration-sized
   output buffer or diagnostic collection is used. A 1 MiB diagnostic-flood test
   verifies bounded stderr, preservation of the final diagnostic, error exit and
   staging cleanup. Normal completion consumes pipe EOF promptly; there is no
   deliberate fixed delay on successful conversion.

4. **Relative PATH entries broke after staging chdir.** FFmpeg is resolved in the
   caller's directory before spawn. The manifest still uses the same build/run
   argv, and a real FFmpeg test now covers a relative PATH entry. Missing tools
   still give an actionable installation/PATH diagnostic.

5. **Argument construction copied the complete graph and repeatedly grew vectors.**
   Input, identity and command vectors reserve capacity from input count. Input
   arguments avoid initializer-list copies, and the graph string moves directly
   into argv. These are concrete allocation/traversal reductions, not a measured
   throughput claim. Pinned inputs retain one descriptor per track; diagnostic
   collection adds one bounded pipe. No extra runtime subprocess was introduced.

Changed files: `main.cpp`, `validate.py`, `entry.json`, `README.md`, and this file.
The existing Makefile and ignore rules remain sufficient; generated binaries,
fixtures, logs and exit files remain ignored.

## DSP, empty tracks and short inputs

FFmpeg remains the DSP implementation. The frozen per-track filters, eight-place
asetrate formatting, concat graph, stereo 48 kHz profile, PCM24 WAV and 320 kbps
libmp3lame MP3 are unchanged. No independent pitch/time mode, padding, resampler
quality reduction or special fast path was added.

The discarded PCM validation sinks remain intentionally decoded, one-frame
outputs. Stream-copy packet counting would be unsound. A new Ogg Opus fixture
contains an audio packet but has its entire decoded frame discarded by valid
skip/granule metadata. The test independently confirms packets exist and decoded
PCM is empty, then verifies that concatenating it after a valid track fails with
no published output. Zero-frame WAVs and ordinary corrupt inputs still fail.
No ffprobe process runs in the entry itself. The Opus regression requires
libopus encoding only in the test environment.

The short-input test covers 5 ms and 20 ms at 32/44.1/48 kHz, each at
25/100/400% speed. All 18 combinations have a nonempty frozen-graph reference,
match its PCM bytes exactly, meet duration tolerance, and have finalized RIFF
and data lengths equal to actual file contents. FFmpeg writes a seekable regular
staging file; output is never sent through a nonseekable WAV pipe.

Two boundary diagnostics use 64 mono source frames at 44.1 and 48 kHz, at 400%.
The frozen reference emits zero samples for both. The entry cleanly fails both
with and without an existing output and preserves old bytes. These are the
separately reported reference-empty limitations permitted by CLARIFICATIONS.md;
they are not supported exports or performance results. An intermediate attempt
to accept header-only output was removed after that clarification. Source
emptiness remains independently checked by decoded validation sinks.

## Verification

Host: arm64 Mac Studio, macOS 27.2, Apple clang 21.0.0, FFmpeg 9.0.2 with
libmp3lame. Work ran with `nice -n 10` in owned `cpp-review-*` screen sessions.

- `make release`: passed with `-std=c++17 -O3 -DNDEBUG -Wall -Wextra -Wpedantic`,
  no warnings. Evidence: `review-build.log`.
- `python3 validate.py`: all 19 focused tests passed. Evidence:
  `review-adversarial.log`. Includes all fourteen contract rate settings with
  finite, exactly reference-equal decoded WAV samples; mixed rates; WAV/MP3
  profile and metadata checks; first-audio-stream selection; manifest cwd/run;
  literal names; invalid inputs/options; missing dependencies; alias/collision
  and overwrite tests; child failures; cancellation; and new adversarial cases.
- `python3 ../../tests/integration.py ./build/resample`: all 11 existing tests
  passed. Evidence: `review-integration.log`; `review-final.exit` is 0.
- After strengthening the short test to compare all 18 PCM references directly,
  `python3 -m unittest -v validate.ContractTests.test_seekable_wav_header_and_short_inputs`
  passed. Evidence: `review-short-reference.log` and `.exit` (0).

The registration retains `contract: resample-linked-v1`, `cwd:
implementations/cpp`, `build: ["make", "release"]`, `run:
["./build/resample"]`, and `outputProfile: zig-48k`. Its actual run argv was
executed by the focused manifest test. Dependency and improvement descriptions
now describe descriptor inheritance, bounded diagnostics and cancellation.

## Remaining limits

- No-overwrite `linkat` is atomically exclusive, including a racing destination.
  Overwrite alias checking and `renameat` are separate operations. POSIX provides
  no portable rename conditional on the inode just checked. Aliases present at
  initial/final checks, including one created during render, are rejected, but a
  hostile writer can swap the destination between final check and rename.
  Rename never writes through that link or changes source audio bytes; strict
  rejection of this last-instant directory-entry replacement is not guaranteed.
- Open descriptors pin source identity, not immutable source contents. Same-user
  mutation of open source data or private staging entries is outside the safety
  boundary. Directory descriptors anchor publication to the opened directory,
  even if its pathname is renamed.
- Atomic visibility is not fsync durability. Wrapper SIGKILL/crash can leave
  staging; uninterruptible kernel I/O can defeat a cancellation time bound.
- Linux/glibc execution, crash durability, full sweep/noise/alias quality gates,
  long-track process-tree RSS and comparative performance remain unverified by
  this review. No speed, memory or cross-language superiority is claimed.
- `/dev/fd`, POSIX spawn extensions, FFmpeg with required codecs/filters and
  hard-link support for no-overwrite publication are real runtime dependencies.
  Windows and filename-dependent/relative-reference optional containers are not
  promised; mandatory standalone WAV/MP3 inputs were exercised.

## Final chapter-metadata fix

The coordinator supplied a late source-metadata finding. The new
`test_source_chapters_are_not_copied` independently reproduced it against the
previous release: a source MP3 chapter survived `-map_metadata -1`, with its title
replaced by FFmpeg's `ch0`. Evidence: `review-chapters-before.log` (expected
regression failure). The fixture verifies that its source MP3 has one real
chapter, a chapter title and a format title before attempting either export.

The primary output argv now explicitly includes `-map_chapters -1` beside
`-map_metadata -1`. The regression renders both MP3 and WAV at 125.5% and checks
that chapters and source titles are absent from the complete ffprobe response.
The frozen DSP graph, codecs, profile, publication behavior and `entry.json`
remain unchanged. No sibling, contract or shared file was edited.

Final checks after the fix, run with `nice -n 10` in the owned
`cpp-review-chapters-final` screen session:

- Release build: passed without warnings (`review-chapters-build.log`).
- New chapter regression: passed for both output formats
  (`review-chapters-targeted.log`).
- Focused suite: all 20 tests passed (`review-chapters-validation.log`), including
  the fourteen-rate reference checks and ordinary short-reference checks.
- Existing integration suite: all 11 tests passed
  (`review-chapters-integration.log`). `review-chapters-final.exit` is 0.
- `git diff --check -- implementations/cpp`: passed.

No comparative testing, commits or manifest changes were made. The remaining
limits listed above are unchanged.
