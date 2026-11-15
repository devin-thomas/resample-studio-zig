"""Focused single-entry contract checks; no competing entry is invoked."""
import array
import hashlib
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

HERE = Path(__file__).resolve().parent
BINARY = (HERE / "build/resample").resolve()
FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")


def tone(path, rate=44100, frames=None, channels=1):
    frames = rate if frames is None else frames
    with wave.open(str(path), "wb") as out:
        out.setparams((channels, 2, rate, 0, "NONE", "not compressed"))
        out.writeframes(b"".join(
            struct.pack("<" + "h" * channels, *[
                round(7000 * math.sin(2 * math.pi * (440 + channel * 270) * i / rate))
                for channel in range(channels)
            ]) for i in range(frames)
        ))


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="cpp-check-", dir=HERE)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.first = self.root / "-tone ' \u65e5\u672c\u8a9e.wav"
        self.second = self.root / "second.wav"
        tone(self.first, channels=2)
        tone(self.second, rate=32000)

    def cli(self, *args, ok=True, env=None):
        result = subprocess.run([str(BINARY), *map(str, args)], cwd=self.root,
                                env=env, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode == 0, ok, result.stderr)
        self.assertEqual(list(self.root.glob(".resample-*.tmp")), [])
        return result

    def test_decimal_and_flag_validation(self):
        for args in [
            ["--speed", "100junk"], ["--speed", " 100"], ["--speed", "0x64"],
            ["--speed", "NaN"], ["--speed", "Infinity"], ["--speed", "1e999"],
            ["--speed", "24.9999"], ["--speed", "400.0001"],
            ["--cents", "-2400.001"], ["--cents", "2400.001"],
            ["--speed", "100", "--speed", "100"], ["--cents", "0", "--speed", "100"],
            ["-o", "second-output.wav"], ["--output"], ["--speed"], ["--bad"],
        ]:
            with self.subTest(args=args):
                self.cli(self.first, "-o", "out.wav", *args, ok=False)
        self.cli("--help")
        self.cli("-h")
        self.cli("--version")
        self.cli("-o", "literal.WaV", "--speed", "+1e2", "--", self.first.name)

    def test_profile_and_reference_samples(self):
        cases = [("--speed", value, value / 100) for value in (25, 50, 100, 200, 400, 73.5, 125.5)]
        cases += [("--cents", value, 2 ** (value / 1200)) for value in (-2400, -1200, -300, 0, 300, 1200, 2400)]
        for index, (flag, value, ratio) in enumerate(cases):
            with self.subTest(flag=flag, value=value):
                out = self.root / f"actual-{index}.wav"
                reference = self.root / f"reference-{index}.wav"
                self.cli(self.first, flag, value, self.second, "-o", out)
                graph = "".join(
                    f"[{i}:a:0]aresample=48000,asetrate={48000 * ratio:.8f},aresample=48000,"
                    f"aformat=sample_fmts=fltp:channel_layouts=stereo,asetpts=N/SR/TB[a{i}];"
                    for i in range(2)
                ) + "[a0][a1]concat=n=2:v=0:a=1[out]"
                subprocess.run([FFMPEG, "-v", "error", "-i", str(self.first), "-i", str(self.second),
                                "-filter_complex", graph, "-map", "[out]", "-map_metadata", "-1",
                                "-c:a", "pcm_s24le", "-f", "wav", str(reference)], check=True)
                def decoded(path):
                    return subprocess.check_output([FFMPEG, "-v", "error", "-i", str(path), "-f", "f32le", "-"])
                data = decoded(out)
                self.assertEqual(data, decoded(reference), "decoded WAV must exactly match the specified graph")
                samples = array.array("f", data)
                self.assertTrue(all(math.isfinite(value) for value in samples))
                self.assertAlmostEqual(len(samples) / 2 / 48000, 2 / ratio, delta=max(.01, .002 / ratio))
                info = json.loads(subprocess.check_output([FFPROBE, "-v", "error", "-show_streams", "-of", "json", str(out)]))["streams"][0]
                self.assertEqual((info["codec_name"], info["sample_rate"], info["channels"]), ("pcm_s24le", "48000", 2))

    def test_aliases_and_invalid_inputs(self):
        before = hashlib.sha256(self.first.read_bytes()).digest()
        for name, make in [("hard.wav", os.link), ("symbolic.wav", os.symlink)]:
            alias = self.root / name
            make(self.first, alias)
            self.cli(self.first, "-o", alias, "--overwrite", ok=False)
        self.assertEqual(before, hashlib.sha256(self.first.read_bytes()).digest())
        fifo = self.root / "fifo.wav"
        os.mkfifo(fifo)
        empty = self.root / "empty.wav"
        empty.touch()
        zero = self.root / "zero-frames.wav"
        tone(zero, frames=0)
        corrupt = self.root / "corrupt.wav"
        corrupt.write_bytes(b"invalid audio")
        text = self.root / "text.txt"
        text.write_text("not audio")
        for source in [fifo, self.root, empty, zero, corrupt, text, self.root / "missing.wav"]:
            with self.subTest(source=source):
                self.cli(source, "-o", "absent.wav", ok=False)
                self.assertFalse((self.root / "absent.wav").exists())
        self.cli(self.first, zero, "-o", "absent.wav", ok=False)
        self.assertFalse((self.root / "absent.wav").exists())
        old = self.root / "old.wav"
        old.write_bytes(b"old-output")
        self.cli(corrupt, "-o", old, "--overwrite", ok=False)
        self.assertEqual(old.read_bytes(), b"old-output")
        dangling = self.root / "dangling.wav"
        dangling.symlink_to(self.root / "missing")
        self.cli(self.first, "-o", dangling, ok=False)
        self.assertTrue(dangling.is_symlink())
        parent = self.root / "readonly"
        parent.mkdir(mode=0o555)
        self.addCleanup(parent.chmod, 0o755)
        self.cli(self.first, "-o", parent / "out.wav", ok=False)
        self.cli(self.first, "-o", "missing-dependency.wav", env={**os.environ, "PATH": str(self.root)}, ok=False)

    def test_packet_present_but_no_decoded_samples(self):
        tiny = self.root / "tiny.wav"
        tone(tiny, rate=48000, frames=1)
        source = self.root / "empty-decoded.opus"
        subprocess.run([FFMPEG, "-v", "error", "-i", str(tiny),
                        "-c:a", "libopus", str(source)], check=True)
        data = bytearray(source.read_bytes())
        self.assertIn(b"OpusHead", data)
        head = data.index(b"OpusHead")
        preskip = struct.unpack_from("<H", data, head + 10)[0]
        last_page = data.rfind(b"OggS")
        # A valid audio packet is fully discarded by the final granule position.
        struct.pack_into("<Q", data, last_page + 6, preskip)
        data[last_page + 22:last_page + 26] = b"\x00" * 4
        crc = 0
        for byte in data[last_page:]:
            crc ^= byte << 24
            for _ in range(8):
                crc = ((crc << 1) ^ (0x04c11db7 if crc & 0x80000000 else 0)) & 0xffffffff
        struct.pack_into("<I", data, last_page + 22, crc)
        source.write_bytes(data)
        packets = json.loads(subprocess.check_output([
            FFPROBE, "-v", "error", "-show_packets", "-of", "json", str(source)]))["packets"]
        self.assertGreater(len(packets), 0)
        decoded = subprocess.check_output([FFMPEG, "-v", "error", "-i", str(source), "-f", "s16le", "-"])
        self.assertEqual(decoded, b"")
        output = self.root / "empty-track.wav"
        result = self.cli(self.first, source, "-o", output, ok=False)
        self.assertIn("empty", result.stderr.lower())
        self.assertFalse(output.exists())

    def test_first_audio_stream_selection(self):
        source = self.root / "two-streams.mka"
        subprocess.run([FFMPEG, "-v", "error", "-i", str(self.first), "-i", str(self.second),
                        "-map", "0:a", "-map", "1:a", "-c:a", "pcm_s16le", str(source)], check=True)
        self.cli(source, "-o", "first-stream.wav")
        self.cli(self.first, "-o", "first-only.wav")
        self.assertEqual((self.root / "first-stream.wav").read_bytes(),
                         (self.root / "first-only.wav").read_bytes())

    def test_mp3_profile_and_metadata(self):
        source = self.root / "tagged.mp3"
        subprocess.run([FFMPEG, "-v", "error", "-i", str(self.first), "-metadata", "title=source-secret",
                        "-c:a", "libmp3lame", str(source)], check=True)
        output = self.root / "output.Mp3"
        self.cli(source, self.second, "-o", output, "--speed", 125.5)
        probe = json.loads(subprocess.check_output([FFPROBE, "-v", "error", "-show_streams", "-show_format",
                                                   "-of", "json", str(output)]))
        stream = probe["streams"][0]
        self.assertEqual((stream["codec_name"], stream["sample_rate"], stream["channels"], stream["bit_rate"]),
                         ("mp3", "48000", 2, "320000"))
        self.assertNotIn("title", probe["format"].get("tags", {}))

    def test_relative_path_dependency_lookup(self):
        bindir = self.root / "relative-bin"
        bindir.mkdir()
        (bindir / "ffmpeg").symlink_to(FFMPEG)
        self.cli(self.first, "-o", "relative-path.wav",
                 env={**os.environ, "PATH": "relative-bin"})

    def test_manifest_entrypoint(self):
        manifest = json.loads((HERE / "entry.json").read_text())
        cwd = HERE.parent.parent / manifest["cwd"]
        self.assertEqual(cwd.resolve(), HERE)
        output = self.root / "manifest.wav"
        result = subprocess.run([*manifest["run"], str(self.first), "--cents", "-300", "-o", str(output)],
                                cwd=cwd, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(output.is_file())

    def start_controlled_child(self, output, extra=(), exit_code=0,
                               execute_real=False, ignore_signals=False,
                               diagnostic_bytes=0, close_diagnostics=False):
        bindir = self.root / "bin"
        bindir.mkdir(exist_ok=True)
        stub = bindir / "ffmpeg"
        script = (f"#!{sys.executable}\n" +
                  "import os, pathlib, signal, sys, time\n")
        if ignore_signals:
            script += "for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP): signal.signal(sig, signal.SIG_IGN)\n"
        if not execute_real:
            script += "pathlib.Path('audio').write_bytes(b'completed test output')\n"
        script += ("pathlib.Path(os.environ['READY']).write_text(str(os.getpid()))\n"
                   "while not pathlib.Path(os.environ['RELEASE']).exists(): time.sleep(.01)\n")
        if execute_real:
            script += f"os.execv({FFMPEG!r}, [{FFMPEG!r}, *sys.argv[1:]])\n"
        if diagnostic_bytes:
            script += f"for _ in range({diagnostic_bytes} // 4096): os.write(2, b'x' * 4096)\n"
            script += "os.write(2, b'FINAL-DIAGNOSTIC\\n')\n"
        if close_diagnostics:
            # Exercise the wait path after stderr EOF, not just poll cancellation.
            script += "os.close(2)\nwhile True: time.sleep(.01)\n"
        script += "raise SystemExit(int(os.environ.get('STUB_EXIT', '0')))\n"
        stub.write_text(script)
        stub.chmod(0o755)
        marker, release = self.root / "ready", self.root / "release"
        env = {**os.environ, "PATH": str(bindir), "READY": str(marker), "RELEASE": str(release), "STUB_EXIT": str(exit_code)}
        process = subprocess.Popen([str(BINARY), str(self.first), "-o", str(output), *extra],
                                   env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        def cleanup():
            if process.poll() is None:
                process.terminate()
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
        self.addCleanup(cleanup)
        deadline = time.monotonic() + 5
        while not marker.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertTrue(marker.exists(), "controlled FFmpeg must start")
        return process, release, int(marker.read_text())

    def test_pinned_input_survives_path_replacement(self):
        reference = self.root / "before.wav"
        self.cli(self.first, "-o", reference)
        output = self.root / "pinned.wav"
        process, release, _ = self.start_controlled_child(output, execute_real=True)
        self.first.unlink()
        os.mkfifo(self.first)  # reopening the validated pathname would now hang
        release.touch()
        _, stderr = process.communicate(timeout=5)
        self.assertEqual(process.returncode, 0, stderr)
        self.assertEqual(output.read_bytes(), reference.read_bytes())
        self.assertEqual(list(self.root.glob(".resample-*.tmp")), [])

    def test_bounded_diagnostics(self):
        output = self.root / "noisy.wav"
        process, release, _ = self.start_controlled_child(
            output, exit_code=19, diagnostic_bytes=1024 * 1024)
        release.touch()
        _, stderr = process.communicate(timeout=5)
        self.assertNotEqual(process.returncode, 0)
        self.assertLess(len(stderr), 17000)
        self.assertIn(b"truncated", stderr)
        self.assertIn(b"FINAL-DIAGNOSTIC", stderr)
        self.assertFalse(output.exists())
        self.assertEqual(list(self.root.glob(".resample-*.tmp")), [])

    def test_cancellation_escalates_and_reaps(self):
        for close_diagnostics in (False, True):
            with self.subTest(stderr_closed=close_diagnostics):
                (self.root / "ready").unlink(missing_ok=True)
                (self.root / "release").unlink(missing_ok=True)
                output = self.root / "stubborn.wav"
                output.write_bytes(b"old-output")
                process, release, child = self.start_controlled_child(
                    output, extra=["--overwrite"], ignore_signals=True,
                    close_diagnostics=close_diagnostics)
                if close_diagnostics:
                    release.touch()
                    time.sleep(.05)
                start = time.monotonic()
                process.terminate()
                _, stderr = process.communicate(timeout=4)
                self.assertEqual(process.returncode, 128 + signal.SIGTERM, stderr)
                self.assertLess(time.monotonic() - start, 3)
                with self.assertRaises(ProcessLookupError):
                    os.kill(child, 0)
                self.assertEqual(output.read_bytes(), b"old-output")
                self.assertEqual(list(self.root.glob(".resample-*.tmp")), [])

    def test_seekable_wav_header_and_short_inputs(self):
        for rate in (32000, 44100, 48000):
            for frames in (rate // 200, rate // 50):
                for speed in (25, 100, 400):
                    with self.subTest(rate=rate, frames=frames, speed=speed):
                        source = self.root / f"short-{rate}-{frames}.wav"
                        tone(source, rate=rate, frames=frames)
                        output = self.root / f"short-{rate}-{frames}-{speed}.wav"
                        self.cli(source, "--speed", speed, "-o", output)
                        raw = output.read_bytes()
                        self.assertEqual(raw[:4], b"RIFF")
                        self.assertEqual(struct.unpack_from("<I", raw, 4)[0], len(raw) - 8)
                        offset = 12
                        while raw[offset:offset + 4] != b"data":
                            size = struct.unpack_from("<I", raw, offset + 4)[0]
                            offset += 8 + size + (size % 2)
                        size = struct.unpack_from("<I", raw, offset + 4)[0]
                        self.assertEqual(size, len(raw) - offset - 8)
                        ratio = speed / 100
                        graph = (f"[0:a:0]aresample=48000,asetrate={48000 * ratio:.8f},aresample=48000,"
                                 "aformat=sample_fmts=fltp:channel_layouts=stereo,asetpts=N/SR/TB[a0];"
                                 "[a0]concat=n=1:v=0:a=1[out]")
                        reference = subprocess.check_output([FFMPEG, "-v", "error", "-i", str(source),
                            "-filter_complex", graph, "-map", "[out]", "-c:a", "pcm_s24le", "-f", "s24le", "-"])
                        self.assertGreater(len(reference), 0, "ordinary short qualification requires a nonempty reference")
                        self.assertEqual(raw[offset + 8:], reference)
                        self.assertAlmostEqual(size / 6 / 48000,
                                               frames / rate * 100 / speed, delta=.005)

    def test_reference_empty_boundary_fails_cleanly(self):
        for rate in (44100, 48000):
            source = self.root / f"boundary-{rate}.wav"
            tone(source, rate=rate, frames=64)
            graph = ("[0:a:0]aresample=48000,asetrate=192000.00000000,aresample=48000,"
                     "aformat=sample_fmts=fltp:channel_layouts=stereo,asetpts=N/SR/TB[a0];"
                     "[a0]concat=n=1:v=0:a=1[out]")
            reference = subprocess.check_output([FFMPEG, "-v", "error", "-i", str(source),
                "-filter_complex", graph, "-map", "[out]", "-f", "f32le", "-"])
            self.assertEqual(reference, b"", "boundary must actually be reference-empty")
            for existing in (False, True):
                output = self.root / f"boundary-{rate}-{existing}.wav"
                if existing:
                    output.write_bytes(b"old-output")
                self.cli(source, "--speed", 400, "-o", output, "--overwrite", ok=False)
                if existing:
                    self.assertEqual(output.read_bytes(), b"old-output")
                else:
                    self.assertFalse(output.exists())

    def test_inherited_sigchld_ignore_and_closed_standard_descriptors(self):
        for close_standard in (False, True):
            def prepare():
                signal.signal(signal.SIGCHLD, signal.SIG_IGN)
                if close_standard:
                    os.close(0)
                    os.close(1)
            output = self.root / f"inherited-{close_standard}.wav"
            result = subprocess.run([str(BINARY), str(self.first), "-o", str(output)],
                preexec_fn=prepare, capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(output.is_file())
            self.assertEqual(list(self.root.glob(".resample-*.tmp")), [])

    def test_atomic_collision(self):
        output = self.root / "race.wav"
        process, release, _ = self.start_controlled_child(output)
        self.assertFalse(output.exists())
        output.write_bytes(b"racing-writer")
        release.touch()
        _, stderr = process.communicate(timeout=5)
        self.assertNotEqual(process.returncode, 0, stderr)
        self.assertEqual(output.read_bytes(), b"racing-writer")
        self.assertEqual(list(self.root.glob(".resample-*.tmp")), [])

    def test_atomic_overwrite_visibility(self):
        output = self.root / "overwrite.wav"
        output.write_bytes(b"old-output")
        process, release, _ = self.start_controlled_child(output, ["--overwrite"])
        self.assertEqual(output.read_bytes(), b"old-output")
        release.touch()
        _, stderr = process.communicate(timeout=5)
        self.assertEqual(process.returncode, 0, stderr)
        self.assertEqual(output.read_bytes(), b"completed test output")
        self.assertEqual(list(self.root.glob(".resample-*.tmp")), [])

    def test_alias_created_during_render(self):
        output = self.root / "alias-race.wav"
        process, release, _ = self.start_controlled_child(output, ["--overwrite"])
        os.link(self.first, output)
        release.touch()
        _, stderr = process.communicate(timeout=5)
        self.assertNotEqual(process.returncode, 0, stderr)
        self.assertTrue(os.path.samefile(output, self.first))
        self.assertEqual(list(self.root.glob(".resample-*.tmp")), [])

    def test_child_failure_preserves_output(self):
        output = self.root / "failed.wav"
        for existing in (False, True):
            (self.root / "ready").unlink(missing_ok=True)
            (self.root / "release").unlink(missing_ok=True)
            if existing:
                output.write_bytes(b"old-output")
            process, release, _ = self.start_controlled_child(output, ["--overwrite"], exit_code=19)
            release.touch()
            _, stderr = process.communicate(timeout=5)
            self.assertNotEqual(process.returncode, 0)
            self.assertIn(b"19", stderr)
            if existing:
                self.assertEqual(output.read_bytes(), b"old-output")
            else:
                self.assertFalse(output.exists())
            self.assertEqual(list(self.root.glob(".resample-*.tmp")), [])

    def test_signal_cleanup(self):
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            with self.subTest(signal=sig):
                marker = self.root / "ready"
                marker.unlink(missing_ok=True)
                output = self.root / "cancelled.wav"
                process, _, child = self.start_controlled_child(output)
                process.send_signal(sig)
                process.communicate(timeout=5)
                self.assertEqual(process.returncode, 128 + sig)
                with self.assertRaises(ProcessLookupError):
                    os.kill(child, 0)
                self.assertFalse(output.exists())
                self.assertEqual(list(self.root.glob(".resample-*.tmp")), [])
        (self.root / "ready").unlink()
        output.write_bytes(b"old-output")
        process, _, _ = self.start_controlled_child(output, ["--overwrite"])
        process.terminate()
        process.communicate(timeout=5)
        self.assertEqual(output.read_bytes(), b"old-output")
        self.assertEqual(list(self.root.glob(".resample-*.tmp")), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
