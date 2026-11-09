import tempfile
import subprocess
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

from isdlib.display import display_candidates, filesystem_occupied, xvfb_command
from isdlib.core import Error
from isdlib.supervisor import Supervisor


class DisplayTests(unittest.TestCase):
    def test_unverified_destructive_runner_refuses_before_spawning(self):
        from tests import sandbox_checks

        with (
            patch.object(sandbox_checks, "_PRIVATE_X11", None),
            patch.object(
                sandbox_checks.subprocess, "Popen", side_effect=AssertionError("spawn")
            ) as spawn,
        ):
            with self.assertRaisesRegex(RuntimeError, "verified private namespaces"):
                sandbox_checks.SandboxChecks().test_reproduction_and_protection()
            spawn.assert_not_called()

    def test_running_supervisor_reports_implemented_allocation_policy(self):
        from isdlib.supervisor import Supervisor

        with tempfile.TemporaryDirectory(dir="/tmp/opencode") as tmp:
            supervisor = Supervisor(Path(tmp), 640, 480)
            self.assertEqual(
                supervisor.status().get("display_allocation"), "linux-abstract-v1"
            )
            argv = xvfb_command(123, 7, [640, 480], Path(tmp) / "server.auth")
            self.assertEqual(argv[9:13], ["-nolisten", "unix", "-listen", "local"])

    def test_transport_order_disables_filesystem_not_abstract(self):
        argv = xvfb_command(123, 7, [640, 480], Path("/private/server.auth"))
        self.assertEqual(argv[:4], ["Xvfb", ":123", "-displayfd", "7"])
        self.assertEqual(
            argv[7:16],
            [
                "-nolisten",
                "tcp",
                "-nolisten",
                "unix",
                "-listen",
                "local",
                "-nopn",
                "-auth",
                "/private/server.auth",
            ],
        )
        for forbidden in ("-ac", "-nolock", "-pn"):
            self.assertNotIn(forbidden, argv)

    def test_all_filesystem_markers_block_even_stale_or_symlink(self):
        with tempfile.TemporaryDirectory(dir="/tmp/opencode") as tmp:
            marker = Path(tmp) / "marker"
            with patch("isdlib.display.Path", return_value=marker):
                self.assertFalse(filesystem_occupied(0))
                marker.symlink_to(Path(tmp) / "missing")
                self.assertTrue(filesystem_occupied(0))
        for occupied in range(4):
            calls = 0

            def stat(_):
                nonlocal calls
                calls += 1
                if calls != occupied + 1:
                    raise FileNotFoundError

            with patch.object(Path, "lstat", stat):
                self.assertTrue(filesystem_occupied(0))
        with patch.object(Path, "lstat", side_effect=PermissionError):
            with self.assertRaises(PermissionError):
                filesystem_occupied(0)

    def test_dynamic_candidates_skip_occupied_and_wrap(self):
        with (
            patch("isdlib.display.secrets.randbelow", return_value=59535),
            patch("isdlib.display.filesystem_occupied", side_effect=lambda n: n == 0),
        ):
            candidates = display_candidates()
            self.assertEqual([next(candidates) for _ in range(3)], [59535, 1, 2])

    def test_failed_candidates_are_bounded_and_never_publish_ready(self):
        with tempfile.TemporaryDirectory(dir="/tmp/opencode") as tmp:
            supervisor = Supervisor(Path(tmp), 640, 480)
            with (
                patch(
                    "isdlib.supervisor.display_candidates",
                    return_value=iter(range(100)),
                ),
                patch.object(
                    supervisor, "start_xvfb_candidate", return_value=False
                ) as start,
            ):
                with self.assertRaisesRegex(Error, "failed to allocate"):
                    supervisor.start_xvfb()
            self.assertEqual(start.call_count, 16)
            self.assertEqual(supervisor.state["phase"], "starting")
            self.assertIsNone(supervisor.state["display"])

    def test_filesystem_check_error_fails_closed_before_spawn(self):
        with tempfile.TemporaryDirectory(dir="/tmp/opencode") as tmp:
            supervisor = Supervisor(Path(tmp), 640, 480)
            with (
                patch(
                    "isdlib.display.filesystem_occupied", side_effect=PermissionError
                ),
                patch.object(supervisor, "start_xvfb_candidate") as start,
            ):
                with self.assertRaises(PermissionError):
                    supervisor.start_xvfb()
                start.assert_not_called()

    def test_readiness_is_framed_and_must_match_explicit_candidate(self):
        for chunks, expected in (
            ([b"12", b"3", b"\n"], True),
            ([b"124\n"], Error),
            ([b"123\nextra"], Error),
            ([b"x" * 65], Error),
            ([b""], False),
            ([b""], subprocess.TimeoutExpired),
        ):
            with self.subTest(chunks=chunks, expected=expected):
                with tempfile.TemporaryDirectory(dir="/tmp/opencode") as tmp:
                    supervisor = Supervisor(Path(tmp), 640, 480)
                    child = MagicMock(pid=123)
                    child.poll.return_value = None
                    if expected is subprocess.TimeoutExpired:
                        child.wait.side_effect = subprocess.TimeoutExpired("Xvfb", 1)
                    with (
                        patch("isdlib.supervisor.subprocess.Popen", return_value=child),
                        patch("isdlib.supervisor.process", return_value=None),
                        patch(
                            "isdlib.supervisor.select.select",
                            return_value=([1], [], []),
                        ),
                        patch("isdlib.supervisor.os.read", side_effect=chunks),
                    ):
                        if expected in (Error, subprocess.TimeoutExpired):
                            with self.assertRaises(Error):
                                supervisor.start_xvfb_candidate(123, Path(tmp) / "auth")
                        else:
                            self.assertIs(
                                supervisor.start_xvfb_candidate(
                                    123, Path(tmp) / "auth"
                                ),
                                expected,
                            )
