"""Deterministic event-level recommendation without affecting publication."""
from collections import Counter
from hashlib import sha256


def compose(rankings, mappings, k, max_per_event=2):
    confidence = {'high': 2, 'medium': 1, 'low': 0}
    depth_type = {'analysis': 2, 'investigation': 2, 'feature': 1}
    rows = sorted(rankings, key=lambda r: (-r['score'], -r['information_gain'],
                                            -confidence.get(r['confidence'], 0),
                                            -depth_type.get(r['news_type'], 0), r['candidate_id']))
    events = {}
    for row in rows:
        cid = row['candidate_id']
        if cid in mappings:
            raw, relation = mappings[cid]
            key = 'composed:' + raw
        elif row.get('event_cluster'):
            key, relation = 'hint:' + row['event_cluster'], row['cluster_type']
        else:
            key, relation = 'single:' + cid, 'primary'
        event = sha256(key.encode()).hexdigest()[:24]
        events.setdefault(event, []).append((row, relation))
    chosen, selected_counts, event_counts = [], Counter(), Counter()
    must_cover = {event for event, members in events.items()
                  if any(r['global_impact'] >= 85 or (r['importance'] >= 92 and r['global_impact'] >= 70) for r, _ in members)}
    primaries = [(members[0][0], event, members[0][1]) for event, members in events.items()]
    primaries.sort(key=lambda x: (x[1] not in must_cover, -x[0]['score'], x[0]['candidate_id']))
    remaining = primaries[:]
    exploration_used = 0
    while remaining and len(chosen) < k:
        first = remaining[0]
        if first[1] in must_cover:
            pick = first
        else:
            # A small exploration allowance favors meaningful, less represented
            # perspectives close to the leading event; it never displaces must-cover.
            exploratory = [x for x in remaining if x[1] not in must_cover
                           and x[0]['importance'] >= 70 and x[0]['global_impact'] >= 50
                           and x[0]['score'] >= first[0]['score'] - 6
                           and (not selected_counts[('region', x[0]['region'])]
                                or not selected_counts[('topic', x[0]['topic'])])]
            if len(chosen) >= max(1, k - max(1, k // 10)) and exploration_used < k // 10 and exploratory:
                pick = min(exploratory, key=lambda x: (-x[0]['score'], x[0]['candidate_id']))
                exploration_used += 1
            else:
                # Diversity only breaks near ties, never substitutes weak news.
                near = [x for x in remaining if x[1] not in must_cover and x[0]['score'] >= first[0]['score'] - 3]
                pick = min(near, key=lambda x: (selected_counts[('source', x[0].get('source',''))] >= max(1, (k + 3) // 4),
                                                    selected_counts[('source', x[0].get('source',''))],
                                                    selected_counts[('region', x[0]['region'])],
                                                    selected_counts[('topic', x[0]['topic'])],
                                                    -x[0]['score'], x[0]['candidate_id']))
        remaining.remove(pick)
        row, event, relation = pick
        chosen.append((row, event, relation))
        event_counts[event] += 1
        for field in ('source','region','topic'):
            selected_counts[(field, row.get(field,''))] += 1
    second_angles = []
    if max_per_event > 1:
        for event, members in events.items():
            if not event_counts[event] or not any(r['importance'] >= 75 or r['global_impact'] >= 75 for r, _ in members):
                continue
            already = {row['candidate_id'] for row, _, _ in chosen}
            for row, relation in members:
                if row['candidate_id'] not in already and relation in ('analysis','different_angle','update'):
                    second_angles.append((row, event, relation))
        second_angles.sort(key=lambda x: (-x[0]['score'], x[0]['candidate_id']))
        for row, event, relation in second_angles:
            if len(chosen) >= k:
                break
            if event_counts[event] < min(2, max_per_event):
                chosen.append((row,event,relation)); event_counts[event] += 1
    recommended = [row['candidate_id'] for row, _, _ in chosen]
    return recommended, {
        'event_count': len(events), 'recommended_event_count': len(event_counts),
        'unique_events_recommended': len(event_counts),
        'largest_event_share': max(event_counts.values(), default=0) / len(chosen) if chosen else 0,
        'duplicate_articles_suppressed': sum(max(0, len(v)-1) for v in events.values()) - sum(max(0,n-1) for n in event_counts.values()),
        'second_angle_articles_selected': sum(max(0,n-1) for n in event_counts.values()),
        'must_cover_events': len(must_cover), 'must_cover_events_selected': len(must_cover & event_counts.keys()),
        'event_clusters': {event: [{'candidate_id': r['candidate_id'], 'type': relation} for r, relation in members]
                           for event, members in events.items() if len(members) > 1}}
