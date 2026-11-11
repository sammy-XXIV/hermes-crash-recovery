"""Demo-only tools; this fixture is never registered by Crash Recovery itself."""

import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path


def register(ctx):
    directory = Path(ctx.get_config('service_directory'))

    def book(args, **kwargs):
        with closing(sqlite3.connect(directory / 'service.sqlite3')) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS bookings (id INTEGER PRIMARY KEY, name TEXT)')
            db.execute('INSERT INTO bookings (name) VALUES (?)', (args['name'],))
        (directory / 'effect-committed').write_text('booking committed', encoding='utf-8')
        # Hold the acknowledgement until the harness force-kills the whole Hermes process.
        while True:
            time.sleep(0.1)

    def inspect(args, **kwargs):
        with closing(sqlite3.connect(directory / 'service.sqlite3')) as db:
            rows = db.execute('SELECT id, name FROM bookings ORDER BY id').fetchall()
        return json.dumps({'booking_count': len(rows), 'bookings': [{'id': row[0], 'name': row[1]} for row in rows]})

    for name, handler, description, properties, required in [
        ('demo_book', book, 'Create one fictional booking in the local demonstration service.', {'name': {'type': 'string'}}, ['name']),
        ('demo_inspect', inspect, 'Read the local demonstration booking service without changing anything.', {}, []),
    ]:
        ctx.register_tool(name=name, toolset='recovery-demo', handler=handler,
                          schema={'name': name, 'description': description,
                                  'parameters': {'type': 'object', 'properties': properties, 'required': required}})
