"""Offline tests for servers that reject MOVE overwrite with HTTP 409."""
from unittest.mock import patch
from urllib.parse import urlsplit

import httpx
import pytest

from src.config import WebDavConfig
from src.uploader.webdav_uploader import WebDavUploader
from src.uploader.webdav_smoke import run_smoke
from tests.test_webdav_delivery import FakeDAV, edition
from tests.test_webdav_smoke import ROOT, PARENT, prepare_smoke


class ConflictDAV(FakeDAV):
    def __init__(self):
        super().__init__()
        self.direct_puts = []
        self.move_requests = []

    def __call__(self, request):
        path = request.url.path
        if request.method == 'MOVE':
            destination = urlsplit(request.headers['Destination']).path
            self.move_requests.append((path, destination, request.headers.get('Overwrite')))
            if request.headers.get('Overwrite') == 'T' and destination in self.files:
                self.calls.append((request.method, path))
                return httpx.Response(409)
        if request.method == 'PUT' and path in self.files:
            self.direct_puts.append((path, request.content))
            self.files[path] = request.content
            self.calls.append((request.method, path))
            return httpx.Response(204)
        return super().__call__(request)


def configured(server, book):
    config = WebDavConfig(enabled=True, url='https://dav.example', username='user', password='secret', remote_path='/reader/news')
    client = httpx.Client(transport=httpx.MockTransport(server), follow_redirects=False)
    with patch('src.uploader.webdav_uploader.get_webdav_config', return_value=config), \
         patch('src.uploader.webdav_uploader.time.sleep'):
        uploader = WebDavUploader(client=client)
        ok = uploader.upload_epub(str(book), edition(book.read_bytes()))
    client.close()
    return uploader, ok


def test_new_target_uses_move_without_overwrite(tmp_path):
    fake = ConflictDAV()
    book = tmp_path / 'edition.epub'
    book.write_bytes(b'new')
    uploader, ok = configured(fake, book)
    assert ok and not fake.direct_puts
    assert all(flag == 'F' for _, _, flag in fake.move_requests)
    assert fake.files['/reader/news/latest.epub'] == b'new'


def test_standard_webdav_uses_move_to_replace_existing_files(tmp_path):
    class Track(FakeDAV):
        def __init__(self):
            super().__init__()
            self.direct_puts, self.overwrites = [], []
        def __call__(self, request):
            if request.method == 'PUT' and request.url.path in self.files:
                self.direct_puts.append(request.url.path)
            if request.method == 'MOVE' and request.headers.get('Overwrite') == 'T':
                self.overwrites.append(urlsplit(request.headers['Destination']).path)
            return super().__call__(request)
    fake = Track()
    fake.files['/reader/news/latest.epub'] = b'old'
    fake.files['/reader/news/latest.previous.epub'] = b'older'
    book = tmp_path / 'edition.epub'
    book.write_bytes(b'new')
    uploader, ok = configured(fake, book)
    assert ok and fake.direct_puts == []
    assert set(fake.overwrites) == {'/reader/news/latest.epub', '/reader/news/latest.previous.epub'}
    assert uploader.last_result['overwrite_fallbacks'] == []


def test_409_put_overwrite_for_latest_and_existing_previous(tmp_path):
    fake = ConflictDAV()
    fake.files['/reader/news/latest.epub'] = b'old'
    fake.files['/reader/news/latest.previous.epub'] = b'older'
    book = tmp_path / 'edition.epub'
    book.write_bytes(b'new')
    uploader, ok = configured(fake, book)
    assert ok
    assert fake.files['/reader/news/latest.epub'] == b'new'
    assert fake.files['/reader/news/latest.previous.epub'] == b'old'
    assert fake.direct_puts == [('/reader/news/latest.previous.epub', b'old'), ('/reader/news/latest.epub', b'new')]
    assert [event['status'] for event in uploader.last_result['overwrite_fallbacks']] == [409, 409]


def test_corrupt_archive_is_repaired_with_verified_put(tmp_path):
    fake = ConflictDAV()
    book = tmp_path / 'edition.epub'
    book.write_bytes(b'new')
    archive = '/reader/news/archive/' + edition(b'new')
    fake.files[archive] = b'corrupt'
    fake.files['/reader/news/latest.epub'] = b'new'
    uploader, ok = configured(fake, book)
    assert ok and fake.files[archive] == b'new'
    assert (archive, b'new') in fake.direct_puts
    assert ('DELETE', archive) not in fake.calls
    assert uploader.last_result['overwrite_fallbacks'][0]['status'] == 409


def test_failed_put_verification_restores_previous_latest(tmp_path):
    class CorruptPUT(ConflictDAV):
        def __call__(self, request):
            if request.method == 'PUT' and request.url.path == '/reader/news/latest.epub' and request.content == b'new':
                self.calls.append((request.method, request.url.path))
                self.direct_puts.append((request.url.path, request.content))
                self.files[request.url.path] = b'corrupt'
                return httpx.Response(204)
            return super().__call__(request)
    fake = CorruptPUT()
    fake.files['/reader/news/latest.epub'] = b'old'
    book = tmp_path / 'edition.epub'
    book.write_bytes(b'new')
    uploader, ok = configured(fake, book)
    assert not ok and uploader.last_error == 'verification'
    assert uploader.last_result['recovery'] == 'restored'
    assert fake.files['/reader/news/latest.epub'] == b'old'
    assert fake.files['/reader/news/latest.previous.epub'] == b'old'
    assert ('/reader/news/latest.epub', b'old') in fake.direct_puts
    archive = '/reader/news/archive/' + edition(b'new')
    assert fake.files[archive] == b'new' and ('DELETE', archive) not in fake.calls


def test_network_failure_during_put_fallback_restores_old_latest(tmp_path):
    class Interrupted(ConflictDAV):
        def __call__(self, request):
            if request.method == 'PUT' and request.url.path == '/reader/news/latest.epub' and request.content == b'new':
                self.calls.append((request.method, request.url.path))
                self.direct_puts.append((request.url.path, request.content))
                self.files[request.url.path] = b'partial'
                raise httpx.ConnectError('interrupted', request=request)
            return super().__call__(request)
    fake = Interrupted()
    fake.files['/reader/news/latest.epub'] = b'old'
    book = tmp_path / 'edition.epub'
    book.write_bytes(b'new')
    uploader, ok = configured(fake, book)
    assert not ok and uploader.last_error == 'network'
    assert uploader.last_result['recovery'] == 'restored'
    assert fake.files['/reader/news/latest.epub'] == b'old'
    assert fake.files['/reader/news/latest.previous.epub'] == b'old'
    assert ('/reader/news/latest.epub', b'old') in fake.direct_puts
    assert fake.files['/reader/news/archive/' + edition(b'new')] == b'new'


@pytest.mark.parametrize('status,category', [(401, 'authentication'), (403, 'authentication'), (500, 'network'), (503, 'network')])
def test_other_move_failures_never_trigger_put_fallback(tmp_path, status, category):
    class Reject(ConflictDAV):
        def __call__(self, request):
            if request.method == 'MOVE' and request.headers.get('Overwrite') == 'T':
                self.calls.append((request.method, request.url.path))
                return httpx.Response(status)
            return super().__call__(request)
    fake = Reject()
    fake.files['/reader/news/latest.epub'] = b'old'
    book = tmp_path / 'edition.epub'
    book.write_bytes(b'new')
    uploader, ok = configured(fake, book)
    assert not ok and uploader.last_error == category
    assert fake.files['/reader/news/latest.epub'] == b'old'
    assert not any(path == '/reader/news/latest.epub' for path, _ in fake.direct_puts)
    assert uploader.last_result['overwrite_fallbacks'] == []


def test_409_on_new_target_is_conflict_not_success_or_put(tmp_path):
    class ConflictOnNew(ConflictDAV):
        def __call__(self, request):
            if request.method == 'MOVE' and request.headers.get('Overwrite') == 'F':
                self.calls.append((request.method, request.url.path))
                return httpx.Response(409)
            return super().__call__(request)
    fake = ConflictOnNew()
    fake.files['/reader/news/latest.epub'] = b'old'
    book = tmp_path / 'edition.epub'
    book.write_bytes(b'new')
    uploader, ok = configured(fake, book)
    assert not ok and uploader.last_error == 'conflict'
    assert '409' in uploader.last_result['error_detail']
    assert fake.direct_puts == [] and fake.files['/reader/news/latest.epub'] == b'old'


def test_smoke_continues_after_409_and_verifies_move_and_put():
    fake = prepare_smoke(ConflictDAV())
    fake.files['/dav/DailyWorldBriefing/latest.epub'] = b'old'
    with httpx.Client(transport=httpx.MockTransport(fake), follow_redirects=False) as client:
        result = run_smoke(ROOT, client, authorized=True)
    assert result['success'] and not result['cleanup_errors'] and result['residual_path'] is None
    assert result['capabilities']['move_new']['verified'] and result['capabilities']['move_new']['source_status'] == 404
    assert result['capabilities']['move_overwrite']['status'] == 409
    assert result['capabilities']['move_overwrite']['supported'] is False
    assert result['capabilities']['put_overwrite']['verified']
    names = [row['operation'] for row in result['operations']]
    assert names.index('GET source after MOVE new') == names.index('GET moved') + 1
    assert fake.files['/dav/DailyWorldBriefing/latest.epub'] == b'old'
    assert PARENT in fake.dirs


def test_smoke_detects_move_which_does_not_remove_source():
    class CopyInsteadOfMove(FakeDAV):
        def __call__(self, request):
            if request.method == 'MOVE' and request.headers.get('Overwrite') == 'F':
                destination = urlsplit(request.headers['Destination']).path
                self.calls.append((request.method, request.url.path))
                self.files[destination] = self.files[request.url.path]
                return httpx.Response(201)
            return super().__call__(request)
    fake = prepare_smoke(CopyInsteadOfMove())
    with httpx.Client(transport=httpx.MockTransport(fake)) as client:
        result = run_smoke(ROOT, client, authorized=True)
    assert not result['success'] and not result['capabilities']['move_new']['verified']
    assert result['capabilities']['move_new']['source_status'] == 200
    assert result['capabilities']['move_new']['supported'] is False
    assert any(row['operation'] == 'GET source after MOVE new' and row['status'] == 200 for row in result['operations'])


def test_409_followed_by_denied_put_is_not_success(tmp_path):
    class DenyPUT(ConflictDAV):
        def __call__(self, request):
            if request.method == 'PUT' and request.url.path == '/reader/news/latest.epub':
                self.calls.append((request.method, request.url.path))
                return httpx.Response(403)
            return super().__call__(request)
    fake = DenyPUT()
    fake.files['/reader/news/latest.epub'] = b'old'
    book = tmp_path / 'edition.epub'
    book.write_bytes(b'new')
    uploader, ok = configured(fake, book)
    assert not ok and uploader.last_error == 'authentication'
    assert fake.files['/reader/news/latest.epub'] == b'old'
    assert fake.files['/reader/news/latest.previous.epub'] == b'old'
    assert uploader.last_result['overwrite_fallbacks'][0]['status'] == 409
    assert fake.files['/reader/news/archive/' + edition(b'new')] == b'new'


def test_409_when_existing_archive_disappears_never_puts_target(tmp_path):
    class Vanished(ConflictDAV):
        def __call__(self, request):
            if request.method == 'MOVE' and request.headers.get('Overwrite') == 'T':
                destination = urlsplit(request.headers['Destination']).path
                if '/archive/' in destination:
                    self.calls.append((request.method, request.url.path))
                    self.files.pop(destination, None)
                    return httpx.Response(409)
            return super().__call__(request)
    fake = Vanished()
    book = tmp_path / 'edition.epub'
    book.write_bytes(b'new')
    archive = '/reader/news/archive/' + edition(b'new')
    fake.files[archive] = b'corrupt'
    fake.files['/reader/news/latest.epub'] = b'old'
    uploader, ok = configured(fake, book)
    assert not ok and uploader.last_error == 'conflict'
    assert 'not confirmed' in uploader.last_result['error_detail']
    assert not any(path == archive for path, _ in fake.direct_puts)
    assert ('DELETE', archive) not in fake.calls
    assert fake.files['/reader/news/latest.epub'] == b'old'


def test_failed_previous_backup_never_updates_latest(tmp_path):
    class CorruptBackup(ConflictDAV):
        def __call__(self, request):
            if request.method == 'PUT' and request.url.path == '/reader/news/latest.previous.epub':
                self.calls.append((request.method, request.url.path))
                self.files[request.url.path] = b'bad-backup'
                return httpx.Response(204)
            return super().__call__(request)
    fake = CorruptBackup()
    fake.files['/reader/news/latest.epub'] = b'old'
    fake.files['/reader/news/latest.previous.epub'] = b'older'
    book = tmp_path / 'edition.epub'
    book.write_bytes(b'new')
    uploader, ok = configured(fake, book)
    assert not ok and uploader.last_error == 'verification'
    assert fake.files['/reader/news/latest.epub'] == b'old'
    assert not any(destination == '/reader/news/latest.epub' for _, destination, _ in fake.move_requests)


def test_smoke_detects_put_overwrite_without_actual_replacement():
    class LyingPUT(ConflictDAV):
        def __call__(self, request):
            if request.method == 'PUT' and request.url.path.endswith('/put-overwrite.bin') and request.url.path in self.files:
                self.calls.append((request.method, request.url.path))
                return httpx.Response(204)
            return super().__call__(request)
    fake = prepare_smoke(LyingPUT())
    with httpx.Client(transport=httpx.MockTransport(fake)) as client:
        result = run_smoke(ROOT, client, authorized=True)
    assert not result['success'] and not result['capabilities']['put_overwrite']['verified']
    assert result['capabilities']['put_overwrite']['supported'] is False
    assert result['capabilities']['move_overwrite']['status'] == 409
    assert any(row['operation'] == 'GET PUT overwritten' and not row['ok'] for row in result['operations'])
    assert not result['cleanup_errors'] and result['residual_path'] is None


def test_transport_error_on_move_does_not_trigger_put_fallback(tmp_path):
    class MoveOffline(ConflictDAV):
        def __call__(self, request):
            if request.method == 'MOVE' and request.headers.get('Overwrite') == 'T':
                self.calls.append((request.method, request.url.path))
                raise httpx.ConnectError('private transport details', request=request)
            return super().__call__(request)
    fake = MoveOffline()
    fake.files['/reader/news/latest.epub'] = b'old'
    book = tmp_path / 'edition.epub'
    book.write_bytes(b'new')
    uploader, ok = configured(fake, book)
    assert not ok and uploader.last_error == 'network'
    assert not any(path == '/reader/news/latest.epub' for path, _ in fake.direct_puts)
    assert fake.files['/reader/news/latest.epub'] == b'old'
    assert 'private transport details' not in str(uploader.last_result)


def test_failed_restore_is_reported_and_previous_copy_survives(tmp_path):
    class RecoveryDenied(ConflictDAV):
        def __call__(self, request):
            if request.method == 'PUT' and request.url.path == '/reader/news/latest.epub':
                self.calls.append((request.method, request.url.path))
                if request.content == b'new':
                    self.files[request.url.path] = b'partial'
                    return httpx.Response(204)
                return httpx.Response(403)
            return super().__call__(request)
    fake = RecoveryDenied()
    fake.files['/reader/news/latest.epub'] = b'old'
    book = tmp_path / 'edition.epub'
    book.write_bytes(b'new')
    uploader, ok = configured(fake, book)
    assert not ok and uploader.last_error == 'verification'
    assert uploader.last_result['recovery'] == 'failed'
    assert fake.files['/reader/news/latest.previous.epub'] == b'old'
    assert fake.files['/reader/news/archive/' + edition(b'new')] == b'new'
