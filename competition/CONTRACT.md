# Resample hackathon contract v1

This contract is frozen before the four implementation sessions start. Scope is
local-file export with **linked varispeed only**. Pitch, speed and duration must
change together. Independent pitch shifting or time stretching is ineligible.
No comparative benchmark is run by the coordinating session.

## Entrypoint

Each new entry lives in `implementations/{c,cpp,go,rust}/` and provides
`entry.json` with keys `id`, `language`, `contract` ("resample-linked-v1"),
`cwd` (repository-relative), `build` (argv array), `run` (argv prefix array),
`outputProfile`, `dependencies` (array), and `improvements` (array).
Build once outside measured runs. Resolve cwd against repository root, run the
build there, and append these arguments to run without shell interpretation:

```
resample [INPUT ...] [-o|--output OUTPUT.wav|OUTPUT.mp3]
         [--speed PERCENT | --cents CENTS] [--overwrite] [-- INPUT ...]
```

No subcommand. Inputs concatenate in supplied order. Options may interleave
with inputs before `--`; after it every token is an input filename. Require
at least one input and exactly one output. Extensions are case insensitive.
Default ratio is 1; speed is finite decimal 25..400 inclusive divided by 100;
cents is finite decimal -2400..2400 inclusive and ratio = 2^(cents/1200).
Reject duplicate rate/output options, combined rate flags, unknown options,
missing values, NaN, infinity and trailing junk. No arguments and -h/--help
print usage and exit 0; --version prints an identifying version and exits 0.
Errors exit nonzero with a useful stderr diagnostic. Successful render exits 0
only after the complete output is closed and published. Diagnostic wording,
version text and informational stdout need not match across languages.

## Audio behavior and eligibility

For every track, output duration = decoded input duration / ratio and tone
frequency = original frequency * ratio. Normalize mixed input sample rates
before linked varispeed. Join tracks with no intentional gap, overlap, fade,
gain normalization, trimming, reordered channels, or substituted audio. Mono
and stereo WAV and MP3 inputs are mandatory; first audio stream is selected.
Other FFmpeg-supported local formats are optional. No network inputs, shell
execution, playback, visualizers or independent pitch/speed features are scored.

The new entries must implement profile `zig-48k`: stereo 48000 Hz,
pcm_s24le WAV or libmp3lame 320 kbps MP3; source metadata is not copied.
The original Zig baseline filter per track is:

```
aresample=48000,asetrate=48000*ratio,aresample=48000,
aformat=sample_fmts=fltp:channel_layouts=stereo,asetpts=N/SR/TB
```

Its asetrate value is formatted with eight fractional decimal places, then
tracks concatenate with `concat=n=N:v=0:a=1`. Reusing that FFmpeg pipeline is
allowed, as are native/library implementations meeting the same behavior and
quality gates. Describe which layer actually performs DSP; do not call a CLI
wrapper a new resampling algorithm. Do not lower filter quality, output bitrate,
sample rate or bit depth to claim a speed improvement.

## File and process behavior

Inputs must be readable local regular files; reject missing, corrupt, empty,
non-audio and FIFO/directory inputs. Handle spaces, quotes, Unicode and leading
dash names literally. Never modify an input, including symlink/hardlink aliases.
Never replace an existing destination without --overwrite, including one created
while rendering. A failed conversion preserves a pre-existing output and leaves
no final output when it did not exist. Render to a unique sibling temporary,
publish only on success and clean up on handled errors. Avoid whole-output
buffering when streaming is possible. Report cancellation/atomic-publication
improvements explicitly; preserve safety under optimization. Child failures
must propagate, dependencies missing on PATH must give an actionable error.

## Existing entry adapters

Zig baseline: source commit 5ab090b54caf4efd9b0c2712571bfd20b24e5554 of
devin-thomas/resample-studio-zig; build `zig build -Doptimize=ReleaseSafe`,
run `zig-out/bin/resample` with canonical arguments directly. On macOS 27 the
documented target/linker workaround in README is allowed and must be recorded.

Node baseline: commit 7c6e7a91bacabb63eb2895af5ec0101a812fce33 on `origin/cli`
of devin-thomas/resample-studio (existing worktree
`/Volumes/PortableSSD/scratch/resample-regular-cli`). Use its documented
dependency install and run `node --import tsx cli/main.ts export ...`.
Canonical --speed P maps to `--mode speed --speed P`; --cents C maps to
`--mode speed --speed (100 * 2^(C/1200))`; no rate maps to
`--mode speed --speed 100`. Output/input/overwrite and -- translate directly.
Never invoke unlinked mode. Adapter must validate canonical arguments before
translation; never use Node's narrower --pitch flag for the full cents range.
Its existing profile is `node-44k-float`: stereo 44100 Hz IEEE float WAV and
lamejs 320 kbps MP3. This is eligible linked processing, but NOT the identical
encoding workload: label separately, no unqualified six-way speed ranking.
Do not rewrite either baseline or silently transcode within measured wrappers.

## Later harness correctness gates

These are specifications for the later harness, not a request to build or run
the comparative harness now. Reuse Zig tests/integration.py for individual
new-entry smoke tests where applicable. It is not the complete quality gate.

Use identical generated fixtures for every entry: silence, mono/stereo distinct
tones, impulses, boundary transients, sweeps and deterministic seeded noise;
44.1/48/32 kHz inputs; WAV and MP3; one and mixed ordered inputs; short clips,
one-second clips and long tracks. Test default, 25/50/100/200/400%, fractional
73.5/125.5%, cents -2400/-1200/-300/0/300/1200/2400. Check metadata using
ffprobe, decode outputs for measurement outside timing, verify finite samples.
WAV duration error <= max(5 ms per input, 0.1% expected duration); MP3 <= that
plus 50 ms encoder padding. Tone error <= max(2 Hz, 0.5% expected frequency).
Check stereo order, audible track order and no unexplained silent tail or gap.
Use matched-profile FFmpeg reference generated from the stated pipeline and
same FFmpeg binary. For zig-48k WAV compare aligned decoded samples: RMS error
<= 1e-4 FS and peak error <= 1e-3 FS on nonclipping fixtures. Alternative DSP
must additionally show no >1 dB passband loss below 0.8 * surviving Nyquist
and alias energy no worse than reference +3 dB using the same analysis windows
and 1e-7 FS measurement floor. Node has semantic/tone/duration gates and its
own profile reference; no bit-exact parity is implied. MP3 quality comparison
must align codec delay and use profile-matched encoders. Quality failures are
exclusions from performance ranking, not a faster result.

Also test invalid/duplicate flags, range boundaries, literal filenames, missing
dependencies, corrupt files, existing outputs, aliases, unwritable parents,
publication collision, and failed child cleanup. Record pre-existing baseline
failures explicitly; never relax new-entry requirements to inherit a bug.

## Later harness performance protocol

Pin source revisions, release build flags/toolchains, architecture, dependency
versions and full FFmpeg build configuration. Same host, fixture bytes, disk,
environment and CPU/thread budget. No concurrent builds or benchmark jobs.
No -march=native advantage unless all native entries use the same CPU target.
Use each implementation's documented release build. Include process startup,
validation, decode, DSP, encode, output write and final publication in external
wall timing; stop only after process exit. No predecoded/cached render result.
Do not fsync only one contender without reporting the durability difference.
Measure fresh outputs and overwrite as separate cases with setup outside timing.
Use one untimed warmup and at least seven measured repetitions, randomized or
rotated entry order; report median and spread, never best-only. Report cold
startup separately from warm-cache end-to-end throughput. Include short clips,
one and ten minute tracks, and 2/16-input concatenations, WAV/MP3 and ratios
0.5/1/1.255/2. Compare peak RSS and process-tree CPU, output size, binary size,
build time and dependencies separately. Include FFmpeg child memory/time.
Speedup = baseline median / candidate median for the SAME profile and case.
Call speed a tie when within 5% or observed variability; improvements such as
lower memory, safer publication, simpler deployment or cancellation may justify
an entry without speed superiority. No speed claims before measurement.

## Builder and fresh reviewer deliverables

Builder: working release entrypoint, entry.json, README with exact build/run,
focused validation, architecture and concrete optimization hypothesis. Keep
all edits inside the assigned language directory. No comparative testing.
Reviewer: a new session after builder completion; inspect adversarially, fix
architecture/safety/performance issues, run focused checks and write REVIEW.md
with findings, actual changes, evidence and unresolved limitations. Preserve
contract v1. Neither role edits the baselines, sibling entries or shared contract.
Coordinator owns integration/commits. No worker commits, pushes, sends external
notifications, spawns workers or creates chats. Stop only owned processes by
exact PID or own screen name; never killall/pkill or touch another session.
