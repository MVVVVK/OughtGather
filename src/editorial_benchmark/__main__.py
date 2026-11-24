"""Finalize an encrypted stage after optional existing Shadow execution."""
import json
import os
from datetime import date
from pathlib import Path
from .crypto import decrypt, encrypt, key_from_env
from .snapshot import finalize
from .storage import AlreadyFrozen, BenchmarkStore


def main():
    path = Path('output/.benchmark-stage.enc')
    if os.getenv('EDITORIAL_BENCHMARK_ENABLED', '') != 'true': return
    try:
        if not path.is_file(): raise ValueError('stage unavailable')
        key = key_from_env()
        stage = decrypt(path.read_bytes(), key)
        report_path = Path('output/ai-editor-report.json')
        report = None
        outcome = os.getenv('BENCHMARK_SHADOW_OUTCOME', '')
        if os.getenv('BENCHMARK_SHADOW_MODE') == 'shadow' and outcome not in ('', 'skipped'):
            try:
                if outcome != 'success': raise ValueError('shadow failed')
                report = json.loads(report_path.read_text(encoding='utf-8'))
            except Exception:
                report = {'mode': 'shadow', 'status': 'degraded'}
        snapshot = finalize(stage, report)
        day = snapshot['target_publication_at'][:10]
        retention = int(os.getenv('EDITORIAL_BENCHMARK_RETENTION_DAYS') or '30')
        blob = encrypt(snapshot, key)
        with BenchmarkStore(retention=retention) as store:
            store.save(day, blob)
            try: store.cleanup(date.fromisoformat(day))
            except Exception:
                print('Benchmark: saved; retention cleanup unavailable')
                return
        print('Benchmark: encrypted snapshot saved')
    except AlreadyFrozen:
        print('Benchmark: already frozen; existing snapshot preserved')
    except Exception:
        # No provider response, path, data, token, or URL is exposed.
        print('Benchmark: unavailable; publication remains independent')
    finally:
        path.unlink(missing_ok=True)


if __name__ == '__main__': main()
