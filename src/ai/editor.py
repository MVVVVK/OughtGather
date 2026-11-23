"""Topic-first shadow editor, isolated from publication and delivery."""
import json
import os
import re
import time
from datetime import datetime, timezone
from .client import DeepSeekClient, ProviderError
from .identifiers import shorten
from .memory.storage import load_profile
from .schemas import validate_groups, validate_selection


def setting(name, default, ceiling):
    try:
        return min(ceiling, max(0, int(os.getenv(name) or default)))
    except (ValueError, TypeError):
        return default


class StageFailure(Exception):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


class Budget:
    """Reserve conservatively before every call, refund only known usage."""
    def __init__(self, report, started):
        self.report = report
        self.max_calls = setting('AI_MAX_CALLS_PER_RUN', 6, 100)
        self.max_input = setting('AI_MAX_INPUT_TOKENS', 80000, 1000000)
        self.max_output = setting('AI_MAX_OUTPUT_TOKENS', 8000, 100000)
        self.max_total = setting('AI_MAX_TOTAL_TOKENS', 88000, 1100000)
        self.deadline = started + setting('AI_TOTAL_TIMEOUT_SECONDS', 60, 300)
        self.reserved_input = self.reserved_output = 0

    @staticmethod
    def estimate(payload):
        return len(json.dumps(payload, ensure_ascii=False).encode('utf8')) + 1024

    def fits(self, payload, output):
        estimate = self.estimate(payload)
        return (self.reserved_input + estimate <= self.max_input and
                self.reserved_output + output <= self.max_output and
                self.reserved_input + self.reserved_output + estimate + output <= self.max_total)

    def account(self, usage, estimate, output):
        for field in ('prompt_tokens', 'completion_tokens', 'total_tokens'):
            self.report[field] += usage[field]
        self.reserved_input += usage['prompt_tokens'] - estimate
        self.reserved_output += usage['completion_tokens'] - output
        if (self.report['prompt_tokens'] > self.max_input or self.report['completion_tokens'] > self.max_output
                or self.report['total_tokens'] > self.max_total):
            raise StageFailure('budget_exhausted')

    def request(self, client, payload, output, validate):
        estimate = self.estimate(payload)
        for attempt in range(2):
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise StageFailure('timeout')
            if self.report['api_calls'] >= self.max_calls or not self.fits(payload, output):
                raise StageFailure('budget_exhausted')
            self.report['api_calls'] += 1
            self.reserved_input += estimate
            self.reserved_output += output
            try:
                data, usage = client.analyze_batch(payload, remaining)
                self.account(usage, estimate, output)
                try:
                    return validate(data)
                except (ValueError, TypeError, KeyError):
                    if attempt:
                        raise StageFailure('invalid_model_output') from None
            except ProviderError as exc:
                if exc.usage is not None:
                    self.account(exc.usage, estimate, output)
                retryable = (exc.category in ('timeout','response_incomplete_max_tokens','missing_output_text',
                                               'invalid_structured_output','invalid_response') or
                             (exc.category == 'http' and exc.status in (429,500,502,503,504)))
                if not retryable or attempt or time.monotonic() + exc.retry_after >= self.deadline:
                    raise StageFailure(exc.category) from None
                if exc.retry_after:
                    time.sleep(exc.retry_after)
        raise StageFailure('invalid_model_output')


def fit_payload(build, budget, output):
    """Uniformly shorten excerpts if necessary, never drop a candidate."""
    lo, hi, best = 0, 600, None
    while lo <= hi:
        middle = (lo + hi) // 2
        payload = build(middle)
        if budget.fits(payload, output):
            best, lo = payload, middle + 1
        else:
            hi = middle - 1
    if best is None:
        raise StageFailure('budget_exhausted')
    return best


def provider_safe(value):
    """Remove URLs and SHA-256-like IDs from untrusted text and profile input."""
    if isinstance(value, str):
        value = re.sub(r'(?:https?://|www\.)[^\s<>\"\']+', '[link]', value, flags=re.IGNORECASE)
        return re.sub(r'[0-9a-fA-F]{64}', '[id]', value)
    if isinstance(value, list):
        return [provider_safe(item) for item in value]
    if isinstance(value, dict):
        return {provider_safe(key) if isinstance(key, str) else key: provider_safe(item) for key, item in value.items()}
    return value


def run(candidates, client=None):
    started = time.monotonic()
    mode = os.getenv('AI_EDITOR_MODE', 'off')
    report = {'mode': mode, 'model': os.getenv('DEEPSEEK_MODEL') or 'deepseek-flash',
              'status': 'off', 'scope': 'rss_only', 'started_at': datetime.now(timezone.utc).isoformat(),
              'candidate_count': len(candidates), 'candidate_cap': setting('AI_MAX_CANDIDATES', 200, 1000),
              'api_calls': 0, 'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0,
              'estimated_cost': None, 'pricing_version': os.getenv('AI_PRICING_VERSION', 'unconfigured'),
              'memory_revision': None, 'memory_status': 'not_loaded', 'fallback_reason': None,
              'topic_count': 0, 'multi_article_topic_count': 0, 'topic_grouping_status': 'not_started',
              'topic_selection_status': 'not_started', 'selected_topic_count': 0, 'selected_article_count': 0,
              'max_articles_per_topic': 0, 'duplicate_candidates_grouped': 0, 'ai_recommended': [],
              'topic_groups': [], 'traditional_selected': [], 'agreement': [], 'ai_only': [], 'traditional_only': []}
    if mode != 'shadow':
        return report
    key = os.getenv('DEEPSEEK_API_KEY', '')
    if not key and client is None:
        report.update(status='unavailable', fallback_reason='missing_key', duration=round(time.monotonic()-started, 3))
        return report
    client = client or DeepSeekClient(key, report['model'])
    profile, revision, memory_status = load_profile()
    report.update(status='ok', memory_revision=revision, memory_status=memory_status)
    cap = report['candidate_cap']
    report['skipped_candidate_cap'] = max(0, len(candidates) - cap)
    report['coverage_status'] = 'candidate_cap_reached' if len(candidates) > cap else 'complete'
    report['traditional_selected'] = [c['candidate_id'] for c in candidates if c.get('traditional_selected')]
    candidates = candidates[:cap]
    if not candidates:
        report.update(topic_grouping_status='not_needed', topic_selection_status='not_needed', duration=round(time.monotonic()-started, 3))
        return report
    budget = Budget(report, started)
    stage = 'topic_grouping'
    try:
        clean = [{'candidate_id': c['candidate_id'], **{k: provider_safe(c.get(k)) for k in ('title','source','language','published_at','rss_excerpt')}} for c in candidates]
        short_candidates, short_to_real = shorten(clean, 'c', width=3)
        by_short = {row['candidate_id']: row for row in short_candidates}
        real_to_short = {real: short for short, real in short_to_real.items()}
        group_output = max(256, len(short_candidates) * 20)
        def grouping_input(limit):
            return [{**row, 'rss_excerpt': (row.get('rss_excerpt') or '')[:limit]} for row in short_candidates]
        payload = fit_payload(lambda n: client.grouping_payload(grouping_input(n), group_output), budget, group_output)
        groups = budget.request(client, payload, group_output, lambda data: validate_groups(data, short_to_real))
        report['topic_grouping_status'] = 'ok'
        report['topic_count'] = len(groups)
        report['multi_article_topic_count'] = sum(len(g['members']) > 1 for g in groups)
        report['duplicate_candidates_grouped'] = sum(len(g['members']) - 1 for g in groups)
        group_short_to_real = {f'g{i:03d}': group['group_id'] for i, group in enumerate(groups, 1)}
        real_to_group_short = {v: k for k, v in group_short_to_real.items()}
        provider_members = {real_to_group_short[g['group_id']]: {real_to_short[c] for c in g['members']} for g in groups}
        safe_groups = []
        for group in groups:
            members = []
            for real in group['members']:
                item = by_short[real_to_short[real]]
                members.append({'candidate_id': real, 'title': item['title'], 'source': item['source']})
            safe_groups.append({'group_id': group['group_id'], 'topic_title': members[0]['title'], 'members': members,
                                'representative_id': None,
                                'shared_slot_reason': 'Grouped as the same news story; at most one representative.' if len(members) > 1 else ''})
        report['topic_groups'] = safe_groups
        stage = 'topic_selection'
        select_output = 800
        def selection_input(limit):
            return [{'group_id': real_to_group_short[g['group_id']],
                     'candidates': [{**by_short[real_to_short[c]], 'rss_excerpt': (by_short[real_to_short[c]].get('rss_excerpt') or '')[:limit]}
                                    for c in g['members']]} for g in groups]
        payload = fit_payload(lambda n: client.selection_payload(selection_input(n), provider_safe(profile), select_output), budget, select_output)
        selected = budget.request(client, payload, select_output, lambda data: validate_selection(data, provider_members))
        selected_real = [(group_short_to_real[group], short_to_real[cid]) for group, cid in selected]
        report['topic_selection_status'] = 'ok'
        report['ai_recommended'] = [cid for _, cid in selected_real]
        report['selected_topic_count'] = report['selected_article_count'] = len(selected_real)
        report['max_articles_per_topic'] = 1 if selected_real else 0
        representative = dict(selected_real)
        for group in safe_groups:
            group['representative_id'] = representative.get(group['group_id'])
            if group['representative_id']:
                group['topic_title'] = next(m['title'] for m in group['members'] if m['candidate_id'] == group['representative_id'])
        traditional = report['traditional_selected']
        report['agreement'] = [cid for cid in report['ai_recommended'] if cid in traditional]
        report['ai_only'] = [cid for cid in report['ai_recommended'] if cid not in traditional]
        report['traditional_only'] = [cid for cid in traditional if cid not in report['ai_recommended']]
    except StageFailure as exc:
        report.update(status='degraded', fallback_reason=exc.reason)
        report[stage + '_status'] = 'failed'
    except Exception:
        report.update(status='degraded', fallback_reason='internal_error')
        report[stage + '_status'] = 'failed'
    try:
        in_price = float(os.environ['AI_INPUT_PRICE_PER_MILLION'])
        out_price = float(os.environ['AI_OUTPUT_PRICE_PER_MILLION'])
        if 0 <= in_price < 1e6 and 0 <= out_price < 1e6:
            report['estimated_cost'] = (report['prompt_tokens'] * in_price + report['completion_tokens'] * out_price) / 1e6
    except (KeyError, ValueError):
        pass
    report['duration'] = round(time.monotonic() - started, 3)
    return report
