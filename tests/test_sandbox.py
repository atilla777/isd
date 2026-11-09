"""Run hazardous reproduction only behind BOTH mount and network isolation."""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


class SandboxTests(unittest.TestCase):
    def test_private_x11_regression(self):
        if not shutil.which("unshare") or not shutil.which("mount"):
            self.skipTest("isolated regression requires existing unshare/mount tools")
        probe = subprocess.run(
            ["unshare", "--user", "--map-root-user", "--mount", "--net", "true"],
            capture_output=True,
            text=True,
        )
        if probe.returncode:
            self.skipTest("user/mount/network namespaces unavailable: " + probe.stderr)
        root = Path(tempfile.mkdtemp(dir="/tmp/opencode", prefix="isd-sandbox-"))
        print(f"ISOLATED REGRESSION ARTIFACTS: {root}", flush=True)
        result = subprocess.run(
            [
                "unshare",
                "--user",
                "--map-root-user",
                "--mount",
                "--net",
                sys.executable,
                str(Path(__file__).with_name("sandbox_checks.py")),
                str(root),
                os.readlink("/proc/self/ns/net"),
                os.readlink("/proc/self/ns/mnt"),
            ],
            capture_output=True,
            text=True,
            timeout=240,
        )
        (root / "checks.log").write_text(result.stdout + result.stderr)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
