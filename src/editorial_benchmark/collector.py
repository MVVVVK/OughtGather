"""Fail-closed opt-in observer, isolated from the existing Shadow collector."""
import hashlib
import json
import re
from datetime import datetime, timezone
from threading import Lock
from src.ai.candidates import CandidateCollector


def source_id(source_key):
    return hashlib.sha256(str(source_key).encode('utf-8')).hexdigest()


def parse_permissions(value):
    data = json.loads(value)
    if not isinstance(data, dict) or not data: raise ValueError('invalid permissions')
    for key, rule in data.items():
        if not re.fullmatch('[0-9a-f]{64}', key) or not isinstance(rule, dict) or set(rule) != {'title', 'excerpt', 'b1'}:
            raise ValueError('invalid permissions')
        if any(type(rule[x]) is not bool for x in ('title', 'excerpt', 'b1')) or not rule['title']:
            raise ValueError('invalid permissions')
    return data


def permitted_candidates(candidates, rule):
    safe = []
    for item in candidates:
        entry = item.get('_entry') or {}
        entry_safe = {'language': entry.get('language')}
        if rule['excerpt']:
            entry_safe.update(summary=entry.get('summary'), description=entry.get('description'))
        safe.append({'url': item.get('url'), 'title': item.get('title'), 'published': item.get('published'),
                     '_media': item.get('_media'), '_entry': entry_safe})
    return safe


class BenchmarkCollector:
    def __init__(self, permissions, excerpt_limit=600):
        self.permissions = permissions
        self.collector = CandidateCollector(excerpt_limit)
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.b1_ids = set()
        self.excerpt_ids = set()
        self.collected_at = {}
        self.lock = Lock()

    def collect(self, source, candidates, source_key=None, feed_language=None, source_language=None):
        rule = self.permissions.get(source_id(source_key))
        if not rule: return
        safe = permitted_candidates(candidates, rule)
        self.collector.collect(source, safe, source_key, feed_language, source_language)
        with self.lock:
            when = datetime.now(timezone.utc).isoformat()
            for item in safe:
                cid = hashlib.sha256((str(source_key or source) + '\0' + (item.get('url') or '')).encode()).hexdigest()
                if rule['b1']: self.b1_ids.add(cid)
                if rule['excerpt']: self.excerpt_ids.add(cid)
                self.collected_at[cid] = when

    def snapshot(self):
        return self.collector.snapshot(1000000)


class Fanout:
    def __init__(self, ai, benchmark): self.ai, self.benchmark = ai, benchmark
    def collect(self, source, candidates, source_key=None, feed_language=None, source_language=None):
        if self.benchmark is None: return
        rule = self.benchmark.permissions.get(source_id(source_key))
        if not rule: return
        try: self.benchmark.collect(source, candidates, source_key, feed_language, source_language)
        except Exception: return  # fail closed for both observation and external Shadow
        if self.ai is not None and rule['b1']:
            self.ai.collect(source, permitted_candidates(candidates, rule), source_key, feed_language, source_language)
