# Linked varispeed entries

[CONTRACT.md](CONTRACT.md) defines eligibility, audio/file semantics, baseline
adapters, correctness gates and the future performance protocol.
[entry.schema.json](entry.schema.json) defines the machine-readable registration
format. The four entry manifests are in `implementations/c`, `cpp`, `go`, and
`rust` relative to the repository root.

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
