"""Durable, conservative execution journal. Python standard library only."""

import hashlib
import json
import os
import sqlite3
import tempfile
import time
import uuid
from contextlib import closing, contextmanager
from contextvars import ContextVar
from pathlib import Path

SCHEMA = 'hermes.crash-recovery.v1'
INSPECTION_TOOLS = frozenset({'crash_recovery_status', 'crash_recovery_verify'})


class RecoveryError(Exception):
    def __init__(self, message, operation_ids=()):
        super().__init__(message)
        self.operation_ids = list(operation_ids)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


class Journal:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.path = self.directory / 'journal.sqlite3'
        self.head_path = self.directory / 'head.json'
        self.directory.mkdir(parents=True, exist_ok=True)
        marker = self.directory / 'initialized'
        retained_state = any(path.exists() for path in (marker, self.head_path,
                             self.directory / 'journal.sqlite3-wal', self.directory / 'journal.sqlite3-shm'))
        if retained_state and not self.path.is_file():
            raise RecoveryError('Journal is missing from an initialized data directory; restore it before continuing.')
        is_new = not self.path.exists()
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('PRAGMA synchronous=FULL')
            db.execute('BEGIN IMMEDIATE')
            if is_new:
                db.execute('CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
                db.execute('CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY, payload TEXT NOT NULL, previous TEXT NOT NULL, hash TEXT NOT NULL)')
                if not db.execute('SELECT value FROM metadata WHERE key = ?', ('schema',)).fetchone():
                    journal_id = str(uuid.uuid4())
                    db.executemany('INSERT INTO metadata VALUES (?, ?)', [('journal_id', journal_id), ('schema', SCHEMA)])
                    self._write_head({'schema': SCHEMA, 'journal_id': journal_id, 'count': 0,
                                      'head': digest({'schema': SCHEMA, 'journal_id': journal_id})})
            # Existing journals are validated, never silently repaired or reinitialized.
            self._read(db)
        marker.touch(exist_ok=True)

    def _write_head(self, checkpoint):
        fd, name = tempfile.mkstemp(prefix='.head-', suffix='.tmp', dir=self.directory)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                handle.write(canonical(checkpoint))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(name, self.head_path)
        finally:
            Path(name).unlink(missing_ok=True)

    @contextmanager
    def _connection(self):
        # mode=rw prevents silently recreating a deleted journal during this process.
        db = sqlite3.connect(self.path.resolve().as_uri() + '?mode=rw', uri=True, timeout=10)
        try:
            db.execute('PRAGMA synchronous=FULL')
            # Share the writer lock for verification as well: the sidecar checkpoint
            # and SQLite snapshot must be observed on the same side of a commit.
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _read(self, db, checkpoint=None):
        meta = dict(db.execute('SELECT key, value FROM metadata'))
        if meta.get('schema') != SCHEMA or not meta.get('journal_id'):
            raise RecoveryError('Journal metadata is missing or incompatible.')
        previous = digest({'schema': SCHEMA, 'journal_id': meta['journal_id']})
        operations = {}
        hashes = {0: previous}
        count = 0
        for seq, payload, prev, event_hash in db.execute('SELECT seq, payload, previous, hash FROM events ORDER BY seq'):
            if seq != count + 1 or prev != previous or digest({'previous': prev, 'payload': payload}) != event_hash:
                raise RecoveryError(f'Journal integrity failure at event {seq}.')
            try:
                event = json.loads(payload)
                op_id, kind = event['operation_id'], event['kind']
                if event['schema'] != SCHEMA or canonical(event) != payload:
                    raise ValueError('noncanonical event')
                if kind == 'started':
                    if op_id in operations:
                        raise ValueError('duplicate operation')
                    operations[op_id] = dict(event, state='pending')
                else:
                    op = operations[op_id]
                    if op['state'] not in {'pending', 'uncertain'}:
                        raise ValueError('operation already closed')
                    if kind == 'returned':
                        op['state'] = 'returned'
                    elif kind == 'uncertain':
                        op['state'] = 'uncertain'
                    elif kind == 'resolved' and event['outcome'] in {'completed', 'not_applied'} and event['evidence'].strip():
                        op['state'] = 'resolved'
                        op['outcome'] = event['outcome']
                    else:
                        raise ValueError('invalid transition')
                    op['last_event'] = event
            except (KeyError, TypeError, ValueError) as exc:
                raise RecoveryError(f'Invalid journal event {seq}.') from exc
            previous = event_hash
            count = seq
            hashes[seq] = event_hash
        summary = {'schema': SCHEMA, 'journal_id': meta['journal_id'], 'count': count, 'head': previous}
        try:
            local_head = json.loads(self.head_path.read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            raise RecoveryError('Local journal checkpoint is missing or unreadable.') from exc
        if local_head != summary:
            raise RecoveryError('Journal does not exactly match its local checkpoint.')
        if checkpoint is not None:
            if (not isinstance(checkpoint, dict) or checkpoint.get('schema') != SCHEMA or checkpoint.get('journal_id') != meta['journal_id']
                    or hashes.get(checkpoint.get('count')) != checkpoint.get('head')):
                raise RecoveryError('Journal does not match the independently retained checkpoint.')
        return summary, operations

    def _append(self, db, summary, event):
        payload = canonical(dict(event, schema=SCHEMA, timestamp_ns=time.time_ns()))
        event_hash = digest({'previous': summary['head'], 'payload': payload})
        db.execute('INSERT INTO events VALUES (?, ?, ?, ?)',
                   (summary['count'] + 1, payload, summary['head'], event_hash))
        # Anchor before SQLite commit, while holding its writer lock. A crash or
        # disk error in this small interval deliberately produces a blocked journal,
        # never an unanchored action that is allowed to execute.
        self._write_head(dict(summary, count=summary['count'] + 1, head=event_hash))

    def verify(self, checkpoint=None):
        with self._connection() as db:
            summary, _ = self._read(db, checkpoint)
            return summary

    def status(self):
        try:
            with self._connection() as db:
                summary, operations = self._read(db)
            return dict(summary, integrity_ok=True,
                        unresolved=[op for op in operations.values() if op['state'] in {'pending', 'uncertain'}])
        except Exception as exc:
            return {'integrity_ok': False, 'blocked': True, 'error': str(exc), 'unresolved': None}

    def begin(self, tool, args, session_id='', tool_call_id=''):
        fingerprint = digest({'tool': tool, 'args': args})
        with self._connection() as db:
            summary, operations = self._read(db)
            unresolved = [op['operation_id'] for op in operations.values() if op['state'] in {'pending', 'uncertain'}]
            if unresolved:
                raise RecoveryError('Protected execution paused: reconcile unresolved actions with the operator CLI.', unresolved)
            op_id = str(uuid.uuid4())
            self._append(db, summary, {'kind': 'started', 'operation_id': op_id, 'tool': tool,
                                      'fingerprint': fingerprint, 'args_hash': digest(args),
                                      'session_id': session_id or '', 'tool_call_id': tool_call_id or ''})
        return op_id

    def finish(self, operation_id, result=None, uncertain=False, reason=None):
        with self._connection() as db:
            summary, operations = self._read(db)
            if operation_id not in operations or operations[operation_id]['state'] not in {'pending', 'uncertain'}:
                raise RecoveryError('Operation is missing or already closed.')
            # Persist hashes, never tool arguments, result text or exception messages.
            self._append(db, summary, {'kind': 'uncertain' if uncertain else 'returned',
                                      'operation_id': operation_id, 'result_hash': digest(result), 'reason': reason})

    def resolve(self, operation_id, outcome, evidence):
        if outcome not in {'completed', 'not_applied'} or not isinstance(evidence, str) or not evidence.strip():
            raise RecoveryError('Choose completed or not_applied and supply operator evidence.')
        with self._connection() as db:
            summary, operations = self._read(db)
            if operation_id not in operations or operations[operation_id]['state'] not in {'pending', 'uncertain'}:
                raise RecoveryError('Operation is missing or already closed.')
            self._append(db, summary, {'kind': 'resolved', 'operation_id': operation_id,
                                      'outcome': outcome, 'evidence': evidence.strip()})


def result_has_error(result):
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except (ValueError, TypeError):
            return False
    if not isinstance(result, dict):
        return False
    return bool(result.get('error') or result.get('isError') is True or result.get('success') is False
                or result.get('ok') is False or result.get('status') in {'error', 'failed', 'cancelled', 'unknown'}
                or (isinstance(result.get('exit_code'), int) and result['exit_code'] != 0))


class RecoveryGuard:
    def __init__(self, journal_factory, read_only_tools=()):
        self.journal_factory = journal_factory
        self.read_only_tools = frozenset(read_only_tools) | INSPECTION_TOOLS
        self.poisoned = False
        self.active_call = ContextVar('crash_recovery_active_call', default=None)

    def blocked(self, exc):
        return {'error': 'crash_recovery_blocked', 'blocked': True, 'message': str(exc),
                'operation_ids': getattr(exc, 'operation_ids', []),
                'next_step': 'Inspect crash_recovery_status; an operator must reconcile the outcome. Do not retry by changing tools or arguments.'}

    def before(self, tool_name='', **kwargs):
        if tool_name in self.read_only_tools:
            return None
        try:
            if self.poisoned:
                raise RecoveryError('A journal write failed after execution. Restore and inspect the journal before restarting Hermes.')
            status = self.journal_factory().status()
            if not status['integrity_ok']:
                raise RecoveryError(status['error'])
            active = self.active_call.get()
            own_operation = None
            if active and active['identity'] == (tool_name, kwargs.get('session_id') or '', kwargs.get('tool_call_id') or '', digest(kwargs.get('args'))):
                own_operation = active['operation_id']
            unresolved = [op for op in status['unresolved'] if op['operation_id'] != own_operation]
            if unresolved:
                raise RecoveryError('Protected execution paused: unresolved action outcome.',
                                    [op['operation_id'] for op in unresolved])
        except Exception as exc:
            return {'action': 'block', 'message': json.dumps(self.blocked(exc))}
        return None

    def execute(self, tool_name='', args=None, next_call=None, session_id='', tool_call_id='', **kwargs):
        if tool_name in self.read_only_tools:
            return next_call(args)
        # Do not raise on pre-execution plugin failures: Hermes middleware fails open.
        try:
            if self.poisoned:
                raise RecoveryError('Journal failed after an action. Restore and inspect it before restarting Hermes.')
            journal = self.journal_factory()
            operation_id = journal.begin(tool_name, args, session_id, tool_call_id)
        except Exception as exc:
            return json.dumps(self.blocked(exc))
        # Hermes versions may place their pre-tool hook inside the execution
        # middleware. A context-local admission lets ONLY that call pass its own
        # hook; matching IDs from another execution context cannot bypass recovery.
        token = self.active_call.set({'operation_id': operation_id,
                                     'identity': (tool_name, session_id or '', tool_call_id or '', digest(args))})
        try:
            try:
                result = next_call(args)
            except BaseException as exc:
                try:
                    journal.finish(operation_id, uncertain=True, reason=type(exc).__name__)
                except Exception:
                    self.poisoned = True
                raise  # Preserve downstream errors and cancellations; never call again.
        finally:
            self.active_call.reset(token)
        try:
            is_error = result_has_error(result)
            journal.finish(operation_id, result, uncertain=is_error, reason='tool_error' if is_error else None)
        except Exception:
            self.poisoned = True
            return json.dumps({'error': 'crash_recovery_record_failed', 'outcome_unknown': True,
                               'operation_id': operation_id, 'message': 'The tool was invoked once, but its response could not be recorded. Do not repeat it; inspect the service and journal.'})
        return result
