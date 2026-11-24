"""Offline synthetic end-to-end checks; no network, credentials, or private data."""
import base64
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from datetime import date

import pytest
from cryptography.exceptions import InvalidTag

from src.editorial_benchmark.collector import BenchmarkCollector, parse_permissions, source_id
from src.editorial_benchmark.crypto import encrypt, decrypt, key_from_env
from src.editorial_benchmark.snapshot import build_stage, finalize
from src.editorial_benchmark.storage import AlreadyFrozen, BenchmarkStore
from src.editorial_benchmark.review import create_review, apply_review
from scripts.editorial_v2.replay import replay, validate
from src.uploader.webdav_uploader import DeliveryError

KEY = b'x' * 32
SOURCE = 'rss:https://example.test/feed'


def test_rss_dates_require_explicit_timezone_and_support_rfc822():
    from src.editorial_benchmark.snapshot import iso_date
    assert iso_date('Tue, 24 Nov 2026 05:30:00 GMT') == '2026-11-24T05:30:00+00:00'
    assert iso_date('2026-11-24T05:30:00') is None
    assert iso_date('bad-date') is None


def permission(title=True, excerpt=True, b1=True):
    return {source_id(SOURCE): {'title': title, 'excerpt': excerpt, 'b1': b1}}


def build():
    collector = BenchmarkCollector(permission(excerpt=False))
    items = [{'url': 'https://example.test/a', 'title': 'Synthetic Alpha', 'published': '2026-11-24T01:00:00Z',
              '_entry': {'summary': 'MUST NOT CAPTURE'}, '_media': True},
             {'url': 'https://example.test/b', 'title': 'Synthetic Beta', 'published': '', '_entry': {}}]
    collector.collect('Synthetic', items, SOURCE, 'en', None)
    by_title = {x['title']: x['candidate_id'] for x in collector.snapshot()}
    ids = [by_title['Synthetic Alpha'], by_title['Synthetic Beta']]
    result = SimpleNamespace(source=SimpleNamespace(type='rss'), quality_report=[
        {'url': items[0]['url'], 'status': 'full_text', 'attempted': True, 'included': True},
        {'url': items[1]['url'], 'status': 'rejected', 'reason': 'quota_limit', 'attempted': False, 'included': False}])
    tracker = SimpleNamespace(make_source_key=lambda _: SOURCE)
    stage = build_stage(collector, [result], tracker, ids, '2026-11-24T06:30:00+08:00',
                        '2099-11-24T07:00:00+00:00')
    return stage, ids


class FakeDAV:
    def __init__(self):
        self.config = SimpleNamespace(url='https://dav.example/dav', remote_path='DailyWorldBriefing', username='u', password='p')
        self._client = object(); self.files = {}; self.fail = False
    def _collection_exists(self, url): return True
    def _read(self, url): return self.files.get(url)
    def _write_verified(self, url, data, overwrite):
        assert overwrite is False
        if self.fail: raise DeliveryError('network', 'unavailable')
        if url in self.files: raise DeliveryError('conflict', 'already exists')
        self.files[url] = data
    def _request(self, method, url, **kwargs):
        if method == 'PROPFIND':
            from urllib.parse import urlsplit
            prefix = urlsplit(url).path.rstrip('/')
            links = ''.join(f'<D:response><D:href>{prefix}/{name.rsplit("/",1)[-1]}</D:href></D:response>' for name in self.files)
            return SimpleNamespace(content=f'<D:multistatus xmlns:D="DAV:">{links}</D:multistatus>'.encode())
        if method == 'DELETE': self.files.pop(url, None); return SimpleNamespace(status_code=204)
        raise AssertionError('unexpected method')


def test_synthetic_end_to_end_encrypted_storage_blind_labels_and_replay():
    stage, ids = build()
    assert all(c['excerpt'] is None and c['media_type'] == 'unknown' for c in stage['candidates'])
    assert stage['recorded_results']['B0']['quality'][ids[1]] == 'quota_limited'
    report = {'mode': 'shadow', 'status': 'ok', 'topic_selection_status': 'ok', 'candidate_cap': 1,
              'topic_groups': [{'members': [{'candidate_id': ids[0]}]}], 'ai_recommended': [ids[0]]}
    snapshot = finalize(stage, report)
    assert snapshot['recorded_results']['B1']['status'] == 'partial'
    assert snapshot['recorded_results']['B1']['observed_ids'] == [ids[0]]
    ciphertext = encrypt(snapshot, KEY)
    assert b'Synthetic Alpha' not in ciphertext and b'MUST NOT CAPTURE' not in ciphertext
    dav = FakeDAV()
    with BenchmarkStore(dav) as store:
        store.save('2026-11-24', ciphertext)
        with pytest.raises(AlreadyFrozen): store.save('2026-11-24', encrypt(snapshot, KEY))
        loaded = decrypt(store.read('2026-11-24'), KEY)
    review, mapping = create_review(loaded, ciphertext, sample=1)
    assert len(review['items']) == 2 and 'B0' not in json.dumps(review) and 'B1' not in json.dumps(review)
    for item in review['items']:
        item['event_id'] = 'event-one'; item['importance'] = 'important'
    review['items'][1]['incremental_against'] = {review['items'][0]['code']: 'substantive_update'}
    labeled = apply_review(loaded, ciphertext, mapping, review)
    assert len(labeled['gold']) == 2
    metrics = replay(labeled, {'B0': lambda _: labeled['recorded_results']['B0'],
                               'B1': lambda _: labeled['recorded_results']['B1']}, allow_private=True)
    assert metrics['B0']['important_event_coverage'] == 1
    assert metrics['B1']['unobserved_count'] == 1
    assert metrics['B0']['quality_full_or_rss_rate'] == 1
    with pytest.raises(ValueError): validate(labeled)


def test_permissions_missing_key_corruption_and_b1_alignment(monkeypatch):
    monkeypatch.delenv('EDITORIAL_BENCHMARK_KEY', raising=False)
    with pytest.raises(ValueError): key_from_env()
    monkeypatch.setenv('EDITORIAL_BENCHMARK_KEY', base64.b64encode(KEY).decode())
    assert key_from_env() == KEY
    for policy in ('{}', json.dumps(permission(title=False)), 'not-json'):
        with pytest.raises(ValueError): parse_permissions(policy)
    c = BenchmarkCollector(parse_permissions(json.dumps(permission())))
    c.collect('no grant', [{'url': 'https://example.test/x', 'title': 'x'}], 'rss:no-grant')
    assert c.snapshot() == []
    with pytest.raises(ValueError): decrypt(encrypt({'a': 1}, KEY)[:-1] + b'X', KEY)
    stage, ids = build()
    result = finalize(stage, {'mode':'shadow', 'status':'ok', 'topic_selection_status':'ok', 'candidate_cap':2,
                              'topic_groups':[], 'ai_recommended':[]})
    assert result['recorded_results']['B1']['status'] == 'failed'
    stage, _ = build(); assert finalize(stage, None)['recorded_results']['B1']['status'] == 'not_run'


def test_storage_permission_network_failure_retention_and_no_overwrite(monkeypatch):
    dav = FakeDAV(); blob = encrypt({'a': 1}, KEY)
    dav._collection_exists = lambda _: False
    with pytest.raises(ValueError):
        with BenchmarkStore(dav): pass
    dav._collection_exists = lambda _: True
    dav.fail = True
    with BenchmarkStore(dav) as store:
        with pytest.raises(DeliveryError): store.save('2026-11-24', blob)
    assert not dav.files
    dav.fail = False
    with BenchmarkStore(dav, retention=30) as store:
        store.save('2026-10-01', blob); store.save('2026-11-24', blob)
        store.cleanup(date(2026, 11, 24))
        assert store.read('2026-10-01') is None and store.read('2026-11-24') == blob
    monkeypatch.setenv('AI_MEMORY_REMOTE_DIR', 'OughtGatherBenchmark')
    with pytest.raises(ValueError): BenchmarkStore(dav)


def test_benchmark_failure_does_not_change_publication_or_dedup(tmp_path, monkeypatch):
    from src.config import ContentSource
    from src.dedup.tracker import DedupTracker
    from src.fetchers.rss_fetcher import RSSFetcher
    from src.main import main
    from tests.test_rss_publication_dedup import item as rss_item, feed
    monkeypatch.setenv('WEBDAV_ENABLED', 'false'); monkeypatch.setenv('AI_EDITOR_MODE', 'off')
    source = ContentSource(type='rss', src='https://example.org/feed', metadata={'limit': 2})
    config = MagicMock(body=[source], limit=2); seen = []
    for mode in ('off', 'failed'):
        work = tmp_path / mode; work.mkdir(); monkeypatch.chdir(work)
        monkeypatch.setenv('EDITORIAL_BENCHMARK_ENABLED', 'true' if mode == 'failed' else 'false')
        monkeypatch.delenv('EDITORIAL_BENCHMARK_KEY', raising=False)
        tracker = DedupTracker(str(work / 'history'))
        with patch('src.main.load_config', return_value=config), patch('src.main.DedupTracker', return_value=tracker), patch.object(RSSFetcher, '_parse_feed', return_value=feed([rss_item(i) for i in range(4)])), patch('src.main.EPUBGenerator') as generator, patch('src.main.SMTPSender') as sender:
            generator.return_value.generate.return_value = 'test.epub'; sender.return_value.send_epub.return_value = True
            main([])
            results = generator.return_value.generate.call_args.args[0]
            seen.append(([a.title for r in results for a in r.articles], (work / 'history').read_bytes()))
            sender.return_value.send_epub.assert_called_once()
    assert seen[0] == seen[1]


def test_main_collects_when_shadow_off_and_stage_is_encrypted(tmp_path, monkeypatch):
    from src.config import ContentSource
    from src.dedup.tracker import DedupTracker
    from src.fetchers.rss_fetcher import RSSFetcher
    from src.main import main
    from tests.test_rss_publication_dedup import item as rss_item, feed
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('WEBDAV_ENABLED', 'false'); monkeypatch.setenv('AI_EDITOR_MODE', 'off')
    monkeypatch.setenv('EDITORIAL_BENCHMARK_ENABLED', 'true')
    monkeypatch.setenv('EDITORIAL_BENCHMARK_KEY', base64.b64encode(KEY).decode())
    source = ContentSource(type='rss', src='https://example.org/feed', metadata={'limit': 2, 'epub_language': 'fr'})
    policy = {source_id('rss:https://example.org/feed'): {'title': True, 'excerpt': False, 'b1': False}}
    monkeypatch.setenv('EDITORIAL_BENCHMARK_PERMISSIONS', json.dumps(policy))
    tracker = DedupTracker(str(tmp_path / 'history'))
    config = MagicMock(body=[source], limit=2)
    with patch('src.main.load_config', return_value=config), patch('src.main.DedupTracker', return_value=tracker), patch.object(RSSFetcher, '_parse_feed', return_value=feed([rss_item(i) for i in range(4)])), patch('src.main.EPUBGenerator') as generator, patch('src.main.SMTPSender') as sender:
        generator.return_value.generate.return_value = 'test.epub'; sender.return_value.send_epub.return_value = True
        main([])
        generator.return_value.generate.assert_called_once(); sender.return_value.send_epub.assert_called_once()
    path = tmp_path / 'output/.benchmark-stage.enc'; blob = path.read_bytes()
    assert b'example.org' not in blob
    stage = decrypt(blob, KEY)
    assert stage['data_class'] == 'private' and len(stage['candidates']) == 4
    assert stage['recorded_results']['B0']['selected']
    assert stage['_b1_eligible'] == {} and not (tmp_path / 'output/.ai-candidates.json').exists()
    assert all(c['excerpt'] is None for c in stage['candidates'])
    assert all(c['language'] != 'fr' for c in stage['candidates'])  # epub_language is not RSS language


def test_shadow_payload_respects_permissions_and_missing_policy_fails_closed(tmp_path, monkeypatch):
    from src.config import ContentSource
    from src.dedup.tracker import DedupTracker
    from src.fetchers.rss_fetcher import RSSFetcher
    from src.main import main
    from tests.test_rss_publication_dedup import item as rss_item, feed
    monkeypatch.setenv('WEBDAV_ENABLED', 'false'); monkeypatch.setenv('AI_EDITOR_MODE', 'shadow')
    monkeypatch.setenv('EDITORIAL_BENCHMARK_ENABLED', 'true')
    monkeypatch.setenv('EDITORIAL_BENCHMARK_KEY', base64.b64encode(KEY).decode())
    source = ContentSource(type='rss', src='https://example.org/feed', metadata={'limit': 2})
    config = MagicMock(body=[source], limit=2)
    for case in ('allowed', 'denied', 'invalid'):
        work = tmp_path / case; work.mkdir(); monkeypatch.chdir(work)
        policy = {source_id('rss:https://example.org/feed'): {'title': True, 'excerpt': False, 'b1': case == 'allowed'}}
        monkeypatch.setenv('EDITORIAL_BENCHMARK_PERMISSIONS', json.dumps(policy) if case != 'invalid' else '{}')
        tracker = DedupTracker(str(work / 'history'))
        with patch('src.main.load_config', return_value=config), patch('src.main.DedupTracker', return_value=tracker), patch.object(RSSFetcher, '_parse_feed', return_value=feed([rss_item(i) for i in range(2)])), patch('src.main.EPUBGenerator') as generator, patch('src.main.SMTPSender') as sender:
            generator.return_value.generate.return_value = 'test.epub'; sender.return_value.send_epub.return_value = True
            main([])
        items = json.loads((work / 'output/.ai-candidates.json').read_text())
        if case == 'allowed': assert len(items) == 2 and all(not c['rss_excerpt'] for c in items)
        else: assert items == []


def test_webdav_verified_move_never_overwrites_and_403_is_failure():
    import httpx
    from src.uploader.webdav_uploader import WebDavUploader
    files = {}; moves = []; deny = [False]
    def handler(request):
        path = request.url.path
        if deny[0]: return httpx.Response(403)
        if request.method == 'GET': return httpx.Response(200, content=files[path]) if path in files else httpx.Response(404)
        if request.method == 'PUT': files[path] = request.content; return httpx.Response(201)
        if request.method == 'MOVE':
            from urllib.parse import urlsplit
            target = urlsplit(request.headers['Destination']).path
            moves.append(request.headers['Overwrite'])
            if target in files: return httpx.Response(412)
            files[target] = files.pop(path); return httpx.Response(201)
        if request.method == 'DELETE': files.pop(path, None); return httpx.Response(204)
        raise AssertionError('unexpected request')
    config = SimpleNamespace(url='https://dav.example/dav', remote_path='DailyWorldBriefing', username='u', password='p')
    client = httpx.Client(transport=httpx.MockTransport(handler))
    with patch('src.uploader.webdav_uploader.get_webdav_config', return_value=config):
        uploader = WebDavUploader(client=client); store = BenchmarkStore(uploader)
        store.save('2026-11-24', encrypt({'a': 1}, KEY))
        target = '/dav/OughtGatherBenchmark/2026-11-24.enc'; saved = files[target]
        with pytest.raises(AlreadyFrozen): store.save('2026-11-24', encrypt({'a': 2}, KEY))
        assert files[target] == saved and moves == ['F']
        deny[0] = True
        with pytest.raises(DeliveryError) as error: store.save('2026-11-25', encrypt({'a': 3}, KEY))
        assert error.value.category == 'authentication' and files[target] == saved
    client.close()


def test_finish_upload_failure_is_sanitized_and_cleans_encrypted_stage(tmp_path, monkeypatch, capsys):
    import src.editorial_benchmark.__main__ as finish
    monkeypatch.chdir(tmp_path); (tmp_path / 'output').mkdir()
    stage, _ = build(); path = tmp_path / 'output/.benchmark-stage.enc'; path.write_bytes(encrypt(stage, KEY))
    monkeypatch.setenv('EDITORIAL_BENCHMARK_ENABLED', 'true')
    monkeypatch.setenv('EDITORIAL_BENCHMARK_KEY', base64.b64encode(KEY).decode())
    monkeypatch.setenv('BENCHMARK_SHADOW_OUTCOME', 'skipped')
    class BrokenStore:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def save(self, *args): raise RuntimeError('PRIVATE_SECRET_URL')
    monkeypatch.setattr(finish, 'BenchmarkStore', BrokenStore)
    finish.main()
    assert not path.exists()
    out = capsys.readouterr().out
    assert 'publication remains independent' in out and 'PRIVATE_SECRET_URL' not in out


def test_enabled_workflow_suppresses_sensitive_artifacts_and_continues_on_error():
    import yaml
    from pathlib import Path
    workflow = yaml.safe_load(Path('.github/workflows/daily-gather.yml').read_text())
    steps = workflow['jobs']['gather']['steps']
    by_name = {s['name']: s for s in steps if 'name' in s}
    finish = by_name['Save encrypted editorial benchmark']
    assert finish['continue-on-error'] is True and 'inputs.test_mode' in finish['if']
    for name in ('Upload AI editor report', 'Upload quality report', 'Upload EPUB artifact', 'Upload dedup records', 'Upload logs'):
        assert "EDITORIAL_BENCHMARK_ENABLED != 'true'" in by_name[name]['if']
    assert '> /dev/null 2>&1' in by_name['Run Ought Gather']['run']


def test_review_supports_unknown_and_disputed_and_b2_without_showing_names():
    stage, ids = build(); snapshot = finalize(stage, None); ciphertext = encrypt(snapshot, KEY)
    review, mapping = create_review(snapshot, ciphertext, sample=1, include_b2=True)
    assert 'B2' not in json.dumps(review) and len(review['items']) == 2
    review['items'][0]['event_id'] = 'event-1'; review['items'][0]['importance'] = 'disputed'
    review['items'][1]['event_id'] = 'event-1'; review['items'][1]['importance'] = 'unknown'
    review['items'][1]['incremental_against'] = {review['items'][0]['code']: 'disputed'}
    result = apply_review(snapshot, ciphertext, mapping, review)
    assert 'B2_local' in result['recorded_results']
    assert len(result['gold']) == 2 and all(x['important'] is None for x in result['gold'].values())
    assert set(result['unresolved_labels'].values()) == {'unknown', 'disputed'}
    assert len(result['disputed_relations']) == 1
    with pytest.raises(ValueError): apply_review(result, b'different', mapping, review)


def test_invalid_shadow_report_retains_b0_as_failed_b1(tmp_path, monkeypatch):
    import src.editorial_benchmark.__main__ as finish
    monkeypatch.chdir(tmp_path); (tmp_path / 'output').mkdir()
    stage, _ = build(); (tmp_path / 'output/.benchmark-stage.enc').write_bytes(encrypt(stage, KEY))
    (tmp_path / 'output/ai-editor-report.json').write_text('{invalid')
    monkeypatch.setenv('EDITORIAL_BENCHMARK_ENABLED', 'true')
    monkeypatch.setenv('EDITORIAL_BENCHMARK_KEY', base64.b64encode(KEY).decode())
    monkeypatch.setenv('BENCHMARK_SHADOW_MODE', 'shadow')
    monkeypatch.setenv('BENCHMARK_SHADOW_OUTCOME', 'success')
    saved = []
    class Store:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def save(self, day, content): saved.append(decrypt(content, KEY))
        def cleanup(self, day): pass
    monkeypatch.setattr(finish, 'BenchmarkStore', Store)
    finish.main()
    assert saved[0]['recorded_results']['B1']['status'] == 'failed'
    assert saved[0]['recorded_results']['B0']['selected']


def test_concurrent_target_creation_refuses_move_overwrite():
    import httpx
    from urllib.parse import urlsplit
    from src.uploader.webdav_uploader import WebDavUploader
    files = {}; prior = b'other frozen ciphertext'; target = '/dav/OughtGatherBenchmark/2026-11-24.enc'
    def handler(request):
        path = request.url.path
        if request.method == 'GET': return httpx.Response(200, content=files[path]) if path in files else httpx.Response(404)
        if request.method == 'PUT': files[path] = request.content; return httpx.Response(201)
        if request.method == 'MOVE':
            assert request.headers['Overwrite'] == 'F'
            files[target] = prior  # another writer wins after the absence check
            return httpx.Response(412)
        if request.method == 'DELETE': files.pop(path, None); return httpx.Response(204)
        raise AssertionError('unexpected request')
    config = SimpleNamespace(url='https://dav.example/dav', remote_path='DailyWorldBriefing', username='u', password='p')
    client = httpx.Client(transport=httpx.MockTransport(handler))
    with patch('src.uploader.webdav_uploader.get_webdav_config', return_value=config):
        store = BenchmarkStore(WebDavUploader(client=client))
        with pytest.raises(DeliveryError): store.save('2026-11-24', encrypt({'a': 1}, KEY))
    assert files[target] == prior and len(files) == 1
    client.close()


def test_fanout_never_passes_unpermitted_fields_to_shadow_observer():
    from src.editorial_benchmark.collector import Fanout
    ai = MagicMock(); collector = BenchmarkCollector(permission(excerpt=False, b1=True))
    candidate = {'url': 'https://example.test/a', 'title': 'Allowed', '_entry': {'summary': 'PRIVATE_EXCERPT'}}
    Fanout(ai, collector).collect('Source', [candidate], SOURCE, 'en', None)
    passed = ai.collect.call_args.args[1]
    assert 'PRIVATE_EXCERPT' not in str(passed) and 'summary' not in passed[0]['_entry']
    ai.reset_mock(); Fanout(ai, collector).collect('Source', [candidate], 'rss:unapproved', 'en', None)
    ai.collect.assert_not_called()
    ai.reset_mock(); Fanout(ai, None).collect('Source', [candidate], SOURCE, 'en', None)
    ai.collect.assert_not_called()


def test_stage_encryption_failure_does_not_block_publication(tmp_path, monkeypatch):
    from src.config import ContentSource
    from src.dedup.tracker import DedupTracker
    from src.fetchers.rss_fetcher import RSSFetcher
    from src.main import main
    from tests.test_rss_publication_dedup import item as rss_item, feed
    monkeypatch.chdir(tmp_path); monkeypatch.setenv('WEBDAV_ENABLED', 'false'); monkeypatch.setenv('AI_EDITOR_MODE', 'off')
    monkeypatch.setenv('EDITORIAL_BENCHMARK_ENABLED', 'true')
    monkeypatch.setenv('EDITORIAL_BENCHMARK_KEY', base64.b64encode(KEY).decode())
    source = ContentSource(type='rss', src='https://example.org/feed', metadata={'limit': 2})
    monkeypatch.setenv('EDITORIAL_BENCHMARK_PERMISSIONS', json.dumps({source_id('rss:https://example.org/feed'): {'title': True, 'excerpt': False, 'b1': False}}))
    tracker = DedupTracker(str(tmp_path / 'history'))
    config = MagicMock(body=[source], limit=2)
    with patch('src.main.load_config', return_value=config), patch('src.main.DedupTracker', return_value=tracker), patch.object(RSSFetcher, '_parse_feed', return_value=feed([rss_item(i) for i in range(2)])), patch('src.main.EPUBGenerator') as generator, patch('src.main.SMTPSender') as sender, patch('src.editorial_benchmark.crypto.encrypt', side_effect=ValueError('private data')):
        generator.return_value.generate.return_value = 'test.epub'; sender.return_value.send_epub.return_value = True
        main([])
        generator.return_value.generate.assert_called_once(); sender.return_value.send_epub.assert_called_once()
    assert (tmp_path / 'history').is_file() and not (tmp_path / 'output/.benchmark-stage.enc').exists()
