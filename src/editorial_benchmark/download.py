"""User-operated encrypted download; no plaintext snapshot is written."""
import argparse
from pathlib import Path
from .crypto import decrypt, key_from_env
from .review import write_new
from .storage import BenchmarkStore
from scripts.editorial_v2.replay import validate


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('day'); parser.add_argument('output')
    args = parser.parse_args()
    try:
        output = Path(args.output).absolute()
        directory = output.parent.resolve(strict=True)
        if not directory.is_dir() or directory.stat().st_mode & 0o077 or directory == Path.cwd() or Path.cwd() in directory.parents:
            raise ValueError('private directory required')
        key = key_from_env()
        with BenchmarkStore() as store: blob = store.read(args.day)
        if blob is None: raise ValueError('snapshot unavailable')
        snapshot = decrypt(blob, key)
        validate(snapshot, allow_private=True)
        if snapshot.get('data_class') != 'private': raise ValueError('invalid snapshot')
        write_new(output, blob)
        print('Encrypted benchmark downloaded')
    except Exception:
        parser.exit(2, 'Benchmark download unavailable; no content emitted.\n')


if __name__ == '__main__': main()
