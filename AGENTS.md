# Maintaining ISD

- ISD is a reusable Linux/Xvfb CLI, not an engine adapter or test-scenario framework.
- Keep project builds, renderer/audio choices, readiness, save isolation, and
  game-specific actions outside the core. Do not modify sibling repositories.
- Public wrapper API: `docs/interface.md`. Preserve protocol-v1 field meanings;
  add optional fields rather than repurposing existing ones.
- Never use the main desktop for tests or helpers. Always supply the isolated
  display, authority, and private runtime environment. Captures must not change
  focus/pointer/input. No `-ac`, TCP X11, fixed display allocation, `eval`,
  `shell=True`, `pkill`, or `killall`.
- Signals must use verified PID/start-time/boot identities and pidfds. Preserve
  subreaper adoption, peer checks, private permissions, and stale-state checks.
- Never persist full environments. Treat app logs as potentially sensitive.
- Stop and artifact removal are separate. Do not promise complete SIGKILL
  cleanup; recorded-identity recovery has explicit limits.
- Before publishing: `python3 -m unittest discover -s tests -v`. Retain live
  artifacts, inspect the emitted PNGs, and verify no owned processes remain.
  No package installation, global configuration, or machine service changes.
- Use only the existing Python standard-library/Pillow/X11 dependencies unless
  a dependency change is approved. The C file is a test fixture, not the runtime.
- Repository publication is authorized for this initial delivery. Future commit
  or publication requests still follow the user's current instructions.
