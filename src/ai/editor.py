"""Offline-comparable shadow editor; no writes to the publication pipeline."""
import json
import os
import time
from collections import Counter
from datetime import datetime, timezone
from .client import DeepSeekClient, ProviderError
from .schemas import validate_batch
from .memory.storage import load_profile


def setting(name, default, ceiling):
    try:
        return min(ceiling, max(0, int(os.getenv(name) or default)))
    except (ValueError, TypeError):
        return int(default)


def run(candidates, client=None):
    started = time.monotonic()
    mode = os.getenv('AI_EDITOR_MODE', 'off')
    model = os.getenv('DEEPSEEK_MODEL') or 'deepseek-flash'
    report = {'mode': mode, 'model': model, 'status': 'off', 'scope': 'rss_only',
              'started_at': datetime.now(timezone.utc).isoformat(), 'candidate_count': len(candidates),
              'analyzed_count': 0, 'skipped_budget': 0, 'failed_count': 0,
              'api_calls': 0, 'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0,
              'estimated_cost': None, 'pricing_version': os.getenv('AI_PRICING_VERSION', 'unconfigured'),
              'memory_revision': None, 'memory_status': 'not_loaded', 'fallback_reason': None,
              'traditional_selected': [], 'ai_recommended': [], 'agreement': [], 'ai_only': [], 'traditional_only': [],
              'ranking': [], 'topic_distribution': {}, 'region_distribution': {}, 'language_distribution': {}, 'event_clusters': {}}
    if mode != 'shadow':
        return report
    key = os.getenv('DEEPSEEK_API_KEY', '')
    if not key and client is None:
        report.update(status='unavailable', fallback_reason='missing_key', duration=round(time.monotonic() - started, 3))
        return report
    client = client or DeepSeekClient(key, model)
    profile, revision, status = load_profile()
    report.update(memory_revision=revision, memory_status=status, status='ok')
    cap = setting('AI_MAX_CANDIDATES', 200, 1000)
    batch_size = max(1, setting('AI_MAX_BATCH_CANDIDATES', 40, 100))
    max_calls = setting('AI_MAX_CALLS_PER_RUN', 6, 100)
    input_cap = setting('AI_MAX_INPUT_TOKENS', 80000, 1000000)
    output_cap = setting('AI_MAX_OUTPUT_TOKENS', 8000, 100000)
    total_cap = setting('AI_MAX_TOTAL_TOKENS', 88000, 1100000)
    deadline = started + setting('AI_TOTAL_TIMEOUT_SECONDS', 60, 300)
    selected = [c['candidate_id'] for c in candidates if c.get('traditional_selected')]
    candidate_truncated = len(candidates) > cap
    all_candidates = candidates
    candidates = candidates[:cap]
    report['traditional_selected'] = selected
    report['candidates'] = [{k: c.get(k) for k in ('candidate_id', 'title', 'source', 'language', 'traditional_selected', 'traditional_status', 'traditional_reason')} for c in all_candidates]
    rankings = []
    # Reserve worst-case usage for requests with unknown outcomes (e.g. timeout).
    reserved_input = reserved_output = 0
    for start in range(0, len(candidates), batch_size):
        batch = candidates[start:start + batch_size]
        # Never transmit selection state or URLs to the model.
        clean = [{k: c.get(k) for k in ('candidate_id', 'title', 'source', 'language', 'published_at', 'rss_excerpt')} for c in batch]
        desired_output = max(256, len(batch) * 160)
        max_tokens = min(desired_output, output_cap - reserved_output, total_cap - reserved_input - reserved_output)
        if max_tokens < desired_output:
            report['status'] = 'budget_exhausted'; break
        payload = client.payload(clean, profile, max_tokens)
        # UTF-8 bytes plus framing overhead conservatively bound token count for typical BPE tokenizers.
        estimate = len(json.dumps(payload, ensure_ascii=False).encode('utf8')) + 1024
        for attempt in range(2):
            remaining = deadline - time.monotonic()
            if (report['api_calls'] >= max_calls or remaining <= 0 or
                    reserved_input + estimate > input_cap or
                    reserved_output + max_tokens > output_cap or
                    reserved_input + reserved_output + estimate + max_tokens > total_cap):
                report['status'] = 'budget_exhausted'; break
            report['api_calls'] += 1
            reserved_input += estimate
            reserved_output += max_tokens
            try:
                data, usage = client.analyze_batch(payload, remaining)
                for key_name in ('prompt_tokens', 'completion_tokens', 'total_tokens'):
                    report[key_name] += usage[key_name]
                reserved_input += usage['prompt_tokens'] - estimate
                reserved_output += usage['completion_tokens'] - max_tokens
                # Discard free-form reason: it could echo private profile or adversarial content.
                parsed = validate_batch(data, {c['candidate_id'] for c in batch})
                allowed = {
                    'topic': {'politics', 'economy', 'law', 'culture', 'society', 'climate', 'health', 'science', 'conflict', 'international'},
                    'region': {'africa', 'middle east', 'europe', 'asia', 'americas', 'world', 'international'},
                    'news_type': {'news', 'analysis', 'opinion', 'feature', 'investigation', 'interview', 'report'}}
                by_candidate = {c['candidate_id']: c for c in batch}
                for item in parsed:
                    strengths = sorted(('importance', 'expected_depth', 'novelty', 'timeliness', 'perspective_value'), key=lambda name: (-item[name], name))[:2]
                    item['reason'] = 'Local score basis: ' + ', '.join(f'{name}={item[name]}' for name in strengths)
                    candidate = by_candidate[item['candidate_id']]
                    if len(candidate.get('rss_excerpt') or '') < 80 and len(candidate.get('title') or '') < 30:
                        item['confidence'] = 'low'
                    for field, values in allowed.items():
                        label = item[field].strip().lower()
                        item[field] = label if label in values else 'other'
                    # Cluster labels are untrusted free text; use a one-way grouping id.
                    import hashlib
                    item['event_cluster'] = hashlib.sha256(item['event_cluster'].encode()).hexdigest()[:12] if item['event_cluster'] else ''
                rankings.extend(parsed)
                if (report['prompt_tokens'] > input_cap or report['completion_tokens'] > output_cap or report['total_tokens'] > total_cap):
                    report['status'] = 'budget_exhausted'
                break
            except ProviderError as exc:
                retryable = exc.category == 'timeout' or (exc.category == 'http' and exc.status in (429, 500, 502, 503, 504))
                if not retryable or attempt or time.monotonic() + exc.retry_after >= deadline:
                    report.update(status='degraded', fallback_reason=exc.category, http_status=exc.status, failed_count=report['failed_count'] + len(batch)); break
                if exc.retry_after:
                    time.sleep(exc.retry_after)
            except (ValueError, TypeError, KeyError):
                if attempt:
                    report.update(status='degraded', fallback_reason='invalid_model_output', failed_count=report['failed_count'] + len(batch))
        if report['status'] != 'ok':
            break
    if candidate_truncated and report['status'] == 'ok':
        report['status'] = 'budget_exhausted'
    report['skipped_budget'] = max(0, report['candidate_count'] - len(rankings) - report['failed_count']) if report['status'] == 'budget_exhausted' else max(0, report['candidate_count'] - cap)
    rankings.sort(key=lambda x: (-x['score'], x['candidate_id']))
    # Reserve exploration positions for important stories outside the profile.
    k = len(selected)
    exploration_count = k // 5
    chosen = []
    for row in sorted(rankings, key=lambda x: (-x['importance'], -x['score'], x['candidate_id'])):
        if len(chosen) >= exploration_count:
            break
        if row['importance'] >= 75 and row['personal_fit'] <= 50:
            chosen.append(row)
    # Where alternatives exist, limit one region or topic to half the recommendations.
    cap_per_group = max(1, (k + 1) // 2)
    for strict in (True, False):
        for row in rankings:
            if len(chosen) >= k:
                break
            if row in chosen:
                continue
            if strict and any(sum(c[group] == row[group] for c in chosen) >= cap_per_group
                              for group in ('region', 'topic') if row[group] != 'other'):
                continue
            chosen.append(row)
    recommended = [x['candidate_id'] for x in chosen]
    by_id = {c['candidate_id']: c for c in candidates}
    for item in rankings:
        candidate = by_id[item['candidate_id']]
        item['title'] = candidate['title']
        item['source'] = candidate['source']
    report['analyzed_count'], report['ranking'], report['ai_recommended'] = len(rankings), rankings, recommended
    report['agreement'] = [x for x in recommended if x in selected]
    report['ai_only'] = [x for x in recommended if x not in selected]
    report['traditional_only'] = [x for x in selected if x not in recommended]
    report['topic_distribution'] = dict(Counter(x['topic'] for x in rankings))
    report['region_distribution'] = dict(Counter(x['region'] for x in rankings))
    report['language_distribution'] = dict(Counter(c.get('language') or 'unknown' for c in candidates))
    clusters = {}
    for item in rankings:
        if item['event_cluster']:
            clusters.setdefault(item['event_cluster'], []).append({'candidate_id': item['candidate_id'], 'type': item['cluster_type']})
    report['event_clusters'] = {k: v for k, v in clusters.items() if len(v) > 1}
    try:
        in_price = float(os.environ['AI_INPUT_PRICE_PER_MILLION'])
        out_price = float(os.environ['AI_OUTPUT_PRICE_PER_MILLION'])
        if 0 <= in_price < 1e6 and 0 <= out_price < 1e6:
            report['estimated_cost'] = (report['prompt_tokens'] * in_price + report['completion_tokens'] * out_price) / 1e6
    except (KeyError, ValueError):
        pass
    report['duration'] = round(time.monotonic() - started, 3)
    return report
