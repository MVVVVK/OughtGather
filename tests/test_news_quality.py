"""Offline coverage for quality checks, fallback, selection and reports."""
from unittest.mock import patch
from bs4 import BeautifulSoup
import feedparser
import pytest

from src.config import ContentSource
from src.fetchers.quality import inspect
from src.fetchers.rss_fetcher import RSSFetcher

EN = '<article><p>International leaders met on Monday to discuss the new agreement and its possible effects on communities across the region.</p><p>The delegates said that negotiations would continue next week after officials have examined the proposals in detail.</p></article>'
FR = '<article><p>Les responsables se sont réunis lundi pour discuter du nouvel accord et de ses conséquences possibles pour les habitants de la région.</p><p>Les délégués ont indiqué que les négociations reprendraient la semaine prochaine après un examen approfondi des propositions.</p></article>'

@pytest.mark.parametrize('html', [EN, FR])
def test_real_article_structure(html):
    assert inspect(html, full=True)[0]

@pytest.mark.parametrize('html,reason', [
    ('<p>JavaScript is disabled in your browser. Please enable JavaScript to proceed.</p>', 'javascript_disabled'),
    ('<p>Please enable the video player to watch this video.</p>', 'player_placeholder'),
    ('<p>Access denied. Verify that you are human.</p>', 'access_blocked'),
    ('<figure><figcaption>A picture of parliament</figcaption></figure>', 'empty_content'),
    ('<p>Lire la suite</p>', 'truncated_content'),
])
def test_bad_pages(html, reason):
    assert inspect(html, full=True)[1] == reason


def entry(title='News', summary='<p>Résumé en français.</p>', content=None):
    data = {'title': title, 'link': 'https://example.org/a', 'summary': summary}
    if content is not None:
        data['content'] = [{'value': content}]
    return feedparser.FeedParserDict(data)


def fetcher(full='Y', limit=2):
    return RSSFetcher(ContentSource(type='rss', src='https://example.org/feed', title='Example', metadata={'full_text': full, 'limit': limit}))


def test_invalid_web_falls_back_to_labelled_summary():
    f = fetcher()
    with patch.object(f, '_fetch_full_text', return_value=('<p>JavaScript is disabled in your browser.</p>', '')):
        result = f.fetch_items([{'_entry': entry()}])
    assert result.quality_report[0]['status'] == 'summary'
    assert 'javascript_disabled' in result.quality_report[0]['reason']
    assert 'Summary / Résumé' in result.articles[0].content
    assert 'JavaScript' not in result.articles[0].content


def test_rss_content_before_summary_and_metadata_fallback():
    f = fetcher()
    with patch.object(f, '_fetch_full_text', return_value=('', '')):
        result = f.fetch_items([{'_entry': entry(content=FR)}, {'_entry': entry(summary='')}])
    assert [r['status'] for r in result.quality_report] == ['rss_content', 'failed']
    assert 'unavailable' in result.articles[1].content


def test_media_rejected_and_later_candidate_fills_quota():
    f = fetcher('N', 1)
    f.source.metadata['text_news_priority'] = 'N'
    items = [entry('Video: latest update'), entry('Audio: news bulletin'), entry('News from Paris')]
    result = f.fetch_items([{'_entry': item} for item in items])
    assert [r['status'] for r in result.quality_report] == ['rejected', 'rejected', 'summary']
    assert result.articles[0].title == 'News from Paris'


def test_summary_survives_epub_and_navigation(tmp_path, monkeypatch):
    import zipfile
    from src.config import _parse_config
    from src.epub.generator import EPUBGenerator
    from src.epub.cover import CoverGenerator
    from PIL import Image
    monkeypatch.chdir(tmp_path)
    config = _parse_config({'title': {'text': 'News édition', 'img': ''}, 'body': [{'type': 'rss', 'src': 'https://example.org/feed', 'title': 'Actualités'}]})
    f = RSSFetcher(config.body[0])
    result = f.fetch_items([{'_entry': entry('Élections en France')}])
    with patch.object(CoverGenerator, '_get_background', return_value=Image.new('RGB', (1440, 1920), 'white')):
        path = EPUBGenerator(config).generate([result])
    with zipfile.ZipFile(path) as book:
        chapter = book.read('EPUB/chapter_0.xhtml').decode('utf-8')
        nav = book.read('EPUB/nav.xhtml').decode('utf-8')
        assert 'Résumé en français' in chapter and 'Summary / Résumé' in chapter
        assert 'Élections en France' in nav and 'chapter_0.xhtml' in nav
        assert 'https://example.org/a' in chapter


def test_fresh_start_does_not_erase_file_on_failure(tmp_path):
    from src.dedup.tracker import DedupTracker
    path = tmp_path / 'history.txt'
    path.write_text('rss:source\tabc\n', encoding='utf-8')
    tracker = DedupTracker(str(path))
    tracker.clear(persist=False)
    assert path.read_text(encoding='utf-8') == 'rss:source\tabc\n'


def test_workflows_use_matching_unique_cache_keys_and_isolate_test_mode():
    from pathlib import Path
    daily = Path('.github/workflows/daily-gather.yml').read_text()
    fresh = Path('.github/workflows/fresh-start.yml').read_text()
    key = '${{ runner.os }}-dedup-v2-${{ github.run_id }}-${{ github.run_attempt }}'
    assert key in daily and key in fresh
    assert 'actions/cache/restore@v5' in daily and 'actions/cache/save@v5' in daily
    assert 'success() && !inputs.test_mode' in daily
    assert 'actions/cache/save@v5' in fresh
