# Hermes Crash Recovery — v1 design

Purpose: prevent a Hermes restart from silently repeating an action whose outcome is unknown. This is a native Python plugin, local-only, using supported Hermes extension APIs.

## Approach

Use `tool_execution` middleware for the durable execution boundary. Commit a started event before calling `next_call(args)` exactly once. Commit a returned event afterward. Catch plugin storage and verification errors explicitly and return a blocked tool result without invoking the downstream call. Hermes middleware exceptions are fail-open, so raising an exception is not an enforcement mechanism.

A complementary `pre_tool_call` hook provides block messages. It does not create execution records. The tested native CLI places this hook inside the execution middleware; a context-local admission identifies only the current operation so it does not block itself. Independent calls with matching IDs still block. Approval/guardrail checks may remain inside the execution pipeline, so a started record is conservative intent, not proof the actual handler ran. Restart discovery is based on durable incomplete execution records, not session-start events alone.

## Journal and integrity

Store events in profile-local SQLite in the documented plugin data directory. Use synchronous FULL transactions, a hash chain over canonical event fields and previous hashes, a separately synced local head checkpoint, and atomic admission under a write transaction. Never hold a database write lock while executing a tool. Updating the checkpoint before the SQLite commit can leave it ahead after a crash; treat that as an integrity block, never silently reset it.

Record operation ID, session and tool-call IDs, tool name, canonical argument digest, timestamps, state, and result digest. Do not persist raw arguments or raw results by default. Verify the chain before admitting protected work. On detected damage or journal access failure, refuse protected calls and report the reason.

Hash chaining detects changes relative to a trusted chain head. It cannot establish authenticity against an attacker who can rewrite the complete journal and local checkpoint, or detect deletion of all local state. An operator-exported checkpoint supports verification against an independently retained head. These limits must be explicit in the README and demo.

## Protection and recovery

Default to protecting tools except an explicit, small read-only allowlist and plugin inspection tools. Unknown tools remain protected. A durable incomplete operation, exception, cancellation, or recognized tool error is an unknown outcome; never label it failed solely because execution did not return cleanly. A normal return records a tool response, not proof of the external business outcome.

Use a profile-wide recovery gate: unresolved operations block protected actions across sessions and argument variations, while configured read-only inspection remains available. This avoids claiming that exact argument fingerprints catch equivalent commands. V1 serializes all protected calls: concurrent admission is refused while any operation is pending. Nested protected tools may therefore also be refused; direct tools are the v1 execution surface. A suspected stale operation remains unresolved until reconciled; do not guess based on PID age.

Expose read-only status, operation inspection and integrity verification to Hermes. Expose resolution through an operator CLI, not a freely callable model tool. The operator records evidence and chooses confirmed-completed or confirmed-not-applied. Resolution appends an event, never rewrites history. Releasing a not-applied operation permits a future normal tool call; the plugin never automatically replays it. Intentional repetition after a known completed call remains possible.

## Proof and scope

Provide a deterministic fictional booking demo in separate processes: write one booking, terminate the worker before recording its tool response, restart, attempt a repeat, observe refusal and exactly one booking, inspect evidence, resolve, verify the journal. No real charges or messages.

Acceptance tests cover process termination and restart, concurrent duplicate admission, cross-session and changed-argument blocking, corrupted records, exported checkpoint mismatch, storage failures, error results, exception/cancellation uncertainty, operator-only resolution, and legitimate repeated completed actions. Test registration and dispatch with the actual Hermes loader; run plugin doctor and catalog validation if the runtime supports this Windows host. Clearly label any unavailable host validation.

Deliver manifest, plugin implementation, tests, demo, README with installation and limitations, catalog entry template, and a demo script under 90 seconds. No automatic publication, catalog PR, social posting, or modification of the user's active Hermes profile.

## Alternatives considered

Observer-only journaling is simpler but leaves an approval-to-execution ambiguity and provides warnings without a durable enforcement boundary. Tool-specific wrappers can reconcile outcomes more precisely but cover only those tools. Native execution middleware plus explicit operator reconciliation is the recommended v1; service-specific reconciliation can follow later.
