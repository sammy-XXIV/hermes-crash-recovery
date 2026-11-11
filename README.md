# Hermes Crash Recovery

**An action happened. Hermes crashed before recording the answer. What happens when it tries again?**

Crash Recovery keeps a durable, hash-chained tool-call journal and refuses protected actions while any previous action has an unresolved outcome. A restart, new session, different arguments, or a different tool does not clear the recovery gate.

Native Hermes plugin. Python standard library only. No hosted service, API key, model calls, or telemetry.

## Try the crash demo

Python 3.11 or newer:

```console
python demo.py
```

The demo starts a worker process, commits a fictional booking to a separate service database, then **force-kills the worker before its tool call returns**. A new process attempts another booking with changed arguments. Recovery blocks it. An independent database query finds exactly one booking, and an operator reconciliation closes the pending journal record.

Expected report:

```json
{
  "bookings": 1,
  "repeat_blocked": true,
  "integrity_ok": true,
  "unresolved_after_resolution": 0
}
```

To retain the journal and fictional service database, use a directory that does not already exist:

```console
python demo.py --directory demo-run
```

This deterministic demo exercises the guard directly. It does not involve a model, a real booking service, or an installed Hermes session. Native Hermes integration is checked separately below.

## Install locally

Requires Hermes 0.23.1 or newer. Copy this repository into the active profile's `plugins/crash-recovery` directory, then enable it:

```console
hermes plugins enable crash-recovery
hermes plugins doctor crash-recovery --ci
```

For the default profile on PowerShell, from the directory containing this repository:

```powershell
New-Item -ItemType Directory -Force "$env:USERPROFILE\.hermes\plugins" | Out-Null
Copy-Item -LiteralPath .\hermes-crash-recovery -Destination "$env:USERPROFILE\.hermes\plugins\crash-recovery" -Recurse
hermes plugins enable crash-recovery
hermes plugins doctor crash-recovery --ci
```

Use the chosen profile's Hermes home when running with profiles or a custom `HERMES_HOME`. Restart Hermes after enabling. Confirm both inspection tools are registered before relying on the guard. A disabled or unloaded plugin cannot protect execution.

You can also install the public repository directly while catalog review is pending:

```console
hermes plugins install sammy-XXIV/hermes-crash-recovery
hermes plugins enable crash-recovery
```

## How it works

1. `pre_tool_call` returns an early block directive when recovery is required.
2. `tool_execution` middleware atomically verifies integrity, checks for unresolved operations, and commits a `started` event before invoking the downstream callback exactly once.
3. A normal tool response adds a `returned` event. Exceptions, cancellations and recognized error responses preserve uncertainty.
4. On the next call, durable pending or uncertain records keep the profile-wide recovery gate closed. No session-start hook or clean shutdown is required.
5. An operator checks the actual service, stops any original worker that might still be running, and appends a reconciliation decision. The plugin never automatically retries an action.

The middleware explicitly returns an error on journal failures before execution. It does not rely on throwing exceptions: Hermes middleware failures can otherwise continue execution. If journaling fails after the downstream call, the guard reports an unknown outcome and refuses further protected calls in that process.

Hermes versions can place their pre-tool hook inside the execution middleware. A context-local admission permits only the current call to pass its own hook; matching tool-call IDs in another execution context do not bypass the gate. The journal records entry into the protected execution pipeline, which may still include approval or guardrail checks. A rejected call can conservatively remain uncertain and require operator reconciliation even though no external action occurred.

## Inspect and reconcile

Hermes gets two read-only tools:

- `crash_recovery_status`: unresolved operation IDs, tool names, timestamps and integrity status.
- `crash_recovery_verify`: hash-chain verification, optionally against a separately retained checkpoint.

There is **no model-facing resolve or replay tool**. Run the operator CLI yourself, with the active profile's data directory:

```console
python operator_cli.py --data-dir PATH/TO/HERMES_HOME/plugin-data/crash-recovery status
python operator_cli.py --data-dir PATH/TO/HERMES_HOME/plugin-data/crash-recovery verify
python operator_cli.py --data-dir PATH/TO/HERMES_HOME/plugin-data/crash-recovery resolve OPERATION_ID --outcome completed --evidence "Checked service receipt 123 after stopping the original worker"
```

Use `--outcome not_applied` only when independent evidence establishes the action did not happen. It releases the gate for a subsequent normal tool call; it does not replay anything. Evidence is saved verbatim: keep secrets out of it.

A timeout or dead process does not establish that the action failed. When the outcome cannot be established, leave the operation unresolved. Stop all workers using this profile before reconciling; a process can otherwise still be executing the original action.

## Read-only tools and concurrent work

The default exempt tools are `read_file`, `search_files`, and the two inspection tools. Unknown tools are protected. An operator may add exact, trusted read-only tool names under `config.yaml`:

```yaml
plugins:
  entries:
    crash-recovery:
      settings:
        read_only_tools:
          - read_file
          - search_files
          - lookup_receipt
```

The configured list replaces the defaults; the two inspection tools remain available. An exempt tool bypasses journaling entirely. Do not exempt a shell, interpreter, delegation tool or other write-capable tool.

V1 admits **one protected call at a time per profile**. A second concurrent call receives a refusal while the first is pending. Calls that spawn nested protected tools, including delegation/code-execution workflows, can therefore be blocked inside the outer call. Use direct tools for protected actions. Tools with asynchronous side effects are recorded only through their immediate response, not through the entire background job.

## Integrity and storage

State stays under `HERMES_HOME/plugin-data/crash-recovery/`, outside the install directory:

- `journal.sqlite3` and SQLite WAL files: append-only event history.
- `head.json`: atomically replaced local checkpoint.
- `initialized`: detects a missing database in an already initialized data directory.

Events contain operation/session/tool-call IDs, tool names, timestamps, argument fingerprints, result hashes and reconciliation decisions. Raw tool arguments, raw results and exception messages are not persisted. Hashes of predictable inputs can still be guessed; this is not encryption. IDs and operator evidence are stored as supplied.

Each event commits to its canonical payload and previous event hash. The separately stored checkpoint detects journal truncation and rollback. It is synced before the SQLite event commits, while holding the database writer lock: a crash in that narrow interval can leave the checkpoint ahead of the database. **This deliberately blocks recovery instead of guessing.** Restore a consistent backup or have an operator investigate; there is no automatic integrity reset.

Export a checkpoint to storage independent of this data directory for stronger verification:

```console
python operator_cli.py --data-dir DATA_DIRECTORY checkpoint > independent-checkpoint.json
python operator_cli.py --data-dir DATA_DIRECTORY verify --checkpoint independent-checkpoint.json
```

The local checkpoint is not a cryptographic trust anchor against an attacker who can rewrite both files. Complete replacement or deletion of all local state is not detectable without independently retained evidence. The plugin is a recovery guard, not a sandbox against an agent or administrator with arbitrary filesystem access. Keep independent checkpoints and consistent backups outside the agent's authority when that threat matters.

`returned` proves that the tool returned a response; it does not prove an external business operation completed. Recognized JSON error fields (`error`, `isError`, `success: false`, `ok: false`, error statuses and nonzero `exit_code`) keep an operation uncertain. Unusual errors encoded as free text may require manual investigation. No generic journal can provide exactly-once external execution without cooperation from the service.

V1 verifies the full history on admission and completion. Work is linear in journal length and records are never automatically pruned. Use local storage with reliable SQLite locking; network filesystems and power-loss durability are not certified.

## Verification

```console
python -m unittest discover -s tests -v
hermes plugins doctor . --ci
hermes plugins validate . --install-deps
python tests/check_host.py --hermes-root PATH/TO/hermes-agent
```

The last command uses an unmodified Hermes checkout, a temporary profile, real plugin discovery, the real middleware dispatcher, the block hook, registered inspection tools, and the operator CLI. It checks that changing sessions/arguments and losing storage cannot bypass the guard. It does not make live model requests.

For the full native CLI/gateway crash-and-restart scenario:

```console
python tests/native_demo.py --hermes-root PATH/TO/hermes-agent --directory PATH/TO/NEW_DEMO_DIRECTORY
```

Run this with an interpreter containing Hermes's runtime dependencies. It starts the unmodified `hermes chat` CLI against a scripted, loopback-only OpenAI-compatible provider and a fictional local booking plugin. After the service commits a booking, it force-kills the isolated gateway, starts a new CLI session, verifies refusal of the changed-argument repeat, performs read-only inspection, stops the demo gateway and records operator reconciliation. The scripted provider deliberately requests the unsafe repeat; no live model or paid service is involved. The output directory retains JSONL session traces and the result report.

## Disclosures

The plugin performs local SQLite and checkpoint I/O inside its own data directory. It observes tool names, arguments, results and correlation IDs in memory to calculate hashes. It does not call a network service, run shell commands, read credential stores, modify Hermes core, or start background processes. The optional demo/test scripts spawn disposable local Python workers and use selected demonstration directories and databases; the demos deliberately force-kill their own workers. The native test also opens a temporary loopback HTTP provider and starts/stops an isolated Hermes CLI/gateway using its normal approval settings. CLI evidence is stored verbatim. Uninstalling or updating the plugin leaves its data intact. Disabling/removing the plugin also removes its protection.

License: MIT.
