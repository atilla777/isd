"""Linux abstract-only X11 allocation: never unlink a shared socket or lock."""

import secrets
from pathlib import Path

ALLOCATION = "linux-abstract-v1"


def filesystem_occupied(number):
    # Treat even dangling symlinks, malformed/stale locks and non-sockets as
    # occupied. We do not repair shared X11 state or connect to foreign servers.
    for name in (
        f"/tmp/.X11-unix/X{number}",
        f"/tmp/.X11-unix/X{number}_",
        f"/tmp/.X{number}-lock",
        f"/tmp/.tX{number}-lock",
    ):
        try:
            Path(name).lstat()
        except FileNotFoundError:
            continue
        return True
    return False


def display_candidates():
    # Same numeric domain as Xserver's automatic allocator, no reserved/fixed
    # display. Randomize the starting point to avoid concurrent startup herds.
    count = 65536 - 6000
    start = secrets.randbelow(count)
    for offset in range(count):
        number = (start + offset) % count
        if not filesystem_occupied(number):
            yield number


def xvfb_command(number, wfd, size, server_auth):
    return [
        "Xvfb",
        f":{number}",
        "-displayfd",
        str(wfd),
        "-screen",
        "0",
        f"{size[0]}x{size[1]}x24",
        "-nolisten",
        "tcp",
        # 'unix' is the filesystem transport. Some Xtrans versions propagate
        # its disable to 'local' too. Enable ONLY 'local' afterwards (Linux
        # abstract AF_UNIX), leaving the filesystem bind/unlink path disabled.
        "-nolisten",
        "unix",
        "-listen",
        "local",
        "-nopn",
        "-auth",
        str(server_auth),
        "-noreset",
    ]
