"""Private WebDAV storage; never initializes over unreadable or corrupt state."""
import os
from urllib.parse import quote, unquote, urlsplit
import httpx
from src.uploader.webdav_uploader import WebDavUploader, DeliveryError
from .crypto import decrypt, encrypt, parse_key
from .model import empty_memory, apply_patch


class MemoryStore:
    def __init__(self, uploader=None, key=None):
        self.dav = uploader or WebDavUploader()
        self.key = key if key is not None else parse_key(os.getenv('AI_MEMORY_KEY', ''))
        self.owned = False
        url = urlsplit(self.dav.config.url or '')
        directory = os.getenv('AI_MEMORY_REMOTE_DIR') or 'OughtGatherState'
        if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError('invalid state endpoint')
        parts = directory.strip('/').split('/')
        if not parts or any(not x or unquote(x) in ('.', '..') or any(c in unquote(x) for c in ('/', '\\')) or '%' in x for x in parts):
            raise ValueError('invalid state directory')
        state_path = '/'.join(unquote(x) for x in parts).lower()
        news_path = unquote(os.getenv('WEBDAV_REMOTE_PATH') or '').strip('/').lower()
        if news_path and (state_path == news_path or state_path.startswith(news_path + '/')):
            raise ValueError('state directory overlaps delivery directory')
        self.base = self.dav.config.url.rstrip('/') + ''.join('/' + quote(x, safe='') for x in parts)
        self.current = self.base + '/editorial-memory.enc'
        self.previous = self.base + '/editorial-memory.previous.enc'
        self.dav._overwrite_fallbacks = []
        self.dav._cleanup_warning = None

    def __enter__(self):
        if self.dav._client is None:
            try:
                total = max(1, min(300, int(os.getenv('AI_TOTAL_TIMEOUT_SECONDS') or '60')))
            except ValueError:
                total = 60
            self.dav._client = httpx.Client(auth=(self.dav.config.username, self.dav.config.password), timeout=min(5.0, total / 8), follow_redirects=False)
            self.owned = True
        # State parent must be created explicitly by the owner.
        try:
            if not self.dav._collection_exists(self.base):
                raise ValueError('state directory unavailable')
            return self
        except Exception:
            if self.owned:
                self.dav._client.close()
                self.dav._client = None
            raise

    def __exit__(self, *args):
        if self.owned:
            self.dav._client.close()
            self.dav._client = None

    def read(self):
        blob = self.dav._read(self.current)
        return (empty_memory(), None) if blob is None else (decrypt(blob, self.key), blob)

    def apply(self, patch, validate_only=False):
        memory, old = self.read()
        updated, changed = apply_patch(memory, patch)
        if not changed or validate_only:
            return updated, changed
        blob = encrypt(updated, self.key)
        if old is not None:
            # Verify decrypted old data above, then persist and verify the recovery copy.
            self.dav._write_verified(self.previous, old, overwrite=True)
            if self.dav._read(self.previous) != old:
                raise DeliveryError('verification', 'memory backup verification failed')
        try:
            self.dav._write_verified(self.current, blob, overwrite=True)
            decrypt(self.dav._read(self.current), self.key)
        except Exception:
            if old is not None:
                try:
                    self.dav._put_verified(self.current, old)
                except Exception:
                    pass  # Verified previous remains available for manual recovery.
            raise
        return updated, True


def load_profile():
    try:
        with MemoryStore() as store:
            memory, blob = store.read()
            return memory['profile'], memory['revision'], 'available' if blob else 'degraded'
    except Exception:
        return empty_memory()['profile'], None, 'degraded'
