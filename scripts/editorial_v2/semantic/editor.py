"""Experimental semantic editorial engine with injected evaluators and bounded verification."""
from datetime import datetime, timezone
from .relations import cluster

LEVELS = {'A': 3.0, 'B': 2.0, 'C': 1.0, 'D': 0.0}


def run(candidates, captured_at, encoder, threshold, importance, verifier, *,
        reviewer=None, facts_by_event=None, capacity=16, verification_budget=40):
    if type(capacity) is not int or not 0 <= capacity <= 16: raise ValueError('invalid capacity')
    if type(verification_budget) is not int or not 0 <= verification_budget <= 40: raise ValueError('invalid verification budget')
    as_of = datetime.fromisoformat(captured_at.replace('Z', '+00:00'))
    if as_of.tzinfo is None or as_of.utcoffset() is None: raise ValueError('invalid cutoff')
    groups = cluster(candidates, encoder, as_of.astimezone(timezone.utc), threshold, reviewer)
    facts_by_event = facts_by_event or {}
    options = []; review_queue = []
    for group in groups:
        event_id = min(c['candidate_id'] for c in group)
        ids = {c['candidate_id'] for c in group}
        facts = facts_by_event.get(event_id, ())
        allowed_facts = {item['id'] for item in facts}
        try:
            assessment = importance(group, facts)
            if not isinstance(assessment, dict) or assessment.get('level') not in LEVELS or assessment.get('uncertainty') not in ('low', 'medium', 'high'):
                raise ValueError('invalid importance')
            evidence = assessment.get('evidence_ids')
            fact_ids = assessment.get('fact_ids')
            if not isinstance(evidence, list) or not evidence or not set(evidence) <= ids: raise ValueError('invalid evidence')
            if not isinstance(fact_ids, list) or not set(fact_ids) <= allowed_facts: raise ValueError('invalid facts')
            if assessment['uncertainty'] == 'high':
                review_queue.append({'event_id': event_id, 'candidate_ids': sorted(ids),
                                     'reason': 'importance_uncertain', 'level': assessment['level']})
                continue
            if assessment['level'] == 'D': continue
            options.append({'group': group, 'event_id': event_id, 'assessment': assessment, 'facts': set(fact_ids)})
        except Exception:
            review_queue.append({'event_id': event_id, 'candidate_ids': sorted(ids),
                                 'reason': 'importance_unavailable', 'level': None})
    selected = []; quality = {}; calls = 0; seen_facts = set(); diagnostics = []
    while options and len(selected) < capacity and calls < verification_budget:
        def gain(option):
            return LEVELS[option['assessment']['level']] + len(option['facts'] - seen_facts)
        choice = min(options, key=lambda item: (-gain(item), item['event_id']))
        options.remove(choice)
        # Pre-selection RSS evidence only. A failed/unknown first choice permits the next.
        ranked = sorted(choice['group'], key=lambda c: (-(len(c.get('excerpt') or '') if c.get('excerpt_permitted') else 0), c['candidate_id']))
        picked = None
        for candidate in ranked:
            if calls >= verification_budget: break
            calls += 1
            try: verification = verifier(candidate)
            except Exception: verification = 'failed'
            verified_facts = set()
            if isinstance(verification, dict):
                status = verification.get('status')
                fact_ids = verification.get('verified_fact_ids')
                if (not isinstance(fact_ids, list) or any(not isinstance(x, str) for x in fact_ids)
                        or len(set(fact_ids)) != len(fact_ids) or not set(fact_ids) <= choice['facts']):
                    status = 'unknown'
                else:
                    verified_facts = set(fact_ids)
            else:
                status = verification
            if status not in ('full_text', 'rss_content', 'summary', 'failed', 'rejected', 'unknown'):
                status = 'unknown'
            quality[candidate['candidate_id']] = status
            if status in ('full_text', 'rss_content'):
                picked = candidate['candidate_id']; break
        if picked is None:
            review_queue.append({'event_id': choice['event_id'],
                                 'candidate_ids': sorted(c['candidate_id'] for c in choice['group']),
                                 'reason': 'representative_unverified', 'level': choice['assessment']['level']})
            continue
        selected.append(picked); seen_facts.update(verified_facts)
        diagnostics.append({'candidate_id': picked, 'level': choice['assessment']['level'],
                            'evidence_ids': choice['assessment']['evidence_ids'],
                            'uncertainty': choice['assessment']['uncertainty'],
                            'event_fact_ids': sorted(choice['facts']),
                            'representative_verified_fact_ids': sorted(verified_facts),
                            'unverified_event_fact_ids': sorted(choice['facts'] - verified_facts)})
    if calls >= verification_budget and len(selected) < capacity:
        for option in options:
            review_queue.append({'event_id': option['event_id'],
                                 'candidate_ids': sorted(c['candidate_id'] for c in option['group']),
                                 'reason': 'verification_budget_exhausted', 'level': option['assessment']['level']})
    return {'status': 'partial' if review_queue else 'ok',
            'observed_ids': sorted(c['candidate_id'] for c in candidates),
            'clusters': [[c['candidate_id'] for c in group] for group in groups],
            'selected': selected, 'quality': quality, 'verification_calls': calls,
            'verification_budget': verification_budget, 'unresolved_events': len(review_queue),
            'review_queue': review_queue,
            'editorial_diagnostics': diagnostics,
            'elapsed_seconds': None, 'cost': None, 'input_tokens': None, 'output_tokens': None}
