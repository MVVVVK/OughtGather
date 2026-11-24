"""Inspect a small fixed set of public RSS feeds without logging article bodies.

Only public publisher feed entries from the audit whitelist are sampled. This is
an audit aid, not a semantic classifier and not production candidate data.
"""
import json
import sys
from urllib.parse import urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import feedparser
import rss_probe

NAMES = {
    'BBC World', 'BBC News — Africa', 'The Guardian World',
    'The Guardian Business', 'The Guardian Technology', 'The Guardian Science',
    'The Guardian — International Development', 'France 24 English',
    'France 24 — Africa (EN)', 'France 24 Français', 'France 24 — Moyen-Orient',
    'RFI Français', 'RFI — Afrique', 'Le Monde — Une',
    'Le Monde — International', 'Le Monde — Justice', 'Le Monde — Économie',
    'Le Monde — Les Décodeurs',
}


def read_sample(source):
    url = source['url']
    parts = urlsplit(url)
    if parts.scheme != 'https' or not parts.netloc:
        return {'source': source['name'], 'status': 'invalid_url'}
    try:
        status, robots = rss_probe.fetch(f'https://{parts.netloc}/robots.txt')
        if status == 200 and robots is not None:
            parser = RobotFileParser()
            parser.parse(robots.decode('utf-8', errors='replace').splitlines())
            if not parser.can_fetch(rss_probe.USER_AGENT, url):
                return {'source': source['name'], 'status': 'robots_disallowed'}
        elif status not in (404, 410):
            return {'source': source['name'], 'status': 'robots_unavailable'}
        status, content = rss_probe.fetch(url)
        if status != 200 or content is None:
            return {'source': source['name'], 'status': 'unavailable', 'http_status': status}
        feed = feedparser.parse(content)
        if not feed.version:
            return {'source': source['name'], 'status': 'not_feed'}
        entries = []
        for entry in feed.entries[:20]:
            title = rss_probe.plain(entry.get('title'))[:180]
            link = str(entry.get('link') or '')
            p = urlsplit(link)
            if title and p.scheme in ('http', 'https') and p.hostname:
                entries.append({'title': title, 'url': urlunsplit((p.scheme, p.netloc, p.path, '', ''))})
        return {'source': source['name'], 'status': 'ok', 'entries': entries}
    except Exception:
        return {'source': source['name'], 'status': 'error'}


def main():
    with open(sys.argv[1], encoding='utf8') as file:
        sources = json.load(file)
    for source in sources:
        if source.get('name') in NAMES:
            print('PUBLIC_RSS_SAMPLE=' + json.dumps(read_sample(source), ensure_ascii=False, separators=(',', ':')))


if __name__ == '__main__':
    main()
