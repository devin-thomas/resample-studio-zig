# Resample Studio

Transform and concatenate audio. Written in Zig, powered by FFmpeg.

Requires [Zig 0.15.2](https://ziglang.org/download/) and [FFmpeg](https://ffmpeg.org/download.html) on `PATH`.

```sh
zig build -Doptimize=ReleaseSafe
zig-out/bin/resample song.wav --cents -300 -o slowed.mp3
zig-out/bin/resample first.wav second.mp3 --speed 125 -o joined.wav
```

Inputs join in order. Pitch and speed change together; `--speed 50` is an octave
down and twice as long. Choose `--cents` or `--speed` (25-400%). Omit both to
concatenate at the original speed. Output is 48 kHz stereo WAV (24-bit) or MP3
(320 kbps). Existing files require `--overwrite`. Use `--` before filenames
starting with `-`.

Builds on macOS, Linux, and Windows. On Windows, use `zig-out\bin\resample.exe`.

```sh
zig build test
python3 tests/integration.py zig-out/bin/resample
zig build -Dtarget=x86_64-linux-musl -Doptimize=ReleaseSafe
zig build -Dtarget=x86_64-windows -Doptimize=ReleaseSafe
```
