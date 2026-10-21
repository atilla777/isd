# ISD — Isolated Display

A small Linux CLI for running graphical applications on independent Xvfb
displays. Agents can capture real pixels and send input without using the focus
or input devices of the main desktop. Application language does not matter.

**This is graphical-session isolation, not a security sandbox.** Processes still
have your UID and filesystem/network permissions. Only Xvfb is implemented;
there is no window manager, viewer, system service, or game-testing framework.

## Requirements and installation

- Linux with `/proc`, `pidfd_open`/`pidfd_send_signal`, and subreaper support;
  Python 3.9+ with those APIs enabled.
- `Xvfb` with `-displayfd` and GLX; `xauth`, `xdotool` (XTEST), `glxinfo`
  (Mesa utilities), ImageMagick's `import`, and Python Pillow.
- For the live tests only: a C compiler and Xlib headers/library (`cc -lX11`).

Use directly from a checkout; nothing is installed globally:

```sh
git clone https://github.com/atilla777/isd.git
cd isd
./isd --help
./isd --version
```

Missing dependencies are errors, not an invitation to fall back to your desktop.
ISD never installs packages. Pillow must be available to the Python interpreter
running `isd`.

## Quick start

Each call is independent; an open shell is not required. Save the `id` returned
by `create`, then pass it explicitly to every subsequent operation:

```sh
./isd create --size 1280x720
# JSON: {"id":"<32 lowercase hex characters>", ...}

# Set ID to the exact value returned above (do not use the angle brackets).
ID=...
./isd launch "$ID" --cwd /absolute/project/path \
  --env SDL_VIDEODRIVER=x11 --env LIBGL_ALWAYS_SOFTWARE=1 \
  -- /absolute/path/to/application argument1 'argument with spaces'
./isd status "$ID"
./isd windows "$ID"

# Set WIN to the numeric id of the chosen window from `windows`.
WIN=...
./isd screenshot "$ID" --window "$WIN" --output /absolute/path/frame.png
./isd screenshot "$ID" --screen --output /absolute/path/screen.png

./isd focus "$ID" --window "$WIN"
./isd key "$ID" tap Return
./isd key "$ID" down Left
./isd key "$ID" up Left
./isd key "$ID" hold Left --seconds 0.5
./isd mouse "$ID" move 100 200
./isd mouse "$ID" move 80 90 --window "$WIN"
./isd mouse "$ID" click 1
./isd mouse "$ID" down 1
./isd mouse "$ID" up 1
./isd mouse "$ID" hold 1 --seconds 0.2

./isd stop "$ID"       # waits for cleanup; repeatable; keeps logs and images
./isd purge "$ID"      # explicitly removes the stopped environment directory
```

Application arguments after `--` are passed as an argv array, **not evaluated**.
`--env NAME=value` is repeatable. The caller's environment is inherited in memory
after desktop-connection variables are stripped. ISD replaces `DISPLAY`,
`XAUTHORITY`, and `XDG_RUNTIME_DIR`, removes Wayland/session endpoints, and points
the local D-Bus session address at a nonexistent private socket. Managed variables
cannot be overridden with `--env`. Application stdout/stderr go to `app.log`;
stdin is `/dev/null`. Full environments and application argv are not saved in
state. Applications can of course print their own secrets into their logs.

## Lifecycle and artifacts

`create` starts one detached, per-environment supervisor and one Xvfb. It returns
after `-displayfd` readiness, authorization setup, and a successful `glxinfo -B`
probe. There is no global daemon or autostart. The supervisor survives the CLI's
exit. `launch` starts **one** primary application per environment; failed exec can
be retried, but a successfully launched application cannot be replaced. Create a
new environment for another run. A successful launch is **not application
readiness**; even an application that immediately exits may have been launched
successfully. Inspect `app_alive`, `app_exit`, the window list, or project-specific
readiness mechanisms.

The display remains available after a normal application exit until `stop`.
Xvfb death triggers application/descendant cleanup. SIGTERM, SIGINT, and SIGHUP
to the supervisor also trigger cleanup. SIGINT/SIGTERM during foreground
`create` request cleanup of its partial startup. Termination escalates from
SIGTERM to SIGKILL, signaling individual verified identities, not whole process
groups. Linux subreaper adoption covers normal double-fork/`setsid` descendants.
Uninterruptible kernel tasks may prevent complete cleanup; that is an error.

Default state: `~/.local/state/isd/ID/`, mode 0700. Each environment contains:

- `state.json`: version, display, lifecycle, PID/start-time/boot identities,
  recorded descendants, exit status, and last lifecycle error;
- `Xauthority` and `server.auth`: private authorization files (0600);
- `runtime/`: private XDG runtime directory;
- `xvfb.log`, `glxinfo.log`, `supervisor.log`, and (after launch) `app.log`;
- `control.sock` while running; `client.lock` after client operations.

Override storage with **`--root /absolute/private/path` before the subcommand**.
Use the same root in all calls. ISD creates it mode 0700, rejects unsafe existing
permissions and symlink paths, and requires a short enough Unix-socket pathname.
Different agents can use one root or separate roots. UUID identifiers and Xvfb's
atomic `-displayfd` allocation allow concurrent creates without reserved numbers.
Operations for the same ID are serialized; different IDs operate independently.
A hold is synchronous (0–60 seconds), blocking other operations on that ID.

`stop` retains the entire artifact directory, including screenshots saved there.
Screenshots outside it are unaffected even by `purge`. Images must have new
filenames: an existing file or symlink is never overwritten. The capture is
decoded/verified with Pillow and atomically published only after validation.
No focus, pointer, or input changes are performed by screenshot capture.

## Input and coordinates

Keyboard input uses X11 keysyms/chords (`Left`, `Return`, `a`, `ctrl+a`) and the
selected X server's current focus. **Focus is an explicit command**; ISD does
not implicitly focus a window on launch, capture, or keyboard input. Mouse button
events go to the isolated pointer's location. `down` remains held across CLI
calls until `up` or server shutdown; `hold` sends down, waits, and attempts up in a
`finally` block. If the foreground CLI disappears during a hold, the supervisor
still completes it. Supervisor SIGKILL cannot guarantee key release; `stop`
recovery shuts down the recorded Xvfb instead.

Mouse coordinates are **X11 pixels**, origin top-left, x rightward and y downward.
Without `--window`, movement is relative to the virtual screen. With `--window`,
it is relative to that window. Window IDs are scoped to an environment and can
be reused by X11 after window destruction: rediscover them when the application
recreates windows. No conversion to game logical coordinates, DPI-scaled UI
coordinates, or application ticks is provided. Successful event delivery does
not prove that the application acted on the event. External screenshots have no
UI-tree or game-tick synchronization guarantees.

## Safety, recovery, and limits

- Cookie material exists **before** Xvfb starts; no `-ac`. Cookies are passed to
  xauth over stdin, not exposed in process arguments. TCP listening is
  disabled. Missing/wrong authority is tested to fail, including another ISD
  environment's cookie.
- All X11 helpers receive the selected display and authority explicitly. No
  operation changes desktop settings, global environment, systemd, or D-Bus.
  Child descriptors are closed except Xvfb's explicit startup pipe.
- Ownership is based on the live supervisor's descendant tree (with subreaper
  adoption) and persisted PID/start-time/boot identities. PID reuse is checked
  **after opening a pidfd**; signals use pidfds. Ancestor identities are also
  revalidated during discovery. No `pkill`, `killall`, process-name guessing,
  fixed display numbers, or unsafe display-number probes.
- On supervisor SIGKILL, crash, or power loss, automatic cleanup is **not**
  promised. `status` reports stale/unreachable state. `stop` recovers by signaling
  only the recorded identities. A descendant forked between inventory updates
  and supervisor death may not be recorded and can remain. A lost state directory
  also loses recovery information. Do not delete active state manually.
- `purge` checks stopped state and recorded identities; it cannot prove absence
  of unrecorded descendants after a supervisor SIGKILL. Recovery warnings are
  intentionally retained. Shared `/tmp/.X*-lock` and `/tmp/.X11-unix/X*` names
  are not manually unlinked: after Xvfb SIGKILL they can be stale, or may already
  belong to a new server. Xvfb handles its normal shutdown and stale startup
  detection. This avoids deleting another server's socket by display number.
- This is **not** a defense against a malicious same-UID process. An application
  can explicitly connect elsewhere, access devices, discover the user's session,
  or modify your files. No filesystem, network, audio, GPU-device, or resource
  sandboxing is provided. Never run untrusted applications on that assumption.
- The project must select a compatible renderer/backend and audio behavior,
  build/launch its application, choose resolution, detect readiness, isolate
  saves/scratch/HOME if needed, and judge test results. ISD embeds no game paths,
  engine-specific settings, or action keys.
- GL probing only proves a GLX context can be created here. Software rendering,
  fullscreen behavior without a window manager, GPU requirements, performance,
  Vulkan, and individual game compatibility need their own checks. No 60 FPS
  or general DragonRuby/Odin compatibility claim is made.

## Errors and automation

Commands return one JSON object on stdout with `ok` and `protocol: 1`. Success is
exit 0, operational/argument errors exit 1, foreground interruption exit 130.
`--help`/`--version` are human-readable exceptions. Do not parse human error text;
check exit status, `ok`, and status fields. Normal application exit codes are
reported in `app_exit` (negative values are terminating signals), not as the
`status` command's exit code. Status after stop or supervisor loss is still a
successful observation, not proof that the application is running.

Common failures:

| Failure | Action |
| --- | --- |
| Missing dependency / no pidfd support | Supply requirements separately; no desktop fallback |
| Xvfb/GL startup error | Inspect artifact path in error, `xvfb.log`, `supervisor.log`, `glxinfo.log` if present |
| Application exec error | Correct command/cwd; failed exec may be retried |
| No windows yet | Use a project readiness check; `windows` may legitimately return `[]` |
| Invalid/destroyed window | Rediscover windows; do not reuse stale window IDs |
| Existing screenshot path | Choose a fresh filename |
| Supervisor unavailable | `status ID`, then `stop ID` for recorded-identity recovery |
| Cleanup incomplete | Retain artifacts; retry `status`/`stop`; do not blindly purge |

The stable wrapper contract is documented in [docs/interface.md](docs/interface.md).
Internal state and socket protocol are implementation details, not wrapper APIs.

## Tests and a reproducible live run

```sh
python3 -m unittest tests.test_core -v  # process identity, paths, protocol, env
python3 -m unittest tests.test_live -v  # real Xvfb + compiled independent Xlib fixture
# or both:
python3 -m unittest discover -s tests -v
```

The live suite prints and retains `/tmp/opencode/isd-live-*/` with logs, PNGs,
compiled fixture, and stopped environment state. If running on another machine,
first create a private `/tmp/opencode` directory for test artifacts; the CLI
itself does not depend on that directory. Tests clean up their running processes
but deliberately keep evidence. They never query or manipulate the main desktop's
focus. Open the emitted `*-02-screen.png`, `*-03-keyboard.png`, and `*-03-mouse.png`
to visually verify the blue initial window, green keyboard response, and red
mouse response. Automated assertions also check actual decoded pixel values and
fixture event logs.

For manual use of the same fixture, compile outside the checkout and launch it
using the commands above:

```sh
cc -Wall -Wextra -Werror tests/fixture.c -o /tmp/opencode/isd-fixture -lX11
./isd create --size 640x480
# ID=... from the result
./isd launch "$ID" -- /tmp/opencode/isd-fixture
./isd windows "$ID"
# WIN=... from the result
./isd focus "$ID" --window "$WIN"
./isd key "$ID" tap a
./isd screenshot "$ID" --window "$WIN" --output /tmp/opencode/isd-manual.png
./isd stop "$ID"
```

See [docs/verification.md](docs/verification.md) for the actual machine-level
verification and its limits.

## Separate next steps

**Viewer:** investigate a read-only viewer (possibly an independently managed VNC
bridge) after approval of any extra dependencies. Closing it must not stop the
application or supervisor. No viewer packages were installed.

**moy integration:** add a thin wrapper *in a separately approved moy change*.
It should build/prepare the project, create an ISD ID, pass the project's launch
command plus explicit SDL/renderer/audio variables and isolated save/scratch
paths, use project readiness checks, then call ISD capture/input/stop. Keep game
scenarios and assertions in moy, not ISD. First validate that exact launch through
ISD; the earlier temporary DragonRuby experiment is not an ISD compatibility test.

MIT licensed. Independent of any application or engine repository.
