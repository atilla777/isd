"""Real Xvfb integration checks. Artifacts retained in /tmp/opencode/isd-live-*.

Run explicitly: python3 -m unittest tests.test_live -v
No operation queries or changes the desktop's focus or input devices.
"""

from concurrent.futures import ThreadPoolExecutor
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

from PIL import Image, ImageChops

from isdlib.core import atomic_json, matches, process, read_state, signal_owned, xenv

REPO = Path(__file__).resolve().parents[1]


class LiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.artifacts = Path(tempfile.mkdtemp(prefix="isd-live-", dir="/tmp/opencode"))
        cls.root = cls.artifacts / "state"
        cls.fixture = cls.artifacts / "fixture"
        subprocess.run(
            [
                "cc",
                "-Wall",
                "-Wextra",
                "-Werror",
                str(REPO / "tests/fixture.c"),
                "-o",
                str(cls.fixture),
                "-lX11",
            ],
            check=True,
        )
        print(f"\nLIVE ARTIFACTS: {cls.artifacts}", flush=True)

    def setUp(self):
        self.ids = []
        self.foreign = []

    def tearDown(self):
        for ident in self.ids:
            self.cli("stop", ident)
        for child in self.foreign:
            if child.poll() is None:
                child.terminate()
            child.wait(timeout=5)

    def cli(self, *args, ok=True, env=None):
        result = subprocess.run(
            [sys.executable, str(REPO / "isd"), "--root", str(self.root), *args],
            capture_output=True,
            text=True,
            timeout=90,
            env=env,
        )
        try:
            data = json.loads(result.stdout)
        except ValueError:
            self.fail(f"not JSON: {args}: {result.stdout!r} {result.stderr!r}")
        self.assertEqual(result.returncode == 0, ok, (args, data, result.stderr))
        self.assertEqual(data["ok"], ok, data)
        return data

    def create(self, env=None):
        data = self.cli("create", "--size", "640x480", env=env)
        self.ids.append(data["id"])
        return data["id"]

    def base(self, ident):
        return self.root / ident

    def status(self, ident):
        return self.cli("status", ident)

    def wait(self, condition, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = condition()
            if value:
                return value
            time.sleep(0.05)
        self.fail("condition timed out")

    def launch_fixture(self, ident):
        self.cli("launch", ident, "--", str(self.fixture))
        windows = self.wait(lambda: self.cli("windows", ident)["windows"])
        return str(next(w["id"] for w in windows if w["name"] == "ISD fixture"))

    def shot(self, ident, name, window=None):
        output = self.artifacts / f"{ident}-{name}.png"
        target = ["--window", window] if window else ["--screen"]
        self.cli("screenshot", ident, *target, "--output", str(output))
        with Image.open(output) as image:
            image.load()
            return image.convert("RGB")

    def tool(self, ident, *args, env=None, ok=True):
        state = self.status(ident)
        result = subprocess.run(
            args,
            env=env or xenv(os.environ, self.base(ident), state["display"]),
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode == 0, ok, result.stderr)
        return result.stdout.strip()

    def log_contains(self, ident, text):
        return text in (self.base(ident) / "app.log").read_text()

    def test_parallel_real_pixels_input_and_independent_stop(self):
        # Start truly concurrently: no display-number check/reservation race.
        with ThreadPoolExecutor(max_workers=2) as pool:
            a, b = list(pool.map(lambda _: self.create(), range(2)))
        self.assertNotEqual(self.status(a)["display"], self.status(b)["display"])
        self.assertNotEqual(
            (self.base(a) / "Xauthority").read_bytes(),
            (self.base(b) / "Xauthority").read_bytes(),
        )
        wa, wb = self.launch_fixture(a), self.launch_fixture(b)
        self.cli("focus", a, "--window", wa)
        self.cli("focus", b, "--window", wb)
        a0, b0 = self.shot(a, "01-initial", wa), self.shot(b, "01-initial", wb)
        self.assertEqual(a0.size, (320, 240))
        self.assertEqual(a0.getpixel((5, 5)), (24, 48, 72))
        self.assertEqual(a0.getpixel((50, 50)), (255, 255, 255))
        self.assertGreater(len(a0.getcolors() or []), 1)
        focus = self.tool(a, "xdotool", "getwindowfocus")
        pointer = self.tool(a, "xdotool", "getmouselocation", "--shell")
        self.shot(a, "02-screen")
        self.assertEqual(focus, self.tool(a, "xdotool", "getwindowfocus"))
        self.assertEqual(
            pointer, self.tool(a, "xdotool", "getmouselocation", "--shell")
        )

        self.cli("key", a, "down", "a")
        self.wait(lambda: self.log_contains(a, "key-down=97"))
        self.cli("key", a, "up", "a")
        self.wait(lambda: self.log_contains(a, "key-up=97"))
        a1 = self.shot(a, "03-keyboard", wa)
        self.assertEqual(a1.getpixel((5, 5)), (44, 160, 90))
        self.assertIsNone(
            ImageChops.difference(b0, self.shot(b, "02-unaffected", wb)).getbbox()
        )
        start = time.monotonic()
        self.cli("key", b, "hold", "b", "--seconds", "0.2")
        self.assertGreaterEqual(time.monotonic() - start, 0.2)
        self.wait(lambda: self.log_contains(b, "key-up=98"))
        self.assertFalse(self.log_contains(a, "key-down=98"))
        self.cli("mouse", b, "move", "80", "90", "--window", wb)
        self.cli("mouse", b, "down", "1")
        self.wait(lambda: self.log_contains(b, "mouse-down=1 at=80,90"))
        self.cli("mouse", b, "up", "1")
        self.wait(lambda: self.log_contains(b, "mouse-up=1"))
        self.assertEqual(self.shot(b, "03-mouse", wb).getpixel((5, 5)), (192, 76, 56))
        self.assertIsNone(
            ImageChops.difference(a1, self.shot(a, "04-unaffected", wa)).getbbox()
        )
        self.cli("mouse", b, "hold", "1", "--seconds", "0.1")
        self.cli("key", a, "tap", "c")
        self.wait(lambda: self.log_contains(a, "key-up=99"))
        self.cli("stop", a)
        self.cli("stop", a)  # idempotent
        self.assertTrue(self.status(b)["app_alive"])
        self.assertTrue((self.base(a) / "app.log").is_file())
        self.shot(b, "04-after-stop-a", wb)
        self.cli(
            "screenshot",
            a,
            "--screen",
            "--output",
            str(self.artifacts / "must-not-exist.png"),
            ok=False,
        )

    def test_authorization_and_tcp_disabled(self):
        fakebin = self.artifacts / "xauth-probe"
        fakebin.mkdir()
        recorded = fakebin / "argv.json"
        wrapper = fakebin / "xauth"
        real_xauth = shutil.which("xauth")
        wrapper.write_text(
            f"#!{sys.executable}\nimport json,os,sys,time\nfrom pathlib import Path\n"
            f"Path({str(recorded)!r}).write_text(json.dumps(sys.argv[1:]))\n"
            f"time.sleep(0.25)\nos.execv({real_xauth!r}, ['xauth',*sys.argv[1:]])\n"
        )
        wrapper.chmod(0o700)
        env = dict(os.environ, PATH=str(fakebin) + os.pathsep + os.environ["PATH"])
        ident = self.create(env=env)
        state = self.status(ident)
        base = self.base(ident)
        self.assertEqual(
            json.loads(recorded.read_text()), ["-f", str(base / "Xauthority")]
        )
        self.assertNotIn(
            (base / "server.auth").read_bytes()[-16:].hex(), recorded.read_text()
        )
        env = xenv(os.environ, base, state["display"])
        env["XAUTHORITY"] = str(base / "nonexistent-auth")
        self.tool(ident, "xdotool", "getdisplaygeometry", env=env, ok=False)
        other = self.create()
        env["XAUTHORITY"] = str(self.base(other) / "Xauthority")
        self.tool(ident, "xdotool", "getdisplaygeometry", env=env, ok=False)
        argv = Path(f"/proc/{state['xvfb']['pid']}/cmdline").read_bytes().split(b"\0")
        self.assertIn(b"-displayfd", argv)
        self.assertNotIn(b"-ac", argv)
        index = argv.index(b"-nolisten")
        self.assertEqual(argv[index + 1], b"tcp")
        inodes = set()
        for fd in Path(f"/proc/{state['xvfb']['pid']}/fd").iterdir():
            try:
                target = os.readlink(fd)
                if target.startswith("socket:["):
                    inodes.add(target[8:-1])
            except FileNotFoundError:
                pass
        for name in ("tcp", "tcp6"):
            for line in Path(f"/proc/net/{name}").read_text().splitlines()[1:]:
                fields = line.split()
                self.assertFalse(fields[3] == "0A" and fields[9] in inodes)
        for path in (base, base / "runtime"):
            self.assertEqual(path.stat().st_mode & 0o777, 0o700)
        for path in (base / "Xauthority", base / "server.auth", base / "state.json"):
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_environment_argv_exit_and_missing_command(self):
        env = dict(
            os.environ,
            DISPLAY=":9876",
            WAYLAND_DISPLAY="real-wayland",
            WAYLAND_SOCKET="999",
            DBUS_SESSION_BUS_ADDRESS="real-bus",
            ISD_TEST_SECRET="do-not-persist-this-secret",
        )
        ident = self.create(env=env)
        code = (
            "import json,os,sys; "
            "print(json.dumps({k:os.environ.get(k) for k in "
            "['DISPLAY','WAYLAND_DISPLAY','WAYLAND_SOCKET','XDG_RUNTIME_DIR',"
            "'DBUS_SESSION_BUS_ADDRESS','PROJECT_VALUE']})); "
            "print(repr(sys.argv[1:])); sys.exit(7)"
        )
        self.cli("launch", ident, "--env", "DISPLAY=:0", "--", "true", ok=False)
        literal = "$(touch /not-executed); space"
        self.cli(
            "launch",
            ident,
            "--env",
            "PROJECT_VALUE=literal value",
            "--",
            sys.executable,
            "-c",
            code,
            literal,
            env=env,
        )
        state = self.wait(
            lambda: s if (s := self.status(ident))["app_exit"] == 7 else None
        )
        text = (self.base(ident) / "app.log").read_text()
        reported = json.loads(text.splitlines()[0])
        self.assertEqual(reported["DISPLAY"], state["display"])
        self.assertIsNone(reported["WAYLAND_DISPLAY"])
        self.assertIsNone(reported["WAYLAND_SOCKET"])
        self.assertEqual(reported["PROJECT_VALUE"], "literal value")
        self.assertEqual(reported["XDG_RUNTIME_DIR"], str(self.base(ident) / "runtime"))
        self.assertNotEqual(reported["DBUS_SESSION_BUS_ADDRESS"], "real-bus")
        self.assertIn(literal, text)
        for path in self.base(ident).glob("*.json"):
            self.assertNotIn(env["ISD_TEST_SECRET"], path.read_text())
        self.cli("launch", ident, "--", "true", ok=False)
        missing = self.create()
        self.cli("launch", missing, "--", "/no/such/isd-application", ok=False)
        self.assertTrue(self.status(missing)["xvfb_alive"])
        self.cli("launch", missing, "--", "true")  # failed exec didn't consume launch
        self.wait(lambda: self.status(missing)["app_exit"] == 0)

    def test_xvfb_death_stops_app_and_descendants(self):
        ident = self.create()
        self.launch_fixture(ident)
        state = self.status(ident)
        self.assertTrue(signal_owned(state["xvfb"], signal.SIGKILL))
        stopped = self.wait(
            lambda: s if not (s := self.status(ident))["supervisor_alive"] else None
        )
        self.assertFalse(matches(state["app"]))
        self.assertEqual(stopped["phase"], "stopped")
        self.assertIn("Xvfb exited", stopped["error"])
        self.cli("key", ident, "tap", "a", ok=False)
        replacement = self.create()
        window = self.launch_fixture(replacement)
        self.assertEqual(
            self.shot(replacement, "after-server-failure", window).size, (320, 240)
        )

    def test_supervisor_signals_and_daemonized_descendants(self):
        ident = self.create()
        # Double-fork + setsid would escape process-group-only cleanup.
        code = (
            "import os,time; p=os.fork(); "
            "os._exit(0) if p else None; os.setsid(); p=os.fork(); "
            "os._exit(0) if p else None; print('orphan='+str(os.getpid()),flush=True); time.sleep(120)"
        )
        self.cli("launch", ident, "--", sys.executable, "-c", code)
        self.wait(lambda: self.log_contains(ident, "orphan="))
        orphan = int((self.base(ident) / "app.log").read_text().strip().split("=")[1])
        identity = process(orphan)
        self.assertIsNotNone(identity)
        state = self.status(ident)
        self.assertTrue(signal_owned(state["supervisor"], signal.SIGTERM))
        self.wait(lambda: not self.status(ident)["supervisor_alive"])
        self.assertFalse(matches(identity))
        self.assertFalse(matches(state["xvfb"]))

    def test_stale_recovery_never_signals_reused_or_foreign_pid(self):
        ident = self.create()
        self.launch_fixture(ident)
        self.wait(lambda: len(read_state(self.base(ident))["owned"]) >= 2)
        state = self.status(ident)
        self.assertTrue(signal_owned(state["supervisor"], signal.SIGKILL))
        self.wait(lambda: not matches(state["supervisor"]))
        foreign = subprocess.Popen(["sleep", "120"])
        self.foreign.append(foreign)
        fake = process(foreign.pid)
        fake["start"] += 1
        disk = read_state(self.base(ident))
        disk["owned"].append(fake)
        atomic_json(self.base(ident) / "state.json", disk)
        self.assertEqual(self.status(ident)["phase"], "stale")
        self.cli("key", ident, "tap", "a", ok=False)
        result = self.cli("stop", ident)
        self.assertTrue(result["recovered"])
        self.assertIsNone(foreign.poll())
        self.assertFalse(matches(state["app"]))
        self.assertFalse(matches(state["xvfb"]))
        self.cli("stop", ident)

    def test_validation_snapshot_errors_and_purge(self):
        ident = self.create()
        wid = self.launch_fixture(ident)
        self.cli("purge", ident, ok=False)
        self.cli("status", "../moy", ok=False)
        self.cli("focus", ident, "--window", "0", ok=False)
        self.cli("key", ident, "hold", "a", "--seconds", "-1", ok=False)
        self.cli("key", ident, "hold", "a", ok=False)
        self.cli("key", ident, "tap", "--evil", ok=False)
        self.cli("mouse", ident, "move", "2", ok=False)
        self.cli("mouse", ident, "down", "99", ok=False)
        self.cli("mouse", ident, "move", "-1", "2", ok=False)
        bad = self.artifacts / f"{ident}-bad.png"
        self.cli(
            "screenshot",
            ident,
            "--window",
            "4294967295",
            "--output",
            str(bad),
            ok=False,
        )
        self.assertFalse(bad.exists())
        self.assertFalse(list(self.artifacts.glob(".isd-shot-*")))
        good = self.artifacts / f"{ident}-no-clobber.png"
        self.cli("screenshot", ident, "--window", wid, "--output", str(good))
        before = good.read_bytes()
        self.cli("screenshot", ident, "--screen", "--output", str(good), ok=False)
        self.assertEqual(before, good.read_bytes())
        self.cli("stop", ident)
        self.cli("purge", ident)
        self.ids.remove(ident)
        self.assertFalse(self.base(ident).exists())
        self.assertTrue(good.exists())

    def test_failed_startup_retains_evidence_and_has_no_fallback(self):
        fakebin = self.artifacts / "fakebin"
        fakebin.mkdir(exist_ok=True)
        # A dependency that exists but fails at runtime, after Xvfb starts.
        tool = fakebin / "glxinfo"
        tool.write_text("#!/bin/sh\necho 'simulated GL failure' >&2\nexit 1\n")
        tool.chmod(0o700)
        env = dict(os.environ, PATH=str(fakebin) + os.pathsep + os.environ["PATH"])
        before = set(self.root.iterdir()) if self.root.exists() else set()
        self.cli("create", "--size", "640x480", env=env, ok=False)
        created = set(self.root.iterdir()) - before
        self.assertEqual(len(created), 1)
        base = created.pop()
        self.ids.append(base.name)
        state = read_state(base)
        self.assertEqual(state["phase"], "stopped")
        self.assertFalse(matches(state["xvfb"]))
        self.assertIn("simulated GL failure", state["error"])
        self.assertTrue((base / "supervisor.log").exists())
        # Xvfb itself fails before publishing displayfd.
        (fakebin / "glxinfo").unlink()
        tool = fakebin / "Xvfb"
        tool.write_text("#!/bin/sh\necho 'simulated Xvfb failure' >&2\nexit 1\n")
        tool.chmod(0o700)
        before = set(self.root.iterdir())
        self.cli("create", env=env, ok=False)
        base = (set(self.root.iterdir()) - before).pop()
        self.ids.append(base.name)
        state = read_state(base)
        self.assertIsNone(state["display"])
        self.assertEqual(state["phase"], "stopped")
        self.assertIn("simulated Xvfb failure", (base / "xvfb.log").read_text())

    def test_create_signal_cleans_partial_startup(self):
        for name in ("Xvfb", "glxinfo"):
            with self.subTest(stage=name):
                fakebin = self.artifacts / f"slow-{name}"
                fakebin.mkdir()
                marker = fakebin / "entered"
                tool = fakebin / name
                tool.write_text(
                    f"#!{sys.executable}\nfrom pathlib import Path\n"
                    f"import time\nPath({str(marker)!r}).touch()\ntime.sleep(120)\n"
                )
                tool.chmod(0o700)
                env = dict(
                    os.environ, PATH=str(fakebin) + os.pathsep + os.environ["PATH"]
                )
                before = set(self.root.iterdir()) if self.root.exists() else set()
                with subprocess.Popen(
                    [
                        sys.executable,
                        str(REPO / "isd"),
                        "--root",
                        str(self.root),
                        "create",
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    env=env,
                ) as creator:
                    self.wait(marker.exists)
                    creator.terminate()
                    out, err = creator.communicate(timeout=20)
                    self.assertEqual(creator.returncode, 130, (out, err))
                base = (set(self.root.iterdir()) - before).pop()
                self.ids.append(base.name)
                state = read_state(base)
                self.assertEqual(state["phase"], "stopped")
                self.assertFalse(matches(state["supervisor"]))
                self.assertFalse(matches(state["xvfb"]))
                self.assertFalse(any(matches(item) for item in state["owned"]))

    def test_stop_escalates_for_term_ignoring_app(self):
        ident = self.create()
        code = (
            "import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); "
            "print('ready',flush=True); time.sleep(120)"
        )
        self.cli("launch", ident, "--", sys.executable, "-c", code)
        self.wait(lambda: self.log_contains(ident, "ready"))
        state = self.status(ident)
        self.cli("stop", ident)
        self.assertFalse(matches(state["app"]))
        self.assertEqual(self.status(ident)["app_exit"], -signal.SIGKILL)

    def test_incomplete_png_is_not_published(self):
        fakebin = self.artifacts / "bad-image"
        fakebin.mkdir()
        tool = fakebin / "import"
        tool.write_text(
            f"#!{sys.executable}\nimport sys\nfrom pathlib import Path\n"
            "Path(sys.argv[-1][4:]).write_bytes(b'\\x89PNG\\r\\n\\x1a\\n')\n"
        )
        tool.chmod(0o700)
        env = dict(os.environ, PATH=str(fakebin) + os.pathsep + os.environ["PATH"])
        ident = self.create(env=env)
        output = self.artifacts / "incomplete.png"
        self.cli("screenshot", ident, "--screen", "--output", str(output), ok=False)
        self.assertFalse(output.exists())
        self.assertFalse(list(self.artifacts.glob(".isd-shot-*")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
