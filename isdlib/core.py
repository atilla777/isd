"""Shared storage, process identity, and local protocol primitives."""

import fcntl
import json
import os
from pathlib import Path
import re
import signal
import socket
import stat
import struct
import tempfile

PROTOCOL = 1
MAX_MESSAGE = 1024 * 1024
ID_RE = re.compile(r"[0-9a-f]{32}\Z")
BOOT = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
BLOCKED = {
    "DISPLAY",
    "XAUTHORITY",
    "WAYLAND_DISPLAY",
    "WAYLAND_SOCKET",
    "XDG_RUNTIME_DIR",
    "DBUS_SESSION_BUS_ADDRESS",
    "DBUS_STARTER_ADDRESS",
    "DBUS_STARTER_BUS_TYPE",
    "SESSION_MANAGER",
    "HYPRLAND_INSTANCE_SIGNATURE",
    "SWAYSOCK",
    "I3SOCK",
    "DESKTOP_STARTUP_ID",
    "XDG_ACTIVATION_TOKEN",
    "XDG_SESSION_ID",
    "XDG_SESSION_TYPE",
    "XDG_SESSION_DESKTOP",
}


class Error(Exception):
    pass


def private_dir(path, create=False):
    path = Path(path).absolute()
    if create:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise Error(f"not an owned directory: {path}")
    if info.st_mode & 0o077:
        raise Error(f"directory must have mode 0700: {path}")
    # Refuse symlinks in any ancestor, too.
    if path.resolve() != path:
        raise Error(f"symlink paths are not allowed: {path}")
    return path


def root_path(value=None):
    return private_dir(value or Path.home() / ".local/state/isd", create=True)


def directory(root, ident):
    if not ID_RE.fullmatch(ident):
        raise Error("invalid environment ID (expected 32 lowercase hex characters)")
    return private_dir(root / ident)


def atomic_json(path, data):
    fd, name = tempfile.mkstemp(prefix=".state-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(data, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def read_state(base):
    path = base / "state.json"
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_mode & 0o077
    ):
        raise Error("unsafe state file")
    data = json.loads(path.read_text())
    if data.get("id") != base.name or data.get("protocol") != PROTOCOL:
        raise Error("state ID/protocol mismatch")
    return data


def process(pid, include_zombie=False):
    try:
        path = Path(f"/proc/{int(pid)}")
        if path.stat().st_uid != os.getuid():
            return None
        raw = (path / "stat").read_text()
        fields = raw[raw.rfind(")") + 2 :].split()
        if fields[0] == "Z" and not include_zombie:
            return None
        return {
            "pid": int(pid),
            "start": int(fields[19]),
            "boot": BOOT,
            "ppid": int(fields[1]),
        }
    except (FileNotFoundError, ProcessLookupError, PermissionError, ValueError):
        return None


def matches(identity):
    if not isinstance(identity, dict) or identity.get("boot") != BOOT:
        return False
    current = process(identity.get("pid", -1))
    return current is not None and current["start"] == identity.get("start")


def signal_owned(identity, sig):
    """Pin PID first, then validate start time; never signal by an unpinned PID."""
    if not isinstance(identity, dict) or identity.get("boot") != BOOT:
        return False
    try:
        fd = os.pidfd_open(identity["pid"])
    except (ProcessLookupError, OSError):
        return False
    try:
        if not matches(identity):
            return False
        signal.pidfd_send_signal(fd, sig)
        return True
    except ProcessLookupError:
        return False
    finally:
        os.close(fd)


def descendants(parent):
    """Discover descendants, including children adopted by a subreaper."""
    table = {}
    for entry in Path("/proc").iterdir():
        if entry.name.isdecimal():
            info = process(int(entry.name))
            if info:
                table[info["pid"]] = info
    root = process(parent)
    if root is None:
        return []
    found, parents = {}, {parent: root}
    while True:
        batch = {}
        for pid, info in table.items():
            ancestor = parents.get(info["ppid"])
            if ancestor is None or pid in found:
                continue
            # A /proc scan is not atomic. Revalidate the ancestor's start time
            # so reuse of an intermediate PID cannot attach a foreign subtree.
            current = process(pid)
            if (
                matches(ancestor)
                and current is not None
                and current["start"] == info["start"]
                and current["ppid"] == ancestor["pid"]
                and current["start"] >= ancestor["start"]
            ):
                batch[pid] = current
        if not batch:
            return list(found.values())
        found.update(batch)
        parents.update(batch)


def clean_env(source):
    return {key: value for key, value in source.items() if key not in BLOCKED}


def xenv(source, base, display):
    env = clean_env(source)
    env.update(
        DISPLAY=display,
        XAUTHORITY=str(base / "Xauthority"),
        XDG_RUNTIME_DIR=str(base / "runtime"),
        DBUS_SESSION_BUS_ADDRESS=f"unix:path={base}/runtime/no-session-bus",
    )
    return env


def receive(sock):
    chunks, length = [], 0
    while True:
        part = sock.recv(min(65536, MAX_MESSAGE + 1 - length))
        if not part:
            break
        chunks.append(part)
        length += len(part)
        if length > MAX_MESSAGE:
            raise Error("message exceeds 1 MiB")
        if b"\n" in part:
            break
    return json.loads(b"".join(chunks))


def request(base, payload, timeout=80):
    state = read_state(base)
    identity = state.get("supervisor")
    if not matches(identity):
        raise Error("supervisor is unavailable; use status, then stop for recovery")
    with socket.socket(socket.AF_UNIX) as sock:
        sock.settimeout(timeout)
        sock.connect(str(base / "control.sock"))
        pid, uid, _ = struct.unpack(
            "3i", sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
        )
        if uid != os.getuid() or pid != identity["pid"] or not matches(identity):
            raise Error("supervisor peer identity mismatch")
        payload = dict(payload, protocol=PROTOCOL, id=base.name)
        raw = json.dumps(payload).encode() + b"\n"
        if len(raw) > MAX_MESSAGE:
            raise Error("request exceeds 1 MiB")
        sock.sendall(raw)
        result = receive(sock)
        if not result.get("ok"):
            raise Error(result.get("error", "supervisor error"))
        return result


def lock(base, name="client.lock"):
    fd = os.open(base / name, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
        os.close(fd)
        raise Error("unsafe lock file")
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd
