import os
from types import SimpleNamespace
import pytest
from src.ai.memory.crypto import encrypt, decrypt
from src.ai.memory.model import empty_memory
from src.ai.memory.storage import MemoryStore, load_profile
from tests.test_ai_memory_foundation import patch


class FakeDav:
    def __init__(self):
        self.config = SimpleNamespace(url='https://dav.example/dav', username='x', password='x')
        self._client = object()
        self.files = {}
        self.fail_target = None
    def _collection_exists(self, url): return True
    def _read(self, url): return self.files.get(url)
    def _write_verified(self, url, content, overwrite):
        self.files[url] = content
        if url == self.fail_target:
            raise IOError('interruption')
    def _put_verified(self, url, content): self.files[url] = content


def test_update_and_restore_current_after_failed_write():
    dav, key = FakeDav(), os.urandom(32)
    store = MemoryStore(dav, key)
    old = encrypt(empty_memory(), key)
    dav.files[store.current] = old
    dav.fail_target = store.current
    with pytest.raises(IOError):
        store.apply(patch())
    assert dav.files[store.current] == old
    assert dav.files[store.previous] == old
    dav.fail_target = None
    memory, changed = store.apply(patch())
    assert changed and memory['revision'] == 1
    assert decrypt(dav.files[store.current], key) == memory


def test_corrupt_memory_never_overwritten():
    dav = FakeDav()
    store = MemoryStore(dav, os.urandom(32))
    dav.files[store.current] = b'corrupt'
    with pytest.raises(ValueError):
        store.apply(patch())
    assert dav.files == {store.current: b'corrupt'}


def test_failure_loads_default_profile(monkeypatch):
    monkeypatch.setattr('src.ai.memory.storage.MemoryStore', lambda: (_ for _ in ()).throw(OSError()))
    profile, revision, status = load_profile()
    assert status == 'degraded' and revision is None and profile['anti_filter_bubble']


def test_memory_directory_cannot_overlap_delivery(monkeypatch):
    monkeypatch.setenv('WEBDAV_REMOTE_PATH', 'DailyWorldBriefing')
    monkeypatch.setenv('AI_MEMORY_REMOTE_DIR', 'DailyWorldBriefing/private')
    with pytest.raises(ValueError):
        MemoryStore(FakeDav(), os.urandom(32))
    monkeypatch.setenv('AI_MEMORY_REMOTE_DIR', 'OughtGatherState')
    assert MemoryStore(FakeDav(), os.urandom(32)).base.endswith('/OughtGatherState')
