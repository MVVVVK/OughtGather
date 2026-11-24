"""Build benchmark stages in memory and strictly align recorded outcome IDs."""
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from hashlib import sha256
from scripts.editorial_v2.replay import validate


def iso_date(value):
    if not value: return None
    try:
        try: date = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError: date = parsedate_to_datetime(value)
        return date.isoformat() if date.tzinfo is not None and date.utcoffset() is not None else None
    except (ValueError, TypeError, AttributeError, OverflowError): return None


def build_stage(collector, results, tracker, ai_order, target_at, cutoff):
    rows = collector.snapshot()
    ids = {row['candidate_id'] for row in rows}
    if len(ids) != len(rows) or not rows: raise ValueError('no permitted candidates')
    candidates = []
    for row in rows:
        cid = row['candidate_id']
        candidates.append({'candidate_id': cid, 'source': row['source'], 'language': row['language'] or 'unknown',
            'published_at': iso_date(row['published_at']), 'collected_at': collector.collected_at[cid],
            'title': row['title'], 'excerpt': row['rss_excerpt'] if cid in collector.excerpt_ids else None,
            'excerpt_permitted': cid in collector.excerpt_ids, 'media_type': 'unknown',
            'media_evidence': ['collector_media_hint'] if row['media_hint'] else []})
    outcomes = {}; selected = []
    for result in results:
        if result.source.type != 'rss': continue
        key = tracker.make_source_key(result.source)
        for row in result.quality_report:
            url = row.get('url')
            if not url: continue
            cid = sha256((str(key) + '\0' + url).encode()).hexdigest()
            if cid not in ids: continue
            if cid in outcomes: raise ValueError('ambiguous outcome alignment')
            reason = row.get('reason') or ''
            status = row.get('status')
            if 'quota_limit' in reason: status = 'quota_limited'
            elif row.get('attempted') is False and status not in ('rejected',): status = 'unattempted'
            elif status not in ('full_text', 'rss_content', 'summary', 'failed', 'rejected'): status = 'unknown'
            outcomes[cid] = status
            if row.get('included') is True: selected.append(cid)
    observed = [row['candidate_id'] for row in rows if row['candidate_id'] in outcomes]
    b0 = {'status': 'ok' if len(observed) == len(ids) else 'partial', 'observed_ids': observed,
          'selected': selected, 'clusters': None, 'quality': outcomes}
    result = {'schema_version': 1, 'data_class': 'private', 'captured_at': cutoff,
        'collection_window_start': collector.started_at, 'collection_window_end': cutoff,
        'target_publication_at': target_at, 'candidates': candidates, 'gold': {},
        'incremental_labels': {}, 'recorded_results': {'B0': b0},
        '_b1_eligible': {cid: index for index, cid in enumerate(ai_order or []) if cid in ids and cid in collector.b1_ids}}
    validate(result, allow_private=True)
    return result


def finalize(stage, report):
    allowed = stage.pop('_b1_eligible')
    all_ids = {c['candidate_id'] for c in stage['candidates']}
    result = {'status': 'not_run', 'observed_ids': [], 'selected': [], 'clusters': None, 'quality': {}}
    if report and report.get('mode') == 'shadow':
        if report.get('status') == 'ok' and report.get('topic_selection_status') == 'not_needed':
            pass
        elif report.get('status') == 'ok' and report.get('topic_selection_status') == 'ok' and isinstance(report.get('candidate_cap'), int):
            # stage's allowed IDs preserve the actual pre-cap order with their original indices.
            cap = report['candidate_cap']
            eligible = {cid for cid, index in allowed.items() if index < cap}
            try:
                seen = []; groups = []
                for group in report['topic_groups']:
                    members = [m['candidate_id'] for m in group['members'] if m['candidate_id'] in eligible]
                    if members: groups.append(members); seen.extend(members)
                if len(seen) != len(set(seen)) or set(seen) != eligible: raise ValueError()
                selected = [cid for cid in report['ai_recommended'] if cid in eligible]
                if len(selected) != len(set(selected)): raise ValueError()
                result = {'status': 'ok' if eligible == all_ids else 'partial', 'observed_ids': sorted(eligible),
                          'selected': selected, 'clusters': groups, 'quality': {},
                          'input_tokens': report.get('prompt_tokens'), 'output_tokens': report.get('completion_tokens'),
                          'elapsed_seconds': report.get('duration')}
            except (KeyError, TypeError, ValueError):
                result = {'status': 'failed', 'observed_ids': [], 'selected': [], 'clusters': None, 'quality': {}}
        else:
            result['status'] = 'failed'
    stage['recorded_results']['B1'] = result
    validate(stage, allow_private=True)
    return stage
