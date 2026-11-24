"""Read-only public RSS audit. Results contain metadata and hashes, never article bodies."""
import concurrent.futures
import hashlib
import html
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import feedparser
import httpx

USER_AGENT = 'OughtGatherRSSAudit/1.0 (+public read-only RSS diagnostics)'
MAX_BYTES = 2 * 1024 * 1024
TIMEOUT = httpx.Timeout(12, connect=5)
MEDIA = re.compile(r'\b(video|audio|podcast|watch|listen|live|livestream|vid[eé]o|direct|en direct)\b', re.I)


class TextOnly(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def plain(value):
    parser = TextOnly()
    parser.feed(str(value or ''))
    return re.sub(r'\s+', ' ', html.unescape(' '.join(parser.parts))).strip()


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()[:20]


def normalized_url(value):
    p = urlsplit(value)
    params = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if not k.lower().startswith('utm_')]
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path.rstrip('/') or '/', urlencode(sorted(params)), ''))


def parsed_date(item, field='published_parsed'):
    parsed = item.get(field)
    if not parsed:
        return None
    try:
        return datetime(*parsed[:6], tzinfo=timezone.utc)
    except (ValueError, TypeError, OverflowError):
        return None


def classify_entry(entry, now):
    title, link = plain(entry.get('title')), entry.get('link', '')
    if not title or not link or urlsplit(link).scheme not in ('http', 'https'):
        return None
    summary = plain(entry.get('summary') or entry.get('description'))
    tags = ' '.join(str(t.get('term', '')) for t in entry.get('tags', []) if isinstance(t, dict))
    path = urlsplit(link).path
    evidence = title + ' ' + tags + ' ' + path
    media = bool(MEDIA.search(evidence) or re.search(r'/vid[eé]os?/', path, re.I))
    published = parsed_date(entry)
    title_key = re.sub(r'[^\w]+', ' ', title.casefold(), flags=re.UNICODE).strip()
    return {'url_id': digest(normalized_url(link)), 'title_id': digest(title_key),
            'published': published.isoformat() if published else None,
            'within_7_days': bool(published and now - timedelta(days=7) <= published <= now + timedelta(minutes=5)),
            'summary_chars': len(summary), 'media_hint': media}


def parse_feed(content, now):
    feed = feedparser.parse(content)
    if not feed.get('version'):
        return {'status': 'not_feed', 'rss_entries': 0, 'items': []}
    entries = [row for item in feed.entries if (row := classify_entry(item, now))]
    dates = [row['published'] for row in entries if row['published']]
    total = len(entries)
    nonempty = sum(x['summary_chars'] > 0 for x in entries)
    media = sum(x['media_hint'] for x in entries)
    return {'status': 'parse_warning' if feed.bozo else 'ok', 'feed_format': feed.version,
            'rss_entries': len(feed.entries), 'valid_title_link': total,
            'recent_items_7d': sum(x['within_7_days'] for x in entries),
            'published_missing': sum(x['published'] is None for x in entries),
            'feed_updated': parsed_date(feed.feed, 'updated_parsed').isoformat() if parsed_date(feed.feed, 'updated_parsed') else None,
            'latest_published': max(dates) if dates else None,
            'summary_nonempty': nonempty, 'summary_nonempty_rate': round(nonempty / total, 4) if total else None,
            'average_summary_chars': round(sum(x['summary_chars'] for x in entries) / total, 1) if total else None,
            'media_hint': media, 'media_hint_rate': round(media / total, 4) if total else None,
            'non_media_hint': total - media, 'items': entries}


def fetch(url):
    with httpx.stream('GET', url, headers={'User-Agent': USER_AGENT, 'Accept': 'application/rss+xml,application/atom+xml,application/xml,text/xml,*/*'},
                      timeout=TIMEOUT, follow_redirects=False) as response:
        chunks, size = [], 0
        for chunk in response.iter_bytes(65536):
            size += len(chunk)
            if size > MAX_BYTES:
                return response.status_code, None
            chunks.append(chunk)
        return response.status_code, b''.join(chunks)


def probe(source, now):
    result = {**source, 'checked_at': datetime.now(timezone.utc).isoformat()}
    target = urlsplit(source['url'])
    if target.scheme != 'https' or not target.hostname:
        return {**result, 'status': 'unsafe_or_non_https'}
    try:
        robots_url = f'https://{target.netloc}/robots.txt'
        robots_status, content = fetch(robots_url)
        if robots_status == 200 and content is not None:
            parser = RobotFileParser()
            parser.parse(content.decode('utf-8', errors='replace').splitlines())
            if not parser.can_fetch(USER_AGENT, source['url']):
                return {**result, 'status': 'robots_disallowed', 'robots_status': robots_status}
        elif robots_status not in (404, 410):
            return {**result, 'status': 'robots_unavailable', 'robots_status': robots_status}
        status, content = fetch(source['url'])
        result.update(http_status=status, robots_status=robots_status)
        if 300 <= status < 400:
            return {**result, 'status': 'redirect_not_followed'}
        if status != 200:
            return {**result, 'status': 'http_error'}
        if content is None:
            return {**result, 'status': 'size_limit'}
        return {**result, **parse_feed(content, now)}
    except httpx.TimeoutException:
        return {**result, 'status': 'timeout'}
    except httpx.HTTPError:
        return {**result, 'status': 'network_error'}
    except Exception:
        return {**result, 'status': 'processing_error'}


COMPARISONS = {
    'Le Monde': ('Le Monde — Une', ['Le Monde — International', 'Le Monde — Justice', 'Le Monde — Économie', 'Le Monde — Les Décodeurs']),
    'Guardian': ('The Guardian World', ['The Guardian Business', 'The Guardian Technology', 'The Guardian Science', 'The Guardian — International Development']),
    'France 24 EN': ('France 24 English', ['France 24 — Africa (EN)']),
    'France 24 FR': ('France 24 Français', ['France 24 — Moyen-Orient']),
    'RFI': ('RFI Français', ['RFI — Afrique']),
}


def compare(rows):
    by_name = {row['name']: row for row in rows}
    output = []
    for publisher, (main, branches) in COMPARISONS.items():
        for branch in branches:
            a, b = by_name.get(main, {}), by_name.get(branch, {})
            row = {'publisher': publisher, 'main': main, 'branch': branch,
                   'main_status': a.get('status'), 'branch_status': b.get('status')}
            if a.get('status') in ('ok', 'parse_warning') and b.get('status') in ('ok', 'parse_warning'):
                aa = [i for i in a['items'] if i['within_7_days']]
                bb = [i for i in b['items'] if i['within_7_days']]
                urls = {i['url_id'] for i in aa}
                titles = {i['title_id'] for i in aa}
                shared = [i for i in bb if i['url_id'] in urls or i['title_id'] in titles]
                row.update(main_items_7d=len(aa), branch_items_7d=len(bb),
                           exact_url_or_title_overlap=len(shared),
                           branch_unmatched_not_proven_independent=len(bb)-len(shared))
            output.append(row)
    return output


def main():
    sources = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    now = datetime.now(timezone.utc)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        rows = list(executor.map(lambda source: probe(source, now), sources))
    report = {'checked_at': now.isoformat(), 'window': 'seven days before checked_at',
              'method': 'URL/title exact matches only; unmatched does not prove a distinct event. Text/media are metadata hints.',
              'sources': rows, 'comparisons': compare(rows)}
    Path(sys.argv[2]).write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    summary = {**report, 'sources': [{k: v for k, v in row.items() if k != 'items'} for row in rows]}
    print('RSS_AUDIT_SUMMARY=' + json.dumps(summary, ensure_ascii=False, separators=(',', ':')))


if __name__ == '__main__':
    main()
