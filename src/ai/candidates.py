"""Private, thread-safe RSS candidate snapshots for a future shadow observer.

Snapshots are detached from the fetcher's mutable objects and never change dedup.
"""
from collections import defaultdict, deque
from copy import deepcopy
from hashlib import sha256
from html import unescape
from bs4 import BeautifulSoup
import re
from threading import Lock


def _plain(value, limit):
    text = BeautifulSoup(unescape(str(value or '')), 'html.parser').get_text(' ')
    text = re.sub(r'https?://[^\s<>]+', '[link]', text, flags=re.IGNORECASE)
    return re.sub(r'\s+', ' ', text).strip()[:limit]


class CandidateCollector:
    def __init__(self, excerpt_limit=600):
        self._items = []
        self._lock = Lock()
        self.excerpt_limit = max(0, min(excerpt_limit, 4000))

    def collect(self, source, candidates, source_key=None, feed_language=None, source_language=None):
        """Collect copies after dedup filtering; URLs are hashed and never sent."""
        entries = []
        for candidate in candidates:
            url = candidate.get('url') or ''
            entry = candidate.get('_entry') or {}
            candidate_id = sha256((str(source_key or source) + '\0' + url).encode('utf-8')).hexdigest()
            entries.append({'candidate_id': candidate_id,
                            'title': _plain(candidate.get('title'), 500),
                            'source': _plain(source, 200),
                            'language': next((value for value in (_plain(x, 20) for x in (entry.get('language'), feed_language, source_language)) if value), 'unknown'),
                            'published_at': _plain(candidate.get('published'), 100),
                            'rss_excerpt': _plain(entry.get('summary') or entry.get('description'), self.excerpt_limit),
                            'media_hint': bool(candidate.get('_media'))})
        with self._lock:
            self._items.extend(entries)

    def snapshot(self, limit):
        """Stable round-robin by source ensures one feed cannot fill the cap."""
        with self._lock:
            items = deepcopy(self._items)
        groups = defaultdict(list)
        seen = set()
        for item in sorted(items, key=lambda row: (row['source'], row['candidate_id'], row['title'], row['rss_excerpt'])):
            if item['candidate_id'] not in seen:
                groups[item['source']].append(item)
                seen.add(item['candidate_id'])
        queues = [deque(sorted(groups[source], key=lambda x: x['candidate_id'])) for source in sorted(groups)]
        result = []
        while len(result) < max(0, limit) and queues:
            active = []
            for queue in queues:
                if len(result) >= limit:
                    break
                result.append(queue.popleft())
                if queue:
                    active.append(queue)
            queues = active
        return result
