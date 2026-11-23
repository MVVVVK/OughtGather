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
PARENT = '/dav/OughtGatherSmoke'


def prepare_smoke(fake):
    fake.dirs.add(PARENT)
    return fake


def test_success_only_touches_owned_isolated_collection():
    fake = prepare_smoke(FakeDAV())
    fake.files['/dav/DailyWorldBriefing/latest.epub'] = b'valuable'
    fake.dirs.add('/dav/DailyWorldBriefing')
    with httpx.Client(transport=httpx.MockTransport(fake), follow_redirects=False) as client, \
         patch('src.dedup.tracker.DedupTracker', side_effect=AssertionError('no dedup access')):
        report = run_smoke(ROOT, client, authorized=True)
    assert report['success'] is True and not report['cleanup_errors']
    assert fake.files['/dav/DailyWorldBriefing/latest.epub'] == b'valuable'
    assert all(path.rstrip('/') == PARENT or path.startswith('/dav/' + report['directory']) for _, path in fake.calls)
    assert not any(method in ('MKCOL', 'DELETE') and path.rstrip('/') in ('/dav', PARENT) for method, path in fake.calls)
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
    fake = prepare_smoke(Unsupported())
    fake.files['/dav/DailyWorldBriefing/latest.epub'] = b'old'
    with httpx.Client(transport=httpx.MockTransport(fake), follow_redirects=False) as client:
        report = run_smoke(ROOT, client, authorized=True)
    assert not report['success']
    failed = [row for row in report['operations'] if not row['ok']]
    assert failed and 'unsupported' in failed[0]['reason']
    assert fake.files['/dav/DailyWorldBriefing/latest.epub'] == b'old'
    assert all(path.rstrip('/') == PARENT or path.startswith('/dav/' + report['directory']) for _, path in fake.calls)
    if unsupported == 'DELETE':
        assert report['cleanup_errors']


def test_overwrite_move_unsupported_is_reported():
    class NoOverwrite(FakeDAV):
        def __call__(self, request):
            if request.method == 'MOVE' and request.headers.get('Overwrite') == 'T':
                self.calls.append((request.method, request.url.path))
                return httpx.Response(501)
            return super().__call__(request)
    fake = prepare_smoke(NoOverwrite())
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


def test_missing_precreated_parent_stops_before_mkcol_and_delete():
    fake = FakeDAV()
    fake.files['/dav/DailyWorldBriefing/latest.epub'] = b'old'
    with httpx.Client(transport=httpx.MockTransport(fake), follow_redirects=False) as client:
        report = run_smoke(ROOT, client, authorized=True)
    assert not report['success'] and report['operations'][0]['status'] == 404
    assert report['operations'][0]['operation'] == 'PROPFIND test parent'
    assert report['residual_path'] is None
    assert not any(method in ('MKCOL', 'PUT', 'MOVE', 'DELETE') for method, _ in fake.calls)
    assert fake.files['/dav/DailyWorldBriefing/latest.epub'] == b'old'


def test_smoke_under_precreated_parent_with_root_mutations_rejected():
    import re
    class RootRestricted(FakeDAV):
        def __call__(self, request):
            path = request.url.path.rstrip('/')
            if request.method in ('MKCOL', 'DELETE') and path.count('/') == 2:
                self.calls.append((request.method, path))
                return httpx.Response(400)
            return super().__call__(request)
    fake = prepare_smoke(RootRestricted())
    fake.files['/dav/DailyWorldBriefing/latest.epub'] = b'old'
    with httpx.Client(transport=httpx.MockTransport(fake), follow_redirects=False) as client:
        report = run_smoke(ROOT, client, authorized=True)
    assert report['success']
    assert re.fullmatch(r'OughtGatherSmoke/t-[0-9a-f]{12}', report['directory'])
    assert report['residual_path'] is None
    assert not any(method in ('MKCOL', 'DELETE') and path.rstrip('/') in ('/dav', PARENT) for method, path in fake.calls)
    assert PARENT in fake.dirs and fake.files['/dav/DailyWorldBriefing/latest.epub'] == b'old'


def test_smoke_mkcol_400_reports_uncertain_path_without_deleting_it():
    class FailMKCOL(FakeDAV):
        def __call__(self, request):
            if request.method == 'MKCOL':
                self.calls.append((request.method, request.url.path))
                return httpx.Response(400)
            return super().__call__(request)
    fake = prepare_smoke(FailMKCOL())
    with httpx.Client(transport=httpx.MockTransport(fake), follow_redirects=False) as client:
        report = run_smoke(ROOT, client, authorized=True)
    failure = next(row for row in report['operations'] if not row['ok'])
    assert failure['operation'] == 'MKCOL' and failure['status'] == 400
    assert report['residual_path'] == report['directory'] and report['cleanup_actions'] == []
    assert not any(method == 'DELETE' for method, _ in fake.calls)
    assert PARENT in fake.dirs


def test_smoke_failure_cleans_only_created_child_and_reports_statuses():
    class FailPUT(FakeDAV):
        def __call__(self, request):
            if request.method == 'PUT':
                self.calls.append((request.method, request.url.path))
                return httpx.Response(403)
            return super().__call__(request)
    fake = prepare_smoke(FailPUT())
    fake.files['/dav/DailyWorldBriefing/latest.epub'] = b'old'
    with httpx.Client(transport=httpx.MockTransport(fake), follow_redirects=False) as client:
        report = run_smoke(ROOT, client, authorized=True)
    assert not report['success'] and not report['cleanup_errors']
    assert report['residual_path'] is None
    assert [row['status'] for row in report['cleanup_actions']] == [204, 204, 204, 204, 404]
    assert PARENT in fake.dirs and fake.files['/dav/DailyWorldBriefing/latest.epub'] == b'old'
    assert all(path.startswith('/dav/' + report['directory']) for method, path in fake.calls if method == 'DELETE')


def test_smoke_failed_cleanup_reports_residual_path():
    class FailDelete(FakeDAV):
        def __call__(self, request):
            if request.method == 'DELETE':
                self.calls.append((request.method, request.url.path))
                return httpx.Response(403)
            return super().__call__(request)
    fake = prepare_smoke(FailDelete())
    with httpx.Client(transport=httpx.MockTransport(fake), follow_redirects=False) as client:
        report = run_smoke(ROOT, client, authorized=True)
    assert report['residual_path'] == report['directory'] and report['cleanup_errors']
    assert any(row['status'] == 403 for row in report['cleanup_errors'])
    assert any(row['item'] == 'verify_collection_absent' for row in report['cleanup_errors'])
    assert PARENT in fake.dirs


def test_smoke_collision_never_cleans_unowned_subdirectory():
    from types import SimpleNamespace
    fake = prepare_smoke(FakeDAV())
    path = PARENT + '/t-' + 'a' * 12
    fake.dirs.add(path)
    fake.files[path + '/valuable'] = b'keep'
    with patch('src.uploader.webdav_smoke.uuid.uuid4', return_value=SimpleNamespace(hex='a' * 32)), \
         httpx.Client(transport=httpx.MockTransport(fake), follow_redirects=False) as client:
        report = run_smoke(ROOT, client, authorized=True)
    assert not report['success'] and report['residual_path'] == report['directory']
    assert report['operations'][-1]['status'] == 405
    assert 'already exist' in report['operations'][-1]['reason']
    assert not any(method == 'DELETE' for method, _ in fake.calls)
    assert fake.files[path + '/valuable'] == b'keep'


def test_smoke_checks_cleanup_when_server_does_not_remove_collection():
    class LyingDelete(FakeDAV):
        def __call__(self, request):
            path = request.url.path.rstrip('/')
            if request.method == 'PUT':
                self.calls.append((request.method, request.url.path))
                return httpx.Response(500)
            if request.method == 'DELETE' and '/t-' in path and path.count('/') == 3:
                self.calls.append((request.method, request.url.path))
                return httpx.Response(204)
            return super().__call__(request)
    fake = prepare_smoke(LyingDelete())
    with httpx.Client(transport=httpx.MockTransport(fake), follow_redirects=False) as client:
        report = run_smoke(ROOT, client, authorized=True)
    assert not report['success'] and report['residual_path'] == report['directory']
    assert any(row['item'] == 'verify_collection_absent' and row['status'] == 207
               for row in report['cleanup_errors'])
    assert PARENT in fake.dirs


def test_cli_invalid_root_reports_safe_actionable_configuration(monkeypatch, capsys):
    monkeypatch.setenv('WEBDAV_URL', 'https://dav.jianguoyun.com/dav/DailyWorldBriefing')
    monkeypatch.setenv('WEBDAV_USERNAME', 'test-user')
    monkeypatch.setenv('WEBDAV_PASSWORD', 'sensitive-password')
    with patch('src.uploader.webdav_smoke.httpx.Client') as client:
        assert main(['--authorize-isolated']) == 1
    client.assert_not_called()
    output = capsys.readouterr().out
    assert 'configuration' in output and 'Jianguoyun /dav root' in output
    assert 'sensitive-password' not in output and 'test-user' not in output


def test_smoke_parent_207_without_collection_never_mutates():
    class ParentIsFile(FakeDAV):
        def __call__(self, request):
            if request.method == 'PROPFIND' and request.url.path.rstrip('/') == PARENT:
                self.calls.append((request.method, request.url.path))
                xml = ('<d:multistatus xmlns:d="DAV:"><d:response><d:href>' + PARENT +
                       '</d:href><d:propstat><d:prop><d:resourcetype/></d:prop>'
                       '<d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response></d:multistatus>')
                return httpx.Response(207, text=xml)
            return super().__call__(request)
    fake = ParentIsFile()
    fake.files['/dav/DailyWorldBriefing/latest.epub'] = b'old'
    with httpx.Client(transport=httpx.MockTransport(fake), follow_redirects=False) as client:
        report = run_smoke(ROOT, client, authorized=True)
    assert not report['success'] and report['operations'][-1]['status'] == 207
    assert 'verification failed' in report['operations'][-1]['reason']
    assert not any(method in ('MKCOL', 'PUT', 'MOVE', 'DELETE') for method, _ in fake.calls)
    assert fake.files['/dav/DailyWorldBriefing/latest.epub'] == b'old'
