"""Hermes Crash Recovery: supported plugin registration only."""

import json

from .recovery import Journal, RecoveryGuard


def register(ctx):
    # Resolve storage lazily so doctor/registration does not touch user state.
    from plugins.plugin_storage import plugin_data_dir

    def journal():
        return Journal(plugin_data_dir('crash-recovery'))

    read_only = ctx.get_config('read_only_tools', ['read_file', 'search_files'])
    if not isinstance(read_only, list) or any(not isinstance(name, str) or not name for name in read_only):
        raise ValueError('read_only_tools must be a list of exact, trusted read-only tool names')
    guard = RecoveryGuard(journal, read_only_tools=read_only)
    ctx.register_middleware('tool_execution', guard.execute)
    ctx.register_hook('pre_tool_call', guard.before)

    def status(args, **kwargs):
        try:
            return json.dumps(journal().status())
        except Exception as exc:
            return json.dumps({'integrity_ok': False, 'blocked': True, 'error': str(exc)})

    def verify(args, **kwargs):
        try:
            return json.dumps(dict(journal().verify(args.get('checkpoint')), integrity_ok=True))
        except Exception as exc:
            return json.dumps({'integrity_ok': False, 'error': str(exc)})

    for name, handler, description, properties in [
        ('crash_recovery_status', status, 'Inspect unresolved tool actions and journal integrity. Read-only; cannot resolve or replay actions.', {}),
        ('crash_recovery_verify', verify, 'Verify the tool journal hash chain, optionally against a separately retained checkpoint. Read-only.',
         {'checkpoint': {'type': 'object', 'description': 'An earlier checkpoint containing schema, journal_id, count and head.'}}),
    ]:
        ctx.register_tool(name=name, toolset='crash-recovery', handler=handler,
                          schema={'name': name, 'description': description,
                                  'parameters': {'type': 'object', 'properties': properties, 'additionalProperties': False}})
