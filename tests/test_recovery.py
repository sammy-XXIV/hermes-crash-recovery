import concurrent.futures
import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from recovery import Journal, RecoveryGuard, RecoveryError
except ImportError:
    Journal = RecoveryGuard = RecoveryError = None


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(Journal, 'The crash recovery implementation is missing')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.journal = Journal(self.root)
        self.guard = RecoveryGuard(lambda: self.journal)

    def call(self, fn, name='send_message', args=None, session='one'):
        return self.guard.execute(tool_name=name, args=args or {'message': 'hello'},
                                  session_id=session, tool_call_id='call-1', next_call=fn)

    def test_completed_calls_can_be_intentionally_repeated(self):
        calls = []
        for _ in range(2):
            self.assertEqual(self.call(lambda a: calls.append(a) or 'sent'), 'sent')
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.journal.status()['unresolved'], [])
        self.assertEqual(self.journal.verify()['count'], 4)

    def test_native_pre_hook_inside_execution_allows_only_its_admitted_call(self):
        calls = []
        def authorized_dispatch(args):
            decision = self.guard.before(tool_name='send_message', args=args, session_id='one', tool_call_id='call-1')
            if decision:
                return json.dumps({'error': decision['message']})
            other = self.guard.before(tool_name='charge', args={}, session_id='one', tool_call_id='other')
            self.assertEqual(other['action'], 'block')
            calls.append(args)
            return 'sent'
        self.assertEqual(self.call(authorized_dispatch), 'sent')
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.journal.status()['unresolved'], [])

    def test_matching_ids_from_another_execution_context_do_not_bypass_gate(self):
        def authorized_dispatch(args):
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                decision = pool.submit(self.guard.before, tool_name='send_message', args=args,
                                       session_id='one', tool_call_id='call-1').result()
            self.assertEqual(decision['action'], 'block')
            return 'sent'
        self.call(authorized_dispatch)

    def test_restart_blocks_changed_arguments_and_other_sessions(self):
        operation = self.journal.begin('send_message', {'message': 'hello'}, 'one', 'call-1')
        restarted = RecoveryGuard(lambda: Journal(self.root))
        calls = []
        result = json.loads(restarted.execute(tool_name='terminal', args={'command': 'send again'},
                                             session_id='new', next_call=lambda a: calls.append(a)))
        self.assertTrue(result['blocked'])
        self.assertEqual(calls, [])
        self.assertEqual(result['operation_ids'], [operation])

    def test_exception_and_error_result_preserve_uncertainty(self):
        def fails(_):
            raise TimeoutError('ack lost')
        with self.assertRaises(TimeoutError):
            self.call(fails)
        self.assertEqual(len(self.journal.status()['unresolved']), 1)
        op = self.journal.status()['unresolved'][0]['operation_id']
        self.journal.resolve(op, 'not_applied', 'Operator checked service request log')
        self.call(lambda _: json.dumps({'error': 'timeout after write'}))
        self.assertEqual(len(self.journal.status()['unresolved']), 1)

    def test_cancellation_is_not_success(self):
        def cancel(_):
            raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.call(cancel)
        self.assertEqual(len(self.journal.status()['unresolved']), 1)

    def test_operator_resolution_is_append_only_and_requires_evidence(self):
        op = self.journal.begin('charge', {'amount': 10}, 'one', 'call-1')
        with self.assertRaises(RecoveryError):
            self.journal.resolve(op, 'completed', '')
        with self.assertRaises(RecoveryError):
            self.journal.resolve(op, 'maybe', 'Unknown')
        self.journal.resolve(op, 'completed', 'Receipt 123 checked on service')
        self.assertEqual(self.journal.status()['unresolved'], [])
        self.assertEqual(self.journal.verify()['count'], 2)
        with self.assertRaises(RecoveryError):
            self.journal.resolve(op, 'not_applied', 'second decision')

    def test_corruption_blocks_writes_but_inspection_reports_it(self):
        self.call(lambda _: 'sent')
        with sqlite3.connect(self.journal.path) as db:
            db.execute("UPDATE events SET payload = '{}' WHERE seq = 1")
        db.close()
        calls = []
        result = json.loads(self.call(lambda _: calls.append(True)))
        self.assertTrue(result['blocked'])
        self.assertEqual(calls, [])
        self.assertFalse(self.journal.status()['integrity_ok'])

    def test_checkpoint_detects_tail_deletion(self):
        self.call(lambda _: 'sent')
        checkpoint = self.journal.verify()
        with sqlite3.connect(self.journal.path) as db:
            db.execute('DELETE FROM events WHERE seq = 2')
        db.close()
        with self.assertRaises(RecoveryError):
            self.journal.verify(checkpoint)

    def test_local_checkpoint_blocks_tail_deletion_without_operator_checkpoint(self):
        self.call(lambda _: 'sent')
        with sqlite3.connect(self.journal.path) as db:
            db.execute('DELETE FROM events')
        db.close()
        with self.assertRaises(RecoveryError):
            Journal(self.root)

    def test_database_only_forged_completion_cannot_clear_recovery_gate(self):
        op = self.journal.begin('charge', {}, 'session', 'call')
        checkpoint = self.journal.verify()
        event = {'schema': checkpoint['schema'], 'operation_id': op, 'kind': 'returned', 'timestamp_ns': 1}
        payload = json.dumps(event, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
        hash_input = json.dumps({'previous': checkpoint['head'], 'payload': payload}, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
        forged_hash = hashlib.sha256(hash_input.encode('utf-8')).hexdigest()
        with sqlite3.connect(self.journal.path) as db:
            db.execute('INSERT INTO events VALUES (?, ?, ?, ?)', (2, payload, checkpoint['head'], forged_hash))
        db.close()
        self.assertFalse(self.journal.status()['integrity_ok'])
        self.assertTrue(json.loads(self.call(lambda _: 'unsafe'))['blocked'])

    def test_replaced_database_is_not_silently_reinitialized(self):
        self.call(lambda _: 'sent')
        self.journal.path.unlink()
        self.journal.path.touch()
        guard = RecoveryGuard(lambda: Journal(self.root))
        self.assertTrue(json.loads(guard.execute(tool_name='charge', args={}, next_call=lambda _: 'unsafe'))['blocked'])

    def test_missing_local_checkpoint_blocks_protected_calls(self):
        self.call(lambda _: 'sent')
        (self.root / 'head.json').unlink(missing_ok=True)
        self.assertTrue(json.loads(self.call(lambda _: 'unsafe'))['blocked'])

    def test_retained_checkpoint_prevents_reinitialization_when_database_and_marker_are_deleted(self):
        self.journal.begin('charge', {}, 'session', 'call')
        checkpoint = (self.root / 'head.json').read_bytes()
        self.journal.path.unlink()
        (self.root / 'initialized').unlink()
        guard = RecoveryGuard(lambda: Journal(self.root))
        self.assertTrue(json.loads(guard.execute(tool_name='charge', args={}, next_call=lambda _: 'unsafe'))['blocked'])
        self.assertEqual((self.root / 'head.json').read_bytes(), checkpoint)

    def test_checkpoint_commit_gap_fails_closed_before_side_effect(self):
        write_head = self.journal._write_head
        def interrupted(checkpoint):
            write_head(checkpoint)
            raise OSError('storage fails before SQLite commit')
        calls = []
        with patch.object(self.journal, '_write_head', interrupted):
            result = json.loads(self.call(lambda _: calls.append(True)))
        self.assertTrue(result['blocked'])
        self.assertEqual(calls, [])
        self.assertFalse(self.journal.status()['integrity_ok'])

    def test_storage_failure_does_not_call_downstream(self):
        def broken():
            raise OSError('disk unavailable')
        guard = RecoveryGuard(broken)
        calls = []
        result = json.loads(guard.execute(tool_name='charge', args={}, next_call=lambda _: calls.append(True)))
        self.assertTrue(result['blocked'])
        self.assertEqual(calls, [])

    def test_post_execution_storage_failure_returns_uncertainty_without_repeating(self):
        calls = []
        def effect(_):
            calls.append(True)
            self.journal.path.unlink()
            return 'sent'
        result = json.loads(self.call(effect))
        self.assertTrue(result['outcome_unknown'])
        self.assertEqual(calls, [True])
        result = json.loads(self.call(lambda _: calls.append(True)))
        self.assertTrue(result['blocked'])
        self.assertEqual(calls, [True])

    def test_parallel_admission_allows_only_one_unresolved_write(self):
        barrier = threading.Barrier(2)
        def begin(_):
            barrier.wait()
            try:
                return Journal(self.root).begin('charge', {'amount': 10}, 's', 'c')
            except RecoveryError:
                return None
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            operations = list(pool.map(begin, range(2)))
        self.assertEqual(sum(x is not None for x in operations), 1)

    def test_raw_args_and_results_are_not_saved(self):
        secret = 'SENSITIVE-SECRET-12345'
        self.call(lambda _: secret, args={'token': secret})
        with sqlite3.connect(self.journal.path) as db:
            text = '\n'.join(row[0] for row in db.execute('SELECT payload FROM events'))
        db.close()
        self.assertNotIn(secret, text)

    def test_explicit_read_only_allowlist_and_plugin_inspection_remain_available(self):
        self.journal.begin('charge', {}, 's', 'c')
        guard = RecoveryGuard(lambda: self.journal, read_only_tools={'lookup_receipt'})
        self.assertEqual(guard.execute(tool_name='lookup_receipt', args={}, next_call=lambda _: 'receipt'), 'receipt')
        self.assertEqual(guard.execute(tool_name='crash_recovery_status', args={}, next_call=lambda _: 'status'), 'status')

    def test_force_killed_process_leaves_one_booking_and_blocks_restart(self):
        result = subprocess.run([sys.executable, str(ROOT / 'demo.py'), '--directory', str(self.root / 'demo')],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report['bookings'], 1)
        self.assertTrue(report['repeat_blocked'])
        self.assertTrue(report['integrity_ok'])
        self.assertEqual(report['unresolved_after_resolution'], 0)


if __name__ == '__main__':
    unittest.main()
