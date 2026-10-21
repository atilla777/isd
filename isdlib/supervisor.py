"""One disposable supervisor per display; no system-wide daemon."""

import ctypes
import json
import os
from pathlib import Path
import select
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import time

from .core import (
    Error,
    PROTOCOL,
    atomic_json,
    clean_env,
    descendants,
    matches,
    process,
    receive,
    signal_owned,
    xenv,
)


class Supervisor:
    def __init__(self, base, width, height):
        self.base = base
        self.state = {
            "protocol": PROTOCOL,
            "id": base.name,
            "phase": "starting",
            "supervisor": process(os.getpid()),
            "xvfb": None,
            "app": None,
            "owned": [],
            "size": [width, height],
            "display": None,
            "app_exit": None,
            "error": None,
        }
        self.xvfb = self.app = None
        self.stopping = False
        self.saved_owned = []
        self.save()

    def save(self):
        atomic_json(self.base / "state.json", self.state)

    def environment(self, source=None):
        return xenv(
            os.environ if source is None else source, self.base, self.state["display"]
        )

    def tool(self, args, timeout=15, allowed=(0,), input_text=None):
        child = subprocess.Popen(
            args,
            env=self.environment(),
            stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        deadline = time.monotonic() + timeout
        try:
            while True:
                if self.stopping:
                    raise Error(f"{args[0]} interrupted by supervisor shutdown")
                if not matches(self.state["xvfb"]):
                    raise Error("Xvfb died during an operation")
                if time.monotonic() >= deadline:
                    raise Error(f"{args[0]} timed out")
                initial_input, input_text = input_text, None
                try:
                    stdout, stderr = child.communicate(input=initial_input, timeout=0.1)
                    break
                except subprocess.TimeoutExpired:
                    continue
            if child.returncode not in allowed:
                raise Error(
                    f"{args[0]} failed ({child.returncode}): {stderr.strip()[:2000]}"
                )
            return stdout
        finally:
            if child.poll() is None:
                child.terminate()
                try:
                    child.communicate(timeout=0.5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.communicate()
            if child.stdout:
                child.stdout.close()
            if child.stderr:
                child.stderr.close()
            if child.stdin:
                child.stdin.close()

    def start_xvfb(self):
        (self.base / "runtime").mkdir(mode=0o700)
        cookie = os.urandom(16)

        # Xserver reads cookies regardless of address/display fields. FamilyWild
        # entry is already populated when Xvfb starts, not an initially empty file.
        def field(value):
            return struct.pack("!H", len(value)) + value

        server_auth = self.base / "server.auth"
        server_auth.write_bytes(
            struct.pack("!H", 65535)
            + field(b"")
            + field(b"0")
            + field(b"MIT-MAGIC-COOKIE-1")
            + field(cookie)
        )
        rfd, wfd = os.pipe()
        try:
            with (self.base / "xvfb.log").open("ab", buffering=0) as log:
                self.xvfb = subprocess.Popen(
                    [
                        "Xvfb",
                        "-displayfd",
                        str(wfd),
                        "-screen",
                        "0",
                        f"{self.state['size'][0]}x{self.state['size'][1]}x24",
                        "-nolisten",
                        "tcp",
                        "-auth",
                        str(server_auth),
                        "-noreset",
                    ],
                    env=clean_env(os.environ),
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=log,
                    pass_fds=(wfd,),
                    start_new_session=True,
                )
            self.state["xvfb"] = process(self.xvfb.pid, include_zombie=True)
            self.save()
        except BaseException:
            os.close(rfd)
            raise
        finally:
            os.close(wfd)
        try:
            deadline = time.monotonic() + 15
            while not select.select([rfd], [], [], 0.1)[0]:
                if self.stopping:
                    raise Error("Xvfb startup interrupted")
                if time.monotonic() >= deadline:
                    raise Error("Xvfb readiness timeout")
            number = os.read(rfd, 64).decode().strip()
            if not number.isdecimal() or self.xvfb.poll() is not None:
                raise Error("Xvfb failed to allocate a display; see xvfb.log")
        finally:
            os.close(rfd)
        self.state["display"] = ":" + number
        # Client entry matches the actual allocated display; same preloaded cookie.
        auth = self.base / "Xauthority"
        auth.touch(mode=0o600)
        # Keep the cookie off argv (and therefore out of other users' ps output).
        self.tool(
            ["xauth", "-f", str(auth)],
            input_text=f"add {self.state['display']} . {cookie.hex()}\n",
        )
        gl = self.tool(["glxinfo", "-B"])
        (self.base / "glxinfo.log").write_text(gl)
        if (
            "OpenGL version string:" not in gl
            and "OpenGL core profile version string:" not in gl
        ):
            raise Error("GL probe did not report an OpenGL version; see glxinfo.log")
        self.state["phase"] = "ready"
        self.save()

    def refresh(self, persist=True):
        if self.app is not None:
            code = self.app.poll()
            if code is not None:
                self.state["app_exit"] = code
        # Peek before reaping: a primary process can exit between poll() and
        # waitpid(). Never steal its exit status from Popen.
        if self.xvfb is not None:
            self.xvfb.poll()
        while True:
            try:
                child = os.waitid(os.P_ALL, 0, os.WEXITED | os.WNOHANG | os.WNOWAIT)
                if child is None:
                    break
                if self.app is not None and child.si_pid == self.app.pid:
                    self.state["app_exit"] = self.app.poll()
                elif self.xvfb is not None and child.si_pid == self.xvfb.pid:
                    self.xvfb.poll()
                else:
                    os.waitpid(child.si_pid, os.WNOHANG)
            except ChildProcessError:
                break
        owned = descendants(os.getpid())
        if owned != self.saved_owned:
            self.state["owned"] = owned
            self.saved_owned = owned
            if persist:
                self.save()
        if (
            self.xvfb is not None
            and self.xvfb.returncode is not None
            and not self.stopping
        ):
            self.state["error"] = "Xvfb exited; application is being stopped"
            self.stopping = True

    def status(self):
        return dict(
            self.state,
            supervisor_alive=True,
            xvfb_alive=matches(self.state["xvfb"]),
            app_alive=matches(self.state["app"]),
            artifacts=str(self.base),
        )

    def ensure_display(self):
        if self.stopping or not matches(self.state["xvfb"]):
            raise Error("isolated Xvfb is unavailable")

    def launch(self, req):
        self.ensure_display()
        if self.app is not None:
            raise Error(
                "this environment has already launched an application; create a new one"
            )
        argv, env = req["argv"], req["env"]
        if not argv or not all(isinstance(x, str) and "\0" not in x for x in argv):
            raise Error("expected a nonempty argv list")
        if not isinstance(env, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in env.items()
        ):
            raise Error("invalid environment")
        with (self.base / "app.log").open("ab", buffering=0) as log:
            self.app = subprocess.Popen(
                argv,
                cwd=req["cwd"],
                env=self.environment(env),
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                start_new_session=True,
            )
        self.state["app"] = process(self.app.pid, include_zombie=True)
        self.state["phase"] = "launched"
        self.save()
        self.refresh()
        return {
            "app": self.state["app"],
            "app_exit": self.state["app_exit"],
            "log": str(self.base / "app.log"),
            "ready": False,
        }

    @staticmethod
    def window(value):
        try:
            number = int(str(value), 0) if str(value).startswith("0x") else int(value)
            if not 0 < number <= 0xFFFFFFFF:
                raise ValueError
            return str(number)
        except (ValueError, TypeError) as exc:
            raise Error("invalid X11 window ID") from exc

    def windows(self):
        # xdotool search returns 1 when no window matches.
        result = self.tool(
            ["xdotool", "search", "--onlyvisible", "--name", "."],
            timeout=10,
            allowed=(0, 1),
        )
        windows = []
        for value in result.splitlines():
            wid = self.window(value)
            windows.append(
                {
                    "id": int(wid),
                    "name": self.tool(["xdotool", "getwindowname", wid]).rstrip("\n"),
                }
            )
        return {"windows": windows}

    def screenshot(self, req):
        from PIL import Image

        target = "root" if req["screen"] else self.window(req["window"])
        output = Path(req["output"])
        if not output.is_absolute() or not output.parent.is_dir():
            raise Error("output must be an absolute path in an existing directory")
        if output.exists() or output.is_symlink():
            raise Error("output already exists; choose a new filename")
        fd, temporary = tempfile.mkstemp(
            prefix=".isd-shot-", suffix=".png", dir=output.parent
        )
        os.close(fd)
        try:
            self.tool(
                [
                    "import",
                    "-display",
                    self.state["display"],
                    "-window",
                    target,
                    "png:" + temporary,
                ]
            )
            with Image.open(temporary) as image:
                image.verify()
            with Image.open(temporary) as image:
                image.load()
                size = list(image.size)
                if image.format != "PNG" or min(size) <= 0:
                    raise Error("invalid PNG screenshot")
            # Atomic no-clobber publication on the same filesystem.
            os.link(temporary, output)
        finally:
            os.unlink(temporary)
        return {"output": str(output), "size": size, "coordinates": "X11 pixels"}

    def input(self, req):
        kind, action = req["kind"], req["action"]
        if kind == "focus":
            self.tool(["xdotool", "windowfocus", self.window(req["window"])])
        elif kind == "mouse" and action == "move":
            args = ["xdotool", "mousemove"]
            if req.get("window"):
                args += ["--window", self.window(req["window"])]
            for coord in (req["x"], req["y"]):
                if not isinstance(coord, int) or not 0 <= coord <= 65535:
                    raise Error("coordinates must be integers in 0..65535")
                args.append(str(coord))
            self.tool(args)
        else:
            value = str(req["value"])
            if kind == "key":
                if (
                    not value
                    or len(value) > 100
                    or not all(c.isalnum() or c in "_+" for c in value)
                ):
                    raise Error(
                        "invalid key/chord (use X11 keysyms, e.g. Left or ctrl+a)"
                    )
                down, up, tap = "keydown", "keyup", "key"
            elif kind == "mouse":
                if value not in {str(n) for n in range(1, 33)}:
                    raise Error("mouse button must be in 1..32")
                down, up, tap = "mousedown", "mouseup", "click"
            else:
                raise Error("invalid input kind")
            if action == "hold":
                seconds = float(req["seconds"])
                if not 0 <= seconds <= 60:
                    raise Error("hold duration must be in 0..60 seconds")
                self.tool(["xdotool", down, value])
                try:
                    deadline = time.monotonic() + seconds
                    while time.monotonic() < deadline and not self.stopping:
                        if not matches(self.state["xvfb"]):
                            raise Error("Xvfb died during hold")
                        time.sleep(min(0.05, max(0, deadline - time.monotonic())))
                finally:
                    if matches(self.state["xvfb"]):
                        self.tool(["xdotool", up, value])
            elif action in ("down", "up", "tap", "click"):
                self.tool(
                    [
                        "xdotool",
                        {"down": down, "up": up, "tap": tap, "click": tap}[action],
                        value,
                    ]
                )
            else:
                raise Error("invalid input action")
        return {"delivered": True, "application_action_verified": False}

    def dispatch(self, req):
        if req.get("protocol") != PROTOCOL or req.get("id") != self.base.name:
            raise Error("protocol/environment mismatch")
        op = req["op"]
        if op == "status":
            return self.status()
        if op == "stop":
            self.stopping = True
            return {"stopping": True}
        self.ensure_display()
        if op == "launch":
            return self.launch(req)
        if op == "windows":
            return self.windows()
        if op == "screenshot":
            return self.screenshot(req)
        if op == "input":
            return self.input(req)
        raise Error("unknown operation")

    def cleanup(self):
        self.stopping = True
        self.state["phase"] = "stopping"
        try:
            self.save()
        except OSError as exc:
            # A full/unwritable artifact filesystem must not prevent signals.
            print(f"Cannot persist stopping state: {exc}", file=sys.stderr)
        # Descendants remain descendants even after setsid/double-fork, thanks to
        # PR_SET_CHILD_SUBREAPER. No process-group-wide signals or PID guessing.
        for sig, duration in ((signal.SIGTERM, 3), (signal.SIGKILL, 2)):
            deadline = time.monotonic() + duration
            while time.monotonic() < deadline:
                owned = descendants(os.getpid())
                if not owned:
                    break
                # Stop app/descendants before Xvfb where possible.
                owned.sort(
                    key=lambda item: (
                        item["pid"] == (self.state["xvfb"] or {}).get("pid")
                    )
                )
                for identity in owned:
                    signal_owned(identity, sig)
                time.sleep(0.05)
                self.refresh(persist=False)
        self.refresh(persist=False)
        self.state["phase"] = (
            "stopped" if not self.state["owned"] else "cleanup_incomplete"
        )
        try:
            self.save()
        finally:
            (self.base / "control.sock").unlink(missing_ok=True)


def serve(base, width, height):
    os.umask(0o077)
    # Fail before spawning anything if orphan adoption is not supported.
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
        raise OSError(ctypes.get_errno(), "cannot enable subreaper")
    supervisor = Supervisor(base, width, height)

    def stop(_sig, _frame):
        supervisor.stopping = True

    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, stop)
    try:
        supervisor.start_xvfb()
        if supervisor.stopping:
            return
        with socket.socket(socket.AF_UNIX) as server:
            server.bind(str(base / "control.sock"))
            server.listen(8)
            server.settimeout(0.1)
            while not supervisor.stopping:
                supervisor.refresh()
                if supervisor.stopping:
                    break
                try:
                    conn, _ = server.accept()
                except TimeoutError:
                    continue
                with conn:
                    conn.settimeout(3)
                    try:
                        _, uid, _ = struct.unpack(
                            "3i",
                            conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12),
                        )
                        if uid != os.getuid():
                            raise Error("peer UID mismatch")
                        result = supervisor.dispatch(receive(conn))
                        response = dict(
                            result, ok=True, protocol=PROTOCOL, id=base.name
                        )
                    except Exception as exc:
                        response = {
                            "ok": False,
                            "protocol": PROTOCOL,
                            "id": base.name,
                            "error": str(exc),
                        }
                    try:
                        conn.sendall(json.dumps(response).encode() + b"\n")
                    except (BrokenPipeError, ConnectionResetError):
                        pass
    except Exception as exc:
        supervisor.state["error"] = str(exc)
        raise
    finally:
        supervisor.cleanup()
