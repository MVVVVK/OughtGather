"""Offline end-to-end tests using an in-memory WebDAV server."""
import hashlib
import os
from datetime import datetime
from unittest.mock import MagicMock, patch
from urllib.parse import urlsplit

import httpx
import pytest
from cryptography.exceptions import InvalidTag

from src.config import WebDavConfig
from src.delivery.service import make_backup, decrypt_backup, deliver, retry_encrypted
from src.uploader.webdav_uploader import WebDavUploader


class FakeDAV:
    def __init__(self):
        self.files = {}
        self.dirs = set()
        self.calls = []
        self.fail_latest = False
        self.bad_stage = False
        self.fail_status = None
        self.transient = 0

    def __call__(self, request):
        path = request.url.path
        self.calls.append((request.method, path))
        if self.fail_status:
            return httpx.Response(self.fail_status)
        if self.transient:
            self.transient -= 1
            return httpx.Response(503)
        if request.method == 'MKCOL':
            existed = path in self.dirs
            self.dirs.add(path)
            return httpx.Response(405 if existed else 201)
        if request.method == 'GET':
            if path not in self.files:
                return httpx.Response(404)
            content = self.files[path]
            if self.bad_stage and '.oughtgather-' in path:
                content = b'corrupt'
            return httpx.Response(200, content=content)
        if request.method == 'PUT':
            self.files[path] = request.content
            return httpx.Response(201)
        if request.method == 'MOVE':
            dest = urlsplit(request.headers['Destination']).path
            if dest.endswith('/latest.epub') and self.fail_latest:
                return httpx.Response(500)
            if path not in self.files:
                return httpx.Response(404)
            if request.headers.get('Overwrite') == 'F' and dest in self.files:
                return httpx.Response(412)
            self.files[dest] = self.files.pop(path)
            return httpx.Response(201)
        if request.method == 'DELETE':
            self.files.pop(path, None)
            self.dirs.discard(path.rstrip('/'))
            return httpx.Response(204)
        if request.method == 'PROPFIND':
            if request.headers.get('Depth') == '0':
                if path.rstrip('/') not in self.dirs:
                    return httpx.Response(404)
                body = ('<d:multistatus xmlns:d="DAV:"><d:response><d:href>' + path +
                        '</d:href><d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop><d:status>HTTP/1.1 200 OK</d:status>'
                        '</d:propstat></d:response></d:multistatus>')
                return httpx.Response(207, content=body.encode())
            children = ''.join('<d:response><d:href>' + name + '</d:href></d:response>'
                               for name in self.files if name.startswith(path))
            return httpx.Response(207, content=('<d:multistatus xmlns:d="DAV:">' + children + '</d:multistatus>').encode())
        raise AssertionError(request.method)


@pytest.fixture
def dav(monkeypatch):
    config = WebDavConfig(enabled=True, url='https://dav.example', username='user', password='pass', remote_path='/reader/news')
    fake = FakeDAV()
    client = httpx.Client(transport=httpx.MockTransport(fake), auth=('user', 'pass'), follow_redirects=False)
    with patch('src.uploader.webdav_uploader.get_webdav_config', return_value=config), \
         patch('src.uploader.webdav_uploader.time.sleep'):
        yield WebDavUploader(client=client), fake
    client.close()


def edition(content, date='2026-11-23T063000'):
    return date + '-' + hashlib.sha256(content).hexdigest() + '.epub'


def test_archive_latest_verified_and_idempotent(dav, tmp_path):
    uploader, server = dav
    book = tmp_path / 'a.epub'
    book.write_bytes(b'edition one')
    name = edition(book.read_bytes())
    assert uploader.upload_epub(str(book), name)
    assert server.files['/reader/news/archive/' + name] == b'edition one'
    assert server.files['/reader/news/latest.epub'] == b'edition one'
    before = len([x for x in server.calls if x[0] == 'PUT'])
    assert uploader.upload_epub(str(book), name)
    assert len([x for x in server.calls if x[0] == 'PUT']) == before


def test_failed_latest_move_preserves_previous(dav, tmp_path):
    uploader, server = dav
    old = b'previous'
    server.files['/reader/news/latest.epub'] = old
    server.fail_latest = True
    book = tmp_path / 'a.epub'
    book.write_bytes(b'new version')
    assert not uploader.upload_epub(str(book), edition(book.read_bytes()))
    assert uploader.last_error == 'network'
    assert server.files['/reader/news/latest.epub'] == old
    assert server.files['/reader/news/latest.previous.epub'] == old


def test_verification_failure_preserves_previous(dav, tmp_path):
    uploader, server = dav
    server.files['/reader/news/latest.epub'] = b'old'
    server.bad_stage = True
    book = tmp_path / 'a.epub'
    book.write_bytes(b'new')
    assert not uploader.upload_epub(str(book), edition(b'new'))
    assert uploader.last_error == 'verification'
    assert server.files['/reader/news/latest.epub'] == b'old'


def test_network_retries_and_auth_does_not_retry(dav, tmp_path):
    uploader, server = dav
    book = tmp_path / 'a.epub'
    book.write_bytes(b'new')
    server.transient = 2
    assert uploader.upload_epub(str(book), edition(b'new'))
    assert len([c for c in server.calls if c[0] == 'PROPFIND']) >= 5
    server.calls.clear()
    server.fail_status = 401
    assert not uploader.upload_epub(str(book), edition(b'new'))
    assert uploader.last_error == 'authentication' and len(server.calls) == 1


def test_retention_and_older_retry_does_not_replace_latest(dav, tmp_path):
    uploader, server = dav
    old = edition(b'old', '2026-09-01T063000')
    newer = edition(b'newer', '2026-11-22T063000')
    server.files['/reader/news/archive/' + old] = b'old'
    server.files['/reader/news/archive/' + newer] = b'newer'
    server.files['/reader/news/archive/keep-other.epub'] = b'other'
    server.files['/reader/news/latest.epub'] = b'newer'
    book = tmp_path / 'a.epub'
    book.write_bytes(b'middle')
    with patch('src.uploader.webdav_uploader.get_now', return_value=datetime(2026, 11, 23)):
        assert uploader.upload_epub(str(book), edition(b'middle', '2026-11-21T063000'))
    assert server.files['/reader/news/latest.epub'] == b'newer'
    assert '/reader/news/archive/' + old not in server.files
    assert '/reader/news/archive/keep-other.epub' in server.files


def test_encrypted_backup_roundtrip_tamper_and_independent_retry(dav, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    secret = 'x' * 48
    monkeypatch.setenv('DELIVERY_BACKUP_KEY', secret)
    uploader, server = dav
    book = tmp_path / 'private.epub'
    book.write_bytes(b'private articles')
    name = make_backup(str(book), secret)
    encrypted = tmp_path / 'output/delivery-retry.enc'
    assert b'private articles' not in encrypted.read_bytes()
    assert decrypt_backup(str(encrypted), secret) == (name, b'private articles')
    damaged = tmp_path / 'damaged.enc'
    blob = bytearray(encrypted.read_bytes())
    blob[-1] ^= 1
    damaged.write_bytes(blob)
    with pytest.raises(InvalidTag):
        decrypt_backup(str(damaged), secret)
    with patch('src.delivery.service.WebDavUploader', return_value=uploader), \
         patch('src.dedup.tracker.DedupTracker', side_effect=AssertionError('dedup must not be loaded')):
        assert retry_encrypted(str(encrypted))
    assert server.files['/reader/news/latest.epub'] == b'private articles'


def test_failed_upload_email_notification_or_optional_attachment(dav, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    uploader, server = dav
    server.fail_status = 403
    book = tmp_path / 'book.epub'
    book.write_bytes(b'private')
    name = make_backup(str(book), 'x' * 48)
    sender = MagicMock()
    sender.send_notification.return_value = True
    sender.send_epub.return_value = True
    result = deliver(str(book), name, uploader=uploader, sender_factory=lambda: sender)
    assert result['error'] == 'authentication' and result['email'] == 'sent'
    sender.send_notification.assert_called_once()
    sender.send_epub.assert_not_called()
    assert (tmp_path / 'output/delivery-retry.enc').exists()
    monkeypatch.setenv('EMAIL_FALLBACK_ATTACHMENT', 'true')
    sender.reset_mock()
    result = deliver(str(book), name, uploader=uploader, sender_factory=lambda: sender)
    assert result['email'] == 'sent'
    sender.send_epub.assert_called_once()


def test_missing_backup_key_stops_before_fetch_or_dedup(tmp_path, monkeypatch):
    from src.main import main
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('WEBDAV_ENABLED', 'true')
    monkeypatch.delenv('DELIVERY_BACKUP_KEY', raising=False)
    with patch('src.main.load_config') as load, patch('src.main.DedupTracker') as tracker:
        with pytest.raises(SystemExit):
            main([])
    load.assert_not_called()
    tracker.assert_not_called()


def test_test_mode_never_initializes_webdav_or_email(tmp_path, monkeypatch):
    from src.config import ContentSource
    from src.fetchers.rss_fetcher import RSSFetcher
    from src.main import main
    from tests.test_rss_publication_dedup import item, feed, FEED_URL
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('WEBDAV_ENABLED', 'true')
    monkeypatch.delenv('DELIVERY_BACKUP_KEY', raising=False)
    source = ContentSource(type='rss', src=FEED_URL)
    with patch('src.main.load_config', return_value=MagicMock(body=[source], limit=2)), \
         patch.object(RSSFetcher, '_parse_feed', return_value=feed([item(1)])), \
         patch('src.main.EPUBGenerator') as generator, \
         patch('src.main.SMTPSender') as sender, \
         patch('src.uploader.webdav_uploader.WebDavUploader') as uploader:
        generator.return_value.generate.return_value = 'test.epub'
        main(['--test-mode'])
    sender.assert_not_called()
    uploader.assert_not_called()
    assert not (tmp_path / 'output/delivery-retry.enc').exists()


def test_generic_email_address_and_legacy_fallback(monkeypatch):
    from src.config import get_smtp_config
    for key, value in {'SMTP_HOST': 'smtp.example.org', 'SMTP_PORT': '465',
                       'SMTP_USERNAME': 'user', 'SMTP_PASSWORD': 'pass',
                       'KINDLE_EMAIL': 'old@example.org', 'EMAIL_RECIPIENT': 'new@example.org'}.items():
        monkeypatch.setenv(key, value)
    assert get_smtp_config()['recipient'] == 'new@example.org'
    monkeypatch.delenv('EMAIL_RECIPIENT')
    assert get_smtp_config()['recipient'] == 'old@example.org'


def test_backup_retry_does_not_delete_unrelated_pending_file(dav, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('DELIVERY_BACKUP_KEY', 'x' * 48)
    book = tmp_path / 'one.epub'
    book.write_bytes(b'one')
    make_backup(str(book), 'x' * 48)
    first = (tmp_path / 'output/delivery-retry.enc').read_bytes()
    recovery = tmp_path / 'recovery.enc'
    recovery.write_bytes(first)
    book.write_bytes(b'two')
    make_backup(str(book), 'x' * 48)
    pending = (tmp_path / 'output/delivery-retry.enc').read_bytes()
    uploader, _ = dav
    with patch('src.delivery.service.WebDavUploader', return_value=uploader):
        assert retry_encrypted(str(recovery))
    assert (tmp_path / 'output/delivery-retry.enc').read_bytes() == pending


def test_cron_and_retry_workflow_are_isolated():
    from pathlib import Path
    daily = Path('.github/workflows/daily-gather.yml').read_text()
    retry = Path('.github/workflows/retry-delivery.yml').read_text()
    assert "cron: '30 22 * * *'" in daily
    assert '18h cooldown' not in daily and 'timedelta(hours=18)' not in daily
    assert 'delivery-retry.enc' in daily and 'retention-days: 30' in daily
    assert 'python -m src.delivery' in retry
    assert 'fetched_urls.txt' not in retry and 'src/main.py' not in retry


def test_retrying_older_edition_restores_missing_latest_from_newest_archive(dav, tmp_path):
    uploader, server = dav
    newer = edition(b'newer', '2026-11-22T063000')
    server.files['/reader/news/archive/' + newer] = b'newer'
    book = tmp_path / 'old.epub'
    book.write_bytes(b'older')
    with patch('src.uploader.webdav_uploader.get_now', return_value=datetime(2026, 11, 23)):
        assert uploader.upload_epub(str(book), edition(b'older', '2026-11-21T063000'))
    assert server.files['/reader/news/latest.epub'] == b'newer'


def test_transport_errors_receive_bounded_retries(tmp_path):
    config = WebDavConfig(enabled=True, url='https://dav.example', username='u', password='p', remote_path='/news')
    calls = []
    def broken(request):
        calls.append(request)
        raise httpx.ConnectError('offline', request=request)
    book = tmp_path / 'book.epub'
    book.write_bytes(b'book')
    with patch('src.uploader.webdav_uploader.get_webdav_config', return_value=config), \
         patch('src.uploader.webdav_uploader.time.sleep'), \
         httpx.Client(transport=httpx.MockTransport(broken)) as client:
        uploader = WebDavUploader(client=client)
        assert not uploader.upload_epub(str(book), edition(b'book'))
    assert len(calls) == 3 and uploader.last_error == 'network'


def test_unexpected_webdav_error_still_notifies_and_keeps_backup(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    book = tmp_path / 'a.epub'
    book.write_bytes(b'book')
    name = make_backup(str(book), 'x' * 48)
    sender = MagicMock()
    sender.send_notification.return_value = True
    with patch('src.delivery.service.WebDavUploader', side_effect=ValueError('missing config')):
        result = deliver(str(book), name, sender_factory=lambda: sender)
    assert result['webdav'] == 'failed' and result['email'] == 'sent'
    assert (tmp_path / 'output/delivery-retry.enc').exists()


def test_failed_latest_can_retry_same_edition_and_repair_corrupt_archive(dav, tmp_path):
    uploader, server = dav
    server.files['/reader/news/latest.epub'] = b'previous'
    server.fail_latest = True
    book = tmp_path / 'book.epub'
    book.write_bytes(b'new')
    name = edition(b'new')
    assert not uploader.upload_epub(str(book), name)
    server.fail_latest = False
    server.files['/reader/news/archive/' + name] = b'corrupt'
    assert uploader.upload_epub(str(book), name)
    assert server.files['/reader/news/archive/' + name] == b'new'
    assert server.files['/reader/news/latest.epub'] == b'new'
    assert server.files['/reader/news/latest.previous.epub'] == b'previous'


def test_daily_failure_then_retry_same_epub_preserves_published_history(dav, tmp_path, monkeypatch):
    import json
    from src.config import _parse_config
    from src.dedup.tracker import DedupTracker
    from src.fetchers.rss_fetcher import RSSFetcher
    from src.main import main
    from tests.test_rss_publication_dedup import FEED_URL, item, feed

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('WEBDAV_ENABLED', 'true')
    monkeypatch.setenv('DELIVERY_BACKUP_KEY', 'x' * 48)
    monkeypatch.delenv('EMAIL_FALLBACK_ATTACHMENT', raising=False)
    config = _parse_config({'title': {'text': 'Daily', 'img': ''},
                            'body': [{'type': 'rss', 'src': FEED_URL}]})
    tracker_path = tmp_path / 'history'
    tracker = DedupTracker(str(tracker_path))
    book = tmp_path / 'daily.epub'
    book.write_bytes(b'original private epub')
    uploader, server = dav
    server.fail_status = 503
    sender = MagicMock()
    sender.send_notification.return_value = True
    with patch('src.main.load_config', return_value=config), \
         patch('src.main.DedupTracker', return_value=tracker), \
         patch.object(RSSFetcher, '_parse_feed', return_value=feed([item(1)])), \
         patch('src.main.EPUBGenerator') as generator, \
         patch('src.main.SMTPSender', return_value=sender), \
         patch('src.delivery.service.WebDavUploader', return_value=uploader):
        generator.return_value.generate.return_value = str(book)
        main([])
    assert json.loads((tmp_path / 'output/delivery-status.json').read_text())['webdav'] == 'failed'
    backup = tmp_path / 'output/delivery-retry.enc'
    assert backup.exists()
    assert tracker.is_fetched(item(1)['link'], tracker.make_source_key(config.body[0]))
    saved_history = tracker_path.read_bytes()
    sender.send_notification.assert_called_once()
    server.fail_status = None
    with patch('src.delivery.service.WebDavUploader', return_value=uploader), \
         patch('src.dedup.tracker.DedupTracker', side_effect=AssertionError('must not touch dedup')):
        assert retry_encrypted(str(backup))
    assert tracker_path.read_bytes() == saved_history
    assert server.files['/reader/news/latest.epub'] == b'original private epub'


def test_retry_failed_keeps_published_history_and_does_not_deliver(tmp_path, monkeypatch):
    from src.config import _parse_config
    from src.dedup.tracker import DedupTracker
    from src.fetchers.rss_fetcher import RSSFetcher
    from src.main import main
    from tests.test_rss_publication_dedup import FEED_URL, item, feed
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('WEBDAV_ENABLED', 'true')
    monkeypatch.setenv('DELIVERY_BACKUP_KEY', 'x' * 48)
    config = _parse_config({'title': {'text': 'Daily', 'img': ''}, 'body': [{'type': 'rss', 'src': FEED_URL}]})
    path = tmp_path / 'history'
    tracker = DedupTracker(str(path))
    key = tracker.make_source_key(config.body[0])
    tracker.mark_as_fetched(item(1)['link'], key)
    tracker.record_failure(item(2)['link'], key)
    tracker.save()
    with patch('src.main.load_config', return_value=config), \
         patch('src.main.DedupTracker', return_value=tracker), \
         patch.object(RSSFetcher, '_parse_feed', return_value=feed([item(1)])), \
         patch('src.delivery.service.WebDavUploader') as uploader, \
         patch('src.main.SMTPSender') as sender:
        main(['--retry-failed'])
    persisted = DedupTracker(str(path))
    assert persisted.is_fetched(item(1)['link'], key)
    assert persisted.retry_count(item(2)['link'], key) == 0
    uploader.assert_not_called()
    sender.assert_not_called()


def test_fresh_start_does_not_require_delivery_key(tmp_path, monkeypatch):
    from src.config import _parse_config
    from src.dedup.tracker import DedupTracker
    from src.fetchers.rss_fetcher import RSSFetcher
    from src.main import main
    from tests.test_rss_publication_dedup import FEED_URL, item, feed
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('WEBDAV_ENABLED', 'true')
    monkeypatch.delenv('DELIVERY_BACKUP_KEY', raising=False)
    config = _parse_config({'title': {'text': 'Daily', 'img': ''}, 'body': [{'type': 'rss', 'src': FEED_URL}]})
    tracker = DedupTracker(str(tmp_path / 'history'))
    with patch('src.main.load_config', return_value=config), \
         patch('src.main.DedupTracker', return_value=tracker), \
         patch.object(RSSFetcher, '_parse_feed', return_value=feed([item(1)])), \
         patch('src.delivery.service.WebDavUploader') as uploader, \
         patch('src.main.SMTPSender') as sender:
        main(['--fresh-start'])
    assert tracker.is_fetched(item(1)['link'], tracker.make_source_key(config.body[0]))
    uploader.assert_not_called()
    sender.assert_not_called()


def test_verification_error_after_move_restores_previous_latest(tmp_path):
    class CorruptOnce(FakeDAV):
        corrupt_next_latest_read = False
        def __call__(self, request):
            if request.method == 'GET' and request.url.path.endswith('/latest.epub') and self.corrupt_next_latest_read:
                self.corrupt_next_latest_read = False
                return httpx.Response(200, content=b'corrupt')
            result = super().__call__(request)
            if request.method == 'MOVE' and urlsplit(request.headers['Destination']).path.endswith('/latest.epub') and result.status_code == 201:
                self.corrupt_next_latest_read = True
            return result
    fake = CorruptOnce()
    fake.files['/reader/news/latest.epub'] = b'old'
    config = WebDavConfig(enabled=True, url='https://dav.example', username='u', password='p', remote_path='/reader/news')
    book = tmp_path / 'book.epub'
    book.write_bytes(b'new')
    with patch('src.uploader.webdav_uploader.get_webdav_config', return_value=config), \
         httpx.Client(transport=httpx.MockTransport(fake)) as client:
        uploader = WebDavUploader(client=client)
        assert not uploader.upload_epub(str(book), edition(b'new'))
    assert fake.files['/reader/news/latest.epub'] == b'old'
    assert fake.files['/reader/news/latest.previous.epub'] == b'old'


def test_backup_creation_failure_keeps_pr3_publication_history(tmp_path, monkeypatch):
    from src.config import _parse_config
    from src.dedup.tracker import DedupTracker
    from src.fetchers.rss_fetcher import RSSFetcher
    from src.main import main
    from tests.test_rss_publication_dedup import FEED_URL, item, feed
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('WEBDAV_ENABLED', 'true')
    monkeypatch.setenv('DELIVERY_BACKUP_KEY', 'x' * 48)
    config = _parse_config({'title': {'text': 'Daily', 'img': ''}, 'body': [{'type': 'rss', 'src': FEED_URL}]})
    path = tmp_path / 'history'
    tracker = DedupTracker(str(path))
    with patch('src.main.load_config', return_value=config), \
         patch('src.main.DedupTracker', return_value=tracker), \
         patch.object(RSSFetcher, '_parse_feed', return_value=feed([item(1)])), \
         patch('src.main.EPUBGenerator') as generator, \
         patch('src.delivery.service.make_backup', side_effect=OSError('disk unavailable')):
        generator.return_value.generate.return_value = 'book.epub'
        main([])
    persisted = DedupTracker(str(path))
    assert persisted.is_fetched(item(1)['link'], tracker.make_source_key(config.body[0]))
    import json
    status = json.loads((tmp_path / 'output/delivery-status.json').read_text())
    assert status['webdav'] == 'failed' and status['error'] == 'backup_unavailable'


def test_configurable_retention_preserves_boundary_day(dav, tmp_path, monkeypatch):
    uploader, server = dav
    monkeypatch.setenv('WEBDAV_RETENTION_DAYS', '2')
    expired = edition(b'expired', '2026-11-21T063000')
    boundary = edition(b'boundary', '2026-11-22T063000')
    server.files['/reader/news/archive/' + expired] = b'expired'
    server.files['/reader/news/archive/' + boundary] = b'boundary'
    book = tmp_path / 'current.epub'
    book.write_bytes(b'current')
    with patch('src.uploader.webdav_uploader.get_now', return_value=datetime(2026, 11, 23)):
        assert uploader.upload_epub(str(book), edition(b'current'))
    assert '/reader/news/archive/' + expired not in server.files
    assert '/reader/news/archive/' + boundary in server.files


def test_quota_error_is_classified_without_retry(dav, tmp_path):
    uploader, server = dav
    server.fail_status = 507
    book = tmp_path / 'book.epub'
    book.write_bytes(b'book')
    assert not uploader.upload_epub(str(book), edition(b'book'))
    assert uploader.last_error == 'quota' and len(server.calls) == 1


def test_existing_daily_and_archive_collections_are_reused_without_mkcol(tmp_path):
    fake = FakeDAV()
    fake.dirs.update({'/dav/DailyWorldBriefing', '/dav/DailyWorldBriefing/archive'})
    fake.files['/dav/DailyWorldBriefing/latest.epub'] = b'old'
    config = WebDavConfig(enabled=True, url='https://dav.example/dav', username='u', password='p', remote_path='/DailyWorldBriefing')
    book = tmp_path / 'book.epub'
    book.write_bytes(b'new')
    with patch('src.uploader.webdav_uploader.get_webdav_config', return_value=config), \
         httpx.Client(transport=httpx.MockTransport(fake)) as client:
        uploader = WebDavUploader(client=client)
        assert uploader.upload_epub(str(book), edition(b'new'))
    assert not any(method == 'MKCOL' for method, _ in fake.calls)
    assert ('PROPFIND', '/dav/DailyWorldBriefing/') in fake.calls
    assert ('PROPFIND', '/dav/DailyWorldBriefing/archive/') in fake.calls
    assert fake.files['/dav/DailyWorldBriefing/latest.previous.epub'] == b'old'


def test_existing_daily_collection_creates_archive_without_root_mkcol(tmp_path):
    class RootRestricted(FakeDAV):
        def __call__(self, request):
            path = request.url.path.rstrip('/')
            if request.method in ('MKCOL', 'DELETE') and path.count('/') == 2:
                self.calls.append((request.method, path))
                return httpx.Response(400)
            return super().__call__(request)
    fake = RootRestricted()
    fake.dirs.add('/dav/DailyWorldBriefing')
    fake.files['/dav/DailyWorldBriefing/latest.epub'] = b'old'
    config = WebDavConfig(enabled=True, url='https://dav.example/dav', username='u', password='p', remote_path='/DailyWorldBriefing')
    book = tmp_path / 'book.epub'
    book.write_bytes(b'new')
    with patch('src.uploader.webdav_uploader.get_webdav_config', return_value=config), \
         httpx.Client(transport=httpx.MockTransport(fake)) as client:
        uploader = WebDavUploader(client=client)
        assert uploader.upload_epub(str(book), edition(b'new'))
    assert ('PROPFIND', '/dav/DailyWorldBriefing/archive/') in fake.calls
    assert ('MKCOL', '/dav/DailyWorldBriefing/archive') in fake.calls
    assert not any(method in ('MKCOL', 'DELETE') and path.rstrip('/') == '/dav/DailyWorldBriefing' for method, path in fake.calls)
    assert fake.files['/dav/DailyWorldBriefing/latest.previous.epub'] == b'old'


@pytest.mark.parametrize('code,category', [(400, 'remote'), (403, 'authentication'), (405, 'unsupported')])
def test_archive_mkcol_failures_are_distinct_and_latest_is_safe(tmp_path, code, category):
    class ArchiveMKCOLFailure(FakeDAV):
        def __call__(self, request):
            if request.method == 'MKCOL' and request.url.path == '/dav/DailyWorldBriefing/archive':
                self.calls.append((request.method, request.url.path))
                return httpx.Response(code)
            return super().__call__(request)
    fake = ArchiveMKCOLFailure()
    fake.dirs.add('/dav/DailyWorldBriefing')
    fake.files['/dav/DailyWorldBriefing/latest.epub'] = b'old'
    config = WebDavConfig(enabled=True, url='https://dav.example/dav', username='u', password='p', remote_path='/DailyWorldBriefing')
    book = tmp_path / 'book.epub'
    book.write_bytes(b'new')
    with patch('src.uploader.webdav_uploader.get_webdav_config', return_value=config), \
         httpx.Client(transport=httpx.MockTransport(fake)) as client:
        uploader = WebDavUploader(client=client)
        assert not uploader.upload_epub(str(book), edition(b'new'))
    assert uploader.last_error == category
    assert 'MKCOL' in uploader.last_result['error_detail'] and str(code) in uploader.last_result['error_detail']
    assert fake.files['/dav/DailyWorldBriefing/latest.epub'] == b'old'
    assert not any(method in ('PUT', 'MOVE', 'DELETE') for method, _ in fake.calls)


def test_mkcol_405_is_accepted_only_after_racing_directory_is_verified(tmp_path):
    class Race(FakeDAV):
        def __call__(self, request):
            if request.method == 'MKCOL' and request.url.path == '/dav/DailyWorldBriefing/archive':
                self.calls.append((request.method, request.url.path))
                self.dirs.add(request.url.path)
                return httpx.Response(405)
            return super().__call__(request)
    fake = Race()
    fake.dirs.add('/dav/DailyWorldBriefing')
    config = WebDavConfig(enabled=True, url='https://dav.example/dav', username='u', password='p', remote_path='/DailyWorldBriefing')
    book = tmp_path / 'book.epub'
    book.write_bytes(b'new')
    with patch('src.uploader.webdav_uploader.get_webdav_config', return_value=config), \
         httpx.Client(transport=httpx.MockTransport(fake)) as client:
        assert WebDavUploader(client=client).upload_epub(str(book), edition(b'new'))
    assert fake.calls.count(('PROPFIND', '/dav/DailyWorldBriefing/archive/')) >= 2


def test_ambiguous_207_never_creates_directory_or_touches_latest(tmp_path):
    class Ambiguous(FakeDAV):
        def __call__(self, request):
            if request.method == 'PROPFIND':
                self.calls.append((request.method, request.url.path))
                return httpx.Response(207, content=b'<d:multistatus xmlns:d="DAV:"/>')
            return super().__call__(request)
    fake = Ambiguous()
    fake.files['/dav/DailyWorldBriefing/latest.epub'] = b'old'
    config = WebDavConfig(enabled=True, url='https://dav.example/dav', username='u', password='p', remote_path='/DailyWorldBriefing')
    book = tmp_path / 'book.epub'
    book.write_bytes(b'new')
    with patch('src.uploader.webdav_uploader.get_webdav_config', return_value=config), \
         httpx.Client(transport=httpx.MockTransport(fake)) as client:
        uploader = WebDavUploader(client=client)
        assert not uploader.upload_epub(str(book), edition(b'new'))
    assert uploader.last_error == 'remote' and 'PROPFIND' in uploader.last_result['error_detail']
    assert not any(method in ('MKCOL', 'PUT', 'MOVE', 'DELETE') for method, _ in fake.calls)
    assert fake.files['/dav/DailyWorldBriefing/latest.epub'] == b'old'
