"""Full Hermes CLI crash/restart proof using a transparent, scripted localhost provider.

No live model or real booking service is used. The Hermes CLI, session persistence,
plugin loader, tool dispatcher, guard, and killed/restarted processes are real.
"""

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ScriptedProvider(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.reply({'object': 'list', 'data': [{'id': 'crash-recovery-demo', 'object': 'model', 'owned_by': 'local-test'}]})

    def reply(self, body):
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        offered = {tool.get('function', {}).get('name') for tool in body.get('tools', [])}
        messages = body.get('messages', [])
        tool_messages = [message for message in messages if message.get('role') == 'tool']
        scenario = self.server.scenario
        call = None
        content = None
        if 'demo_book' not in offered:
            content = 'Crash Recovery Demonstration'
        elif not tool_messages:
            call = ('demo_book', {'name': 'Ada' if scenario == 'crash' else 'Ada changed arguments'})
        elif scenario == 'restart' and len(tool_messages) == 1:
            call = ('demo_inspect', {})
        elif scenario == 'restart' and len(tool_messages) == 2:
            call = ('crash_recovery_status', {})
        else:
            content = 'Recovery blocked the uncertain repeat. Read-only inspection confirms exactly one booking. An operator must reconcile the original action.'
        observed = {'scenario': scenario, 'offered_tools': sorted(name for name in offered if name),
                    'tool_results': [{'tool_call_id': m.get('tool_call_id'), 'content': m.get('content')} for m in tool_messages],
                    'assistant_tool': call[0] if call else None, 'assistant_text': content}
        self.server.observed.append(observed)
        function_call = None
        if call:
            function_call = {'id': f'demo-{scenario}-{len(tool_messages)}', 'type': 'function',
                             'function': {'name': call[0], 'arguments': json.dumps(call[1])}}
        message = {'role': 'assistant', 'content': content}
        if function_call:
            message['tool_calls'] = [function_call]
        finish = 'tool_calls' if call else 'stop'
        base = {'id': f'chatcmpl-demo-{len(self.server.observed)}', 'created': int(time.time()), 'model': 'crash-recovery-demo'}
        if not body.get('stream'):
            self.reply(dict(base, object='chat.completion', choices=[{'index': 0, 'message': message, 'finish_reason': finish}],
                            usage={'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}))
            return
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        delta = {'role': 'assistant'}
        if function_call:
            delta['tool_calls'] = [dict(function_call, index=0)]
        else:
            delta['content'] = content
        for value, finish_reason in [(delta, None), ({}, finish)]:
            chunk = dict(base, object='chat.completion.chunk', choices=[{'index': 0, 'delta': value, 'finish_reason': finish_reason}])
            self.wfile.write(('data: ' + json.dumps(chunk) + '\n\n').encode())
        self.wfile.write(b'data: [DONE]\n\n')
        self.wfile.flush()


def stop_owned_process(process):
    if process.poll() is not None:
        return
    if os.name == 'nt':
        # Windows venv redirectors can have an interpreter child: kill our tree,
        # not just the launcher, to ensure the executing tool is actually stopped.
        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    else:
        process.kill()
    process.wait(timeout=10)


def stop_demo_gateway(home):
    # Modern Hermes CLI launches a gateway that can outlive its transport client.
    # Check the process's actual home before stopping the PID recorded by our profile.
    import psutil
    pid_file = home / 'gateway.pid'
    if not pid_file.exists():
        return
    record = json.loads(pid_file.read_text(encoding='utf-8'))
    pid = int(record['pid'])
    try:
        gateway = psutil.Process(pid)
        actual_home = gateway.environ().get('HERMES_HOME', '')
        if Path(actual_home).resolve() != home.resolve() or 'gateway' not in gateway.cmdline():
            raise RuntimeError('Refusing to stop a process that does not belong to this demo profile')
        if os.name == 'nt':
            subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'], check=False,
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        else:
            gateway.kill()
        gateway.wait(timeout=10)
    except psutil.NoSuchProcess:
        pass


def run_demo(host, directory):
    directory.mkdir(parents=True, exist_ok=False)
    home = directory / 'home'
    recovery = home / 'plugins' / 'crash-recovery'
    recovery.mkdir(parents=True)
    for name in ['__init__.py', 'recovery.py', 'plugin.yaml']:
        shutil.copy2(ROOT / name, recovery / name)
    shutil.copytree(ROOT / 'tests' / 'fixtures' / 'booking_plugin', home / 'plugins' / 'recovery-demo',
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    (directory / 'empty-bundled').mkdir()
    server = ThreadingHTTPServer(('127.0.0.1', 0), ScriptedProvider)
    server.scenario = 'crash'
    server.observed = []
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f'http://127.0.0.1:{server.server_port}/v1'
    config = {'_config_version': 53, 'model': {'provider': 'custom', 'default': 'crash-recovery-demo', 'base_url': url, 'context_length': 131072},
              'plugins': {'enabled': ['crash-recovery', 'recovery-demo'], 'entries': {
                  'crash-recovery': {'settings': {'read_only_tools': ['demo_inspect']}},
                  'recovery-demo': {'settings': {'service_directory': str(directory)}}}},
              'memory': {'enabled': False}, 'agent': {'max_turns': 5}, 'display': {'interface': 'cli'},
              'tools': {'tool_search': {'enabled': 'off'}},
              'session_title': {'enabled': False}}
    (home / 'config.yaml').write_text(json.dumps(config), encoding='utf-8')
    env = dict(os.environ, HERMES_HOME=str(home), HERMES_BUNDLED_PLUGINS=str(directory / 'empty-bundled'),
               HERMES_ENABLE_PROJECT_PLUGINS='0', PYTHONIOENCODING='utf-8', PYTHONPATH=str(host.resolve()))
    command = [sys.executable, '-m', 'hermes_cli.main', 'chat', '--query', 'Create one fictional booking for Ada using demo_book.',
               '--provider', 'custom', '--model', 'crash-recovery-demo', '--base-url', url, '--api-key', 'local-test-only',
               '--toolsets', 'recovery-demo,crash-recovery', '--max-turns', '5', '--ignore-rules', '--format', 'stream-json']
    process = None
    try:
        with (directory / 'crash-session.jsonl').open('w', encoding='utf-8') as stdout, (directory / 'crash-stderr.txt').open('w', encoding='utf-8') as stderr:
            process = subprocess.Popen(command, cwd=host, env=env, stdout=stdout, stderr=stderr)
            deadline = time.monotonic() + 120
            while not (directory / 'effect-committed').exists():
                if process.poll() is not None:
                    raise RuntimeError(f'Hermes exited before the booking committed; inspect {directory / "crash-stderr.txt"}')
                if time.monotonic() > deadline:
                    raise TimeoutError(f'Hermes did not reach demo_book; inspect {directory}')
                time.sleep(0.1)
            stop_demo_gateway(home)
            stop_owned_process(process)
        sys.path.insert(0, str(ROOT))
        from recovery import Journal
        journal = Journal(home / 'plugin-data' / 'crash-recovery')
        before = journal.status()
        if not before['integrity_ok'] or len(before['unresolved']) != 1:
            raise AssertionError('Native crash did not leave exactly one unresolved operation')
        (directory / 'pending-after-crash.json').write_text(json.dumps(before, indent=2), encoding='utf-8')
        server.scenario = 'restart'
        command[command.index('--query') + 1] = 'Try booking for Ada again, then inspect the booking service and recovery status.'
        with (directory / 'restart-session.jsonl').open('w', encoding='utf-8') as stdout, (directory / 'restart-stderr.txt').open('w', encoding='utf-8') as stderr:
            process = subprocess.Popen(command, cwd=host, env=env, stdout=stdout, stderr=stderr)
            try:
                process.wait(timeout=120)
            except BaseException:
                stop_owned_process(process)
                raise
        if process.returncode:
            raise RuntimeError(f'Restarted Hermes exited {process.returncode}; inspect {directory / "restart-stderr.txt"}')
        stop_demo_gateway(home)
        with closing(sqlite3.connect(directory / 'service.sqlite3')) as db:
            bookings = db.execute('SELECT count(*) FROM bookings').fetchone()[0]
        restart_results = [m['content'] for row in server.observed if row['scenario'] == 'restart' for m in row['tool_results']]
        repeat_blocked = any('crash_recovery_blocked' in str(result) for result in restart_results)
        inspected = any('booking_count' in str(result) for result in restart_results)
        if not repeat_blocked or not inspected or bookings != 1:
            raise AssertionError('Native replay refusal or read-only reconciliation was not observed')
        operation = before['unresolved'][0]['operation_id']
        resolution = subprocess.run([sys.executable, str(ROOT / 'operator_cli.py'), '--data-dir', str(journal.directory),
            'resolve', operation, '--outcome', 'completed', '--evidence', 'Both native Hermes workers have exited; independent service DB query found one committed booking.'],
            check=True, capture_output=True, text=True, timeout=30)
        after = json.loads(resolution.stdout)
        (directory / 'resolved.json').write_text(resolution.stdout, encoding='utf-8')
        report = {'ok': True, 'driver': 'native Hermes CLI chat with a scripted localhost OpenAI-compatible provider',
                  'live_model': False, 'core_patched': False, 'native_cli_crash_and_restart': True,
                  'bookings': bookings, 'repeat_blocked': repeat_blocked, 'read_only_inspection': inspected,
                  'integrity_ok': after['integrity_ok'], 'unresolved_after_resolution': len(after['unresolved']),
                  'hermes_commit': subprocess.check_output(['git', '-C', str(host), 'rev-parse', 'HEAD'], text=True).strip()}
        (directory / 'report.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(report, indent=2))
    finally:
        if process is not None:
            stop_owned_process(process)
        stop_demo_gateway(home)
        (directory / 'provider-observations.json').write_text(json.dumps(server.observed, indent=2), encoding='utf-8')
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hermes-root', required=True, type=Path)
    parser.add_argument('--directory', required=True, type=Path, help='A new output directory for the isolated profile and evidence')
    parsed = parser.parse_args()
    run_demo(parsed.hermes_root, parsed.directory)
