# Resample linked export comparison

Complete measurements.

None of the six entries meets the whole contract: all fail the valid two-frame MP3 input case. Performance observations below describe the ordinary fixtures that qualify; they do not establish an overall winner.

## Correctness

| Entry | Required gates | Boundary diagnostics | Complete speed cases | Memory cases |
|---|---|---|---|---|
| zig | 181/198 | 9/28 | 192/192 | 192/192 |
| c | 197/198 | 28/28 | 192/192 | 192/192 |
| cpp | 197/198 | 28/28 | 192/192 | 192/192 |
| go | 197/198 | 28/28 | 192/192 | 192/192 |
| rust | 197/198 | 28/28 | 192/192 | 192/192 |
| node | 194/199 | 26/28 | 192/192 | 192/192 |

Boundary diagnostics are separate from ordinary correctness and performance. Reference-empty results never enter the speed ranking. Supported finite extensions beyond the reference are labeled separately.

### Named failures


**zig**

- reject-zero-frames.wav-1-0: accepted invalid input/options
- reject-zero-frames.wav-2-1: accepted invalid input/options
- reject-zero-frames.wav-2-0: accepted invalid input/options
- reject-packet-only-1-0: accepted invalid input/options
- reject-packet-only-2-1: accepted invalid input/options
- reject-packet-only-2-0: accepted invalid input/options
- input-alias-hardlink: accepted input alias
- process-late-alias-hard: race incorrectly succeeded
- process-late-alias-sym: race incorrectly succeeded
- process-cancel-int: Command '['/Volumes/PortableSSD/scratch/resample-benchmark-20261115/zig/resample', '/Volumes/PortableSSD/scratch/resample-benchmark-20261115/correctness/zig/race-cancel-int/input.wav', '-o', '/Volumes/PortableSSD/scratch/resample-benchmark-20261115/correctness/zig/race-cancel-int/out.wav', '--overwrite']' timed out after 6 seconds
- process-cancel-term: Command '['/Volumes/PortableSSD/scratch/resample-benchmark-20261115/zig/resample', '/Volumes/PortableSSD/scratch/resample-benchmark-20261115/correctness/zig/race-cancel-term/input.wav', '-o', '/Volumes/PortableSSD/scratch/resample-benchmark-20261115/correctness/zig/race-cancel-term/out.wav', '--overwrite']' timed out after 6 seconds
- metadata-and-chapters-mp3: source chapters copied
- boundary-32000-1-0.25: reference empty: header-only/nonfinite success
- boundary-32000-1-1: reference empty: header-only/nonfinite success
- boundary-32000-1-4: reference empty: header-only/nonfinite success
- boundary-32000-10-0.25: reference empty: header-only/nonfinite success
- boundary-32000-10-1: reference empty: header-only/nonfinite success
- boundary-32000-10-4: reference empty: header-only/nonfinite success
- boundary-44100-1-0.25: reference empty: header-only/nonfinite success
- boundary-44100-1-1: reference empty: header-only/nonfinite success
- boundary-44100-1-4: reference empty: header-only/nonfinite success
- boundary-44100-10-0.25: reference empty: header-only/nonfinite success
- boundary-44100-10-1: reference empty: header-only/nonfinite success
- boundary-44100-10-4: reference empty: header-only/nonfinite success
- boundary-44100-64-4: reference empty: header-only/nonfinite success
- boundary-48000-1-0.25: reference empty: header-only/nonfinite success
- boundary-48000-1-4: reference empty: header-only/nonfinite success
- boundary-48000-10-0.25: reference empty: header-only/nonfinite success
- boundary-48000-10-4: reference empty: header-only/nonfinite success
- boundary-48000-64-4: reference empty: header-only/nonfinite success
- cli-finite-decimal-hex-float: accepted non-decimal/nonfinite rate '0x1p7'
- cli-finite-decimal-underscore: accepted non-decimal/nonfinite rate '1_00'
- valid-two-frame-mp3-input: Valid two-frame MP3 input is unsupported by automatic detection: [h263 @ 0x763f048380] Bad UFEP type (6) [h263 @ 0x763f048380] header damaged [fc#0 @ 0x763ec0c000] Stream specifier ':a:0' in filtergraph description [0:a:0]aresample=48000,asetrate=48000.00000000,aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,asetpts=N/SR/TB[a0];[a0]concat=n=1:v=0:a=1[out] matches no streams. Error binding filtergraph inputs/outputs: Invalid argument resample: FFmpeg failed; see its diagnostic above
- reference-empty-absent-destination: reference-empty boundary claimed a header-only/nonfinite success
- actual-active-audio-cancellation: left temporary ['cancel.wav.resample-ff8103be558e4328.tmp']
- atomic-fresh-final-visibility: partial final destination visible: observed min=3538944 final=691200102

**c**

- valid-two-frame-mp3-input: Valid two-frame MP3 input is unsupported by automatic detection: [h263 @ 0x7bb2c58380] Bad UFEP type (6) [h263 @ 0x7bb2c58380] header damaged Stream map '' matches no streams. To ignore this, add a trailing '?' to the map. Failed to set value '0:a:0' for option 'map': Invalid argument Error parsing options for output file -. Error opening output files: Invalid argument resample: ffmpeg failed (exit 234) resample: input '/Volumes/PortableSSD/scratch/resample-benchmark-20261115/supplemental/raw-two-frame.mp3' failed decoded-sample validation

**cpp**

- valid-two-frame-mp3-input: Valid two-frame MP3 input is unsupported by automatic detection: [h263 @ 0x7c9d048380] Bad UFEP type (6) [h263 @ 0x7c9d048380] header damaged Stream map '' matches no streams. To ignore this, add a trailing '?' to the map. Failed to set value '0:a:0' for option 'map': Invalid argument Error parsing options for output file /dev/null. Error opening output files: Invalid argument resample: FFmpeg failed (exit 234); see diagnostic above

**go**

- valid-two-frame-mp3-input: Valid two-frame MP3 input is unsupported by automatic detection: resample: FFmpeg failed: exit status 234 [h263 @ 0x7c24c4c380] Bad UFEP type (6) [h263 @ 0x7c24c4c380] header damaged Stream map '' matches no streams. To ignore this, add a trailing '?' to the map. Failed to set value '0:a:0' for option 'map': Invalid argument Error parsing options for output file /dev/null. Error opening output files: Invalid argument

**rust**

- valid-two-frame-mp3-input: Valid two-frame MP3 input is unsupported by automatic detection: [h263 @ 0x75b1068380] Bad UFEP type (6) [h263 @ 0x75b1068380] header damaged Stream map '' matches no streams. To ignore this, add a trailing '?' to the map. Failed to set value '0:a:0' for option 'map': Invalid argument Error parsing options for output file /dev/null. Error opening output files: Invalid argument resample: FFmpeg failed (exit status: 234); see its diagnostic above

**node**

- process-late-alias-hard: race incorrectly succeeded
- process-late-alias-sym: race incorrectly succeeded
- boundary-48000-1-1: (node:62348) [DEP0205] DeprecationWarning: `module.register()` is deprecated. Use `module.registerHooks()` instead. (Use `node --trace-deprecation ...` to show where the warning was created) resample: Decoded audio is empty or incomplete: /Volumes/PortableSSD/scratch/resample-benchmark-20261115/fixtures/boundary-48000-1.wav
- boundary-48000-10-1: (node:62381) [DEP0205] DeprecationWarning: `module.register()` is deprecated. Use `module.registerHooks()` instead. (Use `node --trace-deprecation ...` to show where the warning was created) resample: Decoded audio is empty or incomplete: /Volumes/PortableSSD/scratch/resample-benchmark-20261115/fixtures/boundary-48000-10.wav
- valid-two-frame-mp3-input: Valid two-frame MP3 input is unsupported by automatic detection: (node:51231) [DEP0205] DeprecationWarning: `module.register()` is deprecated. Use `module.registerHooks()` instead. (Use `node --trace-deprecation ...` to show where the warning was created) resample: Input must contain mono or stereo audio: /Volumes/PortableSSD/scratch/resample-benchmark-20261115/supplemental/raw-two-frame.mp3
- late-corrupt-pcm-single: late damaged PCM packet accepted after a decodable prefix
- late-corrupt-pcm-mixed: late damaged PCM packet accepted after a decodable prefix

### Independent DSP response checks

| Entry | Passed / total |
|---|---|
| zig | 21/21 |
| c | 21/21 |
| cpp | 21/21 |
| go | 21/21 |
| rust | 21/21 |
| node | 21/21 |

## Matched-profile observations

Zig is the pinned comparison denominator and has contract failures listed above. Its timings describe successful ordinary exports; they do not certify its safety. C/C++/Go/Rust are FFmpeg orchestration implementations, not new DSP algorithms. All five use the same primary filter graph and encoding profile.

| Entry | Geometric mean Zig/candidate | Clear faster cases | Ties / overlap | Clear slower cases | Median memory/Zig |
|---|---|---|---|---|---|
| zig | 1.000 | 0 | 0 | 0 | 1.000 |
| c | 0.687 | 1 | 65 | 126 | 0.992 |
| cpp | 1.077 | 8 | 184 | 0 | 1.005 |
| go | 1.058 | 5 | 185 | 2 | 1.216 |
| rust | 0.700 | 2 | 114 | 76 | 1.001 |

The geometric mean weights each case equally. A clear speed observation requires a difference of at least 5% and disjoint observed min/max ranges. It is descriptive, not a causal or statistical confidence claim. See per-case medians, IQR, min/max, throughput, CPU, output sizes and RSS in cases.csv.

## Node profile comparison

Node uses 44.1 kHz float WAV and lamejs 320 kbps MP3; Zig/new entries use 48 kHz PCM24 WAV and libmp3lame. These are different encoding and DSP workloads. Node numbers are observational and must not be included in an unqualified six-way same-workload ranking. Node also fsyncs its temporary before publication; the other entries do not promise fsync durability. No hidden transcode or baseline modification was applied.

Node geometric mean Zig/Node time: 0.197; median memory/ Zig: 9.392. Both have the profile/workload caveat above.

## Measurement and limits

Each of 192 cases has one untimed warmup, at least seven speed repetitions in rotated order, and a separate memory pass. Timing includes startup, source validation, decode, DSP, encode, write, publication and process exit. Repetition output checks require a successful exit and the warmup output size; warmups/reference outputs are fully decoded for finite samples, duration and profile. No header-only or failed output is scored.

RSS is the maximum sampled contemporaneous sum across the wrapper and recursive child tree, including C validators. A requested 2 ms wait follows each scan; achieved cadence is recorded. This is a sampled estimate: process reads within a scan are not atomic, brief peaks may be missed, and shared mappings may be counted more than once. It differs from max single-process RSS and from the sum of unrelated per-process maxima, both retained only for audit. CPU includes the kernel child usage across the command and a sampled process-by-process lower bound. Memory instrumentation never enters the speed medians.

Processes are freshly launched for every export; caches are warm and never purged. First-launch times are retained separately; true disk-cold latency was not established. No physical listening or second-device access is implied. This report qualifies arm64 macOS 27.2 only; it does not establish Linux/Windows runtime or crash/power-loss safety.

Research had other active sessions and a continuously busy one-core Python process at setup. They were preserved. Host load and swap are recorded at each case. Results describe this shared host and their observed spread, rather than an idle-machine maximum.

Builds are outside timing. Build durations reflect local compiler/package caches and are not clean download times. The Node binary size is the runtime launcher; source/packages and shared runtime dependencies are separate deployment costs. Full toolchain, FFmpeg build configuration, executable hashes and source revisions are in environment.json, config.json and builds.json.

## Reproduce

Use competition/harness/README.md for pinned checkout/build/run commands. Fixtures are generated once with seed 20261115; their hashes are in fixtures.json. Full logs, fixtures, binaries and JSONL stay in the task scratch directory. The compact raw repetition CSV and process memory breakdown accompany this report.

## Per-case results

[Case table](cases.csv) · [Raw repetitions](runs.csv) · [Correctness evidence](correctness.csv) · [Performance qualification](performance-gates.csv) · [Memory process tree](memory-processes.json) · [Environment](environment.json)

## Measurement recovery

A PATH audit found that all timed candidates used Homebrew FFmpeg 9.0.1_1, while initial reference generation and probing used showcase FFmpeg 9.0.2. The candidate runtime and timed input bytes never changed. Full correctness and every affected performance qualification were repeated with the actual 9.0.1_1 binary; only corrected qualifications support the final report. Fixture creation remains pinned separately to 9.0.2, with the original hashes retained. See measurement-notes.json for correction status.

The first psutil memory sampler and an intermediate libproc adapter missed short children. Those memory rows were discarded and rerun using the validated native sampler. Uninstrumented speed rows were retained. A brief owned drive-inventory pass was stopped; affected cases were identified for complete reruns. See measurement-notes.json for the interval, affected cases and completion evidence.

The supplemental publication checker initially formatted min(empty observations) even when its success predicate passed. The diagnostic was corrected without changing the predicate, thresholds, candidates or timed samples, and the full supplemental phase was repeated. Coverage requires the 21 spectral tones generated by the existing Nyquist guard; one proposed tone above the fixture bandwidth is not representable. The superseded supplemental records remain in scratch; corrected records alone appear in correctness.csv.

## Shared host measurements

Across recorded case intervals of at least one second, normalized host CPU busy time was median 21.1% (range 14.9–71.3%). This includes the benchmark itself. One-minute load ranged 3.60–8.94. Existing swap use was 17,232,494,592 bytes at the first observation and 16,012,476,416 at the last. This was a shared host, not an idle-machine trial. See host-load.jsonl and host-load-summary.json.

## Process startup

Every benchmark launch is a new process. The following separate probe measures startup through canonical --help, with one initial observation and seven rotated repetitions. Filesystem caches were not purged. Node help exits in the canonical adapter before loading the audio modules; this is help latency, not a substitute for export startup. True cold-disk export latency is unverified.

| Entry | Initial help ms | Median help ms | Min-max ms |
|---|---|---|---|
| zig | 5.656 | 3.110 | 2.579-3.504 |
| c | 3.749 | 3.171 | 2.502-3.424 |
| cpp | 3.584 | 2.863 | 2.624-3.481 |
| go | 15.011 | 3.279 | 2.892-4.197 |
| rust | 5.402 | 2.822 | 2.142-4.334 |
| node | 121.688 | 105.858 | 103.703-121.505 |

## Representative case details

These cases are fixed workload examples, not best-of selections. The complete 192-case table remains authoritative.

### ten-minute_wav-wav_r0.5_fresh

| Entry | Median s | Min–max s | RSS bytes | RSS / Zig | Output bytes |
|---|---|---|---|---|---|
| zig | 2.537 | 1.079–2.936 | 21757952 | 1.000 | 345600102 |
| c | 1.506 | 1.228–1.726 | 21430272 | 0.985 | 345600138 |
| cpp | 1.619 | 1.284–1.964 | 21856256 | 1.005 | 345600102 |
| go | 1.492 | 1.218–1.823 | 25985024 | 1.194 | 345600102 |
| rust | 1.633 | 1.276–1.947 | 22200320 | 1.020 | 345600102 |
| node (different profile) | 5.749 | 5.399–6.601 | 1797177344 | 82.599 | 423360056 |

### ten-minute_wav-mp3_r0.5_fresh

| Entry | Median s | Min–max s | RSS bytes | RSS / Zig | Output bytes |
|---|---|---|---|---|---|
| zig | 6.346 | 6.174–6.488 | 20496384 | 1.000 | 48001964 |
| c | 6.258 | 6.194–7.014 | 20119552 | 0.982 | 48001964 |
| cpp | 6.414 | 6.095–7.554 | 20447232 | 0.998 | 48001964 |
| go | 6.496 | 6.132–7.527 | 24412160 | 1.191 | 48001964 |
| rust | 6.323 | 6.190–6.772 | 20414464 | 0.996 | 48001964 |
| node (different profile) | 23.292 | 23.008–24.346 | 2010939392 | 98.112 | 48001567 |

### concat-16_wav-wav_r1_fresh

| Entry | Median s | Min–max s | RSS bytes | RSS / Zig | Output bytes |
|---|---|---|---|---|---|
| zig | 0.485 | 0.421–0.711 | 39337984 | 1.000 | 17280102 |
| c | 1.171 | 1.002–1.423 | 38813696 | 0.987 | 17280138 |
| cpp | 0.379 | 0.356–0.454 | 41582592 | 1.057 | 17280102 |
| go | 0.394 | 0.358–0.442 | 46186496 | 1.174 | 17280102 |
| rust | 0.455 | 0.388–0.537 | 42385408 | 1.077 | 17280102 |
| node (different profile) | 1.788 | 1.672–1.895 | 178061312 | 4.526 | 21168056 |

## Short MP3 detection diagnostic

The Node 20 ms unity-rate MP3 consists of two MPEG Layer III frames. FFmpeg automatic probing mistakes these 2089 bytes for H.263 video; explicit MP3 demuxing successfully decodes 2304 stereo frames. Verification therefore requires the declared WAV/MP3 demuxer for all entries, in addition to the unchanged codec, sample-rate, channel, finiteness, duration and quality gates. This is outside timing and inserts no transcoding. The automatic-detection limitation is retained in node-short-diagnostic.json; those two Node rows carry a specific verification note.

## Short MP3 input compatibility

The same valid two-frame MP3 is also tested as an input to every frozen CLI. The declared-format decoder proves that it contains audio; the original frozen reference uses automatic input detection. Any failed CLI support is a named required gate, not a fast export. The performance matrix uses shared FFmpeg-generated MP3 inputs whose references qualify. Its timing observations therefore describe those qualified cases and do not establish full contract compliance or an overall winner. See raw-two-frame-input-reference.json and valid-two-frame-mp3-input in correctness.csv.

## Architecture tradeoffs

C launches one FFmpeg validator per input plus the final render (N+1 child launches). C++, Go and Rust attach one-frame decoded validation outputs to one FFmpeg render. All four publish by exclusive hard link or atomic overwrite rename, avoid a second output copy, and pin input descriptors. Zig fresh publication copies a completed temporary into an exclusively created final destination. Node sequentially launches ffprobe and FFmpeg for each input (2N audio-tool child launches), then retains whole decoded/rendered tracks and encoded bytes in memory; it fsyncs before publication. These are orchestration, validation, buffering and durability differences, not a language-only DSP benchmark.

Rust uses a 50 ms fallback poll timeout; observed bimodal short-launch latency is consistent with that process-wait path. This is a source-informed interpretation, not an isolated causal experiment. The fixed candidate source was not tuned after results.

CLI syntax checks for Node exercise the canonical adapter. The baseline receives translated linked speed arguments in the same Node process; no audio transforms or encoders were inserted by the adapter.
