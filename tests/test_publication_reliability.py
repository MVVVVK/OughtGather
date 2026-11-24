"""Offline checks for publication health and the workflow's explicit gate."""
import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

from scripts.publication_health import assess

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('epub,delivery,mode,ai,test,expected', [
    (True, 'success', 'shadow', 'ok', False, ('generated', 'success', 'ok', True)),
    (True, 'failed', 'shadow', 'degraded', False, ('generated', 'failed', 'degraded', False)),
    (False, None, 'shadow', None, False, ('missing', 'unknown', 'degraded', False)),
    (True, 'success', 'off', None, False, ('generated', 'success', 'off', True)),
    (True, 'success', 'shadow', 'ok', True, ('generated', 'success', 'ok', False)),
])
def test_health_is_sanitized_and_delivery_is_independent(tmp_path, epub, delivery, mode, ai, test, expected):
    if epub:
        (tmp_path / 'news.epub').write_bytes(b'epub')
    if delivery:
        (tmp_path / 'delivery-status.json').write_text(json.dumps({'webdav': delivery, 'private': 'never copy'}))
    if ai:
        (tmp_path / 'ai-editor-report.json').write_text(json.dumps({'status': ai, 'private': 'never copy'}))
    result = assess(tmp_path, mode, test)
    assert (result['epub'], result['webdav'], result['ai'], result['verified']) == expected
    assert 'never copy' not in json.dumps(result)


def test_invalid_or_absent_delivery_and_ai_reports_are_not_success(tmp_path):
    (tmp_path / 'news.epub').write_bytes(b'epub')
    (tmp_path / 'delivery-status.json').write_text('{bad')
    result = assess(tmp_path, 'shadow')
    assert result['webdav'] == 'unknown' and result['ai'] == 'degraded' and not result['verified']


@pytest.mark.parametrize('hit,test_mode,force,expected', [
    ('true', '', '', 'false'), ('true', 'true', '', 'true'),
    ('true', '', 'true', 'true'), ('', '', '', 'true'), ('false', '', '', 'true'),
])
def test_gate_decision_executes_actual_workflow_script(tmp_path, hit, test_mode, force, expected):
    doc = yaml.safe_load((ROOT / '.github/workflows/daily-gather.yml').read_text())
    step = next(s for s in doc['jobs']['publication-gate']['steps'] if s.get('id') == 'decision')
    out = tmp_path / 'out'
    env = {**os.environ, 'HIT': hit, 'TEST_MODE': test_mode, 'FORCE': force, 'GITHUB_OUTPUT': str(out)}
    subprocess.run(['bash', '-e', '-c', step['run']], env=env, check=True)
    assert out.read_text().strip() == f'run={expected}'


def test_workflow_serializes_gate_and_preserves_dedup_and_isolation():
    doc = yaml.safe_load((ROOT / '.github/workflows/daily-gather.yml').read_text())
    assert doc['concurrency'] == {'group': 'oughtgather-dedup', 'cancel-in-progress': False}
    assert doc['jobs']['gather']['needs'] == 'publication-gate'
    steps = doc['jobs']['gather']['steps']
    names = [s['name'] for s in steps if 'name' in s]
    assert names.index('Save dedup records') < names.index('Save verified publication marker')
    save = next(s for s in steps if s.get('name') == 'Save verified publication marker')
    assert "steps.verified.outputs.verified == 'true'" in save['if']
    assert save['continue-on-error'] is True
    ai = next(s for s in steps if s.get('name') == 'Run AI Editor Shadow')
    assert ai['continue-on-error'] is True
    assert "ai_editor_mode_override == 'off'" in ai['env']['AI_EDITOR_MODE']
    assert "vars.AI_EDITOR_MODE" in ai['env']['AI_EDITOR_MODE']
    assert "repository_dispatch" in ai['env']['AI_EDITOR_MODE']
