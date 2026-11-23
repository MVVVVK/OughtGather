"""Offline tests for source accounting, media selection, and configuration."""
import json
from unittest.mock import patch

import feedparser
import pytest

from src.config import ContentSource
from src.dedup.tracker import DedupTracker
from src.fetchers.base import FetchResult
from src.fetchers.rss_fetcher import RSSFetcher
from src.main import _fetch_source_task, process_results
from src.quality_report import write_quality_reports

BODY = ('<p>International representatives met on Monday to discuss new proposals and their consequences for communities in the region.</p>'
        '<p>Officials said that discussions would continue next week after participants had examined the details of the proposals.</p>')


def entry(title, path, summary='A news summary.', content=None):
    data = {'title': title, 'link': 'https://example.org/' + path, 'summary': summary}
    if content:
        data['content'] = [{'value': content}]
    return feedparser.FeedParserDict(data)


def feed(entries):
    return feedparser.FeedParserDict({'bozo': False, 'entries': entries, 'feed': {'title': 'Test Feed'}})


def test_media_url_and_structured_signals():
    assert RSSFetcher._is_media(entry('Reportage', 'fr/vid%C3%A9o/story'))
    assert RSSFetcher._is_media(entry('Reportage', 'fr/vidéo/story'))
    item = entry('Reportage', 'story')
    item['media_content'] = [{'medium': 'audio'}]
    assert RSSFetcher._is_media(item)
    assert not RSSFetcher._is_media(entry('International news', 'fr/news/story'))


def test_media_summary_is_rejected_and_later_article_fills_limit():
    source = ContentSource(type='rss', src='https://example.org/feed', metadata={'limit': 1, 'text_news_priority': 'N'})
    f = RSSFetcher(source)
    items = [entry('Reportage', 'fr/vidéo/first'), entry('News', 'fr/news/second')]
    result = f.fetch_items([{'_entry': item} for item in items])
    assert result.quality_report[0]['reason'].endswith('media_without_independent_text')
    assert result.articles[0].title == 'News'


def test_media_with_full_text_is_eligible():
    source = ContentSource(type='rss', src='https://example.org/feed')
    f = RSSFetcher(source)
    item = entry('Reportage', 'fr/vidéo/story', content=BODY)
    result = f.fetch_items([{'_entry': item}])
    assert result.quality_report[0]['status'] == 'rss_content'
    assert len(result.articles) == 1


def test_config_controls_priority_and_summary():
    items = [entry('Reportage', 'fr/vidéo/first', content=BODY), entry('Written news', 'news/second', content=BODY)]
    candidates = [{'_entry': i} for i in items]
    source = ContentSource(type='rss', src='https://example.org/feed', metadata={'limit': 1})
    assert RSSFetcher(source).fetch_items(candidates).articles[0].title == 'Written news'
    source.metadata['text_news_priority'] = 'N'
    assert RSSFetcher(source).fetch_items(candidates).articles[0].title == 'Reportage'
    source.metadata['accept_summary'] = 'N'
    results = RSSFetcher(source).fetch_items([{'_entry': entry('Summary only', 'news/a')}, {'_entry': items[1]}])
    assert 'summary_disabled' in results.quality_report[0]['reason']
    assert results.articles[0].title == 'Written news'


def test_accounting_counts_every_source_and_skip_reason(tmp_path):
    source = ContentSource(type='rss', src='https://example.org/feed', title='Example',
                           delete='Sponsored', metadata={'limit': 1, 'text_news_priority': 'N', 'accept_summary': 'N'})
    items = [entry('Old', 'old', content=BODY), entry('Sponsored', 'ad'),
             entry('Reportage', 'fr/vidéo/one'), entry('Summary only', 'two'),
             entry('Full story', 'three', content=BODY), entry('Over quota', 'four', content=BODY)]
    tracker = DedupTracker(str(tmp_path / 'history'))
    tracker.mark_as_fetched(items[0]['link'], tracker.make_source_key(source))
    tracker.save()
    with patch.object(RSSFetcher, '_parse_feed', return_value=feed(items)):
        result, _ = _fetch_source_task(source, tracker, 10)
    process_results([result], tracker)
    failed_source = ContentSource(type='rss', src='https://example.net/feed', title='Failed Feed')
    failed = FetchResult(source=failed_source, articles=[], success=False, error='feed unavailable')
    all_seen = FetchResult(source=ContentSource(type='rss', src='https://example.net/seen', title='Seen'), articles=[])
    write_quality_reports([result, failed, all_seen], str(tmp_path))
    articles = json.loads((tmp_path / 'quality-report.json').read_text())
    sources = json.loads((tmp_path / 'quality-sources.json').read_text())
    stats = sources[0]
    assert (stats['rss_returned'], stats['historical_dedup'], stats['candidates'], stats['selected']) == (6, 1, 4, 1)
    assert (stats['rss_content'], stats['summary'], stats['quota_limited']) == (1, 0, 1)
    reasons = [row['reason'] for row in articles]
    assert any('historical_dedup' in x for x in reasons)
    assert any('media_without_independent_text' in x for x in reasons)
    assert any('summary_disabled' in x for x in reasons)
    assert 'quota_limit' in reasons and 'title_filter' in reasons
    assert len(sources) == 3 and sources[1]['status'] == 'failed' and sources[1]['reason'] == 'feed unavailable'
    assert any(row['method'] == 'source' and row['reason'] == 'feed unavailable' for row in articles)
    assert sources[2]['selected'] == 0


def test_all_candidates_previously_seen_source_still_reported(tmp_path):
    source = ContentSource(type='rss', src='https://example.org/feed', title='No new news')
    item = entry('Old', 'old')
    tracker = DedupTracker(str(tmp_path / 'history'))
    tracker.mark_as_fetched(item['link'], tracker.make_source_key(source))
    tracker.save()
    with patch.object(RSSFetcher, '_parse_feed', return_value=feed([item])):
        result, _ = _fetch_source_task(source, tracker, 5)
    process_results([result], tracker)
    write_quality_reports([result], str(tmp_path))
    rows = json.loads((tmp_path / 'quality-sources.json').read_text())
    assert rows[0]['rss_returned'] == 1 and rows[0]['historical_dedup'] == 1
    assert rows[0]['candidates'] == 0 and rows[0]['selected'] == 0


def test_actual_feed_failure_is_recorded(tmp_path):
    source = ContentSource(type='rss', src='https://example.org/broken', title='Broken')
    tracker = DedupTracker(str(tmp_path / 'history'))
    with patch.object(RSSFetcher, '_parse_feed', side_effect=ValueError('invalid feed')), \
         patch('src.fetchers.base.time.sleep'):
        result, _ = _fetch_source_task(source, tracker, 5)
    assert not result.success
    write_quality_reports([result], str(tmp_path))
    sources = json.loads((tmp_path / 'quality-sources.json').read_text())
    assert sources[0]['source'] == 'Broken' and sources[0]['status'] == 'failed'
    assert 'invalid feed' in sources[0]['reason'] and sources[0]['rss_returned'] is None


def test_no_entries_is_distinct_from_feed_failure(tmp_path):
    source = ContentSource(type='rss', src='https://example.org/empty')
    tracker = DedupTracker(str(tmp_path / 'history'))
    with patch.object(RSSFetcher, '_parse_feed', return_value=feed([])), \
         patch('src.fetchers.base.time.sleep'):
        result, _ = _fetch_source_task(source, tracker, 5)
    write_quality_reports([result], str(tmp_path))
    sources = json.loads((tmp_path / 'quality-sources.json').read_text())
    assert sources[0]['status'] == 'failed' and sources[0]['rss_returned'] == 0


def test_normal_run_keeps_existing_snapshot_semantics(tmp_path):
    source = ContentSource(type='rss', src='https://example.org/feed', metadata={'limit': 1})
    items = [entry('First', 'one', content=BODY), entry('Second', 'two', content=BODY)]
    path = tmp_path / 'history'
    tracker = DedupTracker(str(path))
    with patch.object(RSSFetcher, '_parse_feed', return_value=feed(items)):
        result, _ = _fetch_source_task(source, tracker, 5)
    process_results([result], tracker)
    tracker.save()
    reloaded = DedupTracker(str(path))
    key = tracker.make_source_key(source)
    assert all(reloaded.is_fetched(item['link'], key) for item in items)
