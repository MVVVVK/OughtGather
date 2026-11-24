"""Independent encrypted immutable per-day WebDAV archive."""
import os
import re
from datetime import date, datetime, timedelta
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree
import httpx
from src.uploader.webdav_uploader import WebDavUploader, DeliveryError

NAME = re.compile(r'^\d{4}-\d{2}-\d{2}\.enc$')


class AlreadyFrozen(ValueError):
    pass


class BenchmarkStore:
    def __init__(self, uploader=None, retention=30):
        if type(retention) is not int or not 1 <= retention <= 3650: raise ValueError('invalid retention')
        self.dav = uploader or WebDavUploader()
        self.retention = retention
        self.owned = False
        parsed = urlsplit(self.dav.config.url or '')
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('invalid endpoint')
        directory = 'oughtgatherbenchmark'
        for other in (self.dav.config.remote_path or '', os.getenv('AI_MEMORY_REMOTE_DIR') or 'OughtGatherState'):
            other = unquote(other).strip('/').lower()
            if other and (other == directory or other.startswith(directory + '/') or directory.startswith(other + '/')):
                raise ValueError('overlapping directories')
        self.base = self.dav.config.url.rstrip('/') + '/OughtGatherBenchmark'
        self.dav._overwrite_fallbacks = []; self.dav._cleanup_warning = None

    def __enter__(self):
        if self.dav._client is None:
            self.dav._client = httpx.Client(auth=(self.dav.config.username, self.dav.config.password), timeout=10, follow_redirects=False)
            self.owned = True
        try:
            if not self.dav._collection_exists(self.base): raise ValueError('benchmark directory unavailable')
            return self
        except Exception:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *args):
        if self.owned:
            self.dav._client.close(); self.dav._client = None; self.owned = False

    def read(self, day):
        date.fromisoformat(day)
        return self.dav._read(self.base + '/' + day + '.enc')

    def save(self, day, ciphertext):
        date.fromisoformat(day)
        if not isinstance(ciphertext, bytes) or not ciphertext.startswith(b'OGBENCH\x01'):
            raise ValueError('invalid encrypted snapshot')
        target = self.base + '/' + day + '.enc'
        if self.dav._read(target) is not None: raise AlreadyFrozen('benchmark already frozen')
        # Uses verified PUT temp + MOVE Overwrite:F, never overwrite fallback.
        self.dav._write_verified(target, ciphertext, overwrite=False)

    def cleanup(self, today):
        cutoff = today - timedelta(days=self.retention - 1)
        response = self.dav._request('PROPFIND', self.base + '/', headers={'Depth': '1'}, allowed=(207,))
        try: root = ElementTree.fromstring(response.content)
        except ElementTree.ParseError: raise ValueError('invalid collection listing') from None
        expected_parent = unquote(urlsplit(self.base).path).rstrip('/')
        for href in root.findall('.//{DAV:}href'):
            path = unquote(urlsplit(href.text or '').path).rstrip('/')
            parent, _, name = path.rpartition('/')
            if parent != expected_parent or not NAME.fullmatch(name): continue
            try: old = date.fromisoformat(name[:10])
            except ValueError: continue
            if old < cutoff:
                self.dav._request('DELETE', self.base + '/' + name, allowed=(200, 204, 404))
