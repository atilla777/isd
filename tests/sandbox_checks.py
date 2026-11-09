"""Explicit isolated regression runner; never run its checks in host namespaces."""

import os
from pathlib import Path
import select
import signal
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

_PRIVATE_X11 = None


def require_private_x11():
    if _PRIVATE_X11 is None:
        raise RuntimeError(
            "Refusing X11 regression without verified private namespaces"
        )
    net, mnt, tmp_identity = _PRIVATE_X11
    stat = Path("/tmp").stat()
    if (
        os.readlink("/proc/self/ns/net") != net
        or os.readlink("/proc/self/ns/mnt") != mnt
        or (stat.st_dev, stat.st_ino) != tmp_identity
    ):
        raise RuntimeError("X11 regression isolation changed")


def main():
    global _PRIVATE_X11
    outer, parent_net, parent_mnt = sys.argv[1:]
    for kind, parent in [("net", parent_net), ("mnt", parent_mnt)]:
        if os.readlink(f"/proc/self/ns/{kind}") == parent:
            raise RuntimeError("Refusing X11 regression outside private namespaces")
    subprocess.run(["mount", "--make-rprivate", "/"], check=True)
    private_tmp = Path(outer) / "tmp"
    private_tmp.mkdir(mode=0o1777)
    original = private_tmp.stat()
    subprocess.run(["mount", "--bind", str(private_tmp), "/tmp"], check=True)
    stat = Path("/tmp").stat()
    if (stat.st_dev, stat.st_ino) != (original.st_dev, original.st_ino):
        raise RuntimeError("Private /tmp bind mount verification failed")
    Path("/tmp/opencode").mkdir(mode=0o700)
    Path("/tmp/.X11-unix").mkdir(mode=0o1777)
    _PRIVATE_X11 = (
        os.readlink("/proc/self/ns/net"),
        os.readlink("/proc/self/ns/mnt"),
        (stat.st_dev, stat.st_ino),
    )
    require_private_x11()
    print(f"VERIFIED PRIVATE X11: {_PRIVATE_X11}", flush=True)
    os.umask(0o077)
    # No desktop/session endpoints are passed to the checks or their helpers.
    from isdlib.core import clean_env

    clean = clean_env(os.environ)
    os.environ.clear()
    os.environ.update(clean)
    suite = unittest.TestSuite()
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(SandboxChecks))
    for module in ("tests.test_display", "tests.test_live"):
        suite.addTests(unittest.defaultTestLoader.loadTestsFromName(module))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())


class SandboxChecks(unittest.TestCase):
    def test_reproduction_and_protection(self):
        require_private_x11()
        from isdlib.core import matches, process, signal_owned, xenv
        from isdlib.supervisor import Supervisor
        from tests.test_live import LiveTests

        artifacts = Path(tempfile.mkdtemp(dir="/tmp/opencode", prefix="foreign-"))
        print(f"FOREIGN SERVER EVIDENCE: {artifacts}", flush=True)

        def stop(child):
            if child.poll() is None:
                self.assertTrue(signal_owned(process(child.pid), signal.SIGTERM))
            child.wait(timeout=10)

        def spawn(argv, base):
            require_private_x11()
            rfd, wfd = os.pipe()
            try:
                with (base / "xvfb.log").open("ab") as log:
                    child = subprocess.Popen(
                        [
                            "Xvfb",
                            *argv,
                            "-displayfd",
                            str(wfd),
                            "-screen",
                            "0",
                            "640x480x24",
                            "-nolisten",
                            "tcp",
                            "-auth",
                            str(base / "server.auth"),
                            "-noreset",
                        ],
                        env=xenv({}, base, ":0"),
                        stdin=subprocess.DEVNULL,
                        stdout=log,
                        stderr=log,
                        pass_fds=(wfd,),
                    )
            finally:
                os.close(wfd)
            try:
                response = b""
                deadline = time.monotonic() + 15
                while b"\n" not in response:
                    self.assertLess(
                        time.monotonic(), deadline, "Xvfb readiness timeout"
                    )
                    if not select.select([rfd], [], [], 0.1)[0]:
                        continue
                    chunk = os.read(rfd, 64)
                    self.assertTrue(chunk, "Xvfb closed readiness pipe")
                    response += chunk
                    self.assertLessEqual(len(response), 64)
                number = response.decode().strip()
                self.assertTrue(number.isdecimal())
                return child, number
            except BaseException:
                stop(child)
                raise
            finally:
                os.close(rfd)

        def authority(name, number):
            base = artifacts / name
            base.mkdir(mode=0o700)
            (base / "runtime").mkdir(mode=0o700)
            cookie = os.urandom(16)
            data = struct.pack("!H", 65535)
            for field in (b"", str(number).encode(), b"MIT-MAGIC-COOKIE-1", cookie):
                data += struct.pack("!H", len(field)) + field
            (base / "server.auth").write_bytes(data)
            subprocess.run(
                ["xauth", "-f", str(base / "Xauthority")],
                input=f"add :{number} . {cookie.hex()}\n",
                text=True,
                env=xenv({}, base, f":{number}"),
                check=True,
                capture_output=True,
            )
            return base

        def probe(base, number):
            subprocess.run(
                ["xdotool", "getdisplaygeometry"],
                env=xenv({}, base, f":{number}"),
                check=True,
                capture_output=True,
                timeout=5,
            )

        # Exact hazardous topology: a live pathname-only foreign server, a
        # live compositor-style lock PID, and no abstract listener at :0.
        base = authority("foreign-original", 0)
        foreign, _ = spawn([":0", "-nolisten", "local"], base)
        Path("/tmp/.X0-lock").write_text(f"{os.getpid():10d}\n")
        try:
            probe(base, 0)
            inode = Path("/tmp/.X11-unix/X0").stat().st_ino
            oldbase = authority("old-allocator", 0)
            old, number = spawn([], oldbase)
            try:
                self.assertEqual(number, "0")
                self.assertNotEqual(Path("/tmp/.X11-unix/X0").stat().st_ino, inode)
            finally:
                stop(old)
            self.assertFalse(Path("/tmp/.X11-unix/X0").exists())
            self.assertIsNone(foreign.poll())
            print(
                "REPRODUCED: old -displayfd replaced live X0 and unlinked it on stop",
                flush=True,
            )
        finally:
            stop(foreign)

        base = authority("foreign-protected", 1)
        foreign, _ = spawn([":1", "-nolisten", "local"], base)
        lock = Path("/tmp/.X1-lock")
        lock.write_text(f"{os.getpid():10d}\n")
        endpoint = Path("/tmp/.X11-unix/X1")
        identity = process(foreign.pid)

        def file_identity(path):
            stat = path.stat()
            return (
                stat.st_dev,
                stat.st_ino,
                stat.st_mode,
                stat.st_uid,
                stat.st_gid,
                stat.st_mtime_ns,
                stat.st_ctime_ns,
            )

        before = (file_identity(endpoint), file_identity(lock), lock.read_bytes())

        def intact():
            self.assertTrue(matches(identity))
            self.assertEqual(file_identity(endpoint), before[0])
            self.assertEqual(file_identity(lock), before[1])
            self.assertEqual(lock.read_bytes(), before[2])
            probe(base, 1)

        try:
            # Force a filesystem endpoint appearing AFTER candidate selection:
            # protection must already be active when Xvfb is exec'd.
            raced = artifacts / ("a" * 32)
            raced.mkdir(mode=0o700)
            supervisor = Supervisor(raced, 640, 480)
            try:
                with patch(
                    "isdlib.supervisor.display_candidates", return_value=iter([1])
                ):
                    with self.assertRaisesRegex(Exception, "endpoint appeared"):
                        supervisor.start_xvfb()
                self.assertEqual(file_identity(endpoint), before[0])
                self.assertEqual(lock.read_bytes(), before[2])
            finally:
                if supervisor.xvfb is not None:
                    stop(supervisor.xvfb)
            intact()

            # Force an abstract bind collision as well; the loser must retry,
            # not unlink the winner or leave its failed attempt running.
            raced2 = artifacts / ("b" * 32)
            raced2.mkdir(mode=0o700)
            second = Supervisor(raced2, 640, 480)
            import socket

            with socket.socket(socket.AF_UNIX) as blocker:
                blocker.bind("\0/tmp/.X11-unix/X2")
                try:
                    with patch(
                        "isdlib.supervisor.display_candidates",
                        return_value=iter([2, 3]),
                    ):
                        second.start_xvfb()
                    self.assertEqual(second.state["display"], ":3")
                    self.assertFalse(Path("/tmp/.X11-unix/X2").exists())
                    self.assertFalse(Path("/tmp/.X11-unix/X3").exists())
                    intact()
                finally:
                    if second.xvfb is not None:
                        stop(second.xvfb)

            # Exercise the public CLI while a genuinely foreign X11 server
            # Atomic arbitration and bounded exhaustion from upstream d67f4e5.
            peers = []
            for letter in ("c", "d"):
                directory = artifacts / (letter * 32)
                directory.mkdir(mode=0o700)
                peers.append(Supervisor(directory, 640, 480))
            try:
                with patch(
                    "isdlib.supervisor.display_candidates",
                    side_effect=lambda: iter([4, 5]),
                ):
                    with ThreadPoolExecutor(max_workers=2) as pool:
                        list(pool.map(lambda peer: peer.start_xvfb(), peers))
                self.assertEqual(
                    {peer.state["display"] for peer in peers}, {":4", ":5"}
                )
                intact()
            finally:
                for peer in peers:
                    if peer.xvfb is not None:
                        stop(peer.xvfb)
            intact()

            from contextlib import ExitStack

            failed_dir = artifacts / ("e" * 32)
            failed_dir.mkdir(mode=0o700)
            failed = Supervisor(failed_dir, 640, 480)
            attempts = []
            original_start = failed.start_xvfb_candidate

            def attempt(number, auth):
                result = original_start(number, auth)
                attempts.append(failed.state["xvfb"])
                self.assertIsNotNone(failed.xvfb.poll())
                return result

            with ExitStack() as stack:
                blockers = []
                for number in range(10, 26):
                    blocker = stack.enter_context(socket.socket(socket.AF_UNIX))
                    blocker.bind(f"\0/tmp/.X11-unix/X{number}")
                    blockers.append(blocker)
                with (
                    patch(
                        "isdlib.supervisor.display_candidates",
                        return_value=iter(range(10, 26)),
                    ),
                    patch.object(failed, "start_xvfb_candidate", side_effect=attempt),
                ):
                    with self.assertRaisesRegex(Exception, "failed to allocate"):
                        failed.start_xvfb()
                self.assertEqual(len(attempts), 16)
                self.assertFalse(any(matches(item) for item in attempts))
                for number, blocker in zip(range(10, 26), blockers):
                    self.assertEqual(
                        blocker.getsockname(), f"\0/tmp/.X11-unix/X{number}".encode()
                    )
                    self.assertFalse(Path(f"/tmp/.X11-unix/X{number}").exists())
                    self.assertFalse(Path(f"/tmp/.X{number}-lock").exists())
                intact()

            # Exercise the public CLI while a genuinely foreign X11 server
            # remains connectable, including concurrent stop and independent use.
            client = LiveTests()
            client.root = artifacts / "state"
            client.ids = []
            try:
                for cycle in range(3):
                    with ThreadPoolExecutor(max_workers=3) as pool:
                        ids = list(pool.map(lambda _: client.create(), range(3)))
                    self.assertEqual(len({client.status(i)["display"] for i in ids}), 3)
                    intact()
                    for ident in ids:
                        client.tool(ident, "xdotool", "getdisplaygeometry")
                        state = client.status(ident)
                        self.assertFalse(
                            Path("/tmp/.X11-unix/X" + state["display"][1:]).exists()
                        )
                    with ThreadPoolExecutor(max_workers=3) as pool:
                        list(pool.map(lambda i: client.cli("stop", i), ids))
                    intact()
                    for ident in ids:
                        state = client.status(ident)
                        self.assertFalse(state["supervisor_alive"])
                        self.assertFalse(state["xvfb_alive"])
            finally:
                for ident in client.ids:
                    client.cli("stop", ident)
            intact()
        finally:
            stop(foreign)


if __name__ == "__main__":
    main()
