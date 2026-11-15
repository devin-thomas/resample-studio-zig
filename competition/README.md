# Linked varispeed entries

[CONTRACT.md](CONTRACT.md) defines eligibility, audio/file semantics, baseline
adapters, correctness gates and the future performance protocol.
[entry.schema.json](entry.schema.json) defines the machine-readable registration
format. The four entry manifests are in `implementations/c`, `cpp`, `go`, and
`rust` relative to the repository root.

[CLARIFICATIONS.md](CLARIFICATIONS.md) records the reference-empty
submillisecond boundary found during fresh review and how the later harness
must report it without scoring failed exports as fast results.

## Comparative results

The separate [November 15 comparison](results/2026-11-15/REPORT.md) evaluates
all six entries on arm64 macOS: 192 cases per entry, seven speed repetitions
and a separate process-tree memory pass per case. Raw measurements, named
correctness evidence, environment details and a standalone interactive page
accompany the report. [Reproduction instructions](harness/README.md) pin the
sources and protocol. All entries fail the valid two-frame MP3 input check;
ordinary-export observations do not establish an overall contract winner.

## Harness integration

Discover `implementations/*/entry.json`, validate against the schema, resolve
`cwd` relative to the checkout root, and execute `build` there without a shell.
Execute `run` in that same cwd, appending canonical contract CLI arguments.
Executable paths beginning `./` are relative to cwd; bare command names resolve
on PATH. Build commands are release builds and must be outside timing. No
environment-dependent absolute paths belong in manifests. The runner supplies
the output path, fixtures, environment, timeout and profiling independently.
Only execute trusted entry manifests, which contain commands.

Node and Zig source revisions and argv translations are specified in the
contract. Register them as separate baseline entries with their actual output
profiles. Node must explicitly use linked speed mode, including for cents
requests translated mathematically to speed. No independent pitch/speed mode
is eligible. Do not run a package manager in a timed launch.

These entries use FFmpeg for audio DSP and implement process/file orchestration
in each named language. They are implementations of the export CLI, not new
resampling algorithms. The measured work includes FFmpeg and all child processes.

The coordinating session does not run the four-way comparison. Per-entry checks
and fresh-session reviews establish buildability and focused correctness only;
the later harness must apply the full quality gates before ranking speed.
