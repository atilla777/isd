# X11 allocation investigation — 2026-10-22 and 2026-11-09

## Repetition investigated 2026-11-09

**Confirmed defect:** the original allocator in commit
`c4cf1cfe61adee2050c458aa891b21e8cf43dc8b` is unsafe against a live pathname-only
server. The installed Xvfb 21.1.24-1 reproduced replacement and removal of its
socket again on November 9, exclusively inside verified private mount/network
namespaces. The preserved abstract-only fix prevents that destructive path;
no further runtime unlink/lock defect was found in the reviewed implementation.

**Confirmed repetition symptom, not confirmed perpetrator:** diagnostics at
`~/.local/state/amnezia-diagnostics/20261108-173926-eq31fpux/` show a live Xwayland
with listening descriptors, missing X0, surviving X0_ and `.X0-lock`, and
`DISPLAY=:0 xcb_error=1`. The X11 directory mtime was 15:58:50 on November 8;
this is not an unlink trace and does not identify the responsible process.
The November 9 15:17:06 crash (PID 2159064, boot
`5cc6be6521a5410292a9e75e22d9cf1a`) was AmneziaVPN launched from the menu,
SIGABRT through Qt `createPlatformIntegration`. Its retained stack is consistent
with platform initialization failure, not proof of a socket deletion or its
author. No kernel OOM event was found for that boot. A missing Wayland plugin
warning also occurs on successful launches and is not independently causal.

**Confirmed deployment gap:** the main checkout's protection was still
uncommitted, with `display.py` and tests untracked. Read-only inventory found:

| Checkout | Allocator at inspection |
| --- | --- |
| `/home/aleksei/plums/isd` | Protected working copy (this investigation) |
| `/home/aleksei/plums/isd-ru-input` | Old automatic `-displayfd`, filesystem transport enabled; no `display.py` |
| `/home/aleksei/plums/isd-slyx-iv132-native` | Copied abstract-only protection, plus separate local changes |

All three have the same Git HEAD above. `isd` itself is an identical tiny Python
entry point in them, so hashing only that executable is insufficient too.
The inspected Slyx wrapper accepts explicit `--isd` / `ISD_BIN`, otherwise finds
the sibling of its main checkout; its documentation pins the original unsafe
commit, not these working changes. This establishes a real route to bypassing
the fix, **not which route was used in either November failure**. No retained
run manifest or syscall trace tying slyx-iv1.4 to either event was located in
the inspected temporary/project locations. Slyx, its tracker, the other ISD
worktrees, and running desktop/VPN processes were not modified.

## Evidence and mechanism

The retained Amnezia diagnostics at
`~/.local/state/amnezia-diagnostics/20261022-090634-0jqhii9c/` and
`20261022-090646-y3tpuo4w/` show:

- Before ISD: X0 and X0_ filesystem sockets, a live Xwayland, successful XCB
  connection, and `.X0-lock` belonging to the live compositor.
- The listener inventory shows pathname listeners, not an abstract X0 listener.
- ISD state records successful Xvfb allocation of `:0` and normal shutdown.
- Afterwards: X0 absent, X0_ and the original lock unchanged, original listening
  descriptors and Xwayland still present, but new XCB connections fail.

Upstream release sources matching the installed Xserver version were inspected:

- [xorg-server 21.1.24](https://www.x.org/releases/individual/xserver/xorg-server-21.1.24.tar.xz),
  `os/utils.c`: parsing `-displayfd` sets `nolock = TRUE`. `LockServer()` then
  returns without consulting `.X0-lock`. A compositor PID in that lock is not
  evidence of an invalid lock, and changing its contents is not a solution.
- `os/connection.c`, `CreateWellKnownSockets()`: automatic allocation starts at
  zero and tries listeners, not lock-file ownership.
- [Xtrans 1.6.0](https://www.x.org/releases/individual/lib/xtrans-1.6.0.tar.xz),
  `Xtranssock.c`, `SocketUNIXCreateListener()`: pathname transport calls
  `unlink(sun_path)` **before** `bind()`. A successful bind therefore does not
  prove the display was unused. `SocketUNIXClose()` unlinks the pathname again
  at shutdown. Abstract listeners use atomic bind without pathname unlink.
  `SocketLocalFuncs` is abstract on Linux; `SocketUNIXFuncs` is pathname based.
  In Xtrans 1.6.0, `NoListen` / `Listen` propagate through transports marked
  `TRANS_ALIAS`; Linux abstract-enabled `unix` is not marked as an alias there.
  Older alias configurations may propagate disabling `unix` to `local`.
  Explicitly enabling ONLY `local` afterwards establishes the intended final
  state across these configurations; do not assume reversing the flags fails
  on every Xtrans version. The installed binary's actual listener inventory,
  not the flag names alone, is checked by integration tests.

The installed Xvfb reproduced the entire mechanism in private mount/network
namespaces: a live, authorized pathname-only foreign X server at `:0`, plus a
lock containing a live parent PID. The old automatic invocation selected `:0`,
changed the X0 inode while the foreign server was alive, and removed X0 when
stopped. This does not require suspend/resume, a dead lock PID, a separate
network namespace, direct unlink by ISD, or death of Xwayland.

The historical incident has no syscall trace or mid-startup inode snapshot;
its exact individual syscalls cannot be reconstructed from the retained logs.
The mechanism above is source-confirmed and experimentally reproduced with the
installed binary, and accounts for the retained evidence. The internal reason
Hyprland used X0_ as its other listener was not investigated and is not required
for this mechanism.

## Protection

`isdlib/display.py` dynamically chooses candidate display numbers over the normal
Xserver allocation domain, with a random starting point. Any existing XN, XN_,
`.XN-lock` or `.tXN-lock` (even stale/malformed/symlink) is skipped without
connecting to it or modifying it. Access errors fail closed.

Before exec, Xvfb's filesystem transport is disabled:

```
Xvfb :<dynamic candidate> -displayfd <fd> ... \
  -nolisten tcp -nolisten unix -listen local -nopn -auth <private file> -noreset
```

Only the abstract binding arbitrates concurrent servers. A collision cannot
replace an existing abstract binding; the failed child must exit before another
candidate is attempted. Startup is bounded to 16 attempts. Readiness must report
exactly the selected number. A second filesystem check rejects a pathname that
appeared during startup, but this is NOT the damage-prevention mechanism: even
that forced race cannot modify the foreign socket. It can briefly shadow its
display number with a different abstract endpoint until failed-startup cleanup;
uninterrupted new connections during such an external race are not promised.

There are no shared socket/lock writes or removals, manual stale-lock repairs,
fixed display reservations, new runtime dependencies, or TCP listeners.
Authorization is preloaded, clients keep normal `DISPLAY=:N`, and each process
still receives its private authority/runtime environment. Supervisor adoption,
PID/start-time/boot identity checks, pidfd signals and recovery semantics are
unchanged. This remains Linux graphical-session isolation, not protection from a
malicious same-UID process or arbitrary replacement of the installed Xvfb binary.

## Regression coverage

- Unit tests: transport flag ordering, skipping every filesystem marker,
  dangling symlinks, access errors, dynamic candidate wraparound.
- Namespace-only destructive reproduction against a real foreign X11 server.
- Forced pathname race: foreign socket/lock identities and contents survive.
- Forced abstract bind collision: safe retry to another candidate.
- Three rounds of three parallel create/use/stop calls while the foreign server
  remains connectable and retains its process and socket identities.
- Existing real integration checks, plus three further parallel lifecycle rounds,
  peer survival, no shared filesystem sockets/locks, and exactly one abstract
  listener in the Xvfb descriptor inventory. Wrong/missing/cross-environment
  cookies still fail and owned TCP listeners remain absent.
- Two supervisors forced onto the same candidate sequence; atomic abstract bind
  makes them select distinct displays without relying on random allocation.
- Sixteen forced abstract collisions exhaust retries; every failed child is
  reaped, blockers survive, and the foreign server remains unchanged/connectable.
- Framed readiness (including split writes), mismatched/oversized responses,
  and refusal to retry a child that closes the pipe but remains alive.
- Xlib and XCB authorized connections succeed; missing/cross-environment cookies
  fail for both. Direct import/invocation of the destructive test refuses to run
  without the verified namespace/mount gate.

The sandbox helper previously read only one readiness chunk: Xserver writes
the digits and newline separately, so the helper could close early and cause
EPIPE. That **test-helper** race was observed and fixed on November 9; the runtime
supervisor already assembled the complete response. The private `/tmp` bind is
now checked by device/inode, in addition to separate mount/network namespaces.

## Consumer copy verification

At investigation time the fix existed only in the working copy; subsequent
commit/publication was separately authorized. Consumers must use the allocation
fix commit or a descendant containing it, not the original HEAD above. A fresh
clone/checkout of that original revision does **not** include the protection.
Do not run its old live suite on the host. Do not copy only `isd`: the sibling
`isdlib/` files are the implementation. `--version`, protocol 1, Git HEAD and a
successful launch after reboot are not allocation-safety checks.

For a Slyx consumer:

1. Select the complete verified checkout explicitly, e.g. command-local
   `ISD_BIN=/home/aleksei/plums/isd/isd`, or its `start --isd` equivalent. Do not
   change the global environment. If a different local ISD branch is needed for
   native/Russian input, its maintainer must integrate and reverify the allocation
   protection there before using it; this investigation did not merge those
   separate features or change sibling worktrees.
2. Record the resolved path and hashes of `isd` **and** `isdlib/*.py`, along with
   working-tree status (including untracked files). A local manifest for this
   verification is retained with the evidence below. Prevent changes to that
   copy during a run. A self-reported policy is not a cryptographic attestation.
3. Before launching the app, require successful `create` to include
   `display_allocation: "linux-abstract-v1"`; retain it with `root` and `id`.
   Recheck via that exact CLI's `status`. Missing/unknown policy means stop the
   environment and refuse launch, never fall back to the desktop.
4. Updating files does not retrofit already-running supervisors. Stop old runs
   using their recorded executable/root/id (never by display number), retain
   their artifacts, and create new runs with the verified copy. Repeat the
   consumer's compatibility tests in isolation. The current Slyx wrapper does
   not enforce the new policy field automatically; its maintainer needs to add
   that pre-launch check and record the verified dependency when authorized.

The investigation did not build Slyx, edit its scenarios/tracker, or publish it.
ISD commit/publication was authorized separately after the investigation.
No claim is made about slyx-iv1.4 compatibility or historical responsibility.

See [verification.md](verification.md) for host-desktop preservation checks and
the retained local artifacts. Do not rerun the old allocator on a real desktop.
