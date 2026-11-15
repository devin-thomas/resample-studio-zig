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
independently. The four other entries use the exact matched-profile FFmpeg
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
This is a sampled concurrent process-tree RSS lower bound, not a single wrapper
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
