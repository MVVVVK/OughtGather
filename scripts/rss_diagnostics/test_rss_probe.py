import importlib.util
from datetime import datetime, timezone
from pathlib import Path

spec = importlib.util.spec_from_file_location('rss_probe', Path(__file__).with_name('rss_probe.py'))
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)

NOW = datetime(2026, 11, 24, tzinfo=timezone.utc)
RSS = b'''<?xml version="1.0"?><rss version="2.0"><channel><title>Test</title><link>https://example.com</link><description>News</description>
<item><title>International event</title><link>https://example.com/story?utm_source=foo</link><pubDate>Mon, 23 Nov 2026 12:00:00 GMT</pubDate><description>News description</description></item>
<item><title>Video: latest headlines</title><link>https://example.com/video/1</link><description>Watch the video</description></item>
<item><title>Missing link</title><description>ignored</description></item>
</channel></rss>'''


def test_parse_news_and_media_without_raw_content():
    result = probe.parse_feed(RSS, NOW)
    assert result['status'] == 'ok' and result['rss_entries'] == 3 and result['valid_title_link'] == 2
    assert result['summary_nonempty'] == 2 and result['media_hint'] == result['non_media_hint'] == 1
    assert sum(x['within_7_days'] for x in result['items']) == 1
    assert result['latest_published'] == '2026-11-23T12:00:00+00:00'
    assert 'News description' not in str(result) and 'International event' not in str(result)


def test_html_and_missing_dates_do_not_become_news():
    assert probe.parse_feed(b'<html><body>Access denied</body></html>', NOW)['status'] == 'not_feed'


def test_publication_and_update_dates_are_not_conflated():
    atom = b'''<feed xmlns="http://www.w3.org/2005/Atom"><title>Test</title>
    <updated>2026-11-23T10:00:00Z</updated><id>https://example.com/</id>
    <entry><title>News</title><id>https://example.com/1</id><link href="https://example.com/1"/>
    <updated>2026-11-23T09:00:00Z</updated><summary>text</summary></entry></feed>'''
    result = probe.parse_feed(atom, NOW)
    assert result['feed_updated'] == '2026-11-23T10:00:00+00:00'
    assert result['latest_published'] is None and result['published_missing'] == 1


def test_overlap_is_explicitly_only_an_exact_lower_bound():
    item = {'url_id': 'url', 'title_id': 'title', 'within_7_days': True}
    other = {'url_id': 'other', 'title_id': 'different', 'within_7_days': True}
    rows = [{'name': 'Le Monde — Une', 'status': 'ok', 'items': [item]},
            {'name': 'Le Monde — International', 'status': 'ok', 'items': [item, other]}]
    result = probe.compare(rows)[0]
    assert result['exact_url_or_title_overlap'] == 1
    assert result['branch_unmatched_not_proven_independent'] == 1


def test_robots_failure_never_fetches_feed(monkeypatch):
    requests = []
    def fake_fetch(url):
        requests.append(url)
        return 403, b''
    monkeypatch.setattr(probe, 'fetch', fake_fetch)
    result = probe.probe({'name': 'example', 'url': 'https://example.com/feed'}, NOW)
    assert result['status'] == 'robots_unavailable' and len(requests) == 1


def test_redirect_is_not_followed(monkeypatch):
    calls = []
    def fake_fetch(url):
        calls.append(url)
        return (404, b'') if url.endswith('/robots.txt') else (302, b'')
    monkeypatch.setattr(probe, 'fetch', fake_fetch)
    result = probe.probe({'name': 'example', 'url': 'https://example.com/feed'}, NOW)
    assert result['status'] == 'redirect_not_followed' and len(calls) == 2


def test_collector_can_fill_200_slots_with_media_hints_before_quality_review():
    from src.ai.candidates import CandidateCollector
    collector = CandidateCollector()
    candidates = [{'url': f'https://example.com/{i}', 'title': f'Candidate {i}',
                   '_entry': {'summary': 'RSS excerpt'}, '_media': i < 200} for i in range(201)]
    collector.collect('example', candidates, 'example')
    selected = collector.snapshot(1000000)[:200]
    assert len(selected) == 200
    assert sum(row['media_hint'] for row in selected) >= 199
