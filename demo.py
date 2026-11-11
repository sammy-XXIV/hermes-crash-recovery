"""Reproducible crash-after-side-effect demo. No network or real bookings."""

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

from recovery import Journal, RecoveryGuard


def worker(directory):
    journal = Journal(directory / 'journal')
    guard = RecoveryGuard(lambda: journal)

    def book(args):
        with sqlite3.connect(directory / 'service.sqlite3') as db:
            db.execute('CREATE TABLE IF NOT EXISTS bookings (id INTEGER PRIMARY KEY, name TEXT)')
            db.execute('INSERT INTO bookings (name) VALUES (?)', (args['name'],))
        db.close()
        # Signal only after the external service transaction commits, then wait.
        # Parent force-kills this process; no exception or shutdown hook can finish the journal.
        (directory / 'effect-committed').write_text('one booking committed', encoding='utf-8')
        import time
        while True:
            time.sleep(1)

    guard.execute(tool_name='book_trip', args={'name': 'Ada'}, session_id='before-crash', next_call=book)


def demo(directory):
    import time
    directory.mkdir(parents=True, exist_ok=False)
    process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--worker', '--directory', str(directory)])
    try:
        deadline = time.monotonic() + 15
        while not (directory / 'effect-committed').exists():
            if process.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError('Demo worker failed before committing the booking')
            time.sleep(0.02)
        process.kill()
        process.wait(timeout=5)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
    # A separate process also performs the restarted attempt through the same guard.
    attempt = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--retry', '--directory', str(directory)],
                             check=True, capture_output=True, text=True, timeout=10)
    refusal = json.loads(attempt.stdout)
    journal = Journal(directory / 'journal')
    before = journal.status()
    with sqlite3.connect(directory / 'service.sqlite3') as db:
        count = db.execute('SELECT count(*) FROM bookings').fetchone()[0]
    db.close()
    if count != 1 or not refusal.get('blocked') or len(before['unresolved']) != 1:
        raise AssertionError('Crash recovery failed to prevent a repeat')
    operation = before['unresolved'][0]['operation_id']
    journal.resolve(operation, 'completed', 'Independent service database inspection found exactly one booking for Ada.')
    after = journal.status()
    report = {'scenario': 'service commits -> worker force-killed -> new process attempts repeat -> operator reconciles',
              'worker_exit_code': process.returncode, 'bookings': count, 'repeat_blocked': refusal['blocked'],
              'operation_id': operation, 'integrity_ok': after['integrity_ok'],
              'unresolved_after_resolution': len(after['unresolved']), 'directory': str(directory)}
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path)
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--retry', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        worker(args.directory)
    elif args.retry:
        def duplicate(_):
            with sqlite3.connect(args.directory / 'service.sqlite3') as db:
                db.execute("INSERT INTO bookings (name) VALUES ('Ada duplicate')")
            db.close()
            return 'booked twice'
        guard = RecoveryGuard(lambda: Journal(args.directory / 'journal'))
        print(guard.execute(tool_name='book_trip', args={'name': 'Ada again'}, session_id='after-crash', next_call=duplicate))
    elif args.directory:
        demo(args.directory)
    else:
        with tempfile.TemporaryDirectory(prefix='hermes-crash-demo-') as temp:
            demo(Path(temp) / 'run')
