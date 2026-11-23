"""Offline tests for HTTP, browser fallback and quality checks end to end."""
from unittest.mock import patch

import feedparser
import httpx
import pytest

from src.config import ContentSource
from src.fetchers.rss_fetcher import RSSFetcher
from src.fetchers.web_fetcher import WebFetcher

URL = 'https://example.com/article'
ARTICLE = ('<article><p>International officials met on Monday to discuss the agreement and its possible effects on people across the region.</p>'
           '<p>Negotiations will continue next week after representatives have examined the proposals and explained their conclusions.</p></article>')
JS_ERROR = '<p>JavaScript is disabled in your browser. Please enable JavaScript to proceed.</p>'
CAPTCHA = '<p>Access denied. Verify that you are human.</p>'
PLAYER = '<p>Please enable the video player to watch this video.</p>'


def response(status, text):
    return httpx.Response(status, text=text, request=httpx.Request('GET', URL))


def rss():
    return RSSFetcher(ContentSource(type='rss', src='https://example.com/feed', metadata={'full_text': 'Y'}))


def rss_item():
    return {'_entry': feedparser.FeedParserDict({'title': 'News', 'link': URL, 'summary': '<p>Independent RSS summary.</p>'})}


@pytest.mark.parametrize('kind', ['rss', 'web'])
def test_http_success_validates_without_browser(kind):
    f = rss() if kind == 'rss' else WebFetcher(ContentSource(type='web', src=URL))
    with patch('src.fetchers.base.safe_request', return_value=response(200, ARTICLE)), \
         patch.object(f, '_make_browser_request') as browser, \
         patch('trafilatura.extract', return_value=ARTICLE):
        result = f.fetch_items([rss_item()]) if kind == 'rss' else f.fetch()
    assert result.articles and result.quality_report[0]['status'] == 'full_text'
    browser.assert_not_called()


@pytest.mark.parametrize('kind', ['rss', 'web'])
def test_browser_fallback_valid_article(kind):
    f = rss() if kind == 'rss' else WebFetcher(ContentSource(type='web', src=URL))
    with patch('src.fetchers.base.safe_request', return_value=response(503, 'Service unavailable')), \
         patch.object(f, '_make_browser_request', return_value=response(200, ARTICLE)) as browser, \
         patch('trafilatura.extract', return_value=ARTICLE):
        result = f.fetch_items([rss_item()]) if kind == 'rss' else f.fetch()
    assert result.articles and result.quality_report[0]['status'] == 'full_text'
    browser.assert_called_once()


@pytest.mark.parametrize('bad', [JS_ERROR, CAPTCHA, PLAYER])
def test_browser_bad_content_falls_back_to_rss_summary(bad):
    f = rss()
    with patch('src.fetchers.base.safe_request', return_value=response(503, 'Service unavailable')), \
         patch.object(f, '_make_browser_request', return_value=response(200, bad)), \
         patch('trafilatura.extract', return_value=bad):
        result = f.fetch_items([rss_item()])
    assert result.quality_report[0]['status'] == 'summary'
    assert 'Independent RSS summary' in result.articles[0].content
    assert bad not in result.articles[0].content


def test_browser_launch_failure_falls_back_to_summary():
    f = rss()
    with patch('src.fetchers.base.safe_request', return_value=response(503, 'Service unavailable')), \
         patch.object(f, '_make_browser_request', side_effect=RuntimeError('browser unavailable')) as browser:
        result = f.fetch_items([rss_item()])
    browser.assert_called_once()
    assert result.quality_report[0]['status'] == 'summary'


@pytest.mark.parametrize('bad', [JS_ERROR, CAPTCHA, PLAYER])
def test_bad_http_200_is_never_accepted(bad):
    # A 200 response is not retried in a browser, even when it is a challenge.
    f = rss()
    with patch('src.fetchers.base.safe_request', return_value=response(200, bad)), \
         patch.object(f, '_make_browser_request') as browser, \
         patch('trafilatura.extract', return_value=bad):
        result = f.fetch_items([rss_item()])
    browser.assert_not_called()
    assert result.quality_report[0]['status'] == 'summary'


@pytest.mark.parametrize('status,body', [(202, 'challenge'), (401, ''), (403, ''), (429, ''), (451, ''), (503, CAPTCHA), (503, 'Subscribe to continue')])
def test_access_restrictions_are_not_retried(status, body):
    f = rss()
    with patch('src.fetchers.base.safe_request', return_value=response(status, body)), \
         patch.object(f, '_make_browser_request') as browser:
        result = f.fetch_items([rss_item()])
    browser.assert_not_called()
    assert result.quality_report[0]['status'] == 'summary'


def test_web_failed_browser_content_is_not_an_article():
    f = WebFetcher(ContentSource(type='web', src=URL))
    with patch('src.fetchers.base.safe_request', return_value=response(503, 'Service unavailable')), \
         patch.object(f, '_make_browser_request', return_value=response(200, CAPTCHA)), \
         patch('trafilatura.extract', return_value=CAPTCHA):
        result = f.fetch()
    assert not result.success and not result.articles
    assert result.quality_report[0]['status'] == 'failed'


def test_browser_failure_uses_rss_full_content_before_summary():
    f = rss()
    item = rss_item()
    item['_entry']['content'] = [{'value': ARTICLE}]
    with patch('src.fetchers.base.safe_request', return_value=response(503, 'Service unavailable')), \
         patch.object(f, '_make_browser_request', side_effect=RuntimeError('browser unavailable')):
        result = f.fetch_items([item])
    assert result.quality_report[0]['status'] == 'rss_content'
    assert 'International officials' in result.articles[0].content


def test_browser_failure_without_rss_body_preserves_metadata():
    f = rss()
    item = rss_item()
    item['_entry']['summary'] = ''
    with patch('src.fetchers.base.safe_request', return_value=response(503, 'Service unavailable')), \
         patch.object(f, '_make_browser_request', side_effect=RuntimeError('browser unavailable')):
        result = f.fetch_items([item])
    assert result.quality_report[0]['status'] == 'failed'
    assert result.articles[0].url == URL
    assert 'unavailable' in result.articles[0].content


def test_web_browser_launch_failure_fails_cleanly():
    f = WebFetcher(ContentSource(type='web', src=URL))
    with patch('src.fetchers.base.safe_request', return_value=response(503, 'Service unavailable')), \
         patch.object(f, '_make_browser_request', side_effect=RuntimeError('browser unavailable')) as browser:
        result = f.fetch()
    browser.assert_called_once()
    assert not result.success and not result.articles


def test_extraction_failure_reason_is_preserved_in_report():
    f = rss()
    truncated = ARTICLE + '<p>Lire la suite</p>'
    with patch('src.fetchers.base.safe_request', return_value=response(200, truncated)), \
         patch('trafilatura.extract', return_value=truncated):
        result = f.fetch_items([rss_item()])
    assert result.quality_report[0]['status'] == 'summary'
    assert 'web:truncated_content' in result.quality_report[0]['reason']
