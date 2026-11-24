import importlib.util
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))
spec = importlib.util.spec_from_file_location('public_samples', Path(__file__).with_name('public_samples.py'))
samples = importlib.util.module_from_spec(spec)
spec.loader.exec_module(samples)


def test_sample_uses_public_titles_only_and_removes_query(monkeypatch):
    monkeypatch.setattr(samples, 'PUBLIC_HOSTS', {'example.com'})
    rss = b'''<rss version="2.0"><channel><title>Public</title><link>https://example.com</link>
      <description>feed</description><item><title>News</title>
      <link>https://example.com/story?private=do-not-record</link>
      <description>body-do-not-record</description></item></channel></rss>'''
    def fake_fetch(url):
        return (404, b'') if url.endswith('/robots.txt') else (200, rss)
    monkeypatch.setattr(samples.rss_probe, 'fetch', fake_fetch)
    row = samples.read_sample({'name': 'Public', 'url': 'https://example.com/rss'})
    assert row['entries'] == [{'title': 'News', 'url': 'https://example.com/story'}]
    assert 'do-not-record' not in str(row)


def test_unavailable_robots_never_fetches_feed(monkeypatch):
    monkeypatch.setattr(samples, 'PUBLIC_HOSTS', {'example.com'})
    seen = []
    def fake_fetch(url):
        seen.append(url)
        return 403, b''
    monkeypatch.setattr(samples.rss_probe, 'fetch', fake_fetch)
    row = samples.read_sample({'name': 'Public', 'url': 'https://example.com/rss'})
    assert row['status'] == 'robots_unavailable' and len(seen) == 1


def test_non_whitelisted_host_never_fetches(monkeypatch):
    monkeypatch.setattr(samples.rss_probe, 'fetch', lambda url: (_ for _ in ()).throw(AssertionError('unexpected fetch')))
    row = samples.read_sample({'name': 'Public', 'url': 'https://private.invalid/rss'})
    assert row['status'] == 'invalid_url'
