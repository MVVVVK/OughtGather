"""Offline experimental hybrid editor. No external models or provider calls.

Heuristics propose relationships, not verified facts or objective importance.
"""
import re
import unicodedata
from datetime import datetime, timezone, timedelta
from .b2 import normalize_url, valid_date

# Deliberately small, auditable language bridges. No claim of broad multilingual coverage.
CUES = {
    'attack': {'attack','attacks','attacked','strike','strikes','struck','assault','attaque','attaques','frappe','frappes','напад','атака','атакує','атаки','удар','атаки','atak'},
    'election': {'election','elections','electoral','élection','élections','vote','voting','выборы','вибори','wybory'},
    'climate': {'climate','climatique','climat','клімат','климат','klimat'},
    'earthquake': {'earthquake','earthquakes','séisme','séismes','tremblement','землетрус','землетрясение','trzęsienie'},
    'trade': {'trade','tariff','tariffs','tarif','tarifs','commerce','торгівля','торговля','handel'},
    'outbreak': {'outbreak','epidemic','épidémie','épidémies','спалах','епідемія','эпидемия','epidemia'},
    'ipo': {'ipo','listing','listed','bourse','上市','розміщення'},
}
IMPACT = {
    'security': {'war','invasion','ceasefire','nuclear','guerre','invasion','nucléaire','війна','война','核武器','战争'},
    'economy': {'inflation','recession','sanctions','tariffs','récession','sanctions','inflacja','інфляція','制裁'},
    'public_health': {'pandemic','pandémie','pandemia','пандемія','пандемия','疫情'},
    'climate': {'climate','climat','climatique','клімат','климат','气候'},
    'international_order': {'treaty','sanction','summit','traité','sommet','договір','саммит'},
    'technology': {'semiconductor','semiconductors','cyberattack','semiconducteur','cyberattaque','напівпровідники'},
}
STOP = {'The','This','That','After','Before','Breaking','Latest','New','News','Le','La','Les','Un','Une','Dans','Pour','Ukraine','Russia','Russian','Ukrainian','Україна','України','Росії','российский'}
CYR = str.maketrans({'а':'a','б':'b','в':'v','г':'h','ґ':'g','д':'d','е':'e','є':'ie','ж':'zh','з':'z','и':'y','і':'i','ї':'i','й':'i','к':'k','л':'l','м':'m','н':'n','о':'o','п':'p','р':'r','с':'s','т':'t','у':'u','ф':'f','х':'kh','ц':'ts','ч':'ch','ш':'sh','щ':'shch','ь':'','ю':'iu','я':'ia','ы':'y','э':'e','ё':'e','ъ':''})


def fold(value):
    value = unicodedata.normalize('NFKD', value.casefold())
    return ''.join(c for c in value if not unicodedata.combining(c)).translate(CYR)


def tokens(text):
    return {fold(w) for w in re.findall(r'[^\W_]+', text, re.UNICODE) if len(w) > 2}


def cues(text):
    words = tokens(text)
    return {kind for kind, aliases in CUES.items() if words & {fold(a) for a in aliases} or any(a in text for a in aliases if len(a) == 2)}


def entities(text):
    # Capitalized proper names, numbers or acronyms are weak indicators, never proof.
    found = re.findall(r'\b[A-ZÀ-ÖØ-ÞА-ЯІЇЄҐ][\wÀ-ÿА-яІіЇїЄєҐґ-]{2,}\b', text)
    cue_words = {fold(alias) for aliases in CUES.values() for alias in aliases}
    return {fold(x) for x in found if x not in STOP and len(fold(x)) >= 4 and fold(x) not in cue_words}


def features(candidate):
    title = candidate['title']
    # Only explicitly permitted pre-selection excerpts may be used.
    excerpt = candidate.get('excerpt') if candidate.get('excerpt_permitted') else None
    text = title + ' ' + (excerpt or '')
    return {'cues': cues(text), 'entities': entities(title), 'words': tokens(text), 'text': text}


def explicit_date(text):
    """Use a unique explicit ISO date as event evidence; never invent a publication date."""
    values = set()
    for value in re.findall(r'\b(?:19|20)\d{2}-\d{2}-\d{2}\b', text):
        try: values.add(datetime.fromisoformat(value).replace(tzinfo=timezone.utc))
        except ValueError: continue
    return next(iter(values)) if len(values) == 1 else None


def relation(a, b, as_of):
    ua, ub = normalize_url(a.get('url')), normalize_url(b.get('url'))
    if ua and ub and ua == ub: return 'same_item'
    da, db = valid_date(a.get('published_at'), as_of), valid_date(b.get('published_at'), as_of)
    fa, fb = features(a), features(b)
    if da is None or db is None:
        da, db = explicit_date(fa['text']), explicit_date(fb['text'])
        if da and da > as_of or db and db > as_of: return 'unknown'
    if da is None or db is None or abs(da - db) > timedelta(hours=72): return 'unknown'
    overlap = fa['entities'] & fb['entities']
    if len(overlap) < 2 or not (fa['cues'] & fb['cues']): return 'unknown'
    # Competing entity anchors on both sides are possible independent events.
    if fa['entities'] - overlap and fb['entities'] - overlap: return 'unknown'
    # Same-language text must also agree lexically. Across languages, two entity
    # anchors plus an explicit translated action are required; uncertainty remains.
    if a['language'] == b['language']:
        union = fa['words'] | fb['words']
        if not union or len(fa['words'] & fb['words']) / len(union) < 0.35: return 'unknown'
    return 'same_event'


def impact_evidence(item):
    words = tokens(features(item)['text'])
    return sorted(category for category, aliases in IMPACT.items() if words & {fold(a) for a in aliases} or any(a in features(item)['text'] for a in aliases if len(a) == 2))


def select(candidates, captured_at, capacity=16):
    as_of = datetime.fromisoformat(captured_at.replace('Z', '+00:00'))
    if as_of.tzinfo is None or as_of.utcoffset() is None: raise ValueError('capture time requires timezone')
    as_of = as_of.astimezone(timezone.utc)
    if type(capacity) is not int or capacity < 0: raise ValueError('invalid capacity')
    # ID ordering makes clustering independent of input ordering, rather than greedily
    # following input order; complete-link prevents a weak transitive bridge.
    ordered = sorted(candidates, key=lambda c: c['candidate_id'])
    groups = []
    for candidate in ordered:
        group = next((g for g in groups if all(relation(candidate, other, as_of) in ('same_item', 'same_event') for other in g)), None)
        if group is None: groups.append([candidate])
        else: group.append(candidate)
    def rep_key(c):
        date = valid_date(c.get('published_at'), as_of)
        excerpt = c.get('excerpt') if c.get('excerpt_permitted') else None
        # Information present in RSS is a proxy, not proof of source reliability or full text.
        return (date is None, -min(len(excerpt or ''), 600), -date.timestamp() if date else 0, c['candidate_id'])
    options = []
    for group in groups:
        rep = min(group, key=rep_key)
        evidence = set().union(*(impact_evidence(x) for x in group))
        ent = set().union(*(entities(x['title']) for x in group))
        options.append({'representative': rep, 'impact': evidence, 'entities': ent})
    def gain(option, covered_impact, covered_entities):
        rep = option['representative']; date = valid_date(rep.get('published_at'), as_of)
        age = max(0, (as_of - date).total_seconds() / 3600) if date else None
        recency = max(0, 1 - age / 168) if age is not None else 0
        impact = min(1, 0.5 * len(option['impact']))
        novelty = len(option['impact'] - covered_impact) * 0.2
        redundancy = 0.15 * min(2, len(option['entities'] & covered_entities))
        return impact + novelty + 0.2 * recency - redundancy

    selected_options = []; covered_impact = set(); covered_entities = set()
    while options and len(selected_options) < capacity:
        # Weak/unrecognized subjects remain eligible; scores are experimental proxies.
        choice = min(options, key=lambda x: (-gain(x, covered_impact, covered_entities), x['representative']['candidate_id']))
        selected_options.append(choice); covered_impact.update(choice['impact']); covered_entities.update(choice['entities']); options.remove(choice)

    def objective(sequence):
        covered_i = set(); covered_e = set(); total = 0.0
        for option in sequence:
            total += gain(option, covered_i, covered_e)
            covered_i.update(option['impact']); covered_e.update(option['entities'])
        return total

    # One bounded best replacement: never an unbounded optimizer or extra call.
    best = selected_options; best_score = objective(best)
    for index in range(len(selected_options)):
        for candidate in sorted(options, key=lambda x: x['representative']['candidate_id']):
            trial = selected_options[:index] + [candidate] + selected_options[index+1:]
            score = objective(trial)
            if score > best_score + 1e-12:
                best, best_score = trial, score
    selected_options = best
    selected = []; diagnostics = []; covered_impact = set(); covered_entities = set()
    for choice in selected_options:
        selected.append(choice['representative']['candidate_id'])
        diagnostics.append({'candidate_id': choice['representative']['candidate_id'],
                            'impact_indicators': sorted(choice['impact']),
                            'marginal_score': round(gain(choice, covered_impact, covered_entities), 6),
                            'importance_status': 'unverified_heuristic'})
        covered_impact.update(choice['impact']); covered_entities.update(choice['entities'])
    return {'status': 'ok', 'observed_ids': [c['candidate_id'] for c in ordered],
            'clusters': [[c['candidate_id'] for c in group] for group in groups], 'selected': selected,
            'quality': {}, 'editorial_diagnostics': diagnostics,
            'elapsed_seconds': None, 'cost': 0, 'input_tokens': 0, 'output_tokens': 0}
