"""Static evidence ledger must remain bounded, public and explicit about uncertainty."""
import json
from pathlib import Path
from urllib.parse import urlsplit


LEDGER = Path(__file__).resolve().parents[2] / 'docs/data/rss_public_event_samples_2026-11-24.json'


def test_ledger_has_public_bounded_sources_and_no_query_strings():
    data = json.loads(LEDGER.read_text(encoding='utf-8'))
    assert data['run_id'] == 45301867050 and len(data['cases']) == 10
    article_hosts = {'www.bbc.co.uk', 'www.lemonde.fr', 'www.theguardian.com', 'www.france24.com', 'www.rfi.fr'}
    feed_hosts = {'feeds.bbci.co.uk', 'www.theguardian.com', 'www.france24.com', 'www.rfi.fr', 'www.lemonde.fr'}
    for case in data['cases']:
        assert len(case['evidence']) == 2
        for e in case['evidence']:
            article = urlsplit(e['public_article_url'])
            feed = urlsplit(e['public_feed_url'])
            assert article.scheme == feed.scheme == 'https'
            assert article.hostname in article_hosts and feed.hostname in feed_hosts
            assert not article.query and not article.fragment
            assert 1 <= e['feed_position'] <= 20
            assert set(e) == {'source', 'public_feed_url', 'feed_position', 'public_article_url', 'observed_at'}


def test_equal_url_examples_and_uncertain_increment_are_explicit():
    data = json.loads(LEDGER.read_text(encoding='utf-8'))
    for case in data['cases']:
        if case['category'] == 'normalized_url_equal':
            assert case['evidence'][0]['public_article_url'] == case['evidence'][1]['public_article_url']
    rfi = next(c for c in data['cases'] if c['id'] == 'rfi_angle')
    assert rfi['semantic_status'] == 'information_gain_unverified'
