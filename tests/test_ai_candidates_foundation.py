from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from src.ai.candidates import CandidateCollector


def test_collector_is_read_only_and_balanced():
    collector = CandidateCollector(excerpt_limit=10)
    candidates = [{'url': f'https://example.test/{i}?secret=yes', 'title': '<b>News</b>',
                   '_entry': {'summary': '<p>Long excerpt with text</p>'}} for i in range(10)]
    original = deepcopy(candidates)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda source: collector.collect(source, candidates), ['a', 'b']))
    snapshot = collector.snapshot(2)
    assert candidates == original
    assert [c['source'] for c in snapshot] == ['a', 'b']
    assert all(len(c['rss_excerpt']) <= 10 and 'url' not in c for c in snapshot)
    assert collector.snapshot(2) == snapshot
    assert collector.snapshot(0) == []


def test_language_fallback_entry_feed_source_and_unknown():
    collector = CandidateCollector()
    entries = [{'url': f'https://example.test/{i}', 'title': 'News', '_entry': entry}
               for i, entry in enumerate([{'language':'fr'}, {}, {}, {}])]
    collector.collect('a', entries[:2], feed_language='en', source_language='es')
    collector.collect('b', entries[2:3], source_language='es')
    collector.collect('c', entries[3:])
    assert {row['language'] for row in collector.snapshot(4)} == {'fr','en','es','unknown'}
