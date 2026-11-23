"""Transactional WebDAV delivery of dated EPUBs."""
import hashlib
import os
import re
import time
import uuid
from datetime import datetime, timedelta
from urllib.parse import quote, unquote, urlsplit
from xml.etree import ElementTree

import httpx
from src.config import get_webdav_config
from src.utils.helpers import get_now
from src.utils.logger import get_logger

ARCHIVE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{6}-[0-9a-f]{64}\.epub$')


class DeliveryError(Exception):
    def __init__(self, category, message):
        self.category = category
        super().__init__(message)


class WebDavUploader:
    def __init__(self, client=None):
        self.logger = get_logger()
        self.config = get_webdav_config()
        self._client = client
        self.last_error = None
        self.last_result = {}

    def _request(self, method, url, *, allowed=(200, 201, 204), **kwargs):
        for attempt in range(3):
            try:
                response = self._client.request(method, url, **kwargs)
                code = response.status_code
                if code in allowed:
                    return response
                if code in (405, 501):
                    raise DeliveryError('unsupported', f'{method} is unsupported (HTTP {code})')
                if code in (401, 403):
                    raise DeliveryError('authentication', f'{method} HTTP {code} (authentication or permission denied)')
                if code in (429, 500, 502, 503, 504):
                    if attempt < 2:
                        time.sleep(0.2 * 2 ** attempt)
                        continue
                    raise DeliveryError('network', f'WebDAV HTTP {code}')
                if code == 507:
                    raise DeliveryError('quota', 'WebDAV storage quota exceeded')
                if 300 <= code < 400:
                    raise DeliveryError('redirect', 'WebDAV redirect refused')
                raise DeliveryError('remote', f'{method} HTTP {code}')
            except httpx.TransportError as exc:
                if attempt == 2:
                    raise DeliveryError('network', 'WebDAV connection failed') from exc
                time.sleep(0.2 * 2 ** attempt)
        raise DeliveryError('network', 'WebDAV retry limit exceeded')

    def _setup(self):
        parsed = urlsplit(self.config.url or '')
        if parsed.scheme not in ('https', 'http') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise DeliveryError('configuration', 'Invalid WebDAV base URL')
        parts = (self.config.remote_path or '/').strip('/').split('/')
        if any(p in ('.', '..') or '\\' in p for p in parts):
            raise DeliveryError('configuration', 'Invalid WebDAV remote directory')
        self.base = self.config.url.rstrip('/')
        for part in filter(None, parts):
            self.base += '/' + quote(part, safe='')
            self._ensure_collection(self.base)
        self.archive = self.base + '/archive'
        self._ensure_collection(self.archive)

    def _collection_exists(self, url):
        """Only an explicit 404 proves absence; ambiguous responses fail closed."""
        listing = self._request('PROPFIND', url + '/', headers={'Depth': '0', 'Content-Type': 'application/xml'},
                                content=b'<d:propfind xmlns:d="DAV:"><d:prop><d:resourcetype/></d:prop></d:propfind>',
                                allowed=(207, 404))
        if listing.status_code == 404:
            return False
        try:
            root = ElementTree.fromstring(listing.content)
            expected = unquote(urlsplit(url).path).rstrip('/')
            exists = any(
                unquote(urlsplit(item.findtext('{DAV:}href', '')).path).rstrip('/') == expected
                and any(re.search(r'\s2\d\d(?:\s|$)', prop.findtext('{DAV:}status', ''))
                        and prop.find('{DAV:}prop/{DAV:}resourcetype/{DAV:}collection') is not None
                        for prop in item.findall('{DAV:}propstat'))
                for item in root.findall('{DAV:}response')
            )
        except ElementTree.ParseError as exc:
            raise DeliveryError('remote', 'Invalid PROPFIND collection response') from exc
        if not exists:
            raise DeliveryError('remote', 'PROPFIND HTTP 207 did not confirm the requested collection')
        return True

    def _ensure_collection(self, url):
        if self._collection_exists(url):
            return
        result = self._request('MKCOL', url, allowed=(201, 405))
        if result.status_code == 405:
            # Another client may have created the directory after the 404.
            if not self._collection_exists(url):
                raise DeliveryError('unsupported', 'MKCOL HTTP 405; collection remains absent')

    def _read(self, url):
        response = self._request('GET', url, allowed=(200, 404))
        return None if response.status_code == 404 else response.content

    def _write_verified(self, target, content, overwrite):
        # Upload and verify a same-directory temporary file before the atomic MOVE.
        temp = target.rsplit('/', 1)[0] + '/.oughtgather-' + uuid.uuid4().hex + '.tmp'
        try:
            self._request('PUT', temp, content=content)
            if self._read(temp) != content:
                raise DeliveryError('verification', 'WebDAV staging verification failed')
            self._request('MOVE', temp, headers={'Destination': target, 'Overwrite': 'T' if overwrite else 'F'})
        finally:
            try:
                self._request('DELETE', temp, allowed=(200, 204, 404))
            except DeliveryError as exc:
                self._cleanup_warning = str(exc)
        if self._read(target) != content:
            raise DeliveryError('verification', 'WebDAV destination verification failed')

    def _list_archives(self):
        response = self._request('PROPFIND', self.archive + '/', headers={'Depth': '1'}, allowed=(207,))
        try:
            root = ElementTree.fromstring(response.content)
            names = set()
            for href in root.findall('.//{DAV:}href'):
                path = urlsplit(href.text or '').path
                name = unquote(path.rstrip('/').split('/')[-1])
                if ARCHIVE_RE.fullmatch(name):
                    names.add(name)
            return names
        except ElementTree.ParseError as exc:
            raise DeliveryError('remote', 'Invalid WebDAV directory listing') from exc

    def upload_epub(self, epub_path: str, edition: str = None) -> bool:
        self.last_error, self.last_result = None, {}
        self._cleanup_warning = None
        if not self.config or not self.config.enabled:
            self.last_error = 'disabled'
            self.logger.info('WebDAV is not enabled, skip pushing.')
            return False
        try:
            with open(epub_path, 'rb') as stream:
                content = stream.read()
            digest = hashlib.sha256(content).hexdigest()
            edition = edition or get_now().strftime('%Y-%m-%dT%H%M%S') + '-' + digest + '.epub'
            if not ARCHIVE_RE.fullmatch(edition) or not edition.endswith(digest + '.epub'):
                raise DeliveryError('configuration', 'Invalid edition identifier')
            retention = int(os.getenv('WEBDAV_RETENTION_DAYS') or '30')
            if not 1 <= retention <= 3650:
                raise DeliveryError('configuration', 'Invalid retention days')
            datetime.strptime(edition[:17], '%Y-%m-%dT%H%M%S')
            if self._client is None:
                self._client = httpx.Client(auth=(self.config.username, self.config.password), timeout=30.0, follow_redirects=False)
                owned = True
            else:
                owned = False
            try:
                self._setup()
                target = self.archive + '/' + quote(edition, safe='')
                existing = self._read(target)
                if existing is None:
                    self._write_verified(target, content, overwrite=False)
                elif existing != content:
                    # The name contains the full digest, so a mismatch is a corrupt
                    # copy of this edition. Repair it via the same verified staging path.
                    self._write_verified(target, content, overwrite=True)
                names = self._list_archives()
                # Use the newest verified archive, even when retrying an older edition.
                newest = max(names | {edition})
                newest_content = content if newest == edition else self._read(self.archive + '/' + quote(newest, safe=''))
                if newest_content is None or hashlib.sha256(newest_content).hexdigest() != newest[-69:-5]:
                    raise DeliveryError('verification', 'Newest archive verification failed')
                latest = self.base + '/latest.epub'
                previous = self._read(latest)
                update_latest = previous != newest_content
                if update_latest:
                    if previous is not None:
                        previous_path = self.base + '/latest.previous.epub'
                        if self._read(previous_path) != previous:
                            self._write_verified(previous_path, previous, overwrite=True)
                    try:
                        self._write_verified(latest, newest_content, overwrite=True)
                    except DeliveryError:
                        # A timeout or failed verification may follow a committed MOVE.
                        # Try to restore the old latest; the verified previous copy
                        # remains available even if the server is unreachable.
                        if previous is not None:
                            try:
                                if self._read(latest) != previous:
                                    self._write_verified(latest, previous, overwrite=True)
                            except DeliveryError:
                                pass
                        raise
                cutoff = get_now().date() - timedelta(days=retention - 1)
                cleanup_error = None
                for name in names:
                    try:
                        date = datetime.strptime(name[:10], '%Y-%m-%d').date()
                        if date < cutoff and name != edition:
                            self._request('DELETE', self.archive + '/' + quote(name, safe=''), allowed=(200, 204, 404))
                    except ValueError:
                        continue
                    except DeliveryError as exc:
                        cleanup_error = exc.category
                        self._cleanup_warning = str(exc)
                        break
                self.last_result = {'archive': edition, 'sha256': digest, 'latest_updated': update_latest, 'latest_edition': newest, 'cleanup_error': cleanup_error, 'cleanup_warning': self._cleanup_warning}
                return True
            finally:
                if owned:
                    self._client.close()
                    self._client = None
        except FileNotFoundError:
            self.last_error = 'missing_file'
        except OSError:
            self.last_error = 'local_io'
        except (ValueError, TypeError):
            self.last_error = 'configuration'
        except DeliveryError as exc:
            self.last_error = exc.category
            self.last_result = {'error_detail': str(exc)}
        except httpx.HTTPError:
            self.last_error = 'network'
        self.logger.error('WebDAV delivery failed (%s)', self.last_error)
        return False
