# Native Hermes CLI crash/restart evidence

This is a real CLI/gateway run against an isolated Hermes profile on Windows. The OpenAI-compatible provider was a scripted localhost test server, and the booking service was fictional local SQLite storage. No live model, external booking, paid API or core patch was involved.

- `report.json`: independently checked scenario result.
- `crash-session.jsonl`: native CLI starts `demo_book`, then reports gateway disconnection when the harness force-kills the gateway after the service commits.
- `pending-after-crash.json`: intact journal with the first operation pending.
- `restart-session.jsonl`: a different CLI session receives a replay refusal, inspects one booking, and reads recovery status.
- `provider-observations.json`: compact scripted-provider decisions and the tool responses actually returned by Hermes.
- `resolved.json`: journal state after independent service inspection and operator reconciliation.

These selected machine-readable traces are retained unchanged. Full profile data, local user identifiers in diagnostic logs, and unrelated startup logs are not included. The fixture and harness are in `tests/native_demo.py` and `tests/fixtures/booking_plugin/`.
