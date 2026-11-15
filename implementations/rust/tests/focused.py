"""Rust-entry correctness checks, never a cross-language benchmark.

Usage: python3 tests/focused.py [target/release/resample]
FFmpeg/ffprobe are test dependencies; only FFmpeg is a runtime dependency.
"""
import array
import json
import math
import os
from pathlib import Path
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import time
import unittest
import wave

ENTRY = Path(__file__).resolve().parents[1]
BINARY = Path(sys.argv.pop(1) if len(sys.argv) > 1 else ENTRY / "target/release/resample").resolve()
FFMPEG = shutil.which("ffmpeg")


def tone(path, rate=44100, channels=1, frames=None):
    frames = rate if frames is None else frames
    with wave.open(str(path), "wb") as out:
        out.setparams((channels, 2, rate, 0, "NONE", "not compressed"))
        out.writeframes(b"".join(
            struct.pack("<" + "h" * channels, *[
                round(5000 * math.sin(2 * math.pi * (440 if c == 0 else 880) * i / rate))
                for c in range(channels)
            ]) for i in range(frames)
        ))


def decode(path):
    return subprocess.check_output([FFMPEG, "-v", "error", "-i", str(path), "-f", "f32le", "-"])


class Focused(unittest.TestCase):
    def setUp(self):
        (ENTRY / "target").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="focused-", dir=ENTRY / "target")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.input = self.root / "first.wav"
        tone(self.input)

    def cli(self, *args, success=True, env=None):
        result = subprocess.run([str(BINARY), *map(str, args)], cwd=self.root,
                                capture_output=True, env=env, timeout=15)
        self.assertEqual(result.returncode == 0, success, result.stderr.decode(errors="replace"))
        self.assertEqual(list(self.root.glob(".resample-rust-*.tmp")), [])
        return result

    def wrapper(self, code):
        # Simulate process failures/races without adding production-only knobs.
        tool = self.root / "ffmpeg"
        tool.write_text(f"#!{sys.executable}\nimport os, sys, time\n" + code)
        tool.chmod(0o700)
        return dict(os.environ, PATH=str(self.root))

    def reference(self, inputs, output, ratio):
        command = [FFMPEG, "-v", "error"]
        for path in inputs:
            command.extend(["-i", str(path)])
        graph = "".join(
            f"[{i}:a:0]aresample=48000,asetrate={48000 * ratio:.8f},aresample=48000,"
            f"aformat=sample_fmts=fltp:channel_layouts=stereo,asetpts=N/SR/TB[a{i}];"
            for i in range(len(inputs))
        ) + "".join(f"[a{i}]" for i in range(len(inputs))) + f"concat=n={len(inputs)}:v=0:a=1[out]"
        subprocess.run(command + ["-filter_complex", graph, "-map", "[out]", "-map_metadata", "-1",
                                  "-map_chapters", "-1",
                                  "-c:a", "pcm_s24le", str(output)], check=True)

    def test_profile_reference_all_contract_rates(self):
        second = self.root / "stereo.wav"
        tone(second, 32000, 2)
        cases = [("--speed", str(s), s / 100) for s in (25, 50, 100, 200, 400, 73.5, 125.5)]
        cases += [("--cents", str(c), 2 ** (c / 1200)) for c in (-2400, -1200, -300, 0, 300, 1200, 2400)]
        for index, (flag, value, ratio) in enumerate(cases):
            with self.subTest(flag=flag, value=value):
                output = self.root / f"out{index}.wav"
                reference = self.root / f"ref{index}.wav"
                self.cli(self.input, flag, value, second, "-o", output)
                self.reference([self.input, second], reference, ratio)
                actual, expected = decode(output), decode(reference)
                self.assertEqual(actual, expected, "decoded samples must equal the contract FFmpeg pipeline")
                self.assertAlmostEqual(len(actual) / (4 * 2 * 48000), 2 / ratio,
                                       delta=max(0.01, 0.001 * 2 / ratio))
                values = array.array("f", actual)
                self.assertTrue(all(math.isfinite(v) for v in values))

    def test_silence_impulse_noise_sweep_and_short_clip(self):
        import random
        randomizer = random.Random(741)
        for name in ("silence", "impulse", "noise", "sweep", "short"):
            source = self.root / f"{name}.wav"
            count = 97 if name == "short" else 4800
            samples = []
            for i in range(count):
                value = {"silence": 0, "impulse": 16000 if i == 0 else 0,
                         "noise": randomizer.randrange(-5000, 5001),
                         "sweep": round(4000 * math.sin(2 * math.pi * (100 * i / 48000 + 18000 * (i / 48000) ** 2))),
                         "short": 3000 if i == 48 else 0}[name]
                samples.append(struct.pack("<h", value))
            with wave.open(str(source), "wb") as out:
                out.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
                out.writeframes(b"".join(samples))
            output, reference = self.root / f"{name}-out.wav", self.root / f"{name}-ref.wav"
            self.cli(source, "--speed", "125.5", "-o", output)
            self.reference([source], reference, 1.255)
            self.assertEqual(decode(output), decode(reference))
        for rate in (32000, 44100, 48000):
            source = self.root / f"short-{rate}.wav"
            tone(source, rate=rate, frames=97)
            for speed in (25, 100, 400):
                output = self.root / f"short-{rate}-{speed}.wav"
                reference = self.root / f"short-{rate}-{speed}-reference.wav"
                self.cli(source, "--speed", speed, "-o", output)
                self.reference([source], reference, speed / 100)
                self.assertEqual(decode(output), decode(reference))

    def test_hardlink_and_symlink_aliases(self):
        original = self.input.read_bytes()
        for kind in ("hard", "sym"):
            destination = self.root / f"{kind}.wav"
            if kind == "hard":
                os.link(self.input, destination)
            else:
                destination.symlink_to(self.input)
            self.cli(self.input, "-o", destination, "--overwrite", success=False)
            self.assertEqual(self.input.read_bytes(), original)

    def test_ordinary_short_clips(self):
        for rate in (32000, 44100, 48000):
            for milliseconds in (5, 20):
                source = self.root / f"short-{rate}-{milliseconds}.wav"
                tone(source, rate=rate, frames=round(rate * milliseconds / 1000))
                for speed in (25, 100, 400):
                    name = f"{rate}-{milliseconds}-{speed}"
                    output = self.root / f"{name}.wav"
                    reference = self.root / f"{name}-ref.wav"
                    self.reference([source], reference, speed / 100)
                    expected = decode(reference)
                    self.assertTrue(expected, "ordinary short reference must contain samples")
                    self.cli(source, "--speed", speed, "-o", output)
                    self.assertEqual(decode(output), expected)

    def test_reference_empty_boundaries_fail_cleanly(self):
        # Explicitly excluded from speed ranking by competition/CLARIFICATIONS.md.
        for rate, frames, speed in ((44100, 1, 100), (48000, 32, 400)):
            source = self.root / f"tiny-{rate}.wav"
            tone(source, rate=rate, frames=frames)
            reference = self.root / f"tiny-{rate}-ref.wav"
            self.reference([source], reference, speed / 100)
            self.assertEqual(decode(reference), b"")
            output = self.root / f"tiny-{rate}-out.wav"
            self.cli(source, "--speed", speed, "-o", output, success=False)
            self.assertFalse(output.exists())
            output.write_bytes(b"preserved")
            self.cli(source, "--speed", speed, "-o", output, "--overwrite", success=False)
            self.assertEqual(output.read_bytes(), b"preserved")

    def test_dangling_destination(self):
        output = self.root / "dangling.wav"
        output.symlink_to(self.root / "missing.wav")
        self.cli(self.input, "-o", output, success=False)
        self.assertTrue(output.is_symlink())
        self.cli(self.input, "-o", output, "--overwrite")
        self.assertFalse(output.is_symlink())
        self.assertFalse((self.root / "missing.wav").exists())

    def test_empty_nonaudio_and_directory(self):
        for name, data in (("empty.wav", b""), ("text.wav", b"not audio")):
            path = self.root / name
            path.write_bytes(data)
            self.cli(path, "-o", self.root / "bad.wav", success=False)
        self.cli(self.root, "-o", "bad.wav", success=False)
        empty = self.root / "zero-frames.wav"
        tone(empty, frames=0)
        self.cli(empty, "-o", "bad.wav", success=False)
        self.cli(empty, self.input, "-o", "bad.wav", success=False)
        self.cli(self.input, empty, "-o", "bad.wav", success=False)
        # Nonzero packets alone do not prove decoded samples: this WAV has a
        # partial PCM sample. Both standalone and joined renders must fail.
        partial = self.root / "partial-sample.wav"
        tone(partial, frames=1)
        data = bytearray(partial.read_bytes()[:-1])
        struct.pack_into("<I", data, 4, len(data) - 8)
        struct.pack_into("<I", data, 40, 1)
        partial.write_bytes(data)
        self.cli(partial, "-o", "bad.wav", success=False)
        self.cli(self.input, partial, "-o", "bad.wav", success=False)
        # Corruption after the first decoded frame must still fail even though
        # each validation encoder stops after one frame; the main graph decodes on.
        late_partial = self.root / "late-partial.wav"
        late_partial.write_bytes(self.input.read_bytes()[:-1])
        self.cli(late_partial, "-o", "bad.wav", success=False)
        self.cli(self.input, late_partial, "-o", "bad.wav", success=False)
        self.assertFalse((self.root / "bad.wav").exists())

    def test_unicode_quotes_and_dash_names(self):
        source = self.root / "-quoted ' \" 音.wav"
        source.write_bytes(self.input.read_bytes())
        self.cli("-o", "-音.MP3", "--", source.name)
        info = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(self.root / "-音.MP3")]))["streams"][0]
        self.assertEqual((info["sample_rate"], info["channels"], info["bit_rate"]), ("48000", 2, "320000"))

    def test_source_chapters_and_titles_not_copied(self):
        metadata = self.root / "source.ffmetadata"
        metadata.write_text(";FFMETADATA1\ntitle=Rust source title sentinel\n"
                            "[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=500\n"
                            "title=Rust source chapter sentinel\n")
        source = self.root / "chaptered.mp3"
        subprocess.run([FFMPEG, "-v", "error", "-i", str(self.input), "-f", "ffmetadata",
                        "-i", str(metadata), "-map", "0:a:0", "-map_metadata", "1",
                        "-map_chapters", "1", "-c:a", "libmp3lame", "-b:a", "320k",
                        str(source)], check=True)

        def probe(path):
            return json.loads(subprocess.check_output([
                "ffprobe", "-v", "error", "-show_chapters", "-show_format", "-show_streams",
                "-of", "json", str(path)]))

        original = probe(source)
        self.assertEqual(len(original["chapters"]), 1)
        self.assertEqual(original["format"]["tags"]["title"], "Rust source title sentinel")
        self.assertEqual(original["chapters"][0]["tags"]["title"], "Rust source chapter sentinel")
        for extension in ("mp3", "wav"):
            with self.subTest(extension=extension):
                output = self.root / f"stripped.{extension}"
                self.cli(source, "-o", output)
                info = probe(output)
                self.assertEqual(info.get("chapters", []), [])
                self.assertNotIn("Rust source title sentinel", json.dumps(info))
                self.assertNotIn("Rust source chapter sentinel", json.dumps(info))

    def test_invalid_cli(self):
        for tail in (["--speed", "nan"], ["--speed", "100x"], ["--speed", "1e309"],
                     ["--cents", "-2400.1"], ["--speed", "100", "--speed", "100"],
                     ["--cents", "0", "--cents", "0"], ["-o", "second.wav"], ["--unknown"],
                     ["--speed"], ["--cents", "0", "--speed", "100"]):
            self.cli(self.input, "-o", "bad.wav", *tail, success=False)
        self.cli("-o", "bad.wav", success=False)
        self.cli(self.input, success=False)
        self.assertIn(b"Usage:", self.cli("--help").stdout)

    def test_failed_child_and_missing_dependency(self):
        output = self.root / "keep.wav"
        output.write_bytes(b"original")
        env = self.wrapper("os.write(1, b'partial')\nsys.exit(7)\n")
        self.cli(self.input, "-o", output, "--overwrite", env=env, success=False)
        self.assertEqual(output.read_bytes(), b"original")
        self.cli(self.input, "-o", "absent.wav", env=env, success=False)
        self.assertFalse((self.root / "absent.wav").exists())
        (self.root / "ffmpeg").unlink()
        result = self.cli(self.input, "-o", "absent.wav", env=env, success=False)
        self.assertIn(b"install FFmpeg", result.stderr)

    def test_publication_collision(self):
        output = self.root / "collision.wav"
        env = self.wrapper(f"open({str(output)!r}, 'wb').write(b'racer')\nos.execv({FFMPEG!r}, [{FFMPEG!r}] + sys.argv[1:])\n")
        self.cli(self.input, "-o", output, env=env, success=False)
        self.assertEqual(output.read_bytes(), b"racer")

    def test_late_input_alias_is_rejected(self):
        original = self.input.read_bytes()
        for kind in ("hard", "sym"):
            output = self.root / f"late-{kind}.wav"
            create = f"os.link({str(self.input)!r}, {str(output)!r})" if kind == "hard" else f"os.symlink({str(self.input)!r}, {str(output)!r})"
            env = self.wrapper(create + f"\nos.execv({FFMPEG!r}, [{FFMPEG!r}] + sys.argv[1:])\n")
            self.cli(self.input, "-o", output, "--overwrite", env=env, success=False)
            self.assertEqual(output.read_bytes(), original)
            self.assertEqual(self.input.read_bytes(), original)

    def test_input_descriptor_pins_validated_inode(self):
        backup = self.root / "original.wav"
        output = self.root / "pinned.wav"
        env = self.wrapper(f"os.rename({str(self.input)!r}, {str(backup)!r})\nos.mkfifo({str(self.input)!r})\nos.execv({FFMPEG!r}, [{FFMPEG!r}] + sys.argv[1:])\n")
        self.cli(self.input, "-o", output, env=env)
        reference = self.root / "reference.wav"
        self.reference([backup], reference, 1)
        self.assertEqual(decode(output), decode(reference))

    def test_validation_encodes_only_one_frame_per_input(self):
        second = self.root / "second.wav"
        tone(second, 32000, 2)
        # Instrument only the null muxers as framecrc outputs. This records the
        # number of encoded validation packets, without measuring another entry.
        env = self.wrapper(
            f"root = {str(self.root)!r}\n"
            "args = sys.argv[1:]\ncount = 0\n"
            "for i in range(len(args) - 2):\n"
            "    if args[i:i+3] == ['-f', 'null', '/dev/null']:\n"
            "        args[i+1:i+3] = ['framecrc', root + '/validation-' + str(count) + '.txt']\n"
            "        count += 1\n"
            f"os.execv({FFMPEG!r}, [{FFMPEG!r}] + args)\n")
        self.cli(self.input, second, "-o", "joined.wav", env=env)
        for i in range(2):
            packets = [line for line in (self.root / f"validation-{i}.txt").read_text().splitlines()
                       if line and not line.startswith("#")]
            self.assertEqual(len(packets), 1)

    def test_seekable_wav_header_finalized(self):
        output = self.root / "finalized.wav"
        self.cli(self.input, "--speed", "73.5", "-o", output)
        data = output.read_bytes()
        self.assertEqual(data[:4], b"RIFF")
        self.assertEqual(struct.unpack_from("<I", data, 4)[0], len(data) - 8)
        offset = 12
        while data[offset:offset + 4] != b"data":
            size = struct.unpack_from("<I", data, offset + 4)[0]
            offset += 8 + size + (size % 2)
        self.assertEqual(struct.unpack_from("<I", data, offset + 4)[0], len(data) - offset - 8)

    def test_bounded_child_diagnostics(self):
        env = self.wrapper("os.write(2, b'x' * (1024 * 1024))\nos.write(2, b'END-OF-DIAGNOSTIC')\nsys.exit(9)\n")
        result = self.cli(self.input, "-o", "absent.wav", env=env, success=False)
        self.assertLess(len(result.stderr), 17000)
        self.assertIn(b"diagnostics truncated", result.stderr)
        self.assertIn(b"END-OF-DIAGNOSTIC", result.stderr)
        self.assertFalse((self.root / "absent.wav").exists())

    def test_replaced_temporary_is_not_deleted(self):
        env = self.wrapper(
            f"root = {str(self.root)!r}\n"
            "temp = next(p for p in os.listdir(root) if p.startswith('.resample-rust-'))\n"
            "path = root + '/' + temp\nos.unlink(path)\n"
            "open(path, 'wb').write(b'racer')\nos.write(1, b'output')\n")
        result = subprocess.run([str(BINARY), str(self.input), "-o", str(self.root / "out.wav")],
                                env=env, capture_output=True, timeout=5)
        self.assertNotEqual(result.returncode, 0)
        replacement, = self.root.glob(".resample-rust-*.tmp")
        self.assertEqual(replacement.read_bytes(), b"racer")
        self.assertFalse((self.root / "out.wav").exists())
        replacement.unlink()

    def test_inherited_ignored_sigchld(self):
        # Launch through Python so SIGCHLD really is ignored across exec.
        launcher = ("import os, signal, sys; signal.signal(signal.SIGCHLD, signal.SIG_IGN); "
                    "os.execv(sys.argv[1], sys.argv[1:])")
        result = subprocess.run([sys.executable, "-c", launcher, str(BINARY), str(self.input),
                                 "-o", str(self.root / "waitable.wav")], capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_closed_standard_descriptors(self):
        launcher = "import os, sys; os.close(0); os.close(1); os.execv(sys.argv[1], sys.argv[1:])"
        output = self.root / "closed-stdio.wav"
        result = subprocess.run([sys.executable, "-c", launcher, str(BINARY), str(self.input),
                                 "-o", str(output)], capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        reference = self.root / "reference.wav"
        self.reference([self.input], reference, 1)
        self.assertEqual(decode(output), decode(reference))

    def test_cancel_with_closed_or_noisy_stderr(self):
        for mode in ("closed", "noisy"):
            with self.subTest(mode=mode):
                marker = self.root / f"{mode}-pid"
                code = f"open({str(marker)!r}, 'w').write(str(os.getpid()))\n"
                code += "os.close(2)\ntime.sleep(20)\n" if mode == "closed" else "while True:\n    os.write(2, b'x' * 8192)\n"
                env = self.wrapper(code)
                output = self.root / f"{mode}.wav"
                process = subprocess.Popen([str(BINARY), str(self.input), "-o", str(output)],
                                           env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                try:
                    deadline = time.monotonic() + 5
                    while not marker.exists() and time.monotonic() < deadline:
                        time.sleep(0.005)
                    self.assertTrue(marker.exists())
                    child_pid = int(marker.read_text())
                    process.send_signal(signal.SIGTERM)
                    _, diagnostic = process.communicate(timeout=5)
                    self.assertEqual(process.returncode, 128 + signal.SIGTERM, diagnostic)
                    self.assertLess(len(diagnostic), 17000)
                    with self.assertRaises(ProcessLookupError):
                        os.kill(child_pid, 0)
                    self.assertFalse(output.exists())
                    self.assertEqual(list(self.root.glob(".resample-rust-*.tmp")), [])
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.wait()

    def test_unwritable_parent(self):
        parent = self.root / "readonly"
        parent.mkdir(mode=0o500)
        try:
            self.cli(self.input, "-o", parent / "out.wav", success=False)
        finally:
            parent.chmod(0o700)

    def test_cancel_cleans_child_and_temp_preserves_destination(self):
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            output, marker = self.root / "old.wav", self.root / "child-pid"
            output.write_bytes(b"old")
            marker.unlink(missing_ok=True)
            env = self.wrapper(f"open({str(marker)!r}, 'w').write(str(os.getpid()))\nos.write(1, b'partial')\ntime.sleep(20)\n")
            process = subprocess.Popen([str(BINARY), str(self.input), "-o", str(output), "--overwrite"],
                                       env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                deadline = time.monotonic() + 5
                while not marker.exists() and time.monotonic() < deadline:
                    time.sleep(0.005)
                self.assertTrue(marker.exists(), "child did not start")
                child_pid = int(marker.read_text())
                process.send_signal(sig)
                _, stderr = process.communicate(timeout=5)
                self.assertEqual(process.returncode, 128 + sig, stderr)
                with self.assertRaises(ProcessLookupError):
                    os.kill(child_pid, 0)
                self.assertEqual(output.read_bytes(), b"old")
                self.assertEqual(list(self.root.glob(".resample-rust-*.tmp")), [])
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()


if __name__ == "__main__":
    unittest.main()
