"""Operator-only local inspection and reconciliation; never a registered agent tool."""

import argparse
import json
import sys
from pathlib import Path

from recovery import Journal


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', required=True, type=Path, help='The active profile plugin-data/crash-recovery directory')
    subs = parser.add_subparsers(dest='command', required=True)
    subs.add_parser('status')
    verify = subs.add_parser('verify')
    verify.add_argument('--checkpoint', type=Path)
    subs.add_parser('checkpoint', help='Print a checkpoint to retain independently of the journal')
    resolve = subs.add_parser('resolve')
    resolve.add_argument('operation_id')
    resolve.add_argument('--outcome', choices=['completed', 'not_applied'], required=True)
    resolve.add_argument('--evidence', required=True, help='Operator observation; persisted verbatim, do not include secrets')
    args = parser.parse_args()
    try:
        if not (args.data_dir / 'journal.sqlite3').is_file():
            raise ValueError('No journal at this directory. Check the active Hermes profile; inspection never initializes storage.')
        journal = Journal(args.data_dir)
        if args.command == 'resolve':
            journal.resolve(args.operation_id, args.outcome, args.evidence)
            result = journal.status()
        elif args.command == 'status':
            result = journal.status()
        else:
            checkpoint = json.loads(args.checkpoint.read_text(encoding='utf-8-sig')) if getattr(args, 'checkpoint', None) else None
            result = dict(journal.verify(checkpoint), integrity_ok=True)
        print(json.dumps(result, indent=2))
        return 0 if result.get('integrity_ok') else 1
    except Exception as exc:
        print(json.dumps({'error': str(exc), 'blocked': True}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
