# Practical verification

## Separately authorized publication check — 2026-11-09

After the investigation the user explicitly authorized committing and publishing
ISD. The full suite was rerun: **26 tests passed** in 75.271 seconds, including
the namespace regression (not skipped). Evidence:
`/tmp/opencode/isd-publication-15dkyind/tests-final.log`,
`/tmp/opencode/isd-sandbox-lnhy2bcc/`, and
`/tmp/opencode/isd-live-z6xmukyj/`. The host blue screen, green keyboard and red
mouse PNGs were opened and visually inspected. The process audit at
`/tmp/opencode/isd-publication-15dkyind/process-audit.json` covered 128 retained
states from both publication-check attempts: no recorded identities remained
live, and the separate `/proc` scan found no Xvfb, ISD supervisor or fixture.

The first attempt exposed a unit-test assumption about umask: `mkdir(0755)`
became 0700 under the check's private umask 077, so it did not create the intended
unsafe-directory fixture. The fixture now explicitly chmods its own temporary
directory to 0755; runtime permission enforcement is unchanged. Its initial log
is retained as `tests.log` next to the successful log. No Slyx changes or new
main-desktop probes were performed during publication verification.

## Repetition investigation verified 2026-11-09

This run rechecked the existing uncommitted protection rather than assuming the
October result applied to every checkout. See [x11-allocation.md](x11-allocation.md)
for source analysis, the unsafe alternate worktree, historical attribution limits,
and the required consumer migration/checks.

`python3 -m unittest discover -s tests -v`: **26 tests passed** in 69.866 seconds
(13 unit tests, 12 host live tests, 1 sandbox runner containing 20 further checks).
The sandbox was available, not skipped: distinct mount AND network namespaces
were verified, mount propagation made private, and a fresh bind-mounted `/tmp`
verified by device/inode before any destructive reproduction. Direct unittest
invocation of the reproduction without that gate is tested to refuse.

The installed Xvfb 21.1.24-1 again replaced a live authorized pathname-only foreign
X0 with the old allocator, then removed X0 at stop. Protected allocation preserved
the foreign process/socket/lock identities and lock contents and kept new
connections working through a forced pathname race, abstract collision/retry,
two starts forced onto the same candidates, sixteen exhausted collisions with
reaped failed children, and three rounds of three concurrent public CLI runs.
The full live suite also ran inside that namespace, and separately on the host
with only protected isolated displays. Listener inventory showed exactly one
abstract X11 endpoint, no filesystem endpoint/lock, and no owned TCP listener.
Both Xlib and XCB accepted the correct cookie and rejected missing/foreign cookies.
Real GLX, capture, keyboard/mouse, failure, interruption, recovery and independent
peer shutdown checks passed. The blue screen, green keyboard and red mouse PNGs
from the host run were opened and visually inspected.

Explicitly authorized windowless XCB handshakes on the main `:0` returned error
**0** before and after testing. X0 and X0_ retained device/inode, mode, uid/gid,
mtime/ctime; `.X0-lock` retained those fields and contents. Hyprland PID 1673 and
Xwayland PID 2173 retained PID/start/boot identity, argv, and X11 listener fds
57/58 (socket inodes 12217/12218); the X11 `/proc/net/unix` entries were identical.
Other compositor client fds may change naturally and are not safety invariants.
No main-desktop windows, screenshots, focus/pointer queries or input were used.

Retained local evidence (private logs/authority files; do not publish wholesale):

- `/tmp/opencode/isd-investigation-20261109-kcyqtkwt/`:
  `full-tests.log`, `before.json`, `after-final.json`,
  `desktop-comparison.json`, `desktop_snapshot.py`, `process-audit.json`,
  `copies.json`, `manifest.json`, `sources/` with inspected upstream excerpts
  and release-tarball SHA256/URLs, `coredump-info.txt`, `journal-crash.txt`,
  `oom.txt`, `packages.txt`, and `worktrees.txt`.
- `/tmp/opencode/isd-sandbox-mcvjqdiw/checks.log`: namespace identities and
  successful destructive reproduction/protection assertions. Namespace paths
  printed in that log map to its `tmp/opencode/` subtree on the host;
  foreign-server evidence is `tmp/opencode/foreign-pxwqvae7/`, including all
  Xvfb logs and forced-attempt states. Namespace live evidence is
  `tmp/opencode/isd-live-c6rz7nmb/`.
- `/tmp/opencode/isd-live-2zpm9ip5/`: host live evidence and inspected PNGs:
  `db56f8cc005c43d4a133ecfdddcf889a-02-screen.png`,
  `db56f8cc005c43d4a133ecfdddcf889a-03-keyboard.png`,
  `56533ef487c1496eb75e4f017734f525-03-mouse.png`.

The process audit covered **119** retained state files across this investigation's
development/final host and namespace runs: no recorded identity remained live.
A separate read-only `/proc` inventory found no Xvfb, ISD supervisor or test
fixture processes. The sandbox's two-peer starts and exhausted retries leave
internal test states in `ready`/`starting`, but their children and recorded test
supervisor identities are dead; no claim of public lifecycle completion is made
for these direct internal-helper calls.

Development failures are retained too: the added mount-identity assertion first
tried to stat the now-hidden host path (fixed by snapshotting before the mount);
the old helper's short readiness read caused EPIPE (fixed by waiting for newline);
one new assertion compared an abstract socket's bytes address to text (corrected).
These were test-harness failures, not evidence that protected runtime allocation
deleted a foreign socket. `after.json` lacked the compositor entry because an fd
closed during inventory; the per-fd race handling was corrected and
`after-final.json` / `desktop-comparison.json` contain the complete comparison.

No packages, global settings, sibling worktrees, Slyx code/tracker, desktop,
VPN processes or services were changed/restarted. No core was extracted, no old
allocator ran on the host, and no commit/publication occurred. Evidence under
`/tmp` is local and may disappear on reboot; preserve it privately if needed.
This establishes protection of the tested allocator on this installed Linux/Xvfb,
not attribution of the November events, long-term absence of other socket
deleters, consumer deployment, suspend/resume correctness, or Slyx compatibility.
The original unsafe commit remains unchanged until separately authorized delivery.

## Allocation fix verified 2026-10-22

The earlier 2026-10-21 run below did not establish preservation of a foreign
pathname-only X11 server. It is superseded for allocation safety by this check.

After the user rebooted, a specifically authorized, windowless XCB connection
confirmed main `DISPLAY=:0` was healthy. Socket/process identities were recorded
before any live tests. Safe unit tests and the full namespace-isolated regression
ran before the first host live run.

`python3 -m unittest discover -s tests -v`: **22 tests passed** in 67.161 seconds
(9 unit tests, 12 host live tests, 1 sandbox runner containing 16 further tests).
The sandbox reproduced the old failure using the installed Xvfb, then confirmed
foreign-server preservation with the fix, including forced collisions and three
parallel create/use/stop rounds. Host live tests included three more rounds of
three parallel environments, independent use after peer stop, authorization,
captures, failure/recovery and cleanup.

Before/after the full run, main XCB connection error was **0**. The X0 and X0_
device/inode/owner/mode/mtime/ctime, X11 lock identity/content, and Hyprland and
Xwayland PID/start-time/boot identities and argv were unchanged. Only the narrowly
authorized connection probe touched the main X11; no windows, focus/pointer/input
queries or changes were made there.

Retained private evidence:

- `/tmp/opencode/isd-verification-x4xl9x4r/`: `before.json`, `after.json`, `tests.log`;
- `/tmp/opencode/isd-sandbox-2llqz9n4/`: `checks.log` and `tmp/opencode/` artifacts;
- `/tmp/opencode/isd-live-4ty6c6oa/`: host live artifacts and PNGs.

The blue screen, green keyboard and red mouse PNGs from this host run were opened
and visually inspected. All 61 retained state files from the final host/sandbox
run were audited: no recorded identity remained live. A read-only `/proc` check
also found no matching supervisors or fixture processes.

No packages, system/session configuration or sibling repositories were changed;
no commit/push or suspend/resume was performed. AmneziaVPN's GUI and the moy test
were not launched; suspend/resume behavior and unrelated Amnezia failures are not
covered. The historical event was not syscall-traced; see the distinction between
retained evidence and reproduced mechanism in [x11-allocation.md](x11-allocation.md).

## Earlier verification (2026-10-21)

Verified on the target Linux computer on 2026-10-21, with Python 3.14.7,
Pillow 12.3.0, installed Xvfb/XTEST, ImageMagick, and Xlib 1.8.13.
The real `glxinfo -B` probe reported Mesa 26.2.2, llvmpipe (LLVM 22.1.8), and
OpenGL 4.6. No packages were installed, and no desktop settings or sibling
repositories were changed.

## Reproduce

```sh
python3 -m unittest discover -s tests -v
```

**17 tests passed** in 29.449 seconds: 6 unit tests and 11 real integration tests. Live tests compile
`tests/fixture.c` outside the repository and run it on real isolated X servers.
This is not a mocked screenshot or a test of a neighboring game project.

Covered behavior:

- True parallel display allocation; separate authorities, private directories,
  and independent window/input state.
- Actual rendered 320×240 window captures and 640×480 screen captures, opened and
  fully decoded as PNG. Background, white rectangle, and text visibly present.
- Visible keyboard transition from blue to green and mouse transition to red,
  corroborated by fixture key/button down/up event logs and pointer coordinates.
- Persistent down/up across CLI calls, synchronous keyboard/mouse holds, taps,
  explicit focus, window-relative movement, and unaffected parallel environment.
- Capturing does not change the isolated focus or pointer. No test queries or
  manipulates main-desktop focus; each operation explicitly selects an isolated
  display. Deliberately poisoned desktop environment variables are replaced.
- Wrong/missing authority rejected; another environment's cookie also rejected.
  Xvfb's actual arguments and owned TCP socket inodes confirm no TCP listener.
- An instrumented, delayed xauth wrapper confirms the cookie is not present in
  process arguments and authorization setup survives asynchronous stdin handling.
- Normal/failed exec, literal argv and project environment, nonzero application
  exit status, managed-variable override rejection, and no saved full environment.
- Xvfb death, supervisor SIGTERM, daemonized double-fork/setsid descendants,
  SIGTERM-ignoring application escalation to SIGKILL, repeated stop, and separate
  purge. Stopping one environment leaves the other usable.
- Partial startup cleanup when foreground create receives SIGTERM during Xvfb
  startup or the GL probe. Runtime Xvfb/GL failure yields an explicit error and
  retained logs, never a desktop launch.
- Supervisor SIGKILL stale recovery; an unrelated live process represented by
  a deliberately mismatched PID start time remains untouched. Unit tests also
  cover boot mismatch and pidfd identity validation.
- Path/ID validation, private file modes, active-purge refusal, invalid windows,
  existing-output preservation, capture failure cleanup, and rejection of a
  truncated PNG even when the capture helper exits successfully.
- Cleanup still signals owned processes if state persistence fails.

Representative local evidence from the final verification cycle is retained at
`/tmp/opencode/isd-live-zz4kqj8k/`, including:

- `0b7ecf6902eb4968b115620fe061f804-02-screen.png`: blue fixture on black screen;
- `0b7ecf6902eb4968b115620fe061f804-03-keyboard.png`: green window;
- `a762b5409c934438950ca35ce1426e01-03-mouse.png`: red window;
- `state/ID/`: stopped state, Xvfb/application/GL logs, authority, runtime directory.

All three PNGs above were opened and visually inspected, in addition to automated
decode/pixel assertions. These local artifacts are not committed because logs
and authority files are private runtime data. Every subsequent run prints its
own evidence directory.

After verification, recorded supervisor, Xvfb, app, and descendant identities
were checked: none remained live. A read-only `/proc` audit for this checkout's
supervisors and its fixture executables also found none. Evidence directories
from development and repeated test runs are deliberately retained under
`/tmp/opencode/isd-dev` and `/tmp/opencode/isd-live-*`. Shared X11 lock/socket
paths are not deleted by display number; see the recovery limitations in README.

## Not established by this verification

- ISD compatibility with DragonRuby, Odin applications, moy, or any other real
  game. The earlier temporary DragonRuby experiment was feasibility evidence,
  not an ISD integration test.
- A guaranteed FPS, arbitrary renderer/GPU compatibility, Vulkan, audio behavior,
  window-manager/fullscreen behavior, or logical game/tick synchronization.
- Malicious-process containment or complete cleanup after supervisor SIGKILL.
  Recovery covers recorded identities; unrecorded descendants may escape it.
- Actual numeric PID recycling was not forced. The start-time and boot mismatch
  cases exercise the refusal path without risking another user's processes.
- A viewer, headless Wayland, or isolation of application files/saves. These are
  either explicitly deferred or owned by project wrappers.
