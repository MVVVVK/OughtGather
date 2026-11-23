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
