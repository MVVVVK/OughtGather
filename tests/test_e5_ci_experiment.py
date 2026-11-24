"""Offline plumbing tests only: these fakes are not a real E5 experiment."""
import json
from pathlib import Path
from types import SimpleNamespace

import yaml

from scripts.editorial_v2.semantic import ci_experiment as task


def make_snapshot(tmp_path):
    root = tmp_path / task.REVISION
    root.mkdir()
    for name in ('config.json', 'model.safetensors', 'tokenizer_config.json', 'tokenizer.json'):
        (root / name).write_bytes(b'x')
    return root


class FakeEncoder:
    def encode(self, texts):
        assert len(texts) == 4
        return [[1.0] + [0.0] * 383 for _ in texts]


def test_smoke_plumbing_outputs_aggregates_only(tmp_path):
    root = make_snapshot(tmp_path); report = tmp_path / 'report.json'
    assert task.run(report, downloader=lambda: root, encoder_factory=FakeEncoder, benchmark_runner=lambda _: {'fixture_only': True})
    data = json.loads(report.read_text())
    assert data['status'] == 'ok' and data['dimensions'] == 384
    assert data['downloaded_bytes'] == 4 and data['norm_min'] == data['norm_max'] == 1
    assert data['data_experiment'] == 'skipped_license_unconfirmed'
    assert not any(title in report.read_text() for title in task.TITLES)


def test_later_validation_failure_preserves_smoke_status(tmp_path):
    root = make_snapshot(tmp_path); report = tmp_path / 'report.json'
    def fail(_): raise ValueError('sensitive text')
    assert not task.run(report, downloader=lambda: root, encoder_factory=FakeEncoder, benchmark_runner=fail)
    data = json.loads(report.read_text())
    assert data['smoke_status'] == 'ok' and data['failure_stage'] == 'synthetic_validation'
    assert 'sensitive text' not in report.read_text()


def test_download_403_is_sanitized_and_no_inference(tmp_path):
    class Failure(Exception):
        response = SimpleNamespace(status_code=403)
    def download(): raise Failure('sensitive response content')
    def no_model(): raise AssertionError('must not run')
    path = tmp_path / 'report.json'
    assert not task.run(path, downloader=download, encoder_factory=no_model)
    data = json.loads(path.read_text())
    assert data['failure_stage'] == 'model_download' and data['http_status'] == 403
    assert 'sensitive' not in path.read_text()


def test_bad_or_unpinned_model_fails_without_inference(tmp_path):
    root = tmp_path / 'different-revision'; root.mkdir(); path = tmp_path / 'report.json'
    assert not task.run(path, downloader=lambda: root, encoder_factory=FakeEncoder)
    assert json.loads(path.read_text())['failure_stage'] == 'model_download'


def test_experiment_job_is_manual_branch_gated_and_secrets_free():
    data = yaml.safe_load(Path('.github/workflows/ci.yml').read_text())
    job = data['jobs']['b3-e5-experiment']
    assert "github.event_name == 'workflow_dispatch'" in job['if']
    assert "refs/heads/codex/editorial-b3-hybrid" in job['if']
    assert job['permissions'] == {'contents': 'read'} and job['timeout-minutes'] == 30
    assert all('secrets.' not in json.dumps(step) for step in job['steps'])
    assert job['steps'][0]['with']['persist-credentials'] is False
    assert not any('daily-gather' in json.dumps(step).lower() or 'webdav' in json.dumps(step).lower() for step in job['steps'])
