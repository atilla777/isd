"""JSON CLI. Application argv is separated by -- and never evaluated."""

import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
import uuid

from . import VERSION
from .core import (
    BLOCKED,
    Error,
    PROTOCOL,
    atomic_json,
    clean_env,
    directory,
    lock,
    matches,
    read_state,
    request,
    root_path,
    signal_owned,
)

ENTRY = Path(__file__).resolve().parent.parent / "isd"


class JSONParser(argparse.ArgumentParser):
    def error(self, message):
        raise Error(message)


def parser():
    result = JSONParser(description="ISD — isolated Xvfb displays (JSON protocol v1)")
    result.add_argument("--version", action="version", version=VERSION)
    result.add_argument(
        "--root", help="private state directory (default ~/.local/state/isd)"
    )
    commands = result.add_subparsers(dest="op", required=True)
    create = commands.add_parser("create", help="start a display and its supervisor")
    create.add_argument("--size", default="1280x720")
    launch = commands.add_parser(
        "launch", help="launch once: launch ID [options] -- COMMAND ARGS"
    )
    launch.add_argument("id")
    launch.add_argument("--cwd", default=os.getcwd())
    launch.add_argument("--env", action="append", default=[], metavar="NAME=VALUE")
    for name in ("status", "windows", "stop", "purge"):
        cmd = commands.add_parser(name)
        cmd.add_argument("id")
    shot = commands.add_parser("screenshot")
    shot.add_argument("id")
    target = shot.add_mutually_exclusive_group(required=True)
    target.add_argument("--screen", action="store_true")
    target.add_argument("--window")
    shot.add_argument("--output", required=True)
    focus = commands.add_parser("focus")
    focus.add_argument("id")
    focus.add_argument("--window", required=True)
    key = commands.add_parser("key")
    key.add_argument("id")
    key.add_argument("action", choices=("down", "up", "tap", "hold"))
    key.add_argument("value")
    key.add_argument("--seconds", type=float)
    mouse = commands.add_parser("mouse")
    mouse.add_argument("id")
    mouse.add_argument("action", choices=("move", "down", "up", "click", "hold"))
    mouse.add_argument("values", nargs="+", type=int)
    mouse.add_argument("--seconds", type=float)
    mouse.add_argument(
        "--window", help="origin for explicit move; otherwise screen origin"
    )
    return result


def stale_status(base, state):
    return dict(
        state,
        ok=True,
        protocol=PROTOCOL,
        id=base.name,
        phase="unreachable"
        if matches(state.get("supervisor"))
        else "stale"
        if state["phase"] != "stopped"
        else "stopped",
        supervisor_alive=matches(state.get("supervisor")),
        xvfb_alive=matches(state.get("xvfb")),
        app_alive=matches(state.get("app")),
        artifacts=str(base),
        warning=(
            "No live supervisor response; use stop for recovery. "
            "After supervisor SIGKILL, unrecorded descendants may remain."
        )
        if state["phase"] != "stopped" or state.get("recovered")
        else None,
    )


def wait_stopped(base, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = read_state(base)
        if not matches(state.get("supervisor")):
            return state
        time.sleep(0.05)
    raise Error("supervisor has not stopped; artifacts retained; retry status/stop")


def stop(base):
    state = read_state(base)
    if matches(state.get("supervisor")):
        try:
            request(base, {"op": "stop"}, timeout=3)
        except (Error, OSError, ValueError):
            signal_owned(state["supervisor"], signal.SIGTERM)
        state = wait_stopped(base)
    # Recovery addresses only recorded identities, never a process group or
    # inferred numeric PID. A dead supervisor cannot discover escaped orphans.
    identities = state.get("owned", []) + [state.get("app"), state.get("xvfb")]
    for sig, timeout in ((signal.SIGTERM, 3), (signal.SIGKILL, 2)):
        deadline = time.monotonic() + timeout
        while any(matches(item) for item in identities) and time.monotonic() < deadline:
            for identity in identities:
                signal_owned(identity, sig)
            time.sleep(0.05)
    remaining = [item for item in identities if matches(item)]
    if remaining:
        raise Error("some recorded processes did not stop; artifacts retained")
    recovery = state["phase"] != "stopped"
    state["phase"] = "stopped"
    state["recovered"] = recovery or state.get("recovered", False)
    atomic_json(base / "state.json", state)
    (base / "control.sock").unlink(missing_ok=True)
    return {
        "ok": True,
        "protocol": PROTOCOL,
        "id": base.name,
        "phase": "stopped",
        "recovered": state["recovered"],
        "artifacts": str(base),
        "warning": "Recovery stops recorded identities only; unrecorded descendants may remain."
        if state["recovered"]
        else None,
    }


def create(root, size):
    if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
        raise Error("Python/Linux pidfd support is required")
    probe = os.pidfd_open(os.getpid())
    try:
        signal.pidfd_send_signal(probe, 0)
    finally:
        os.close(probe)
    try:
        width, height = map(int, size.split("x"))
        if not 64 <= width <= 8192 or not 64 <= height <= 8192:
            raise ValueError
    except ValueError as exc:
        raise Error("size must be WIDTHxHEIGHT, each dimension in 64..8192") from exc
    for tool in ("Xvfb", "xauth", "xdotool", "glxinfo", "import"):
        if shutil.which(tool) is None:
            raise Error(f"missing dependency: {tool}")
    import PIL.Image  # fail before allocating processes if Pillow is missing

    ident = uuid.uuid4().hex
    base = root / ident
    if len(os.fsencode(base / "control.sock")) >= 108:
        raise Error("state path is too long for a Unix socket; use a shorter --root")
    base.mkdir(mode=0o700)
    child = None
    try:
        with (base / "supervisor.log").open("ab", buffering=0) as log:
            child = subprocess.Popen(
                [
                    sys.executable,
                    str(ENTRY),
                    "_serve",
                    str(base),
                    str(width),
                    str(height),
                ],
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                env=clean_env(os.environ),
                start_new_session=True,
            )
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if child.poll() is not None:
                state = read_state(base) if (base / "state.json").exists() else {}
                raise Error(
                    f"create failed: {state.get('error') or 'supervisor exited'}; artifacts: {base}"
                )
            if (base / "control.sock").exists():
                status = request(base, {"op": "status"})
                if status["xvfb_alive"]:
                    return status
            time.sleep(0.05)
        raise Error(f"create timed out; artifacts: {base}")
    except BaseException:
        if child is not None and child.poll() is None:
            child.terminate()  # direct unreaped child: PID cannot have been reused
            try:
                child.wait(timeout=12)
            except subprocess.TimeoutExpired:
                pass  # retain evidence; do not claim successful cleanup
        raise


def run(args, app_argv):
    root = root_path(args.root)
    if args.op == "create":
        return create(root, args.size)
    base = directory(root, args.id)
    # Serialize clients, including stop/purge, with the same per-ID lock.
    fd = lock(base)
    try:
        if args.op == "status":
            state = read_state(base)
            try:
                return request(base, {"op": "status"}, timeout=3)
            except (Error, OSError, ValueError):
                return stale_status(base, state)
        if args.op == "stop":
            return stop(base)
        if args.op == "purge":
            state = read_state(base)
            identities = state.get("owned", []) + [
                state.get(k) for k in ("supervisor", "xvfb", "app")
            ]
            if state["phase"] != "stopped" or any(matches(item) for item in identities):
                raise Error("purge requires a stopped environment; run stop first")
            shutil.rmtree(base)
            return {"ok": True, "protocol": PROTOCOL, "id": args.id, "purged": True}
        payload = {"op": args.op}
        if args.op == "launch":
            if not app_argv:
                raise Error("launch requires -- COMMAND [ARGS...]")
            env = clean_env(os.environ)
            for item in args.env:
                key, sep, value = item.partition("=")
                if not sep or not key or "=" in key or "\0" in item:
                    raise Error("--env expects NAME=VALUE")
                if key in BLOCKED:
                    raise Error(f"{key} is managed by ISD and cannot be overridden")
                env[key] = value
            payload.update(argv=app_argv, cwd=str(Path(args.cwd).absolute()), env=env)
        elif args.op == "screenshot":
            payload.update(
                screen=args.screen,
                window=args.window,
                output=str(Path(args.output).absolute()),
            )
        elif args.op == "windows":
            pass
        elif args.op == "focus":
            payload.update(op="input", kind="focus", action="focus", window=args.window)
        elif args.op in ("key", "mouse"):
            if args.action == "hold" and args.seconds is None:
                raise Error("hold requires --seconds")
            payload.update(
                op="input", kind=args.op, action=args.action, seconds=args.seconds
            )
            if args.op == "key":
                payload["value"] = args.value
            else:
                if args.action == "move":
                    if len(args.values) != 2:
                        raise Error("mouse move requires X Y")
                    payload.update(
                        x=args.values[0], y=args.values[1], window=args.window
                    )
                else:
                    if len(args.values) != 1 or args.window:
                        raise Error(
                            "mouse button action requires one button and no --window"
                        )
                    payload["value"] = args.values[0]
        return request(base, payload)
    finally:
        os.close(fd)


def main():
    os.umask(0o077)
    if len(sys.argv) > 1 and sys.argv[1] == "_serve":
        from .core import private_dir
        from .supervisor import serve

        serve(private_dir(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]))
        return
    argv = sys.argv[1:]
    app_argv = []
    if "--" in argv:
        index = argv.index("--")
        argv, app_argv = argv[:index], argv[index + 1 :]
    try:
        args = parser().parse_args(argv)
        if app_argv and args.op != "launch":
            raise Error("-- COMMAND is only valid with launch")

        # SIGTERM of the foreground create CLI follows the same cleanup path
        # as Ctrl-C. A SIGKILL cannot run this handler.
        def interrupted(_sig, _frame):
            raise KeyboardInterrupt

        signal.signal(signal.SIGTERM, interrupted)
        result = run(args, app_argv)
        print(json.dumps(result, sort_keys=True))
    except (Error, OSError, ValueError, ImportError) as exc:
        print(json.dumps({"ok": False, "protocol": PROTOCOL, "error": str(exc)}))
        sys.exit(1)
    except KeyboardInterrupt:
        print(json.dumps({"ok": False, "protocol": PROTOCOL, "error": "interrupted"}))
        sys.exit(130)
