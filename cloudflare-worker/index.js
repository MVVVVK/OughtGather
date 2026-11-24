const DISPATCH_CRON = '30 22 * * *';
const MONITOR_CRON = '0 23 * * *';
const TIMEOUT_MS = 10000;

class SafeError extends Error {
  constructor(category, status = null) { super(category); this.category = category; this.status = status; }
}

function log(event, fields = {}) {
  console.log(JSON.stringify({ event, time: new Date().toISOString(), ...fields }));
}

function config(env) {
  const { GITHUB_OWNER: owner, GITHUB_REPO: repo, GITHUB_PAT: token } = env;
  if (!owner || !repo || !token || !/^[\w.-]+$/.test(owner) || !/^[\w.-]+$/.test(repo)) {
    throw new SafeError('configuration');
  }
  return { owner, repo, token };
}

async function github(env, path, options = {}, wantsJson = false) {
  const { owner, repo, token } = config(env);
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    const response = await fetch(`https://api.github.com/repos/${owner}/${repo}/${path}`, {
      ...options, signal: controller.signal,
      headers: { Authorization: `Bearer ${token}`, Accept: 'application/vnd.github+json',
        'User-Agent': 'OughtGather-Scheduler', 'X-GitHub-Api-Version': '2022-11-28',
        ...(options.headers || {}) },
    });
    if (!response.ok) throw new SafeError('github_http', response.status);
    if (wantsJson) {
      try { return await response.json(); }
      catch (error) { if (error?.name === 'AbortError') throw error; throw new SafeError('invalid_response'); }
    }
    return response;
  } catch (error) {
    if (error instanceof SafeError) throw error;
    throw new SafeError(error?.name === 'AbortError' ? 'timeout' : 'network');
  } finally { clearTimeout(timer); }
}

async function dispatch(env) {
  await github(env, 'dispatches', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ event_type: 'Daily_Gather_cf_triggerd' }) });
  log('dispatch', { result: 'accepted' });
}

function shanghaiDay(now) { return new Date(now.getTime() + 8 * 3600000).toISOString().slice(0, 10); }

async function inspectPublication(env, now = new Date()) {
  const day = shanghaiDay(now);
  const start = Date.parse(`${day}T00:00:00+08:00`);
  const payload = await github(env, 'actions/workflows/daily-gather.yml/runs?per_page=100', {}, true);
  if (!payload || !Array.isArray(payload.workflow_runs)) throw new SafeError('invalid_response');
  const runs = payload.workflow_runs.filter(r => ['schedule', 'repository_dispatch', 'workflow_dispatch'].includes(r.event)
    && Date.parse(r.created_at) >= start && Date.parse(r.created_at) <= now.getTime())
    .sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at));
  if (!runs.length) return { day, status: 'not_started' };
  let foundReal = false, pending = false, failure = 'status_unavailable';
  for (const run of runs) {
    const data = await github(env, `actions/runs/${run.id}/jobs?per_page=100`, {}, true);
    if (!data || !Array.isArray(data.jobs)) throw new SafeError('invalid_response');
    const job = data.jobs.find(j => j.name === 'gather');
    if (!job) { if (run.status !== 'completed') pending = true; continue; }
    const names = new Set((job.steps || []).filter(s => s.conclusion === 'success').map(s => s.name));
    if (names.has('Health: test only')) continue;
    foundReal = true;
    if (names.has('Health: WebDAV delivered')) {
      if (run.status !== 'completed') { pending = true; continue; }
      return { day, status: names.has('Health: AI degraded') ? 'ai_degraded' : 'delivered' };
    }
    if (run.status !== 'completed') { pending = true; continue; }
    if (names.has('Health: EPUB missing')) failure = 'epub_missing';
    else if (names.has('Health: WebDAV failed')) failure = 'webdav_failed';
    else if (names.has('Health: WebDAV unknown')) failure = 'webdav_unknown';
  }
  if (pending) return { day, status: 'running' };
  if (!foundReal) return { day, status: 'not_started' };
  return { day, status: failure };
}

async function alert(env, result) {
  const value = env.ALERT_WEBHOOK_URL;
  if (!value) throw new SafeError('alert_not_configured');
  let url;
  try { url = new URL(value); } catch { throw new SafeError('alert_configuration'); }
  if (url.protocol !== 'https:' || url.username || url.password) throw new SafeError('alert_configuration');
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    const response = await fetch(url.toString(), { method: 'POST', signal: controller.signal, redirect: 'manual',
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ event: 'oughtgather_health', ...result }) });
    if (!response.ok) throw new SafeError('alert_http', response.status);
  } catch (error) {
    if (error instanceof SafeError) throw error;
    throw new SafeError(error?.name === 'AbortError' ? 'alert_timeout' : 'alert_network');
  } finally { clearTimeout(timer); }
}

async function monitor(env, now) {
  let result;
  try { result = await inspectPublication(env, now); }
  catch (e) { result = { day: shanghaiDay(now), status: 'monitor_error', category: e instanceof SafeError ? e.category : 'unknown' }; }
  log('monitor', result);
  if (result.status !== 'delivered') {
    try { await alert(env, result); log('alert', { result: 'sent' }); }
    catch (e) { log('alert', { result: 'failed', category: e instanceof SafeError ? e.category : 'unknown' }); }
  }
  return result;
}

export default {
  async scheduled(event, env, ctx) {
    if (event.cron === MONITOR_CRON) {
      ctx.waitUntil(monitor(env, new Date(event.scheduledTime)));
    } else if (event.cron === DISPATCH_CRON) {
      if (!env.SCHEDULER_MODE || env.SCHEDULER_MODE === 'dry_run') {
        log('dispatch', { result: 'dry_run' });
      } else if (env.SCHEDULER_MODE === 'active') {
        ctx.waitUntil(dispatch(env).catch(e => { log('dispatch', { result: 'failed', category: e instanceof SafeError ? e.category : 'unknown', status: e instanceof SafeError ? e.status : null }); throw e; }));
      } else { log('dispatch', { result: 'failed', category: 'configuration' }); }
    } else { log('schedule', { result: 'ignored', category: 'unknown_cron' }); }
  },
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method !== 'GET' || url.pathname !== '/') return new Response('Not Found', { status: 404 });
    if (!env.TRIGGER_SHARED_SECRET || request.headers.get('X-Worker-Trigger-Secret') !== env.TRIGGER_SHARED_SECRET) return new Response('Unauthorized', { status: 401 });
    try { await dispatch(env); return new Response('GitHub Actions trigger sent successfully!', { status: 200 }); }
    catch (e) { log('dispatch', { result: 'failed', category: e instanceof SafeError ? e.category : 'unknown', status: e instanceof SafeError ? e.status : null }); return new Response('Unable to trigger GitHub Actions', { status: 502 }); }
  },
};
