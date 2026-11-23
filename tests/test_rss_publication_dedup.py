"""Offline publication, retry, migration and feed diagnostics tests."""
import json
from unittest.mock import MagicMock, patch

import feedparser
import httpx
import pytest

from src.config import ContentSource
from src.dedup.tracker import DedupTracker
from src.fetchers.rss_fetcher import RSSFetcher
from src.main import (_fetch_source_task, process_results, mark_epub_articles,
                      record_article_failures, main)
from src.quality_report import write_quality_reports
from src.utils.helpers import generate_content_id

FEED_URL = 'https://example.org/feed'
BODY = ('<p>Representatives gathered on Monday to discuss the proposed agreement and how it would affect communities across the region.</p>'
        '<p>Officials said discussions would continue after the delegates reviewed the details and considered their consequences.</p>')


def item(n, content=BODY):
    value = {'title': f'Article {n}', 'link': f'https://example.org/{n}'}
    if content is not None:
        value['content'] = [{'value': content}]
    return feedparser.FeedParserDict(value)


def feed(items):
    return feedparser.FeedParserDict({'bozo': False, 'entries': items, 'feed': {'title': 'Example'}})


def gather(source, tracker, items):
    with patch.object(RSSFetcher, '_parse_feed', return_value=feed(items)):
        result, _ = _fetch_source_task(source, tracker, 2)
    process_results([result], tracker)
    record_article_failures([result], tracker)
    return result


def test_quota_items_are_eligible_next_run_and_published_are_not(tmp_path):
    source = ContentSource(type='rss', src=FEED_URL, metadata={'limit': 2})
    path = tmp_path / 'history'
    items = [item(n) for n in range(4)]
    tracker = DedupTracker(str(path))
    first = gather(source, tracker, items)
    assert [a.title for a in first.articles] == ['Article 0', 'Article 1']
    assert not any(tracker.is_fetched(e['link'], tracker.make_source_key(source)) for e in items)
    mark_epub_articles([first], tracker)
    tracker.save()
    tracker = DedupTracker(str(path))
    second = gather(source, tracker, items)
    assert [a.title for a in second.articles] == ['Article 2', 'Article 3']
    mark_epub_articles([second], tracker)
    tracker.save()
    third = gather(source, DedupTracker(str(path)), items)
    assert third.articles == []


def test_failed_retries_have_cooldown_can_recover_and_preserve_history(tmp_path):
    source = ContentSource(type='rss', src=FEED_URL, metadata={'limit': 2})
    path = tmp_path / 'history'
    tracker = DedupTracker(str(path))
    key = tracker.make_source_key(source)
    with patch('src.dedup.tracker.time.time', return_value=100000):
        first = gather(source, tracker, [item(1, None)])
        assert first.quality_report[0]['status'] == 'failed'
        mark_epub_articles([first], tracker)
        tracker.save()
    tracker = DedupTracker(str(path))
    assert not tracker.is_fetched(item(1)['link'], key)
    with patch('src.dedup.tracker.time.time', return_value=100001):
        waiting = gather(source, tracker, [item(1)])
        assert not waiting.articles
        assert waiting.quality_report[0]['reason'] == 'retry_cooldown'
    with patch('src.dedup.tracker.time.time', return_value=200000):
        recovered = gather(source, tracker, [item(1)])
        assert len(recovered.articles) == 1
        mark_epub_articles([recovered], tracker)
        tracker.save()
    tracker = DedupTracker(str(path))
    assert tracker.is_fetched(item(1)['link'], key)
    assert tracker.retry_count(item(1)['link'], key) == 0


def test_retry_limit_and_manual_reset_do_not_clear_published(tmp_path):
    path = tmp_path / 'history'
    t = DedupTracker(str(path))
    t.mark_as_fetched('https://example.org/published', 'rss:feed')
    for now in (0, 90000, 180000):
        t.record_failure('https://example.org/fail', 'rss:feed', now=now)
    t.save()
    t = DedupTracker(str(path))
    assert t.retry_status('https://example.org/fail', 'rss:feed', now=9999999) == 'retry_exhausted'
    t.reset_retries()
    t.save()
    t = DedupTracker(str(path))
    assert t.retry_status('https://example.org/fail', 'rss:feed') is None
    assert t.is_fetched('https://example.org/published', 'rss:feed')


def test_legacy_snapshot_loads_and_retry_records_roundtrip(tmp_path):
    path = tmp_path / 'history'
    url = 'https://example.org/old'
    path.write_text('rss:feed\t' + generate_content_id(url) + '\n', encoding='utf-8')
    tracker = DedupTracker(str(path))
    assert tracker.is_fetched(url, 'rss:feed')
    tracker.record_failure('https://example.org/new', 'rss:feed', now=100)
    tracker.save()
    reload = DedupTracker(str(path))
    assert reload.is_fetched(url, 'rss:feed')
    assert reload.retry_count('https://example.org/new', 'rss:feed') == 1


def test_epub_generation_failure_does_not_commit_rss(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    source = ContentSource(type='rss', src=FEED_URL)
    tracker = DedupTracker(str(tmp_path / 'history'))
    config = MagicMock(body=[source], limit=2)
    with patch('src.main.load_config', return_value=config), \
         patch('src.main.DedupTracker', return_value=tracker), \
         patch.object(RSSFetcher, '_parse_feed', return_value=feed([item(1)])), \
         patch('src.main.EPUBGenerator') as generator:
        generator.return_value.generate.side_effect = RuntimeError('EPUB failed')
        with pytest.raises(SystemExit):
            main([])
    assert not tracker.is_fetched(item(1)['link'], tracker.make_source_key(source))
    sources = json.loads((tmp_path / 'output/quality-sources.json').read_text())
    assert sources[0]['selected'] == 0


def test_test_mode_isolates_history_and_delivery(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    source = ContentSource(type='rss', src=FEED_URL)
    ledger = tmp_path / 'data/fetched_urls.txt'
    ledger.parent.mkdir()
    ledger.write_text('rss:legacy\t' + generate_content_id('https://example.org/old') + '\n')
    original = ledger.read_bytes()
    config = MagicMock(body=[source], limit=2)
    with patch('src.main.load_config', return_value=config), \
         patch.object(RSSFetcher, '_parse_feed', return_value=feed([item(1)])), \
         patch('src.main.EPUBGenerator') as generator, \
         patch('src.main.SMTPSender') as sender:
        generator.return_value.generate.return_value = 'test.epub'
        main(['--test-mode'])
    assert ledger.read_bytes() == original
    sender.assert_not_called()


def test_fresh_start_success_and_failure_preserve_contract(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    source = ContentSource(type='rss', src=FEED_URL)
    path = tmp_path / 'history'
    tracker = DedupTracker(str(path))
    tracker.mark_as_fetched('https://example.org/old', tracker.make_source_key(source))
    tracker.save()
    original = path.read_bytes()
    config = MagicMock(body=[source], limit=2)
    with patch('src.main.load_config', return_value=config), \
         patch('src.main.DedupTracker', return_value=tracker), \
         patch.object(RSSFetcher, 'fetch_list', return_value=None):
        with pytest.raises(SystemExit):
            main(['--fresh-start'])
    assert path.read_bytes() == original
    tracker = DedupTracker(str(path))
    with patch('src.main.load_config', return_value=config), \
         patch('src.main.DedupTracker', return_value=tracker), \
         patch.object(RSSFetcher, '_parse_feed', return_value=feed([item(1), item(2)])):
        main(['--fresh-start'])
    tracker = DedupTracker(str(path))
    key = tracker.make_source_key(source)
    assert tracker.is_fetched(item(1)['link'], key) and tracker.is_fetched(item(2)['link'], key)
    assert not tracker.is_fetched('https://example.org/old', key)


def test_feed_diagnostics_distinguish_dates_and_capture_http(tmp_path):
    xml = b'''<?xml version="1.0"?><rss version="2.0"><channel><title>News</title><link>https://example.org</link><description>News</description><lastBuildDate>Mon, 02 Jan 2023 10:00:00 GMT</lastBuildDate><item><title>Article</title><link>https://example.org/1</link><pubDate>Sun, 01 Jan 2023 10:00:00 GMT</pubDate><description>News summary</description></item></channel></rss>'''
    source = ContentSource(type='rss', src=FEED_URL)
    response = httpx.Response(200, content=xml, request=httpx.Request('GET', FEED_URL))
    tracker = DedupTracker(str(tmp_path / 'history'))
    with patch.object(RSSFetcher, '_make_request', return_value=response):
        result, _ = _fetch_source_task(source, tracker, 2)
    write_quality_reports([result], str(tmp_path))
    row = json.loads((tmp_path / 'quality-sources.json').read_text())[0]
    assert row['rss_success'] is True and row['http_status'] == 200
    assert row['latest_published'].startswith('2023-01-01')
    assert row['feed_updated'].startswith('2023-01-02')
    assert row['fetched_at'] != row['feed_updated']
    assert row['stale_warning'] == 'latest_article_older_than_30_days'
    assert row['text_candidates'] == 1 and row['media_candidates'] == 0


def test_single_phase_fallback_respects_retry_and_published_history(tmp_path):
    source = ContentSource(type='rss', src=FEED_URL)
    tracker = DedupTracker(str(tmp_path / 'history'))
    key = tracker.make_source_key(source)
    tracker.mark_as_fetched(item(1)['link'], key)
    tracker.record_failure(item(2)['link'], key, now=100000)
    f = RSSFetcher(source)
    f._single_phase_tracker = tracker
    with patch.object(f, '_parse_feed', return_value=feed([item(1), item(2), item(3)])), \
         patch('src.dedup.tracker.time.time', return_value=100001):
        result = f.fetch()
    assert [a.title for a in result.articles] == ['Article 3']
    assert [r['reason'] for r in result.quality_report[:2]] == ['historical_dedup', 'retry_cooldown']


def test_http_failure_status_is_reported(tmp_path):
    source = ContentSource(type='rss', src=FEED_URL)
    response = httpx.Response(403, request=httpx.Request('GET', FEED_URL))
    error = httpx.HTTPStatusError('Forbidden', request=response.request, response=response)
    tracker = DedupTracker(str(tmp_path / 'history'))
    with patch.object(RSSFetcher, '_make_request', side_effect=error), \
         patch('src.fetchers.base.time.sleep'):
        result, _ = _fetch_source_task(source, tracker, 2)
    write_quality_reports([result], str(tmp_path))
    row = json.loads((tmp_path / 'quality-sources.json').read_text())[0]
    assert row['rss_success'] is False and row['http_status'] == 403
    assert row['fetched_at'] and row['latest_published'] is None


def test_unknown_legacy_format_fails_closed_without_overwriting(tmp_path):
    path = tmp_path / 'history'
    original = 'hash-without-source\n'
    path.write_text(original)
    with pytest.raises(ValueError, match='unsupported legacy'):
        DedupTracker(str(path))
    assert path.read_text() == original


def test_malformed_feed_records_parser_error_and_keeps_entries(tmp_path):
    xml = b'<rss><channel><title>News</title><item><title>One</title><link>https://example.org/1</link><description>News</description></item></channel>'
    source = ContentSource(type='rss', src=FEED_URL)
    response = httpx.Response(200, content=xml, request=httpx.Request('GET', FEED_URL))
    tracker = DedupTracker(str(tmp_path / 'history'))
    with patch.object(RSSFetcher, '_make_request', return_value=response):
        result, _ = _fetch_source_task(source, tracker, 2)
    write_quality_reports([result], str(tmp_path))
    row = json.loads((tmp_path / 'quality-sources.json').read_text())[0]
    assert row['rss_success'] is True and row['http_status'] == 200
    assert row['parse_error'] and row['rss_returned'] == 1


def test_two_actual_epubs_publish_distinct_articles(tmp_path, monkeypatch):
    from zipfile import ZipFile
    from PIL import Image
    from src.config import _parse_config
    from src.epub.cover import CoverGenerator
    from src.epub.generator import EPUBGenerator

    monkeypatch.chdir(tmp_path)
    config = _parse_config({'title': {'text': 'News', 'img': ''}, 'limit': 2,
                            'body': [{'type': 'rss', 'src': FEED_URL, 'metadata': {'limit': 2}}]})
    source = config.body[0]
    tracker = DedupTracker(str(tmp_path / 'history'))
    items = [item(i) for i in range(4)]
    observed = []
    with patch.object(CoverGenerator, '_get_background', return_value=Image.new('RGB', (1440, 1920), 'white')):
        for _ in range(2):
            result = gather(source, tracker, items)
            path = EPUBGenerator(config).generate([result])
            with ZipFile(path) as book:
                nav = book.read('EPUB/nav.xhtml').decode('utf-8')
                observed.append([e['title'] for e in items if e['title'] in nav])
            mark_epub_articles([result], tracker)
            tracker.save()
            tracker = DedupTracker(str(tmp_path / 'history'))
    assert observed == [['Article 0', 'Article 1'], ['Article 2', 'Article 3']]


def test_duplicate_feed_urls_do_not_use_quota_or_publish_twice(tmp_path):
    source = ContentSource(type='rss', src=FEED_URL, metadata={'limit': 2})
    tracker = DedupTracker(str(tmp_path / 'history'))
    result = gather(source, tracker, [item(1), item(1), item(2)])
    assert [a.title for a in result.articles] == ['Article 1', 'Article 2']
    assert any(row['reason'] == 'duplicate_candidate' for row in result.quality_report)
