"""Validate or apply a private JSON patch without command-line key material."""
import argparse
import json
import os
import sys
from pathlib import Path
from .model import validate_patch
from .storage import MemoryStore


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=('validate', 'apply'))
    parser.add_argument('patch', nargs='?', help='patch file; omit to use private environment')
    parser.add_argument('--validate-only', action='store_true')
    args = parser.parse_args()
    try:
        patch = json.loads(Path(args.patch).read_text(encoding='utf8') if args.patch else os.environ['AI_MEMORY_PATCH'])
        validate_patch(patch)
        if args.command == 'validate':
            report = {'status': 'valid'}
        else:
            with MemoryStore() as store:
                memory, changed = store.apply(patch, validate_only=args.validate_only)
            report = {'status': 'validated' if args.validate_only else ('updated' if changed else 'unchanged'),
                      'new_revision': memory['revision'], 'entry_kind': (patch.get('entry') or {}).get('kind')}
        print(json.dumps(report))
        return 0
    except Exception:
        print(json.dumps({'status': 'failed', 'reason': 'validation_or_storage_error'}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
