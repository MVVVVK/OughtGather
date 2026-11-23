"""Isolated smoke probe: all network operations use a fake transport."""
import json
from unittest.mock import patch
from urllib.parse import urlsplit

import httpx
import pytest

from src.config import WebDavConfig
from src.uploader.webdav_smoke import run_smoke, main, _root_url
from src.uploader.webdav_uploader import WebDavUploader
from tests.test_webdav_delivery import FakeDAV, edition

ROOT = 'https://dav.jianguoyun.com/dav/'


def test_success_only_touches_owned_isolated_collection():
    fake = FakeDAV()
    fake.files['/dav/DailyWorldBriefing/latest.epub'] = b'valuable'
    fake.dirs.add('/dav/DailyWorldBriefing')
    with httpx.Client(transport=httpx.MockTransport(fake), follow_redirects=False) as client, \
         patch('src.dedup.tracker.DedupTracker', side_effect=AssertionError('no dedup access')):
        report = run_smoke(ROOT, client, authorized=True)
    assert report['success'] is True and not report['cleanup_errors']
    assert fake.files['/dav/DailyWorldBriefing/latest.epub'] == b'valuable'
    assert all(path.startswith('/dav/' + report['directory']) for _, path in fake.calls)
    assert not any(path.startswith('/dav/' + report['directory']) for path in fake.files)
    assert [entry['operation'] for entry in report['operations'] if entry['operation'].startswith('MOVE')] == ['MOVE new', 'MOVE overwrite']
    assert any(entry['operation'] == 'PROPFIND deleted collection' for entry in report['operations'])


@pytest.mark.parametrize('url', [
    'https://dav.jianguoyun.com/dav/DailyWorldBriefing',
    'https://dav.jianguoyun.com/dav/%44ailyWorldBriefing',
    'https://dav.jianguoyun.com/dav/../DailyWorldBriefing',
    'https://user:password@dav.jianguoyun.com/dav/',
    'http://dav.jianguoyun.com/dav/',
    'https://example.org/dav/',
    'https://dav.jianguoyun.com/dav/?token=sensitive',
])
def test_rejects_unsafe_or_non_root_url_before_network(url):
    fake = FakeDAV()
    with httpx.Client(transport=httpx.MockTransport(fake), follow_redirects=False) as client:
        with pytest.raises(ValueError):
            run_smoke(url, client, authorized=True)
    assert fake.calls == []


def test_explicit_authorization_required_before_network():
    fake = FakeDAV()
    with httpx.Client(transport=httpx.MockTransport(fake)) as client:
        with pytest.raises(ValueError, match='authorization'):
            run_smoke(ROOT, client)
    assert not fake.calls


@pytest.mark.parametrize('unsupported', ['MKCOL', 'PUT', 'GET', 'PROPFIND', 'MOVE', 'DELETE'])
def test_unsupported_operation_reported_and_formal_latest_untouched(unsupported):
    class Unsupported(FakeDAV):
        def __call__(self, request):
            if request.method == unsupported:
                self.calls.append((request.method, request.url.path))
                return httpx.Response(405)
            return super().__call__(request)
    fake = Unsupported()
    fake.files['/dav/DailyWorldBriefing/latest.epub'] = b'old'
    with httpx.Client(transport=httpx.MockTransport(fake), follow_redirects=False) as client:
        report = run_smoke(ROOT, client, authorized=True)
    assert not report['success']
    failed = [row for row in report['operations'] if not row['ok']]
    assert failed and 'unsupported' in failed[0]['reason']
    assert fake.files['/dav/DailyWorldBriefing/latest.epub'] == b'old'
    assert all(path.startswith('/dav/' + report['directory']) for _, path in fake.calls)
    if unsupported == 'DELETE':
        assert report['cleanup_errors']


def test_overwrite_move_unsupported_is_reported():
    class NoOverwrite(FakeDAV):
        def __call__(self, request):
            if request.method == 'MOVE' and request.headers.get('Overwrite') == 'T':
                self.calls.append((request.method, request.url.path))
                return httpx.Response(501)
            return super().__call__(request)
    fake = NoOverwrite()
    with httpx.Client(transport=httpx.MockTransport(fake)) as client:
        result = run_smoke(ROOT, client, authorized=True)
    failure = next(row for row in result['operations'] if not row['ok'])
    assert failure['operation'] == 'MOVE overwrite' and '501' in failure['reason']
    assert not result['cleanup_errors']


def test_unconfirmed_cli_reads_no_credentials_or_network(monkeypatch, capsys):
    monkeypatch.setenv('WEBDAV_PASSWORD', 'sensitive-password')
    with patch('src.uploader.webdav_smoke.httpx.Client') as client:
        with pytest.raises(SystemExit):
            main([])
    client.assert_not_called()
    assert 'sensitive-password' not in capsys.readouterr().err


@pytest.mark.parametrize('method', ['PUT', 'GET', 'PROPFIND', 'MOVE'])
def test_uploader_reports_unsupported_and_preserves_latest(method, tmp_path):
    class Unsupported(FakeDAV):
        def __call__(self, request):
            if request.method == method:
                return httpx.Response(405)
            return super().__call__(request)
    fake = Unsupported()
    fake.files['/reader/news/latest.epub'] = b'previous'
    config = WebDavConfig(enabled=True, url='https://dav.example', username='user', password='secret', remote_path='/reader/news')
    book = tmp_path / 'book.epub'
    book.write_bytes(b'new')
    with patch('src.uploader.webdav_uploader.get_webdav_config', return_value=config), \
         httpx.Client(transport=httpx.MockTransport(fake)) as client:
        uploader = WebDavUploader(client=client)
        assert not uploader.upload_epub(str(book), edition(b'new'))
    assert uploader.last_error == 'unsupported'
    assert method in uploader.last_result['error_detail']
    assert fake.files['/reader/news/latest.epub'] == b'previous'


def test_mkcol_405_missing_collection_is_not_treated_as_success(tmp_path):
    class NoMKCOL(FakeDAV):
        def __call__(self, request):
            if request.method == 'MKCOL':
                return httpx.Response(405)
            return super().__call__(request)
    fake = NoMKCOL()
    fake.files['/reader/news/latest.epub'] = b'previous'
    config = WebDavConfig(enabled=True, url='https://dav.example', username='user', password='secret', remote_path='/reader/news')
    book = tmp_path / 'book.epub'
    book.write_bytes(b'new')
    with patch('src.uploader.webdav_uploader.get_webdav_config', return_value=config), \
         httpx.Client(transport=httpx.MockTransport(fake)) as client:
        uploader = WebDavUploader(client=client)
        assert not uploader.upload_epub(str(book), edition(b'new'))
    assert uploader.last_error == 'unsupported' and 'MKCOL' in uploader.last_result['error_detail']
    assert fake.files['/reader/news/latest.epub'] == b'previous'


def test_plaintext_artifact_opt_in_and_encrypted_backup_unchanged():
    from pathlib import Path
    daily = Path('.github/workflows/daily-gather.yml').read_text()
    assert 'upload_plaintext_epub:' in daily
    input_block = daily.split('upload_plaintext_epub:', 1)[1].split('retry_failed:', 1)[0]
    assert 'default: false' in input_block
    assert "github.event_name == 'workflow_dispatch' && inputs.upload_plaintext_epub" in daily
    assert 'output/delivery-retry.enc' in daily


def test_unsupported_overwrite_move_preserves_original_latest(tmp_path):
    class NoOverwrite(FakeDAV):
        def __call__(self, request):
            if request.method == 'MOVE' and request.headers.get('Overwrite') == 'T':
                return httpx.Response(405)
            return super().__call__(request)
    fake = NoOverwrite()
    fake.files['/reader/news/latest.epub'] = b'previous'
    config = WebDavConfig(enabled=True, url='https://dav.example', username='user', password='secret', remote_path='/reader/news')
    book = tmp_path / 'book.epub'
    book.write_bytes(b'new')
    with patch('src.uploader.webdav_uploader.get_webdav_config', return_value=config), \
         httpx.Client(transport=httpx.MockTransport(fake)) as client:
        uploader = WebDavUploader(client=client)
        assert not uploader.upload_epub(str(book), edition(b'new'))
    assert uploader.last_error == 'unsupported' and 'MOVE' in uploader.last_result['error_detail']
    assert fake.files['/reader/news/latest.epub'] == b'previous'


def test_unsupported_delete_is_reported_without_erasing_previous(tmp_path):
    class NoDelete(FakeDAV):
        def __call__(self, request):
            if request.method == 'DELETE':
                return httpx.Response(405)
            return super().__call__(request)
    fake = NoDelete()
    fake.files['/reader/news/latest.epub'] = b'previous'
    config = WebDavConfig(enabled=True, url='https://dav.example', username='user', password='secret', remote_path='/reader/news')
    book = tmp_path / 'book.epub'
    book.write_bytes(b'new')
    with patch('src.uploader.webdav_uploader.get_webdav_config', return_value=config), \
         httpx.Client(transport=httpx.MockTransport(fake)) as client:
        uploader = WebDavUploader(client=client)
        assert uploader.upload_epub(str(book), edition(b'new'))
    assert 'DELETE' in uploader.last_result['cleanup_warning']
    assert fake.files['/reader/news/latest.previous.epub'] == b'previous'


def test_smoke_workflow_has_explicit_approval_and_no_production_state():
    from pathlib import Path
    workflow = Path('.github/workflows/webdav-smoke.yml').read_text()
    assert 'authorize:' in workflow and 'default: false' in workflow
    assert '--authorize-isolated' in workflow
    assert 'WEBDAV_REMOTE_PATH' not in workflow
    assert 'CONFIG_JSON' not in workflow and 'fetched_urls' not in workflow


def test_exception_details_never_enter_smoke_report():
    def broken(request):
        raise RuntimeError('sensitive-password')
    with httpx.Client(transport=httpx.MockTransport(broken)) as client:
        report = run_smoke(ROOT, client, authorized=True)
    assert not report['success']
    assert 'RuntimeError' in report['operations'][0]['reason']
    assert 'sensitive-password' not in json.dumps(report)
