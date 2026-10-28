"""End-to-end audio checks; requires FFmpeg and ffprobe on PATH."""

import array
import json
import math
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
import wave


BINARY = Path(sys.argv.pop(1)).resolve()


def tone(path, frequency, rate=44100, channels=1):
    with wave.open(str(path), "wb") as audio:
        audio.setparams((channels, 2, rate, 0, "NONE", "not compressed"))
        audio.writeframes(b"".join(
            struct.pack("<h", round(12000 * math.sin(2 * math.pi * frequency * i / rate))) * channels
            for i in range(rate)
        ))


def samples(path):
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-f", "f32le", "-ac", "1", "-ar", "48000", "-"],
        check=True, capture_output=True,
    )
    data = array.array("f", result.stdout)
    if sys.byteorder != "little":
        data.byteswap()
    return data


def frequency(data, start, end):
    segment = data[round(start * 48000):round(end * 48000)]
    crossings = sum(a <= 0 < b for a, b in zip(segment, segment[1:]))
    return crossings / (len(segment) / 48000)


class AudioTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="resample test ")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.first = self.root / "first tone.wav"
        self.second = self.root / "second ' tone.wav"
        tone(self.first, 440)
        tone(self.second, 880, rate=32000, channels=2)

    def cli(self, *args, success=True, env=None):
        result = subprocess.run(
            [str(BINARY), *map(str, args)], capture_output=True, text=True,
            cwd=self.root, env=env, timeout=30,
        )
        if success:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertFalse(list(self.root.glob("*.resample-*.tmp")))
        return result

    def test_octave_down(self):
        output = self.root / "slow.wav"
        self.cli(self.first, "--cents", -1200, "-o", output)
        data = samples(output)
        self.assertAlmostEqual(len(data) / 48000, 2, delta=0.005)
        self.assertAlmostEqual(frequency(data, 0.1, 1.9), 220, delta=1)

    def test_concat_order_and_mixed_formats(self):
        compressed = self.root / "second.mp3"
        subprocess.run(["ffmpeg", "-v", "error", "-i", str(self.second), str(compressed)], check=True)
        output = self.root / "joined.WAV"
        self.cli(self.first, compressed, "--speed", 200, "-o", output)
        data = samples(output)
        self.assertAlmostEqual(len(data) / 48000, 1, delta=0.01)
        self.assertAlmostEqual(frequency(data, 0.1, 0.4), 880, delta=4)
        self.assertAlmostEqual(frequency(data, 0.6, 0.9), 1760, delta=4)
        info = json.loads(subprocess.check_output([
            "ffprobe", "-v", "error", "-show_streams", "-of", "json", str(output),
        ]))["streams"][0]
        self.assertEqual(info["sample_rate"], "48000")
        self.assertEqual(info["channels"], 2)
        self.assertEqual(info["codec_name"], "pcm_s24le")

    def test_mp3_export(self):
        output = self.root / "mix.mp3"
        self.cli(self.first, self.second, "-o", output)
        data = samples(output)
        self.assertAlmostEqual(len(data) / 48000, 2, delta=0.02)
        self.assertAlmostEqual(frequency(data, 0.1, 0.9), 440, delta=2)
        self.assertAlmostEqual(frequency(data, 1.1, 1.9), 880, delta=2)

    def test_rate_boundaries(self):
        for speed in (25, 400):
            output = self.root / f"rate-{speed}.wav"
            self.cli(self.first, "--speed", speed, "-o", output)
            self.assertAlmostEqual(len(samples(output)) / 48000, 100 / speed, delta=0.005)

    def test_existing_output_and_overwrite(self):
        output = self.root / "existing.wav"
        output.write_bytes(b"keep me")
        self.cli(self.first, "-o", output, success=False)
        self.assertEqual(output.read_bytes(), b"keep me")
        self.cli(self.first, "-o", output, "--overwrite")
        self.assertAlmostEqual(len(samples(output)) / 48000, 1, delta=0.005)

    def test_never_overwrite_input(self):
        original = self.first.read_bytes()
        self.cli(self.first, "-o", self.first, "--overwrite", success=False)
        self.assertEqual(self.first.read_bytes(), original)

    def test_corrupt_input_preserves_output(self):
        corrupt = self.root / "corrupt.wav"
        corrupt.write_bytes(b"invalid audio")
        output = self.root / "keep.wav"
        output.write_bytes(b"existing output")
        self.cli(corrupt, "-o", output, "--overwrite", success=False)
        self.assertEqual(output.read_bytes(), b"existing output")
        absent = self.root / "absent.wav"
        self.cli(corrupt, "-o", absent, success=False)
        self.assertFalse(absent.exists())

    def test_missing_file_and_dependency(self):
        output = self.root / "missing.wav"
        self.cli(self.root / "absent.wav", "-o", output, success=False)
        env = dict(os.environ, PATH=str(self.root))
        result = self.cli(self.first, "-o", output, success=False, env=env)
        self.assertIn("install FFmpeg", result.stderr)
        self.assertFalse(output.exists())

    def test_help_and_invalid_options(self):
        self.assertIn("Usage:", self.cli().stdout)
        self.assertIn("0.1.0", self.cli("--version").stdout)
        for arguments in [
            ["--speed", "nan"], ["--speed", "inf"], ["--speed", "0"],
            ["--cents", "2401"], ["--cents", "0", "--speed", "100"],
            ["--unknown"], ["--speed"],
        ]:
            self.cli(self.first, "-o", "bad.wav", *arguments, success=False)
        self.cli(self.first, "-o", "bad.ogg", success=False)
        self.cli(self.first, success=False)

    def test_literal_filenames(self):
        unusual = self.root / "-$(literal) ; tone.wav"
        tone(unusual, 440)
        self.cli("-o", "literal.wav", "--", unusual.name)
        self.assertTrue((self.root / "literal.wav").is_file())


if __name__ == "__main__":
    unittest.main()
