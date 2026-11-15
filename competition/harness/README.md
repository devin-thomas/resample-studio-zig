# Reproduce the linked export comparison

The harness never edits entry or baseline source. It validates the canonical
Node CLI and invokes only linked speed mode. Node remains a separate profile:
44.1 kHz float WAV / lamejs MP3, versus 48 kHz PCM24 / libmp3lame.

Use an arm64 macOS host with FFmpeg/ffprobe, Zig 0.15.2, C/C++ compilers,
Go, Rust/Cargo, Node and npm. The recorded environment has exact versions,
FFmpeg build configuration and binary hashes. Linux portability of this harness
has not been established. Builds and measurements run sequentially with `nice`
in a task-owned `screen`; leave unrelated process trees alone.

```sh
WORK=/absolute/path/to/scratch/run
python3 -m venv "$WORK/venv"
"$WORK/venv/bin/pip" install -r competition/harness/requirements.txt
git worktree add --detach "$WORK/candidates" ebb4fb2b19b68c0a4cce02b5b125c8e4be1c349a
git worktree add --detach "$WORK/zig" 5ab090b54caf4efd9b0c2712571bfd20b24e5554
# In a fetched devin-thomas/resample-studio checkout:
git worktree add --detach "$WORK/node" 7c6e7a91bacabb63eb2895af5ec0101a812fce33
# Return to this harness checkout. Set ZIG to the Zig 0.15.2 executable.
ZIG=/absolute/path/to/zig nice -n 10 "$WORK/venv/bin/python" competition/harness/prepare.py --work "$WORK"
nice -n 10 "$WORK/venv/bin/python" competition/harness/correctness.py --work "$WORK"
nice -n 10 "$WORK/venv/bin/python" competition/harness/benchmark.py --work "$WORK" --memory-repetitions 1
nice -n 10 "$WORK/venv/bin/python" competition/harness/supplemental.py --work "$WORK"
"$WORK/venv/bin/python" competition/harness/report.py --work "$WORK" --destination "$WORK/page"
```

`prepare.py` records the environment, validates entry manifests, builds outside
timing, and creates seeded PCM fixtures and shared MP3 encodings. Its PATH adds
research's existing compiler directory; change that directory to the toolchain
location on a different host before preparation, and retain the recorded PATH.
No native CPU tuning or fast-math flags are added. FFmpeg's own default thread
selection is shared, and its build configuration is recorded. The pinned Zig
macOS 14 target avoids the documented macOS 27 linker failure.

`correctness.py` emits named gate evidence to `correctness.jsonl`. A failed gate
does not change the contract or candidate source. Boundary diagnostics are
separate. The Node profile reference imports its pinned shared DSP and lamejs
encoder directly; duration, tones, channel/track order and finiteness are checked
independently. Zig and the four new entries use the exact matched-profile FFmpeg
reference, including codec-delay alignment for MP3. Exact waveform agreement
with the frozen pipeline establishes their resampler quality; they do not claim
an alternative DSP algorithm.

The 192 performance cases cross six workloads (20 ms, 1 s, 60 s, 600 s,
2 x 30 s and 16 x 3.75 s), two input formats, two output formats, four ratios
and two publication modes. Concatenation sources intentionally repeat the same
fixture to isolate input-count overhead. Ordered distinct mixed-rate sources
are covered in correctness tests. Every case first requires a nonempty reference
and a fully decoded, finite, profile/duration-valid candidate warmup. Failed or
empty exports are excluded. Every measured run checks exit and output size.

Seven measured speed repetitions rotate entry positions, following one untimed
warmup per entry/case. Wall time includes process launch, validation, audio
processing, output write and publication through process exit. Output setup and
validation are outside timing. All processes are newly launched; file caches
are warm and never forcibly purged. The separately retained first launches do
not establish disk-cold latency. No best-of-only claims are made.

Memory passes run separately. A sampler requests a 2 ms pause between scans,
recursively discovers children and sums their contemporaneous resident sizes.
It records the achieved cadence, identities and process breakdown at the peak.
This is a sampled concurrent process-tree RSS estimate, not a single wrapper
RSS, not the sum of unrelated process maxima, and not unique physical memory
(shared mappings may be counted more than once). Short-lived peaks between
samples may be missed. C's sequential validators are included. CPU uses the
kernel's RUSAGE_CHILDREN difference around the whole command; sampled per-PID
CPU is also retained as a lower bound for diagnosing incomplete child coverage.
Neither profiler time nor sampled wall time enters the speed ranking.

Raw files are append-only and resumable by case/entry/pass/repetition. To rerun
from scratch, select a new task-owned scratch directory; do not combine machines
or configurations in one run. Keep fixture bytes, binaries, full JSONL and logs
in scratch. The report's compact CSVs and environment manifest retain the
measurements needed for independent assessment.

`supplemental.py` runs after the timed matrix. It checks real active FFmpeg
cancellation, 1 ms observation of final-file publication, join continuity, all
remaining 32/48 kHz waveform fixtures, and independent passband/alias response
with identical central windows and a 1e-7 FS floor. It also preserves full probe
evidence for the Node 20 ms unity-rate MP3 automatic-detection diagnostic. It appends named
gates to the same correctness file; it never runs during timing.

To repeat a selected case, retain/archive the prior raw rows for that case,
remove only those rows from the active scratch result file, and pass its exact
ID with `--case`. Multiple `--case` flags are allowed. A selected rerun performs
fresh candidate warmups, seven speed repetitions and the requested memory passes.
The full matrix remains in cases.json. The published run repeated the cases
affected by an owned drive-inventory pass after that pass had been stopped.
Measurement notes identify the complete repeated case set and discarded rows.

Resumed benchmark launches verify all three pinned checkout revisions, clean
tracked candidate/baseline source, and the recorded executable hashes. The four
new entries remain detached at the same candidate commit even while this harness
and its report are committed on the competition branch.

`report.py` writes the interactive review page, REPORT.md, raw runs.csv,
case summaries, named correctness evidence, per-case qualification, fixture
hashes, environment/build details, and concurrent memory process breakdowns.
Throughput is input audio seconds divided by elapsed wall seconds. Output bytes
and source sizes are separate measures. RSS ratios/deltas compare identical cases
against Zig; Node's ratios always retain the different-profile caveat. Memory
has one separate sampled pass per case in the published study and therefore no
repeat-based memory confidence interval. Short-case RSS is especially sensitive
to sampling phase; small memory differences are descriptive, not clear wins.

Overwrite setup uses the same 25-byte regular-file seed (`pre-existing destination\n`) for every entry; it is created outside timing. It does not model deletion of a large prior export. Fresh setup removes the task-owned destination before launch.

The recorded study uses Homebrew FFmpeg/ffprobe 9.0.1_1 for every candidate,
matched reference, and final probe. Input MP3 fixtures were generated once with
showcase FFmpeg 9.0.2; their bytes remain identical for every entry. To reproduce
those fixture hashes, pass `--fixture-ffmpeg /path/to/that/ffmpeg-9.0.2` to
prepare.py while putting the recorded runtime FFmpeg 9.0.1_1 on PATH. The
harness resolves its reference executable after constructing PATH and refuses a
reference/runtime mismatch. An initial mismatch was corrected without changing
the timed runtime: all correctness gates and every affected performance
qualification were repeated against the actual 9.0.1_1 binary. Superseded
qualification records remain in private scratch evidence.

For qualification-only reproduction, use benchmark.py with `--qualify-only
--refresh-qualification`. Add `--case` flags to bound it to named cases. This
regenerates matched references and fully decodes fresh candidate exports without
adding timing samples. Reports use the latest qualification per entry/case.

Output verification forces the declared WAV or MP3 demuxer for every entry;
codec, rate, channels, finite samples, duration and waveform tolerances remain
unchanged. This rejects wrong containers while avoiding a demonstrated FFmpeg
false-positive H.263 auto-detection on a valid 2089-byte, two-frame Node MP3.
The diagnostic records automatic probing, explicit MP3 probing/decoding, MPEG
header fields and packet framing. Timed commands and encoders are unaffected.
That same valid raw MP3 is independently exercised as an input to every CLI; any
unsupported automatic input detection is a named required compatibility failure.
The frozen reference's result is retained separately. Ordinary performance
observations concern only the fixtures whose references and exports qualify;
they do not imply complete contract compliance or an overall competitive winner.

The final report requires all 198 named required gates per native entry (199
for Node), 28 boundary diagnostics and 22 independent DSP checks per entry,
alongside the complete qualified performance matrix. It retains the latest
result for repeated named checks. Audio-quality failures suppress speed verdicts
and aggregate comparisons; raw elapsed times remain available. Invalid measured
repetitions are named and keep the report provisional. Repetition validation is
exit status and warmup byte-size agreement; complete audio decoding occurs in
the untimed qualification, rather than after every timed repetition.
