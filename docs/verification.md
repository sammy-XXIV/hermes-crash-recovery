# Verification evidence

Verified on Windows on 2026-11-11 against unmodified Hermes source commit `f19432adb7ac4aa2920797a7d092d3987012d4b2`.

| Check | Observed result |
|---|---|
| Core regression tests, Python 3.11 | 21 passed |
| Core regression tests, Python 3.13.14 | 21 passed |
| Force-kill / restart booking scenario | One booking, repeat blocked, integrity valid, operator reconciliation recorded |
| Full native Hermes CLI/gateway kill and fresh CLI restart | Passed: actual tool dispatch, persisted pending call, changed-argument repeat refused, one booking, read-only inspection and operator reconciliation |
| Native Hermes plugin loader and middleware dispatcher | Passed in a disposable profile |
| Native pre-tool block hook | Passed |
| Registered inspection tools | Passed |
| Read-only lookup during recovery | Passed |
| Native dispatch after storage loss | Blocked without invoking downstream callback |
| Hermes Plugin Doctor | Passed: two tools, one hook |
| Hermes catalog validator | Passed; security scan safe; no warnings |

Reproduce core tests and the crash scenario:

```console
python -m unittest discover -s tests -v
python demo.py
```

With a Hermes source checkout and its validation dependencies available to the chosen Python interpreter:

```console
python tests/check_host.py --hermes-root PATH/TO/hermes-agent
python tests/verify_host.py --hermes-root PATH/TO/hermes-agent --output docs/host-verification.json
```

The host test uses the actual loader, tool registry, middleware chain and hook dispatch. Its tools' terminal callbacks are controlled local test functions, not a live model. It invokes the real operator CLI in a separate process. The force-kill test separately uses a real worker process and a second service database.

The full CLI demonstration additionally ran the unmodified `hermes chat --format stream-json` command against an isolated profile and a scripted localhost provider. A real demo tool committed a booking and waited before returning. The harness force-killed the profile's gateway, stopped the transport client, and launched a fresh CLI/gateway session. Hermes returned `crash_recovery_blocked` for the scripted provider's next booking request, allowed `demo_inspect` to observe exactly one booking, and allowed `crash_recovery_status` to show the original pending action. After both workers stopped, the operator CLI recorded independent service evidence. Selected raw JSONL traces and result data are in [native-demo/](native-demo/).

Reproduce the native CLI demonstration with Hermes's runtime dependencies installed:

```console
python tests/native_demo.py --hermes-root PATH/TO/hermes-agent --directory PATH/TO/NEW_DEMO_DIRECTORY
```

The isolated profile uses normal approval checks and direct plugin tool definitions (`tools.tool_search.enabled: off`). Hermes core and SDK objects were not patched. The provider is a deterministic local test server, not a live model, and its text is not accepted as verification: the harness separately checks native tool results and the service database.

The final read-only review found two integrity issues; both were reproduced and fixed with regression coverage: accepting an unanchored database extension, and recreating a missing database despite a surviving checkpoint. These tests now require exact local-head equality and preservation of the retained checkpoint.

The full CLI run exposed another integration issue: the native execution middleware encloses the pre-tool hook on the tested core. A context-local admission now allows only the active operation through its own hook; identical IDs from another execution context do not bypass recovery. Both cases have regression tests, and a focused independent review found no further important issue.

No live model or real external booking service was tested. Linux/macOS and Python 3.14 were not run locally; GitHub Actions covers Ubuntu/Windows and Python 3.11/3.13/3.14. Publication and catalog review are separate from the recorded local validation.
