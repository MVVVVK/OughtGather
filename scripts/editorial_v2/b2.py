"""B2: deterministic, conservative offline lexical baseline, not a news editor."""
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote_plus, urlsplit, urlunsplit

TRACKING = {'utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content', 'fbclid', 'gclid'}
STOP = {'The', 'This', 'That', 'After', 'Before', 'Breaking', 'Latest', 'New', 'News', 'A', 'An', 'Le', 'La', 'Les', 'Un', 'Une'}


def normalize_url(value):
    if not value:
        return None
    try:
        p = urlsplit(value)
        if p.scheme.lower() not in ('http', 'https') or not p.hostname or p.username or p.password:
            return None
        # Preserve path, parameter values, duplicates, order, and non-tracking parameters.
        params = [part for part in p.query.split('&') if unquote_plus(part.partition('=')[0]).lower() not in TRACKING]
        return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path or '/', '&'.join(params), ''))
    except ValueError:
        return None


def valid_date(value, as_of):
    if not value: return None
    try:
        date = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if date.tzinfo is None or date.utcoffset() is None: return None
        date = date.astimezone(timezone.utc)
        return date if date <= as_of + timedelta(minutes=5) else None
    except (ValueError, TypeError, AttributeError, OverflowError): return None


def grams(text):
    text = re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', text).casefold()).strip()
    return {text[i:i+3] for i in range(len(text) - 2)}


def entities(text):
    return {x.casefold() for x in re.findall(r'\b[A-ZÀ-ÖØ-Þ][\wÀ-ÿ-]{2,}\b', text) if x not in STOP}


def same_story(a, b, as_of):
    ua, ub = normalize_url(a.get('url')), normalize_url(b.get('url'))
    if ua and ub and ua == ub: return True
    da, db = valid_date(a.get('published_at'), as_of), valid_date(b.get('published_at'), as_of)
    if not da or not db or abs(da - db) > timedelta(hours=36): return False
    if a['language'] != b['language'] or a['language'] == 'unknown': return False
    ea, eb = entities(a['title']), entities(b['title'])
    if len(ea & eb) < 2: return False
    ga, gb = grams(a['title']), grams(b['title'])
    return bool(ga and gb and len(ga & gb) / len(ga | gb) >= 0.72)


def select(candidates, captured_at, capacity=16):
    as_of = datetime.fromisoformat(captured_at.replace('Z', '+00:00'))
    if as_of.tzinfo is None or as_of.utcoffset() is None: raise ValueError('capture time requires timezone')
    as_of = as_of.astimezone(timezone.utc)
    if type(capacity) is not int or capacity < 0: raise ValueError('invalid capacity')
    def key(c):
        dt = valid_date(c.get('published_at'), as_of)
        return (dt is None, -dt.timestamp() if dt else 0, c['candidate_id'])
    ordered = sorted(candidates, key=key)
    groups = []
    for candidate in ordered:
        # Complete-link: no candidate joins a group on a single weak bridge.
        group = next((g for g in groups if all(same_story(candidate, other, as_of) for other in g)), None)
        if group is None: groups.append([candidate])
        else: group.append(candidate)
    return {'status': 'ok', 'observed_ids': [c['candidate_id'] for c in ordered],
            'clusters': [[c['candidate_id'] for c in group] for group in groups],
            'selected': [group[0]['candidate_id'] for group in groups[:capacity]],
            'quality': {}, 'elapsed_seconds': None, 'cost': 0, 'input_tokens': 0, 'output_tokens': 0}
