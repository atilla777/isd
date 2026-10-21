import json
import contextlib
import io
import os
from pathlib import Path
import signal
import socket
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from isdlib.core import (
    BOOT,
    Error,
    atomic_json,
    clean_env,
    directory,
    matches,
    private_dir,
    process,
    read_state,
    receive,
    signal_owned,
    xenv,
)


class CoreTests(unittest.TestCase):
    def test_cleanup_signals_even_if_state_cannot_be_written(self):
        from isdlib.supervisor import Supervisor

        with tempfile.TemporaryDirectory(
            dir="/tmp/opencode", prefix="isd-unit-"
        ) as tmp:
            base = Path(tmp) / ("a" * 32)
            base.mkdir(mode=0o700)
            supervisor = Supervisor(base, 640, 480)
            with subprocess.Popen(["sleep", "30"]) as child:
                identity = process(child.pid)
                try:
                    with (
                        patch.object(
                            supervisor, "save", side_effect=OSError("disk full")
                        ),
                        patch.object(supervisor, "refresh"),
                        patch(
                            "isdlib.supervisor.descendants",
                            side_effect=lambda _: (
                                [identity] if matches(identity) else []
                            ),
                        ),
                        contextlib.redirect_stderr(io.StringIO()),
                    ):
                        with self.assertRaises(OSError):
                            supervisor.cleanup()
                    self.assertFalse(matches(identity))
                    child.wait(timeout=5)
                finally:
                    if child.poll() is None:
                        child.terminate()

    def test_process_identity_and_pid_reuse_guard(self):
        with subprocess.Popen(["sleep", "30"]) as child:
            identity = process(child.pid)
            try:
                self.assertTrue(matches(identity))
                fake = dict(identity, start=identity["start"] + 1)
                self.assertFalse(matches(fake))
                self.assertFalse(signal_owned(fake, signal.SIGTERM))
                self.assertIsNone(child.poll())
                self.assertFalse(
                    signal_owned(dict(identity, boot="another-boot"), signal.SIGTERM)
                )
                self.assertTrue(signal_owned(identity, signal.SIGTERM))
                child.wait(timeout=5)
                self.assertFalse(matches(identity))
            finally:
                if child.poll() is None:
                    child.terminate()

    def test_private_paths_and_ids(self):
        with tempfile.TemporaryDirectory(
            dir="/tmp/opencode", prefix="isd-unit-"
        ) as tmp:
            base = Path(tmp)
            self.assertEqual(private_dir(base), base)
            unsafe = base / "unsafe"
            unsafe.mkdir(mode=0o755)
            with self.assertRaises(Error):
                private_dir(unsafe)
            link = base / "link"
            link.symlink_to(base, target_is_directory=True)
            with self.assertRaises(Error):
                private_dir(link)
            for ident in ("../moy", ".", "A" * 32, "0" * 31):
                with self.assertRaises(Error):
                    directory(base, ident)

    def test_state_atomic_and_bound_to_directory(self):
        with tempfile.TemporaryDirectory(
            dir="/tmp/opencode", prefix="isd-unit-"
        ) as tmp:
            base = Path(tmp) / ("a" * 32)
            base.mkdir(mode=0o700)
            atomic_json(base / "state.json", {"protocol": 1, "id": base.name})
            self.assertEqual(read_state(base)["id"], base.name)
            self.assertEqual((base / "state.json").stat().st_mode & 0o777, 0o600)
            atomic_json(base / "state.json", {"protocol": 1, "id": "b" * 32})
            with self.assertRaises(Error):
                read_state(base)

    def test_environment_cannot_reconnect_to_desktop(self):
        original = {
            "DISPLAY": ":99",
            "WAYLAND_DISPLAY": "wayland-1",
            "WAYLAND_SOCKET": "5",
            "XAUTHORITY": "/real/auth",
            "DBUS_SESSION_BUS_ADDRESS": "real-bus",
            "XDG_RUNTIME_DIR": "/run/user/1000",
            "KEEP": "yes",
        }
        self.assertEqual(clean_env(original), {"KEEP": "yes"})
        env = xenv(original, Path("/private"), ":77")
        self.assertEqual(env["DISPLAY"], ":77")
        self.assertEqual(env["XAUTHORITY"], "/private/Xauthority")
        self.assertNotIn("WAYLAND_SOCKET", env)
        self.assertEqual(original["DISPLAY"], ":99")

    def test_protocol_receive(self):
        left, right = socket.socketpair()
        with left, right:
            left.sendall(
                json.dumps({"id": "test", "text": "line\nline"}).encode() + b"\n"
            )
            self.assertEqual(receive(right)["text"], "line\nline")


if __name__ == "__main__":
    unittest.main()
