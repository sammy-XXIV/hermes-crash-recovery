# Demo script — approximately 70 seconds

0–10s: “Agents can repeat expensive actions after a crash because they never received the answer. Crash Recovery records the action before it runs.” Show the enabled plugin and its two inspection tools in Hermes.

10–30s: Run `python demo.py --directory demo-run-1` from a terminal. Explain that the fictional service commits one booking, then the parent force-kills the worker before its tool response can be recorded. The restarted worker uses changed arguments and a new session.

30–45s: Show `bookings: 1` and `repeat_blocked: true`. “The guard refuses another write. It carries the uncertain outcome across restarts, sessions and tools.”

45–60s: Show `integrity_ok: true` and `unresolved_after_resolution: 0`. “An operator independently inspects the service, then records evidence that the original booking completed. Recovery never automatically repeats it.”

60–70s: “The journal is hash-chained with a separate local checkpoint. It detects changes and truncation; independent exported checkpoints strengthen verification. No API key or hosted service.”

For the native version, run `tests/native_demo.py` and show the saved CLI JSONL traces in `docs/native-demo/`: the first tool starts, the gateway disconnects, a new session refuses the repeat, and `demo_inspect` reports one booking. The native CLI/gateway run is recorded; the provider is scripted localhost tooling, not a live model. Label that distinction explicitly. No video has been recorded yet.
