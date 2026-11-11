# Catalog submission preparation

After publishing the repository, add `plugin-catalog/crash-recovery.yaml` in a PR to `NousResearch/hermes-agent` using the final public repository URL, your GitHub handle, and the complete 40-character commit SHA. Do not submit an unpinned entry or claim acceptance before maintainer review.

```yaml
name: crash-recovery
version: "0.1.0"
repo: https://github.com/sammy-XXIV/hermes-crash-recovery
sha: YOUR_FULL_40_CHARACTER_COMMIT_SHA
description: "Durable tool-call journal that blocks protected actions while previous outcomes are unknown. Disclosure — stores tool/session metadata, argument and result hashes, local integrity checkpoints and operator evidence in the active profile's plugin-data directory. Serializes protected calls per profile. No network, shell execution or background tasks in the plugin; optional demo/tests spawn disposable local workers."
maintainer: sammy-XXIV
tier: community
category: tools
requires_hermes: ">=0.23.1"
platforms: [linux, macos, windows]
capabilities:
  provides_tools: [crash_recovery_status, crash_recovery_verify]
  provides_hooks: [pre_tool_call]
  provides_middleware: [tool_execution]
  requires_env: []
```

Suggested PR description: “Adds Crash Recovery, a local tool-execution journal and operator reconciliation gate. Pending or uncertain operations block protected actions across sessions and tools. Uses documented middleware, the block hook and per-plugin storage. No runtime core overrides. See README for tamper-evidence limits, serialized execution, the default read-only exemptions and local data disclosures. Validation evidence and reproduction commands are in docs/verification.md.”
