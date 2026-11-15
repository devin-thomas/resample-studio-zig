"""Focused Go entry checks plus the unchanged baseline's individual-entry tests.

Run: python3 validate.py bin/resample
All generated fixtures stay in this directory's ignored .validation/ folder.
"""
import importlib.util
import base64
import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import wave

sys.dont_write_bytecode = True

HERE = Path(__file__).resolve().parent
binary = Path(sys.argv.pop(1)).resolve()
scratch = HERE / ".validation"
scratch.mkdir(exist_ok=True)
tempfile.tempdir = str(scratch)
spec = importlib.util.spec_from_file_location("baseline", HERE.parent.parent / "tests/integration.py")
sys.argv.insert(1, str(binary))
baseline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(baseline)


class GoTests(baseline.AudioTests):
    def cli(self, *args, **kwargs):
        result = super().cli(*args, **kwargs)
        self.assertFalse(list(self.root.glob(".resample-*.tmp")))
        return result

    def test_aliases_and_symlink_parent(self):
        original = self.first.read_bytes()
        for name, link in (("hard.wav", os.link), ("symbolic.wav", os.symlink)):
            output = self.root / name
            link(self.first, output)
            self.cli(self.first, "-o", output, "--overwrite", success=False)
            self.assertEqual(self.first.read_bytes(), original)
        alias = self.root / "alias"
        alias.symlink_to(self.root, target_is_directory=True)
        self.cli(self.first, "-o", alias / self.first.name, "--overwrite", success=False)
        self.assertEqual(self.first.read_bytes(), original)

    def test_empty_non_audio_and_empty_decoded(self):
        for name, contents in (("empty.wav", b""), ("text.wav", b"not audio")):
            source = self.root / name
            source.write_bytes(contents)
            self.cli(self.first, source, "-o", "bad.wav", success=False)
            self.assertFalse((self.root / "bad.wav").exists())
        empty = self.root / "no-frames.wav"
        with wave.open(str(empty), "wb") as f:
            f.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
        self.cli(self.first, empty, "-o", "bad.wav", success=False)
        self.assertFalse((self.root / "bad.wav").exists())

    def test_fractional_and_cents_audio(self):
        for flag, values in (("--speed", (50, 100, 73.5, 125.5, 200)), ("--cents", (-2400, -300, 0, 300, 1200, 2400))):
            for value in values:
                ratio = value / 100 if flag == "--speed" else 2 ** (value / 1200)
                output = self.root / f"{flag}-{value}.wav"
                self.cli(self.first, flag, value, "-o", output)
                data = baseline.samples(output)
                self.assertAlmostEqual(len(data) / 48000, 1 / ratio, delta=.005)
                f = baseline.frequency(data, .05 / ratio, .95 / ratio)
                self.assertAlmostEqual(f, 440 * ratio, delta=max(2, .005 * 440 * ratio))

    def fake_child(self, body):
        tools = self.root / "tools"
        tools.mkdir(exist_ok=True)
        tool = tools / "ffmpeg"
        tool.write_text(f"#!{sys.executable}\n" + body)
        tool.chmod(0o755)
        return dict(os.environ, PATH=str(tools))

    def test_packet_present_but_no_decoded_samples(self):
        # 20 ms Opus with a valid Ogg checksum and pre-skip larger than all
        # available samples. Demuxing succeeds and yields packets; decoding
        # succeeds but yields zero PCM. No optional encoder is needed to test it.
        empty = self.root / "packet-only.ogg"
        empty.write_bytes(base64.b64decode(
            'T2dnUwACAAAAAAAAAACvaYX2AAAAAHjED60BE09wdXNIZWFkAQH//4C7AAAAAABPZ2dTAAAAAAAAAAAAAK9phfYBAAAA+oct'
            'FwE8T3B1c1RhZ3MMAAAATGF2ZjYzLjEuMTAyAQAAABwAAABlbmNvZGVyPUxhdmM2My4xLjEwMiBsaWJvcHVzT2dnUwAE+AQA'
            'AAAAAACvaYX2AgAAAOWv62cE/zT/KfhySUcnEOTIW3gni4b32O/b6HXj7DbmEfIQJ9omiLU4ys7y9FFC072ouwRXd/S8C2Xb'
            'WTqfCtrwjwAs2nZy5qhKlpcZWHv9VILmJ5YO18hDbJj4aboLES7dFxDRMbvhn+Y+NxQ/y6GY+uLld+eGaFg0Jemq7Bw096oZ'
            'fe9r3rfPDKFttEtzZDgauWBjJLky4zzNd1FgM9J+Vuveb+UBpF9mw57xV4jvOYnX14m4mW1w/Zs9rVIAP2W5Xt9+8nCiIc+k'
            'tW46Ic+lLN2RfKnt6pEqe1he70yrH4MjqVDXCsQONm1+GPuQnGpOfhmCcwBlkHdxX+vgfUIK15X6Bv+y3SVikwqWjdbuuIrL'
            'yz5KRO60WiyLklTW8+gQziERMzOaT5HSMgc6k4ZG0ZOEq/Cgxk8kMiX4ea4Dfb+4JRQPK6wtpQoCy5Ylt7BIKwop+nDTznri'
            'jSnjLUuGsMdOu4Y3H7IhMu25ZJy0uD8o90gnBnVa4Z6CvIsmEBvzyiknXjF4dSot4qv1RIizwRB+Ds+13gZcUci0f3JmvYc8'
            'tKfrDC7w00BSSXQP+fdEdyWaeXvQ7fccJmXe57gAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAQswcnz1jJpaTE'
            'DV9oc4R+dX0/Or6JpD/Rc6kP5ZEldv1WAnJEpm5KAMgYsX1G2oIHe/W8HfreBFxFCRRrCN2JNp+EuSSmNqxDHa+DnmeL4iw3'
            'odbHhM9sBwa3O7oSD58F0XaJ+K2AIfniBYnoY9MC5LQ3xNmzzQuO8PYrhO3UB0bxrA=='
        ))
        packets = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_packets", "-of", "json", str(empty)]))["packets"]
        self.assertTrue(packets)
        self.assertEqual(subprocess.check_output(["ffmpeg", "-v", "error", "-i", str(empty), "-f", "f32le", "-"]), b"")
        for tracks in ((empty, self.first), (self.first, empty)):
            output = self.root / "rejected.wav"
            self.cli(*tracks, "-o", output, success=False)
            self.assertFalse(output.exists())
        output.write_bytes(b"preserve existing")
        self.cli(self.first, empty, "-o", output, "--overwrite", success=False)
        self.assertEqual(output.read_bytes(), b"preserve existing")

    def test_inputs_are_pinned_before_child_start(self):
        actual = shutil.which("ffmpeg")
        held = self.root / "original.wav"
        env = self.fake_child(
            "import os,pathlib,sys\n"
            f"os.rename({str(self.first)!r}, {str(held)!r})\n"
            f"pathlib.Path({str(self.first)!r}).write_bytes(b'replacement must not be decoded')\n"
            f"os.execv({actual!r}, [{actual!r}, *sys.argv[1:]])\n"
        )
        output = self.root / "pinned.wav"
        self.cli(self.first, "-o", output, env=env)
        self.assertAlmostEqual(len(baseline.samples(output)) / 48000, 1, delta=.005)
        self.assertAlmostEqual(baseline.frequency(baseline.samples(output), .1, .9), 440, delta=2)
        self.assertEqual(self.first.read_bytes(), b"replacement must not be decoded")

    def test_alias_created_during_render(self):
        original = self.first.read_bytes()
        for link in ("link", "symlink"):
            output = self.root / f"late-{link}.wav"
            env = self.fake_child(
                "import os,pathlib,sys\n"
                f"os.{link}({str(self.first)!r}, {str(output)!r})\n"
                "pathlib.Path(sys.argv[-1]).write_bytes(b'complete render')\n"
            )
            self.cli(self.first, "-o", output, "--overwrite", env=env, success=False)
            self.assertEqual(self.first.read_bytes(), original)
            self.assertEqual(output.read_bytes(), original)

    def test_unrelated_and_dangling_output_symlinks(self):
        target = self.root / "target"
        target.write_bytes(b"preserve target")
        for destination in (target, self.root / "absent"):
            output = self.root / f"{destination.name}.wav"
            output.symlink_to(destination)
            self.cli(self.first, "-o", output, success=False)
            self.cli(self.first, "-o", output, "--overwrite")
            self.assertFalse(output.is_symlink())
            self.assertAlmostEqual(len(baseline.samples(output)) / 48000, 1, delta=.005)
        self.assertEqual(target.read_bytes(), b"preserve target")

    def test_bounded_child_diagnostics(self):
        output = self.root / "noisy.wav"
        env = self.fake_child(
            "import sys\n"
            "sys.stderr.write('x'*2000000+'final decoder error\\n')\n"
            "sys.exit(8)\n"
        )
        result = self.cli(self.first, "-o", output, env=env, success=False)
        self.assertLess(len(result.stderr), 17000)
        self.assertTrue(result.stderr.endswith("final decoder error\n"))
        self.assertFalse(output.exists())

    def test_descendant_stderr_does_not_hold_parent_open(self):
        output = self.root / "held-pipe.wav"
        output.write_bytes(b"preserve existing")
        pidfile = self.root / "descendant.pid"
        env = self.fake_child(
            "import pathlib,subprocess,sys\n"
            "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'])\n"
            f"pathlib.Path({str(pidfile)!r}).write_text(str(p.pid))\n"
            "pathlib.Path(sys.argv[-1]).write_bytes(b'complete render')\n"
        )
        started = time.monotonic()
        try:
            result = self.cli(self.first, "-o", output, "--overwrite", env=env, success=False)
            self.assertLess(time.monotonic() - started, 5)
            self.assertIn("WaitDelay", result.stderr)
            self.assertEqual(output.read_bytes(), b"preserve existing")
        finally:
            if pidfile.exists():
                try:
                    os.kill(int(pidfile.read_text()), signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def test_short_inputs_match_exact_reference(self):
        import struct
        for frames in (1, 240, 960):
            source = self.root / f"short-{frames}.wav"
            with wave.open(str(source), "wb") as f:
                f.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
                f.writeframes(struct.pack("<h", 1000) * frames)
            for speed in ((100,) if frames == 1 else (25, 100, 125.5, 400)):
                output, reference = self.root / "short.wav", self.root / "reference.wav"
                self.cli(source, "--speed", speed, "-o", output, "--overwrite")
                graph = f"[0:a:0]aresample=48000,asetrate={48000 * speed / 100:.8f},aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,asetpts=N/SR/TB[a0];[a0]concat=n=1:v=0:a=1[out]"
                subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(source), "-filter_complex", graph, "-map", "[out]", "-c:a", "pcm_s24le", str(reference)], check=True)
                self.assertEqual(baseline.samples(output), baseline.samples(reference))
                self.assertAlmostEqual(len(baseline.samples(output)) / 48000, frames / 48000 * 100 / speed, delta=.005)
        # Below FFmpeg's resampler delay the specified graph itself can emit
        # zero frames. Do not publish an unusable output or fabricate samples.
        source = self.root / "short-1.wav"
        output = self.root / "tiny-preserved.wav"
        output.write_bytes(b"preserve tiny failure")
        for speed in (25, 400):
            reference = self.root / "reference-empty.wav"
            graph = f"[0:a:0]aresample=48000,asetrate={48000 * speed / 100:.8f},aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,asetpts=N/SR/TB[a0];[a0]concat=n=1:v=0:a=1[out]"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(source), "-filter_complex", graph, "-map", "[out]", "-c:a", "pcm_s24le", str(reference)], check=True)
            self.assertEqual(len(baseline.samples(reference)), 0)
            self.cli(source, "--speed", speed, "-o", output, "--overwrite", success=False)
        self.assertEqual(output.read_bytes(), b"preserve tiny failure")

    def test_finalized_wav_headers_and_mp3_profile(self):
        import struct
        output = self.root / "seekable.wav"
        self.cli(self.first, "-o", output)
        data = output.read_bytes()
        self.assertEqual(data[:4], b"RIFF")
        self.assertEqual(struct.unpack_from("<I", data, 4)[0], len(data) - 8)
        position = 12
        while position + 8 <= len(data):
            kind, size = struct.unpack_from("<4sI", data, position)
            if kind == b"data":
                self.assertEqual(size, 48000 * 2 * 3)
                self.assertEqual(position + 8 + size, len(data))
                break
            position += 8 + size + (size % 2)
        else:
            self.fail("WAV has no data chunk")
        output = self.root / "profile.mp3"
        self.cli(self.first, "-o", output)
        info = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(output)]))["streams"][0]
        self.assertEqual((info["codec_name"], info["sample_rate"], info["channels"], info["bit_rate"]), ("mp3", "48000", 2, "320000"))

    def test_first_audio_metadata_and_mixed_reference(self):
        source = self.root / "two-streams.mkv"
        subprocess.run(["ffmpeg", "-v", "error", "-i", str(self.first), "-i", str(self.second), "-map", "0:a:0", "-map", "1:a:0", "-c:a", "pcm_s16le", "-metadata", "title=source title", "-metadata:s:a:0", "title=source stream title", str(source)], check=True)
        output, reference = self.root / "mixed.wav", self.root / "mixed-reference.wav"
        self.cli(source, self.second, "--cents", -300, "-o", output)
        rate = 48000 * 2 ** (-300 / 1200)
        chain = f"aresample=48000,asetrate={rate:.8f},aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,asetpts=N/SR/TB"
        graph = f"[0:a:0]{chain}[a0];[1:a:0]{chain}[a1];[a0][a1]concat=n=2:v=0:a=1[out]"
        subprocess.run(["ffmpeg", "-v", "error", "-i", str(source), "-i", str(self.second), "-filter_complex", graph, "-map", "[out]", "-c:a", "pcm_s24le", str(reference)], check=True)
        self.assertEqual(baseline.samples(output), baseline.samples(reference))
        info = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(output)]))
        self.assertNotIn("title", info["format"].get("tags", {}))
        self.assertNotIn("title", info["streams"][0].get("tags", {}))
        data = baseline.samples(output)
        self.assertAlmostEqual(baseline.frequency(data, .1, 1), 440 * rate / 48000, delta=2)

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root can bypass directory write permissions")
    def test_unwritable_output_parent(self):
        parent = self.root / "read-only"
        parent.mkdir()
        output = parent / "keep.wav"
        output.write_bytes(b"preserve existing")
        parent.chmod(0o500)
        try:
            self.cli(self.first, "-o", output, "--overwrite", success=False)
            self.assertEqual(output.read_bytes(), b"preserve existing")
            absent = parent / "absent.wav"
            self.cli(self.first, "-o", absent, success=False)
            self.assertFalse(absent.exists())
        finally:
            parent.chmod(0o700)

    def test_publication_collision(self):
        output = self.root / "collision.wav"
        env = self.fake_child(
            "import pathlib,sys\n"
            f"pathlib.Path({str(output)!r}).write_bytes(b'racing output')\n"
            "pathlib.Path(sys.argv[-1]).write_bytes(b'completed render')\n"
        )
        self.cli(self.first, "-o", output, env=env, success=False)
        self.assertEqual(output.read_bytes(), b"racing output")

    def test_child_failure_and_cancellation(self):
        output = self.root / "cancel.wav"
        output.write_bytes(b"keep me")
        pidfile = self.root / "child.pid"
        ready = self.root / "ready"
        env = self.fake_child(
            "import os,pathlib,sys,time\n"
            f"pathlib.Path({str(pidfile)!r}).write_text(str(os.getpid()))\n"
            "pathlib.Path(sys.argv[-1]).write_bytes(b'partial')\n"
            f"pathlib.Path({str(ready)!r}).touch()\n"
            "time.sleep(30)\n"
        )
        for sig in (signal.SIGINT, signal.SIGTERM):
            ready.unlink(missing_ok=True)
            child = subprocess.Popen([str(binary), str(self.first), "-o", str(output), "--overwrite"], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                deadline = time.monotonic() + 5
                while not ready.exists() and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertTrue(ready.exists(), "FFmpeg stand-in did not start")
                child.send_signal(sig)
                _, error = child.communicate(timeout=5)
                self.assertEqual(child.returncode, 130, error)
                with self.assertRaises(ProcessLookupError):
                    os.kill(int(pidfile.read_text()), 0)
                self.assertEqual(output.read_bytes(), b"keep me")
                self.assertFalse(list(self.root.glob(".resample-*.tmp")))
            finally:
                if child.poll() is None:
                    child.kill()
                    child.wait()
        # A handled child error must never publish its partial temporary.
        tool = Path(env["PATH"]) / "ffmpeg"
        tool.write_text(f"#!{sys.executable}\nimport pathlib,sys\npathlib.Path(sys.argv[-1]).write_bytes(b'partial')\nprint('decode failed',file=sys.stderr)\nsys.exit(9)\n")
        self.cli(self.first, "-o", output, "--overwrite", env=env, success=False)
        self.assertEqual(output.read_bytes(), b"keep me")

    def test_profile_reference_and_stereo_order(self):
        source = self.root / "stereo.wav"
        import math, struct
        with wave.open(str(source), "wb") as f:
            f.setparams((2, 2, 48000, 0, "NONE", "not compressed"))
            f.writeframes(b"".join(struct.pack("<hh", round(8000 * math.sin(2 * math.pi * 440 * i / 48000)), round(8000 * math.sin(2 * math.pi * 880 * i / 48000))) for i in range(48000)))
        out, ref = self.root / "output.wav", self.root / "reference.wav"
        self.cli(source, "--speed", "125.5", "-o", out)
        graph = "[0:a:0]aresample=48000,asetrate=60240.00000000,aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,asetpts=N/SR/TB[a0];[a0]concat=n=1:v=0:a=1[out]"
        subprocess.run(["ffmpeg", "-v", "error", "-i", str(source), "-filter_complex", graph, "-map", "[out]", "-map_metadata", "-1", "-c:a", "pcm_s24le", str(ref)], check=True)
        def decoded(path):
            return subprocess.check_output(["ffmpeg", "-v", "error", "-i", str(path), "-f", "f32le", "-"])
        self.assertEqual(decoded(out), decoded(ref))
        import array
        data = array.array("f", decoded(out))
        if sys.byteorder != "little": data.byteswap()
        for channel, hz in ((data[::2], 552.2), (data[1::2], 1104.4)):
            self.assertAlmostEqual(baseline.frequency(channel, .1, .7), hz, delta=2)
        info = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(out)]))["streams"][0]
        self.assertEqual((info["sample_rate"], info["channels"], info["bits_per_sample"]), ("48000", 2, 24))


if __name__ == "__main__":
    unittest.main()
