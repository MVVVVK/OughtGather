"""Run after the delivery step; report failures without failing the daily job."""
import json
import os
from pathlib import Path
from .editor import run


def main():
    path = Path('output/.ai-candidates.json')
    try:
        candidates = json.loads(path.read_text(encoding='utf8'))
        report = run(candidates)
    except Exception:
        report = {'mode': os.getenv('AI_EDITOR_MODE', 'off'), 'status': 'degraded',
                  'fallback_reason': 'internal_error', 'scope': 'rss_only'}
    finally:
        path.unlink(missing_ok=True)
    if report['mode'] == 'off':
        return
    Path('output').mkdir(exist_ok=True)
    Path('output/ai-editor-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    Path('output/ai-editor-summary.md').write_text(
        '# AI Shadow Editor\n\nStatus: {}\n\nCandidates: {} · Topics: {} · Selected: {} · Calls: {}\n'.format(
            report['status'], report.get('candidate_count', 0), report.get('topic_count', 0), report.get('selected_article_count', 0), report.get('api_calls', 0)), encoding='utf8')


if __name__ == '__main__':
    main()
