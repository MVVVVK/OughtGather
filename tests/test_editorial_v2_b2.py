import copy
from scripts.editorial_v2.b2 import normalize_url, same_story, select, valid_date
from datetime import datetime, timezone

NOW = '2026-11-24T12:00:00Z'


def row(cid, title, date='2026-11-24T10:00:00Z', url=None, language='en'):
    return {'candidate_id': cid, 'title': title, 'published_at': date, 'url': url, 'language': language}


def test_conservative_url_preserves_meaningful_query_and_credentials_rejected():
    assert normalize_url('https://EXAMPLE.org/article?id=4&utm_source=feed#part') == 'https://example.org/article?id=4'
    assert normalize_url('https://example.org/article?id=5') != normalize_url('https://example.org/article?id=4')
    assert normalize_url('https://user:pw@example.org/article') is None
    assert normalize_url('https://example.org/article?edition=2&edition=1') != normalize_url('https://example.org/article?edition=1&edition=2')
    assert normalize_url('https://example.org/article?sig=a%20b&utm_medium=rss') == 'https://example.org/article?sig=a%20b'


def test_real_b2_groups_same_item_and_close_headlines_but_not_independent_news():
    items = [
        row('a', 'Ukraine strikes Russian vessel near Benghazi', url='https://example.org/story?id=1&utm_source=x'),
        row('b', 'Ukraine strikes Russian vessel near Benghazi', url='https://example.org/story?id=1'),
        row('c', 'Ukraine strikes Russian vessel near Benghazi port'),
        row('d', 'Nigeria election commission announces election result'),
        row('e', 'South Africa election commission announces election result'),
        row('f', 'Ukraine frappe un navire russe près de Benghazi', language='fr'),
    ]
    result = select(items, NOW, 16)
    clusters = [set(g) for g in result['clusters']]
    assert {'a', 'b', 'c'} in clusters
    assert {'d'} in clusters and {'e'} in clusters and {'f'} in clusters
    assert len(set(result['selected']) & {'a', 'b', 'c'}) == 1
    assert result == select(list(reversed(copy.deepcopy(items))), NOW, 16)


def test_missing_future_and_distant_dates_do_not_supply_event_links():
    now = datetime.fromisoformat(NOW.replace('Z', '+00:00'))
    a = row('a', 'Ukraine strikes Russian vessel near Benghazi')
    for b in (row('b', a['title'], None), row('b', a['title'], '2026-11-25T10:00:00Z'),
              row('b', a['title'], '2026-11-20T10:00:00Z')):
        assert not same_story(a, b, now)
    assert valid_date('broken', now) is None
    result = select([row('missing', 'Unknown dated story', None), a], NOW)
    assert result['selected'] == ['a', 'missing']


def test_complete_link_does_not_bridge_different_query_urls():
    # Exact URL can group one pair, but does not pull a third item through a bridge.
    items = [row('a', 'Alpha Beta report', url='https://x.org/item?id=1'),
             row('b', 'Completely different headline', url='https://x.org/item?id=1'),
             row('c', 'Completely different headline', url='https://x.org/item?id=2')]
    result = select(items, NOW)
    assert {frozenset(g) for g in result['clusters']} == {frozenset({'a', 'b'}), frozenset({'c'})}
