# Wrapper interface — CLI v1

Invoke the checkout's `isd` executable (or `python3 /path/to/isd/isd`) with an
argument array. Never build a shell command string. `--root PATH`, when used,
must precede the subcommand and must be reused for every operation on that ID.
Paths can be absolute; `--cwd`/`--output` also accept caller-relative paths, which
the CLI resolves before talking to the supervisor.

## Output and versioning

- Exactly one JSON object on stdout; `--help` and `--version` excepted.
- All results have `ok: boolean` and `protocol: 1`.
- Exit 0: command succeeded; exit 1: argument/operational error; exit 130:
  foreground interruption. Errors include a diagnostic `error` string.
- Successful environment operations include `id`, a 32-character lowercase
  hexadecimal identifier. It is the operation target, not a PID or display name.
- Unknown additional JSON fields must be ignored. The CLI flags and fields
  listed below are the stable wrapper interface for protocol 1. Human error
  strings, on-disk state, Unix sockets, process inventories, and private helpers
  are not stable interfaces. No third-party Python API is promised.

## Commands

| Invocation | Stable result fields beyond `ok`, `protocol`, `id` |
| --- | --- |
| `create [--size WxH]` | `phase`, `display`, `size: [w,h]`, `artifacts`, `supervisor_alive`, `xvfb_alive` |
| `launch ID [--cwd DIR] [--env NAME=value]... -- COMMAND [ARG]...` | `app` (identity object or null), `app_exit` (integer or null), `log`, `ready: false` |
| `status ID` | `phase`, `supervisor_alive`, `xvfb_alive`, `app_alive`, `app_exit`, `display`, `size`, `artifacts`, `error` |
| `windows ID` | `windows: [{id: integer, name: string}, ...]` |
| `screenshot ID (--screen \| --window WID) --output FILE` | `output` (absolute), `size: [w,h]`, `coordinates: "X11 pixels"` |
| `focus ID --window WID` | `delivered: true`, `application_action_verified: false` |
| `key ID (down\|up\|tap) KEYSYM_OR_CHORD` | same input result |
| `key ID hold KEYSYM_OR_CHORD --seconds N` | same input result |
| `mouse ID move X Y [--window WID]` | same input result |
| `mouse ID (down\|up\|click) BUTTON` | same input result |
| `mouse ID hold BUTTON --seconds N` | same input result |
| `stop ID` | `phase: "stopped"`, `recovered: boolean`, `artifacts`, `warning` (string or null) |
| `purge ID` | `purged: true` |

Dimensions: 64–8192 each, default 1280×720, 24-bit Xvfb screen. Holds: finite
0–60 seconds. Mouse buttons: 1–32. Movement coordinates: integers 0–65535;
the X server may clamp them to its screen. Window IDs: positive decimal or `0x`
hexadecimal X11 IDs, scoped only to the requested display. No remembered/global
"current environment" or "first game" exists.

`windows` lists visible, nonempty-named windows using xdotool; it is a convenience
discovery API, not an exhaustive UI tree. Choose a window by project knowledge,
not the ordering of the list. Empty lists are valid, and window lifecycle races
can yield errors. The same numeric window ID on different displays is normal.

`status` phases: `ready`, `launched`, `stopping`, `stopped`, `stale`,
`unreachable`, `cleanup_incomplete` (and `starting` in startup state). A normal
application exit does not terminate Xvfb or change `launched` to `stopped`;
use `app_alive`/`app_exit`. Null `app_exit` means not launched or not yet observed
to exit. Application exit code is not the CLI exit code. `status` succeeds even
when observing a stopped or stale environment. `error` is the last lifecycle
error, not a project test result. `warning`, when present, must not be mistaken
for evidence of complete recovery. IDs remain queryable after `stop`, not after
`purge`.

`launch` inherits the caller's non-session environment and applies repeatable
`--env` assignments literally. Managed desktop/session variable overrides are
rejected, not silently accepted. There is one successful launch per environment;
an exec failure leaves the slot free. No command string, shell, or `eval` is
involved. Readiness is always project-owned. Requests are bounded to 1 MiB,
including the inherited environment; enormous environments can fail explicitly.

`stop` waits for normal supervisor cleanup, or performs recorded-identity recovery
if the supervisor is dead. Repeating it succeeds. It never implies deletion of
artifacts. `purge` requires stopped state and no live recorded identities, and
removes only that environment's directory, not arbitrary screenshot outputs.
After supervisor SIGKILL, unrecorded descendants may remain despite successful
recorded-identity recovery; wrappers must surface that warning.

Same-ID calls are serialized, including synchronous holds and stop/purge.
Different IDs remain independent. Client cancellation does not generally cancel
an already accepted operation; recheck `status` and explicitly `stop`. Foreground
`create` handles SIGINT/SIGTERM, not SIGKILL. Keep IDs durably in the project if
the agent itself may disappear.

## Responsibility boundary

ISD owns Xvfb allocation/authorization, supervision, private runtime resources,
real capture, explicit X11 input, logs, and safe stop/recovery. The wrapper owns
builds, command argv, SDL/renderer/audio choices, HOME/save/scratch isolation,
readiness, game coordinates, game scenarios, tick/UI synchronization, and result
assertions. Never depend on a particular display number or process identity
object layout. Do not connect the application directly to the desktop if ISD
fails.
