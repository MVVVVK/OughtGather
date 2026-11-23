"""Offline-comparable shadow editor; no writes to the publication pipeline."""
import hashlib
import json
import os
import re
import time
import unicodedata
from collections import Counter
from datetime import date, datetime, timezone
from .client import DeepSeekClient, ProviderError
from .schemas import validate_batch, validate_composition
from .composition import compose
from .memory.storage import load_profile


class CompositionBudgetExceeded(Exception):
    pass


def setting(name, default, ceiling):
    try:
        return min(ceiling, max(0, int(os.getenv(name) or default)))
    except (ValueError, TypeError):
        return int(default)


def cluster_id(signature, batch_start):
    """Only structured, dated semantic signatures may group across batches.

    Other valid labels are scoped to their batch; invalid or ambiguous text is
    discarded before hashing. Model text itself never reaches the report.
    """
    normalized = unicodedata.normalize('NFKC', signature).casefold().strip()
    normalized = re.sub(r'\s+', ' ', normalized)
    if not 2 <= len(normalized) <= 100 or not re.fullmatch(r'[\w |\-]+', normalized):
        return ''
    parts = [part.strip() for part in normalized.split('|')]
    placeholders = {'actor', 'action', 'place', 'date', 'unknown', 'none', 'other', 'unspecified'}
    semantic = (len(parts) == 4 and all(2 <= len(part) <= 40 and part not in placeholders for part in parts)
                and all(re.fullmatch(r'[\w -]+', part) for part in parts[:3]))
    if semantic:
        try:
            semantic = bool(re.fullmatch(r'\d{4}-\d{2}-\d{2}', parts[3]) and date.fromisoformat(parts[3]))
        except ValueError:
            semantic = False
    if semantic:
        key = 'semantic:' + '|'.join(parts)
    else:
        key = f'batch:{batch_start}:' + normalized
    return hashlib.sha256(key.encode('utf-8')).hexdigest()[:24]


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
              'ranking': [], 'topic_distribution': {}, 'region_distribution': {}, 'language_distribution': {}, 'event_clusters': {},
              'event_grouping_source': 'fallback_hints'}
    if mode != 'shadow':
        return report
    key = os.getenv('DEEPSEEK_API_KEY', '')
    if not key and client is None:
        report.update(status='unavailable', fallback_reason='missing_key', duration=round(time.monotonic() - started, 3))
        return report
    client = client or DeepSeekClient(key, model)
    profile, revision, status = load_profile()
    report.update(memory_revision=revision, memory_status=status, status='ok')
    personalized = any(profile.get(k) for k in ('hard_rules', 'soft_rules', 'prefer_signals', 'deprioritize_signals'))
    cap = setting('AI_MAX_CANDIDATES', 200, 1000)
    batch_size = max(1, setting('AI_MAX_BATCH_CANDIDATES', 40, 100))
    max_calls = setting('AI_MAX_CALLS_PER_RUN', 6, 100)
    input_cap = setting('AI_MAX_INPUT_TOKENS', 80000, 1000000)
    output_cap = setting('AI_MAX_OUTPUT_TOKENS', 8000, 100000)
    total_cap = setting('AI_MAX_TOTAL_TOKENS', 88000, 1100000)
    deadline = started + setting('AI_TOTAL_TIMEOUT_SECONDS', 60, 300)
    selected = [c['candidate_id'] for c in candidates if c.get('traditional_selected')]
    candidate_truncated = len(candidates) > cap
    report.update(candidate_cap=cap, skipped_candidate_cap=max(0, len(candidates)-cap),
                  coverage_status='candidate_cap_reached' if candidate_truncated else 'complete',
                  composition_status='not_needed', composition_candidate_count=0)
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
        desired_output = max(256, len(batch) * 180)
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
                parsed = validate_batch(data, {c['candidate_id'] for c in batch}, personalized=personalized)
                allowed_news_types = {'news', 'analysis', 'opinion', 'feature', 'investigation', 'interview', 'report'}
                by_candidate = {c['candidate_id']: c for c in batch}
                for item in parsed:
                    strengths = sorted(('importance', 'global_impact', 'expected_depth', 'information_gain', 'timeliness', 'perspective_value'), key=lambda name: (-item[name], name))[:2]
                    item['reason'] = 'Local score basis: ' + ', '.join(f'{name}={item[name]}' for name in strengths)
                    candidate = by_candidate[item['candidate_id']]
                    if len(candidate.get('rss_excerpt') or '') < 80 and len(candidate.get('title') or '') < 30:
                        item['confidence'] = 'low'
                    item['news_type'] = item['news_type'].strip().lower() if item['news_type'].strip().lower() in allowed_news_types else 'other'
                    item['event_cluster'] = cluster_id(item['event_cluster'], start)
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
    report['skipped_budget'] = max(0, min(report['candidate_count'], cap) - len(rankings) - report['failed_count']) if report['status'] == 'budget_exhausted' else 0
    rankings.sort(key=lambda x: (-x['score'], x['candidate_id']))
    by_id = {c['candidate_id']: c for c in candidates}
    for row in rankings:
        candidate = by_id[row['candidate_id']]
        row['title'], row['source'] = candidate['title'], candidate['source']

    mappings = {}
    compose_cap = setting('AI_COMPOSE_MAX_CANDIDATES', 60, 100)
    to_compose = rankings[:compose_cap]
    report['composition_candidate_count'] = len(to_compose)
    if len(to_compose) >= 2:
        clean_composition = [{k: row.get(k) for k in ('candidate_id','title','source','topic','region','news_type',
                                                      'importance','global_impact','score','event_cluster')} for row in to_compose]
        desired = max(256, len(to_compose) * 60)
        remaining = deadline - time.monotonic()
        if report['status'] == 'degraded':
            report['composition_status'] = 'fallback_upstream_degraded'
        elif remaining <= 0:
            report['composition_status'] = 'fallback_timeout'
        elif report['status'] != 'ok' or report['api_calls'] >= max_calls or desired > output_cap - reserved_output:
            report['composition_status'] = 'fallback_budget'
        else:
            try:
                payload = client.composition_payload(clean_composition, desired)
                estimate = len(json.dumps(payload, ensure_ascii=False).encode('utf8')) + 1024
                if (reserved_input + estimate > input_cap or reserved_input + reserved_output + estimate + desired > total_cap):
                    report['composition_status'] = 'fallback_budget'
                elif (remaining := deadline - time.monotonic()) <= 0:
                    report['composition_status'] = 'fallback_timeout'
                else:
                    report['api_calls'] += 1
                    reserved_input += estimate
                    reserved_output += desired
                    data, usage = client.analyze_batch(payload, remaining)
                    for name in ('prompt_tokens','completion_tokens','total_tokens'):
                        report[name] += usage[name]
                    reserved_input += usage['prompt_tokens'] - estimate
                    reserved_output += usage['completion_tokens'] - desired
                    if report['prompt_tokens'] > input_cap or report['completion_tokens'] > output_cap or report['total_tokens'] > total_cap:
                        raise CompositionBudgetExceeded()
                    mappings = validate_composition(data, {r['candidate_id'] for r in to_compose})
                    # Missing candidates are always singletons, not model-deleted.
                    mappings = {cid: ('model:' + event, relation) for cid, (event, relation) in mappings.items()}
                    for row in to_compose:
                        mappings.setdefault(row['candidate_id'], ('singleton:' + row['candidate_id'], 'primary'))
                    report['composition_status'] = 'ok'
                    report['event_grouping_source'] = 'composition'
            except CompositionBudgetExceeded:
                report['composition_status'] = 'fallback_budget'
            except ProviderError as exc:
                report['composition_status'] = ('fallback_timeout' if exc.category == 'timeout' else
                                                'fallback_invalid_output' if exc.category in ('invalid_response', 'invalid_usage') else 'fallback_provider')
            except (ValueError, TypeError, KeyError, AttributeError):
                report['composition_status'] = 'fallback_invalid_output'
            except Exception:
                report['composition_status'] = 'fallback_unavailable'
    recommended, diagnostics = compose(rankings, mappings, len(selected), max(1, setting('AI_MAX_ARTICLES_PER_EVENT', 2, 2)))
    report.update(diagnostics)
    report['analyzed_count'], report['ranking'], report['ai_recommended'] = len(rankings), rankings, recommended
    report['agreement'] = [x for x in recommended if x in selected]
    report['ai_only'] = [x for x in recommended if x not in selected]
    report['traditional_only'] = [x for x in selected if x not in recommended]
    report['topic_distribution'] = dict(Counter(x['topic'] for x in rankings))
    report['region_distribution'] = dict(Counter(x['region'] for x in rankings))
    report['language_distribution'] = dict(Counter(c.get('language') or 'unknown' for c in candidates))
    try:
        in_price = float(os.environ['AI_INPUT_PRICE_PER_MILLION'])
        out_price = float(os.environ['AI_OUTPUT_PRICE_PER_MILLION'])
        if 0 <= in_price < 1e6 and 0 <= out_price < 1e6:
            report['estimated_cost'] = (report['prompt_tokens'] * in_price + report['completion_tokens'] * out_price) / 1e6
    except (KeyError, ValueError):
        pass
    report['duration'] = round(time.monotonic() - started, 3)
    return report
