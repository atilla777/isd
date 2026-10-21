# Practical verification

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
