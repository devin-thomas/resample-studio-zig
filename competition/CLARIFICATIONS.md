# Contract v1 clarifications

## Reference-empty submillisecond inputs

Fresh reviews found that the frozen FFmpeg resampler graph can emit zero
samples for a valid, nonempty submillisecond source at some ratios. This is a
reference limitation, not evidence of an empty input and not a speed result.

The future harness must first establish that its matched-profile reference
produces a valid audio stream for each performance case. Reference-empty cases
are correctness diagnostics and are excluded from speed ranking. Record the
fixture, rate and reference result explicitly. Do not count a failed render or
header-only export as fast processing. Ordinary short-clip qualification uses
5 ms and 20 ms fixtures and checks the reference before scoring; retain
submillisecond cases as separately reported boundary diagnostics.

Input emptiness is determined from decoded source samples, not compressed
packet count or the post-resampling duration. A genuinely zero-frame source
must fail even in a concatenation. For a reference-empty boundary case, a
candidate may fail cleanly with a diagnostic while preserving a pre-existing
destination. It must not claim supported output for that case. If a candidate
adds support beyond the reference, report and validate that extension
separately; it is not part of an identical-workload speed claim.

This clarification does not change the canonical CLI, eligible linked
processing, output profiles, primary graph or ordinary quality gates. It
records an observed baseline boundary before comparative testing exists.

## Source chapters

The metadata rule includes chapter structures and chapter tags. New entries
explicitly disable chapter copying with `-map_chapters -1` on the primary
output, in addition to `-map_metadata -1`. Fresh review reproduced chapter
structures surviving the latter flag alone on chapter-bearing MP3 inputs.
This output option does not change the primary DSP graph or encoding profile.

The pinned Zig source supplies only `-map_metadata -1`. Chapter-bearing
baseline compatibility therefore must be checked and reported by the future
harness; do not assume that stripping global tags removes chapters. The
coordinator did not execute or change that baseline. Keep any discovered
baseline failure visible and do not inherit it in the new entries.
