import json
import httpx
import pytest
from src.ai.client import DeepSeekClient
from src.ai.editor import run


def candidate(i=1):
    return {'candidate_id': str(i), 'title': 'News', 'source': 'Source', 'language': 'en',
            'published_at': '', 'rss_excerpt': 'untrusted', 'traditional_selected': i == 1}


@pytest.fixture(autouse=True)
def environment(monkeypatch):
    monkeypatch.setenv('AI_EDITOR_MODE', 'shadow')
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'PRIVATE-KEY-ABC')
    monkeypatch.setattr('src.ai.editor.load_profile', lambda: ({}, None, 'degraded'))


def test_daily_test_mode_requires_explicit_ai_override(tmp_path, monkeypatch):
    from unittest.mock import MagicMock, patch
    from src.config import ContentSource
    from src.fetchers.rss_fetcher import RSSFetcher
    from src.main import main
    from tests.test_rss_publication_dedup import item as rss_item, feed
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('AI_EDITOR_MODE', 'shadow')
    monkeypatch.delenv('AI_TEST_OVERRIDE', raising=False)
    source = ContentSource(type='rss', src='https://example.org/feed')
    config = MagicMock(body=[source], limit=1)
    with patch('src.main.load_config', return_value=config), patch.object(RSSFetcher, '_parse_feed', return_value=feed([rss_item(1)])), patch('src.main.EPUBGenerator') as generator, patch('src.main.SMTPSender') as sender:
        generator.return_value.generate.return_value = 'test.epub'
        main(['--test-mode'])
        sender.assert_not_called()
    assert not (tmp_path / 'output/.ai-candidates.json').exists()


def test_real_main_delivers_before_separate_shadow_failure(tmp_path, monkeypatch):
    from unittest.mock import MagicMock, patch
    from src.config import ContentSource
    from src.dedup.tracker import DedupTracker
    from src.fetchers.rss_fetcher import RSSFetcher
    from src.main import main
    from tests.test_rss_publication_dedup import item as rss_item, feed
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('AI_EDITOR_MODE', 'shadow')
    monkeypatch.setenv('WEBDAV_ENABLED', 'false')
    source = ContentSource(type='rss', src='https://example.org/feed')
    config = MagicMock(body=[source], limit=1)
    tracker = DedupTracker(str(tmp_path / 'history'))
    with patch('src.main.load_config', return_value=config), patch('src.main.DedupTracker', return_value=tracker), patch.object(RSSFetcher, '_parse_feed', return_value=feed([rss_item(1), rss_item(2)])), patch('src.main.EPUBGenerator') as generator, patch('src.main.SMTPSender') as sender:
        generator.return_value.generate.return_value = 'test.epub'
        sender.return_value.send_epub.return_value = True
        main([])
        sender.return_value.send_epub.assert_called_once()
    before = (tmp_path / 'history').read_bytes()
    candidates = json.loads((tmp_path / 'output/.ai-candidates.json').read_text())
    assert sum(c['traditional_selected'] for c in candidates) == 1
    client = DeepSeekClient('x', transport=httpx.MockTransport(lambda req: httpx.Response(503)))
    report = run(candidates, client)
    assert report['status'] == 'degraded'
    assert (tmp_path / 'history').read_bytes() == before


def test_workflows_do_not_expose_patch_or_ai_secrets_to_gather():
    from pathlib import Path
    daily = Path('.github/workflows/daily-gather.yml').read_text()
    gather = daily.split('      - name: Run Ought Gather', 1)[1].split('      - name: Save dedup records', 1)[0]
    shadow = daily.split('      - name: Run AI Editor Shadow', 1)[1].split('      - name: Upload AI editor report', 1)[0]
    patch_workflow = Path('.github/workflows/apply-ai-memory.yml').read_text()
    assert 'DEEPSEEK_API_KEY' not in gather and 'AI_MEMORY_KEY' not in gather
    assert 'continue-on-error: true' in shadow and 'DEEPSEEK_API_KEY' in shadow
    inputs = patch_workflow.split('  workflow_dispatch:', 1)[1].split('concurrency:', 1)[0]
    assert 'AI_MEMORY_PATCH' not in inputs
    assert 'DEEPSEEK_API_KEY' not in patch_workflow


def test_offline_report_contains_no_feedback_or_credentials(tmp_path, monkeypatch):
    from src.ai.__main__ import main
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'output').mkdir()
    (tmp_path / 'output/.ai-candidates.json').write_text(json.dumps([candidate()]))
    monkeypatch.setenv('AI_EDITOR_MODE', 'shadow')
    monkeypatch.delenv('DEEPSEEK_API_KEY')
    main()
    text = (tmp_path / 'output/ai-editor-report.json').read_text() + (tmp_path / 'output/ai-editor-summary.md').read_text()
    for phrase in ('DEEPSEEK_API_KEY', 'AI_MEMORY_KEY', 'AI_MEMORY_PATCH', 'original_feedback', 'raw_memory', 'PRIVATE-KEY-ABC'):
        assert phrase not in text
    assert not (tmp_path / 'output/.ai-candidates.json').exists()


def test_main_off_and_shadow_publication_equivalence(tmp_path, monkeypatch):
    from unittest.mock import MagicMock, patch
    from src.config import ContentSource
    from src.dedup.tracker import DedupTracker
    from src.fetchers.rss_fetcher import RSSFetcher
    from src.main import main
    from tests.test_rss_publication_dedup import item as rss_item, feed
    monkeypatch.setenv('WEBDAV_ENABLED', 'false')
    source = ContentSource(type='rss', src='https://example.org/feed', metadata={'limit': 2})
    config = MagicMock(body=[source], limit=2)
    records = []
    for mode in ('off', 'shadow'):
        workdir = tmp_path / mode
        workdir.mkdir()
        monkeypatch.chdir(workdir)
        monkeypatch.setenv('AI_EDITOR_MODE', mode)
        tracker = DedupTracker(str(workdir / 'history'))
        with patch('src.main.load_config', return_value=config), patch('src.main.DedupTracker', return_value=tracker), patch.object(RSSFetcher, '_parse_feed', return_value=feed([rss_item(i) for i in range(4)])), patch('src.main.EPUBGenerator') as generator, patch('src.main.SMTPSender') as sender:
            generator.return_value.generate.return_value = 'test.epub'
            sender.return_value.send_epub.return_value = True
            main([])
            results = generator.return_value.generate.call_args.args[0]
            records.append(([a.title for result in results for a in result.articles], (workdir / 'history').read_bytes()))
            sender.return_value.send_epub.assert_called_once()
        assert (workdir / 'output/.ai-candidates.json').exists() == (mode == 'shadow')
    assert records[0] == records[1]


def test_webdav_delivery_completes_before_provider_unavailable(tmp_path, monkeypatch):
    from unittest.mock import MagicMock, patch
    from src.config import ContentSource
    from src.dedup.tracker import DedupTracker
    from src.fetchers.rss_fetcher import RSSFetcher
    from src.main import main
    from tests.test_rss_publication_dedup import item as rss_item, feed
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('AI_EDITOR_MODE', 'shadow')
    monkeypatch.setenv('WEBDAV_ENABLED', 'true')
    source = ContentSource(type='rss', src='https://example.org/feed')
    tracker = DedupTracker(str(tmp_path / 'history'))
    config = MagicMock(body=[source], limit=1)
    with patch('src.main.load_config', return_value=config), patch('src.main.DedupTracker', return_value=tracker), patch.object(RSSFetcher, '_parse_feed', return_value=feed([rss_item(1)])), patch('src.main.EPUBGenerator') as generator, patch('src.delivery.service.backup_key', return_value='fake-key'), patch('src.delivery.service.make_backup', return_value='edition'), patch('src.delivery.service.deliver', return_value={'webdav': 'success'}) as deliver:
        generator.return_value.generate.return_value = 'test.epub'
        main([])
        deliver.assert_called_once()
    before = (tmp_path / 'history').read_bytes()
    candidates = json.loads((tmp_path / 'output/.ai-candidates.json').read_text())
    report = run(candidates, DeepSeekClient('x', transport=httpx.MockTransport(lambda req: httpx.Response(503))))
    assert report['status'] == 'degraded'
    assert (tmp_path / 'history').read_bytes() == before
