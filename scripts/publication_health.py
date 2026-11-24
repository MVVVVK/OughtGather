"""Produce a small, non-sensitive publication health summary from local outputs."""
import json
import os
from pathlib import Path


def _read(path):
    try:
        obj = json.loads(path.read_text(encoding='utf8'))
        return obj if isinstance(obj, dict) else {}
    except (OSError, ValueError):
        return {}


def assess(directory, mode, test_mode=False):
    root = Path(directory)
    epub = any(path.is_file() and path.stat().st_size > 0 for path in root.glob('*.epub'))
    delivery = _read(root / 'delivery-status.json').get('webdav')
    webdav = delivery if delivery in ('success', 'failed') else 'unknown'
    ai = 'off' if mode == 'off' else ('ok' if _read(root / 'ai-editor-report.json').get('status') == 'ok' else 'degraded')
    return {'epub': 'generated' if epub else 'missing', 'webdav': webdav,
            'ai': ai, 'test': bool(test_mode), 'verified': bool(epub and webdav == 'success' and not test_mode)}


def main():
    result = assess('output', os.getenv('HEALTH_AI_MODE', 'off'), os.getenv('HEALTH_TEST_MODE') == 'true')
    Path('output').mkdir(exist_ok=True)
    Path('output/publication-health.json').write_text(json.dumps(result) + '\n', encoding='utf8')
    if os.getenv('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf8') as stream:
            for key, value in result.items():
                stream.write(f'{key}={str(value).lower()}\n')


if __name__ == '__main__':
    main()
