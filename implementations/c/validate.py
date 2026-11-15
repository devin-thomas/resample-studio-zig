"""Focused C-entry checks. No other implementation is built or invoked."""
import array
import json
import math
import os
from pathlib import Path
import random
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import time
import unittest
import wave

BINARY = Path(sys.argv.pop(1)).resolve()
FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")


def command(args, **kwargs):
    return subprocess.run(args, check=True, capture_output=True, timeout=30, **kwargs)


def fixture(path, rate=48000, channels=2, kind="tone"):
    rng = random.Random(23)
    frames = bytearray()
    for i in range(rate // 5):
        for c in range(channels):
            v = (0 if kind == "silence" else (12000 if i == 100 else 0)
                 if kind == "impulse" else rng.randint(-8000, 8000)
                 if kind == "noise" else round(10000 * math.sin(2 * math.pi * (440 + 440*c) * i / rate)))
            frames.extend(struct.pack("<h", v))
    with wave.open(str(path), "wb") as wav:
        wav.setparams((channels, 2, rate, 0, "NONE", "not compressed"))
        wav.writeframes(frames)


def decode(path):
    return command([FFMPEG, "-v", "error", "-i", str(path), "-f", "f32le", "-"]).stdout


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="resample-c-check-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.input = self.root / "input.wav"
        fixture(self.input)

    def cli(self, *args, ok=True, env=None):
        p = subprocess.run([str(BINARY), *map(str, args)], cwd=self.root,
                           capture_output=True, env=env, timeout=30)
        self.assertEqual(p.returncode == 0, ok, p.stderr.decode(errors="replace"))
        self.assertEqual(list(self.root.glob(".resample-*")), [])
        return p

    def test_argument_validation(self):
        for value in ("nan", "NaN", "inf", "-inf", "25x", "0x64", " 100", "100 ",
                      "", "1e999", "1e-999", "24.999", "400.001", ".", "1e", "--help"):
            self.cli(self.input, "-o", "bad.wav", "--speed", value, ok=False)
        for args in (("--cents", "-2400.01"), ("--cents", "2400.01"),
                     ("--speed", "100", "--speed", "100"),
                     ("--cents", "0", "--speed", "100"), ("-o", "other.wav"),
                     ("--output", "other.wav"), ("--bogus",), ("--speed",)):
            self.cli(self.input, "-o", "bad.wav", *args, ok=False)
        self.cli("-o", "bad.wav", ok=False)
        self.cli(self.input, ok=False)
        for arg in ((), ("--help",), ("-h",), ("--version",)):
            self.cli(*arg)
        self.assertFalse((self.root / "bad.wav").exists())

    def test_reference_pipeline_and_all_rates(self):
        second = self.root / "second.wav"
        fixture(second, rate=32000, channels=1)
        rates = [("--speed", v, v / 100) for v in (25, 50, 100, 200, 400, 73.5, 125.5)]
        rates += [("--cents", v, 2 ** (v / 1200)) for v in (-2400, -1200, -300, 0, 300, 1200, 2400)]
        rates += [(None, None, 1)]
        for index, (flag, value, ratio) in enumerate(rates):
            with self.subTest(flag=flag, value=value):
                output = self.root / f"output-{index}.WAV"
                reference = self.root / f"reference-{index}.wav"
                args = [self.input, "-o", output, second]
                if flag:
                    args += [flag, value]
                self.cli(*args)
                graph = "".join(
                    f"[{i}:a:0]aresample=48000,asetrate={48000*ratio:.8f},aresample=48000,"
                    f"aformat=sample_fmts=fltp:channel_layouts=stereo,asetpts=N/SR/TB[a{i}];"
                    for i in range(2)) + "[a0][a1]concat=n=2:v=0:a=1[out]"
                command([FFMPEG, "-v", "error", "-i", str(self.input), "-i", str(second),
                         "-filter_complex", graph, "-map", "[out]", "-map_metadata", "-1",
                         "-c:a", "pcm_s24le", str(reference)])
                data = decode(output)
                self.assertEqual(data, decode(reference))
                floats = array.array("f", data)
                self.assertTrue(all(map(math.isfinite, floats)))
                self.assertAlmostEqual(len(data) / (4 * 2 * 48000), .4 / ratio, delta=.010)
                info = json.loads(command([FFPROBE, "-v", "error", "-show_streams", "-of", "json", str(output)]).stdout)["streams"][0]
                self.assertEqual((info["codec_name"], info["sample_rate"], info["channels"]), ("pcm_s24le", "48000", 2))

    def test_signal_and_silence_fixtures(self):
        for kind in ("silence", "impulse", "noise"):
            path = self.root / f"{kind}.wav"
            fixture(path, rate=44100, kind=kind)
            self.cli(path, "--speed", "125.5", "-o", self.root / f"{kind}-out.wav")

    def test_mp3_and_stereo_order(self):
        mp3 = self.root / "input.mp3"
        command([FFMPEG, "-v", "error", "-i", str(self.input), "-c:a", "libmp3lame", "-b:a", "320k", str(mp3)])
        output = self.root / "output.MP3"
        self.cli(mp3, "-o", output)
        info = json.loads(command([FFPROBE, "-v", "error", "-show_streams", "-of", "json", str(output)]).stdout)["streams"][0]
        self.assertEqual((info["codec_name"], info["sample_rate"], info["channels"], info["bit_rate"]), ("mp3", "48000", 2, "320000"))
        values = array.array("f", decode(output))
        if sys.byteorder != "little": values.byteswap()
        for channel, hz in ((0, 440), (1, 880)):
            values_c = values[4800*2+channel:7200*2:2]
            crossings = sum(a <= 0 < b for a, b in zip(values_c, values_c[1:]))
            self.assertAlmostEqual(crossings / .05, hz, delta=20)

    def test_empty_corrupt_non_audio_and_nonregular(self):
        empty = self.root / "empty.wav"
        with wave.open(str(empty), "wb") as wav:
            wav.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
        zero = self.root / "zero.wav"
        zero.touch()
        corrupt = self.root / "corrupt.wav"
        corrupt.write_bytes(b"this is not audio")
        fifo = self.root / "pipe.wav"
        os.mkfifo(fifo)
        video = self.root / "video.mp4"
        command([FFMPEG, "-v", "error", "-f", "lavfi", "-i", "color=size=16x16:duration=0.1", "-an", str(video)])
        for path in (empty, zero, corrupt, fifo, self.root, video, self.root / "missing.wav", "https://invalid.invalid/a.wav"):
            self.cli(self.input, path, "-o", "bad.wav", ok=False)
            self.assertFalse((self.root / "bad.wav").exists())

    def test_literal_paths_and_aliases(self):
        unusual = self.root / "-quoted ' 音 ; $(false).wav"
        shutil.copyfile(self.input, unusual)
        self.cli("-o", "literal.wav", "--", unusual.name)
        original = self.input.read_bytes()
        for name, kind in (("hard.wav", "hard"), ("symbolic.wav", "sym")):
            alias = self.root / name
            if kind == "hard": os.link(self.input, alias)
            else: alias.symlink_to(self.input)
            self.cli(self.input, "-o", alias, "--overwrite", ok=False)
            self.assertEqual(self.input.read_bytes(), original)
            self.assertEqual(alias.read_bytes(), original)

    def test_missing_dependencies_and_failed_child(self):
        output = self.root / "keep.wav"
        output.write_bytes(b"keep")
        self.cli(self.input, "-o", output, "--overwrite", env=dict(os.environ, PATH=""), ok=False)
        self.assertEqual(output.read_bytes(), b"keep")
        env = self.fake_environment("failure")
        self.cli(self.input, "-o", output, "--overwrite", env=env, ok=False)
        self.assertEqual(output.read_bytes(), b"keep")
        output.unlink()
        self.cli(self.input, "-o", output, env=env, ok=False)
        self.assertFalse(output.exists())

    def fake_environment(self, behavior):
        bindir = self.root / "bin"
        bindir.mkdir(exist_ok=True)
        fake = bindir / "ffmpeg"
        fake.write_text(f'''#!{sys.executable}
import json, os, pathlib, signal, sys, time
root = pathlib.Path(os.environ["TEST_ROOT"])
behavior = os.environ["BEHAVIOR"]
with (root / "calls.jsonl").open("a") as log: log.write(json.dumps(sys.argv[1:]) + "\\n")
if "null" in sys.argv and behavior != "validation-pause":
    os.execv({FFMPEG!r}, [{FFMPEG!r}, *sys.argv[1:]])
if behavior == "real": os.execv({FFMPEG!r}, [{FFMPEG!r}, *sys.argv[1:]])
if behavior == "flood":
    os.write(2, b"x" * 262144 + b"\\nlast useful diagnostic\\n")
    raise SystemExit(17)
if behavior == "ignore":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
if behavior != "pin": os.write(1, b"completed fake render")
(root / "ready").write_text(str(os.getpid()))
if behavior == "failure": raise SystemExit(17)
while not (root / "release").exists(): time.sleep(.01)
if behavior == "pin": os.execv({FFMPEG!r}, [{FFMPEG!r}, *sys.argv[1:]])
''')
        fake.chmod(0o755)
        return dict(os.environ, PATH=str(bindir), TEST_ROOT=str(self.root), BEHAVIOR=behavior)

    def start_paused(self, behavior="pause", overwrite=False, output=None):
        args = [str(BINARY), str(self.input), "-o", str(output or self.root / "race.wav")]
        if overwrite: args.append("--overwrite")
        p = subprocess.Popen(args, env=self.fake_environment(behavior),
                             stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        def stop_owned_process():
            if p.poll() is None:
                p.terminate()  # Let the CLI reap its own child, including escalation.
                try: p.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    p.kill()
                    p.communicate(timeout=5)
        self.addCleanup(stop_owned_process)
        end = time.monotonic() + 10
        while not (self.root / "ready").exists():
            if p.poll() is not None: self.fail(p.communicate()[1].decode())
            if time.monotonic() >= end: self.fail("child did not start")
            time.sleep(.01)
        return p

    def test_publication_collision(self):
        p = self.start_paused()
        output = self.root / "race.wav"
        output.write_bytes(b"concurrent winner")
        (self.root / "release").touch()
        p.communicate(timeout=10)
        self.assertNotEqual(p.returncode, 0)
        self.assertEqual(output.read_bytes(), b"concurrent winner")
        self.assertEqual(list(self.root.glob(".resample-*")), [])

    def test_cancellation_reaps_child_and_removes_temporary(self):
        p = self.start_paused()
        child = int((self.root / "ready").read_text())
        p.send_signal(signal.SIGTERM)
        p.communicate(timeout=10)
        self.assertEqual(p.returncode, 128 + signal.SIGTERM)
        self.assertFalse((self.root / "race.wav").exists())
        self.assertEqual(list(self.root.glob(".resample-*")), [])
        with self.assertRaises(ProcessLookupError): os.kill(child, 0)

    def test_bounded_diagnostics(self):
        result = self.cli(self.input, "-o", "bad.wav", env=self.fake_environment("flood"), ok=False)
        self.assertLess(len(result.stderr), 8500)
        self.assertIn(b"truncated", result.stderr)
        self.assertIn(b"last useful diagnostic", result.stderr)
        self.assertFalse((self.root / "bad.wav").exists())

    def test_closed_diagnostic_reader(self):
        output = self.root / "bad.wav"
        p = subprocess.Popen([str(BINARY), str(self.input), "-o", str(output)],
                             env=self.fake_environment("failure"), stderr=subprocess.PIPE,
                             stdout=subprocess.DEVNULL)
        p.stderr.close()
        self.assertNotEqual(p.wait(timeout=10), 0)
        self.assertFalse(output.exists())
        self.assertEqual(list(self.root.glob(".resample-*")), [])

    def test_forced_cancellation_and_validation_cancellation(self):
        for behavior in ("ignore", "validation-pause"):
            with self.subTest(behavior=behavior):
                p = self.start_paused(behavior)
                child = int((self.root / "ready").read_text())
                p.send_signal(signal.SIGTERM)
                p.communicate(timeout=5)
                self.assertEqual(p.returncode, 128 + signal.SIGTERM)
                with self.assertRaises(ProcessLookupError): os.kill(child, 0)
                self.assertFalse((self.root / "race.wav").exists())
                self.assertEqual(list(self.root.glob(".resample-*")), [])
                (self.root / "ready").unlink()

    def test_alias_created_during_render(self):
        original = self.input.read_bytes()
        for kind in ("hard", "sym"):
            for overwrite in (False, True):
                with self.subTest(kind=kind, overwrite=overwrite):
                    p = self.start_paused(overwrite=overwrite)
                    output = self.root / "race.wav"
                    if kind == "hard": os.link(self.input, output)
                    else: output.symlink_to(self.input)
                    (self.root / "release").touch()
                    p.communicate(timeout=10)
                    self.assertNotEqual(p.returncode, 0)
                    self.assertEqual(output.read_bytes(), original)
                    self.assertEqual(self.input.read_bytes(), original)
                    self.assertEqual(list(self.root.glob(".resample-*")), [])
                    for path in (output, self.root / "release", self.root / "ready"): path.unlink()

    def test_pinned_input_and_output_directory(self):
        parent = self.root / "destination"
        parent.mkdir()
        p = self.start_paused("pin", output=parent / "race.wav")
        # Both input and output pathnames change while the child owns its fds.
        self.input.rename(self.root / "original.wav")
        fixture(self.input, kind="silence")
        moved = self.root / "moved"
        parent.rename(moved)
        parent.mkdir()
        (self.root / "release").touch()
        _, diagnostic = p.communicate(timeout=10)
        self.assertEqual(p.returncode, 0, diagnostic)
        self.assertEqual(decode(moved / "race.wav"), decode(self.root / "original.wav"))
        self.assertEqual(list(parent.iterdir()), [])
        self.assertEqual(list(moved.glob(".resample-*")), [])

    def test_decoded_empty_packets_and_late_corruption(self):
        incomplete = self.root / "incomplete.wav"
        with wave.open(str(incomplete), "wb") as wav:
            wav.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
            wav.writeframes(b"x")
        packets = json.loads(command([FFPROBE, "-v", "quiet", "-select_streams", "a:0",
                                      "-count_packets", "-show_entries", "stream=nb_read_packets",
                                      "-of", "json", str(incomplete)]).stdout)
        self.assertGreater(int(packets["streams"][0]["nb_read_packets"]), 0)
        for paths in ((incomplete,), (self.input, incomplete), (incomplete, self.input)):
            self.cli(*paths, "-o", "bad.wav", ok=False)
            self.assertFalse((self.root / "bad.wav").exists())
        damaged = self.root / "damaged.wav"
        with wave.open(str(damaged), "wb") as wav:
            wav.setparams((2, 2, 48000, 0, "NONE", "not compressed"))
            wav.writeframes(b"\0" * (48000 * 4 * 10 + 1))
        output = self.root / "keep.wav"
        output.write_bytes(b"keep")
        self.cli(damaged, "-o", output, "--overwrite", env=self.fake_environment("real"), ok=False)
        calls = [json.loads(line) for line in (self.root / "calls.jsonl").read_text().splitlines()]
        self.assertEqual(len(calls), 2)  # First-frame validation passed; full render rejected the tail.
        self.assertEqual(output.read_bytes(), b"keep")

    def test_short_clips_and_seekable_wav_header(self):
        for rate in (32000, 44100, 48000):
            for frames in (1, 10, 100, rate // 200, rate // 50):
                source = self.root / f"short-{rate}-{frames}.wav"
                with wave.open(str(source), "wb") as wav:
                    wav.setparams((1, 2, rate, 0, "NONE", "not compressed"))
                    wav.writeframes(b"\1\0" * frames)
                for speed in (25, 100, 400):
                    output = self.root / "short-out.wav"
                    reference = self.root / "short-reference.wav"
                    graph = (f"aresample=48000,asetrate={48000*speed/100:.8f},aresample=48000,"
                             "aformat=sample_fmts=fltp:channel_layouts=stereo,asetpts=N/SR/TB,"
                             "concat=n=1:v=0:a=1")
                    command([FFMPEG, "-v", "error", "-y", "-i", str(source), "-af", graph,
                             "-map_metadata", "-1", "-c:a", "pcm_s24le", str(reference)])
                    expected = decode(reference)
                    output.write_bytes(b"keep")
                    self.cli(source, "--speed", speed, "-o", output, "--overwrite", ok=bool(expected))
                    if not expected:
                        self.assertEqual(output.read_bytes(), b"keep")
                        continue
                    self.assertEqual(decode(output), expected)
                    data = output.read_bytes()
                    self.assertEqual(data[:4], b"RIFF")
                    self.assertEqual(struct.unpack_from("<I", data, 4)[0], len(data) - 8)
                    offset = 12
                    while data[offset:offset+4] != b"data":
                        chunk_size = struct.unpack_from("<I", data, offset+4)[0]
                        offset += 8 + chunk_size + chunk_size % 2
                    data_size = struct.unpack_from("<I", data, offset+4)[0]
                    self.assertEqual(data_size, len(data) - offset - 8)
                    self.assertAlmostEqual(data_size / (48000 * 6), frames / rate / (speed/100), delta=.005)
        # MP3 short clips that survive the reference filter must still be usable.
        self.cli(source, "--speed", "400", "-o", "short.mp3")
        self.assertGreater(len(decode(self.root / "short.mp3")), 0)

    def test_first_stream_and_metadata(self):
        second = self.root / "other.wav"
        fixture(second, kind="silence")
        source = self.root / "streams.mka"
        command([FFMPEG, "-v", "error", "-i", str(self.input), "-i", str(second),
                 "-map", "0:a:0", "-map", "1:a:0", "-c:a", "pcm_s16le",
                 "-metadata", "title=source_marker", "-metadata:s:a:0", "title=source_marker",
                 str(source)])
        output = self.root / "first.wav"
        self.cli(source, "-o", output)
        self.assertEqual(decode(output), decode(self.input))
        info = command([FFPROBE, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(output)])
        self.assertNotIn(b"source_marker", info.stdout)
        metadata = self.root / "chapters.txt"
        metadata.write_text(";FFMETADATA1\ntitle=source_marker\n[CHAPTER]\n"
                            "TIMEBASE=1/1000\nSTART=0\nEND=100\ntitle=source_marker\n")
        chapter_source = self.root / "chapters.mp3"
        command([FFMPEG, "-v", "error", "-i", str(self.input), "-i", str(metadata),
                 "-map", "0:a:0", "-map_metadata", "1", "-map_chapters", "1",
                 "-c:a", "libmp3lame", str(chapter_source)])
        source_info = json.loads(command([FFPROBE, "-v", "error", "-show_chapters", "-of", "json",
                                          str(chapter_source)]).stdout)
        self.assertGreater(len(source_info["chapters"]), 0)
        chapter_output = self.root / "no-chapters.mp3"
        self.cli(chapter_source, "-o", chapter_output)
        info = command([FFPROBE, "-v", "error", "-show_chapters", "-show_format", "-show_streams",
                        "-of", "json", str(chapter_output)])
        self.assertEqual(json.loads(info.stdout)["chapters"], [])
        self.assertNotIn(b"source_marker", info.stdout)

    def test_closed_standard_descriptors(self):
        def close_stdio():
            for fd in range(3): os.close(fd)
        output = self.root / "closed.wav"
        p = subprocess.run([str(BINARY), str(self.input), "-o", str(output)],
                           preexec_fn=close_stdio, timeout=10)
        self.assertEqual(p.returncode, 0)
        self.assertEqual(decode(output), decode(self.input))

    def test_unwritable_output_parent(self):
        parent = self.root / "readonly"
        parent.mkdir()
        parent.chmod(0o500)
        try:
            self.cli(self.input, "-o", parent / "out.wav", ok=False)
            self.assertFalse((parent / "out.wav").exists())
        finally:
            parent.chmod(0o700)


if __name__ == "__main__":
    unittest.main(verbosity=2)
