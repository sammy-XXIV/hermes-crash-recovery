"""Optional integration test against an unmodified Hermes loader and middleware dispatcher."""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def check(host_root):
    sys.path.insert(0, str(host_root.resolve()))
    with tempfile.TemporaryDirectory(prefix='crash-recovery-host-') as scratch:
        home = Path(scratch)
        os.environ['HERMES_HOME'] = str(home)
        os.environ['HERMES_BUNDLED_PLUGINS'] = str(home / 'empty-bundled')
        os.environ['HERMES_ENABLE_PROJECT_PLUGINS'] = '0'
        (home / 'empty-bundled').mkdir()
        plugin = home / 'plugins' / 'crash-recovery'
        plugin.mkdir(parents=True)
        for name in ['plugin.yaml', '__init__.py', 'recovery.py']:
            shutil.copy2(ROOT / name, plugin / name)
        (home / 'config.yaml').write_text(json.dumps({'plugins': {'enabled': ['crash-recovery'],
            'entries': {'crash-recovery': {'settings': {'read_only_tools': ['lookup_receipt']}}}}}), encoding='utf-8')

        from hermes_cli.plugins import get_plugin_manager
        from hermes_cli.middleware import run_tool_execution_middleware
        from tools.registry import registry

        manager = get_plugin_manager()
        try:
            manager.discover_and_load()
            if not manager.has_middleware('tool_execution'):
                raise AssertionError('Plugin execution middleware was not loaded')
            for name in ['crash_recovery_status', 'crash_recovery_verify']:
                if registry.get_entry(name) is None:
                    raise AssertionError(f'Tool not registered: {name}')
            calls = []
            def effect(args):
                calls.append(args)
                return json.dumps({'receipt': 'receipt-1'})
            first = run_tool_execution_middleware('send_message', {'text': 'hello'}, effect,
                                                  session_id='original', tool_call_id='c1')
            assert json.loads(first)['receipt'] == 'receipt-1'
            def uncertain(args):
                calls.append(args)
                return json.dumps({'error': 'acknowledgement lost after applying action'})
            run_tool_execution_middleware('send_message', {'text': 'again'}, uncertain,
                                           session_id='original', tool_call_id='c2')
            repeated = json.loads(run_tool_execution_middleware('terminal', {'command': 'try another way'}, effect,
                                                               session_id='new-session', tool_call_id='c3'))
            assert repeated['blocked'] and len(calls) == 2
            directives = manager.invoke_hook('pre_tool_call', tool_name='send_message', args={}, session_id='new-session')
            assert any(isinstance(d, dict) and d.get('action') == 'block' for d in directives)
            read = run_tool_execution_middleware('lookup_receipt', {}, lambda _: 'receipt-1')
            assert read == 'receipt-1'
            status_handler = registry.get_entry('crash_recovery_status').handler
            status = json.loads(status_handler({}))
            assert status['integrity_ok'] and len(status['unresolved']) == 1
            operator = subprocess.run([sys.executable, str(ROOT / 'operator_cli.py'), '--data-dir',
                str(home / 'plugin-data' / 'crash-recovery'), 'resolve', status['unresolved'][0]['operation_id'],
                '--outcome', 'completed', '--evidence', 'Operator verified receipt-1 after stopping the original worker.'],
                capture_output=True, text=True, timeout=30)
            assert operator.returncode == 0, operator.stderr
            assert json.loads(status_handler({}))['unresolved'] == []
            run_tool_execution_middleware('send_message', {'text': 'new intentional message'}, effect,
                                           session_id='new-session', tool_call_id='c4')
            assert len(calls) == 3
            # Remove live storage: the callback must return a refusal, rather than raise
            # and let Hermes's fail-open middleware invoke the downstream action.
            (home / 'plugin-data' / 'crash-recovery' / 'journal.sqlite3').unlink()
            denied = json.loads(run_tool_execution_middleware('send_message', {}, effect))
            assert denied['blocked'] and len(calls) == 3
            report = {'ok': True, 'native_loader': True, 'native_middleware': True, 'native_block_hook': True,
                      'inspection_tools': True, 'operator_resolution': True, 'storage_failure_blocks': True,
                      'changed_argument_and_session_repeat_blocked': True, 'read_only_lookup_available': True,
                      'hermes_commit': subprocess.check_output(['git', '-C', str(host_root), 'rev-parse', 'HEAD'], text=True).strip()}
            print(json.dumps(report, indent=2))
        finally:
            manager.unload()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hermes-root', required=True, type=Path)
    check(parser.parse_args().hermes_root)
