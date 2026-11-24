import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const sourcePath = new URL("./index.js", import.meta.url);
const source = (await readFile(sourcePath, "utf8"))
  .replace("export default {", "const worker = {") + "\nexport default worker;";
const worker = (await import(`data:text/javascript,${encodeURIComponent(source)}`)).default;

const env = {
  GITHUB_OWNER: "owner",
  GITHUB_REPO: "repo",
  GITHUB_PAT: "test-pat",
  TRIGGER_SHARED_SECRET: "shared-secret",
  SCHEDULER_MODE: "active",
};

test("rejects manual trigger without the shared secret", async () => {
  let called = false;
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => { called = true; return new Response(null, { status: 204 }); };
  try {
    const response = await worker.fetch(new Request("https://worker.example/"), env, {});
    assert.equal(response.status, 401);
    assert.equal(called, false);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("rejects a wrong shared secret", async () => {
  let called = false;
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => { called = true; return new Response(null, { status: 204 }); };
  try {
    const request = new Request("https://worker.example/", {
      headers: { "X-Worker-Trigger-Secret": "wrong-secret" },
    });
    const response = await worker.fetch(request, env, {});
    assert.equal(response.status, 401);
    assert.equal(called, false);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("does not reveal missing Worker configuration", async () => {
  const request = new Request("https://worker.example/", {
    headers: { "X-Worker-Trigger-Secret": "shared-secret" },
  });
  const response = await worker.fetch(request, {
    TRIGGER_SHARED_SECRET: "shared-secret",
  }, {});
  assert.equal(response.status, 502);
  assert.equal(await response.text(), "Unable to trigger GitHub Actions");
});

test("dispatches only after authenticating the manual trigger", async () => {
  let called = 0;
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url, options) => {
    called += 1;
    assert.match(url, /api\.github\.com\/repos\/owner\/repo\/dispatches$/);
    assert.equal(options.headers.Authorization, "Bearer test-pat");
    return new Response(null, { status: 204 });
  };
  try {
    const request = new Request("https://worker.example/", {
      headers: { "X-Worker-Trigger-Secret": "shared-secret" },
    });
    const response = await worker.fetch(request, env, {});
    assert.equal(response.status, 200);
    assert.equal(called, 1);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("does not expose GitHub error details", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => new Response("secret response body", { status: 500 });
  try {
    const request = new Request("https://worker.example/", {
      headers: { "X-Worker-Trigger-Secret": "shared-secret" },
    });
    const response = await worker.fetch(request, env, {});
    assert.equal(response.status, 502);
    assert.equal(await response.text(), "Unable to trigger GitHub Actions");
  } finally {
    globalThis.fetch = originalFetch;
  }
});

async function fireScheduled(cron, eventTime, environment = env, checkedTime = eventTime) {
  let promise;
  const RealDate = globalThis.Date;
  globalThis.Date = class extends RealDate {
    constructor(...args) { super(...(args.length ? args : [checkedTime])); }
    static now() { return RealDate.parse(checkedTime); }
  };
  try {
    await worker.scheduled({ cron, scheduledTime: RealDate.parse(eventTime) }, environment,
      { waitUntil(p) { promise = p; } });
    if (promise) await promise;
  } finally { globalThis.Date = RealDate; }
}

test('scheduled dispatch uses the existing event type', async () => {
  const original = globalThis.fetch;
  let calls = 0;
  globalThis.fetch = async (_url, options) => {
    calls++;
    assert.equal(JSON.parse(options.body).event_type, 'Daily_Gather_cf_triggerd');
    return new Response(null, { status: 204 });
  };
  try { await fireScheduled('30 22 * * *', '2026-11-23T22:30:00Z'); assert.equal(calls, 1); }
  finally { globalThis.fetch = original; }
});

test('cron defaults to dry run and never dispatches without opt in', async () => {
  const original = globalThis.fetch; let calls = 0;
  globalThis.fetch = async () => { calls++; return new Response(null, { status: 204 }); };
  try {
    await fireScheduled('30 22 * * *', '2026-11-23T22:30:00Z', { ...env, SCHEDULER_MODE: '' });
    assert.equal(calls, 0);
  } finally { globalThis.fetch = original; }
});

for (const [name, fake, expected] of [
  ['GitHub rejects', async () => new Response('private body', { status: 403 }), 'github_http'],
  ['network fails', async () => { throw new Error('private address'); }, 'network'],
  ['request times out', async () => { throw new DOMException('private', 'AbortError'); }, 'timeout'],
]) {
  test(name + ' is sanitized without retries', async () => {
    const origFetch = globalThis.fetch, origLog = console.log;
    const messages = []; let calls = 0;
    console.log = x => messages.push(x);
    globalThis.fetch = async (...args) => { calls++; return fake(...args); };
    try {
      await assert.rejects(fireScheduled('30 22 * * *', '2026-11-23T22:30:00Z'));
      assert.equal(calls, 1);
      assert.equal(JSON.parse(messages.at(-1)).category, expected);
      assert.ok(!messages.join('').includes('private'));
      assert.ok(!messages.join('').includes('test-pat'));
    } finally { globalThis.fetch = origFetch; console.log = origLog; }
  });
}

test('scheduled missing configuration reports only category', async () => {
  const original = console.log; const logs = []; console.log = x => logs.push(x);
  try {
    await assert.rejects(fireScheduled('30 22 * * *', '2026-11-23T22:30:00Z', { SCHEDULER_MODE: 'active' }));
    assert.equal(JSON.parse(logs.at(-1)).category, 'configuration');
  } finally { console.log = original; }
});

function monitorFetch({ runs, steps = [], stepsByRun = {}, alertStatus = 204 }) {
  const alerts = [];
  const jobRequests = [];
  return { alerts, jobRequests, fetch: async (url, opts = {}) => {
    if (String(url).includes('/workflows/daily-gather.yml/runs')) return Response.json({ workflow_runs: runs });
    if (String(url).includes('/actions/runs/')) {
      const id = String(url).match(/\/actions\/runs\/(\d+)\//)[1];
      jobRequests.push(id);
      return Response.json({ jobs: [{ name: 'gather', steps: (stepsByRun[id] || steps).map(name => ({ name, conclusion: 'success' })) }] });
    }
    if (String(url).startsWith('https://alerts.example/')) { alerts.push(JSON.parse(opts.body)); return new Response(null, { status: alertStatus }); }
    throw new Error('unexpected request');
  }};
}

const runToday = { id: 123, head_branch: 'main', event: 'repository_dispatch', created_at: '2026-11-23T22:31:00Z', status: 'completed' };
for (const [name, runs, steps, expected] of [
  ['no run', [], [], 'not_started'],
  ['EPUB missing', [runToday], ['Health: EPUB missing'], 'epub_missing'],
  ['WebDAV failed', [runToday], ['Health: WebDAV failed'], 'webdav_failed'],
  ['WebDAV unknown', [runToday], ['Health: WebDAV unknown'], 'webdav_unknown'],
  ['AI degraded', [runToday], ['Health: WebDAV delivered', 'Health: AI degraded'], 'ai_degraded'],
  ['test-only run', [runToday], ['Health: test only'], 'not_started'],
]) {
  test('monitor alerts for ' + name, async () => {
    const original = globalThis.fetch;
    const mock = monitorFetch({ runs, steps }); globalThis.fetch = mock.fetch;
    try {
      await fireScheduled('0 23 * * *', '2026-11-23T23:00:00Z', { ...env, ALERT_WEBHOOK_URL: 'https://alerts.example/hook' });
      assert.equal(mock.alerts.length, 1);
      assert.equal(mock.alerts[0].status, expected);
      assert.equal(mock.alerts[0].day, '2026-11-24');
      assert.ok(!JSON.stringify(mock.alerts).includes('test-pat'));
    } finally { globalThis.fetch = original; }
  });
}

test('verified delivery does not alert and does not dispatch', async () => {
  const original = globalThis.fetch;
  const mock = monitorFetch({ runs: [runToday], steps: ['Health: WebDAV delivered'] }); globalThis.fetch = mock.fetch;
  try { await fireScheduled('0 23 * * *', '2026-11-23T23:00:00Z', { ...env, ALERT_WEBHOOK_URL: 'https://alerts.example/hook' }); assert.equal(mock.alerts.length, 0); }
  finally { globalThis.fetch = original; }
});

test('monitor API failure alerts safely instead of claiming no run', async () => {
  const original = globalThis.fetch; const alerts = [];
  globalThis.fetch = async (url, opts = {}) => {
    if (String(url).includes('api.github.com')) return new Response('sensitive error', { status: 403 });
    alerts.push(JSON.parse(opts.body)); return new Response(null, { status: 204 });
  };
  try {
    await fireScheduled('0 23 * * *', '2026-11-23T23:00:00Z', { ...env, ALERT_WEBHOOK_URL: 'https://alerts.example/hook' });
    assert.equal(alerts[0].status, 'monitor_error');
    assert.equal(alerts[0].category, 'github_http');
    assert.ok(!JSON.stringify(alerts).includes('sensitive'));
  } finally { globalThis.fetch = original; }
});

test('alert delivery failure is visible without exposing the URL', async () => {
  const origFetch = globalThis.fetch, origLog = console.log, logs = [];
  console.log = x => logs.push(x);
  globalThis.fetch = monitorFetch({ runs: [], alertStatus: 503 }).fetch;
  try {
    await fireScheduled('0 23 * * *', '2026-11-23T23:00:00Z', { ...env, ALERT_WEBHOOK_URL: 'https://alerts.example/hook' });
    assert.ok(logs.some(x => JSON.parse(x).category === 'alert_http'));
    assert.ok(!logs.join('').includes('alerts.example'));
  } finally { globalThis.fetch = origFetch; console.log = origLog; }
});

test('monitor excludes yesterday and recognizes a still running publication', async () => {
  const original = globalThis.fetch;
  const runs = [{ ...runToday, status: 'in_progress' }, { ...runToday, id: 122, created_at: '2026-11-22T22:31:00Z' }];
  const mock = monitorFetch({ runs, steps: [] }); globalThis.fetch = mock.fetch;
  try {
    await fireScheduled('0 23 * * *', '2026-11-23T23:00:00Z', { ...env, ALERT_WEBHOOK_URL: 'https://alerts.example/hook' });
    assert.equal(mock.alerts[0].status, 'running');
  } finally { globalThis.fetch = original; }
});

test('non-main and missing branch runs never count as production delivery', async () => {
  const original = globalThis.fetch;
  const runs = [
    { ...runToday, id: 124, head_branch: 'feature', event: 'workflow_dispatch' },
    { ...runToday, id: 125, head_branch: undefined },
  ];
  const mock = monitorFetch({ runs, steps: ['Health: WebDAV delivered'] }); globalThis.fetch = mock.fetch;
  try {
    await fireScheduled('0 23 * * *', '2026-11-23T23:00:00Z', { ...env, ALERT_WEBHOOK_URL: 'https://alerts.example/hook' });
    assert.equal(mock.alerts[0].status, 'not_started');
    assert.deepEqual(mock.jobRequests, []);
  } finally { globalThis.fetch = original; }
});

test('a successful feature run cannot hide a failed main run', async () => {
  const original = globalThis.fetch;
  const runs = [runToday, { ...runToday, id: 124, head_branch: 'feature', event: 'workflow_dispatch', created_at: '2026-11-23T22:40:00Z' }];
  const mock = monitorFetch({ runs, stepsByRun: { 123: ['Health: WebDAV failed'], 124: ['Health: WebDAV delivered'] } });
  globalThis.fetch = mock.fetch;
  try {
    await fireScheduled('0 23 * * *', '2026-11-23T23:00:00Z', { ...env, ALERT_WEBHOOK_URL: 'https://alerts.example/hook' });
    assert.equal(mock.alerts[0].status, 'webdav_failed');
    assert.deepEqual(mock.jobRequests, ['123']);
  } finally { globalThis.fetch = original; }
});

test('main delivery is recognized among unrelated branch and test runs', async () => {
  const original = globalThis.fetch;
  const runs = [runToday, { ...runToday, id: 124, head_branch: 'feature' },
    { ...runToday, id: 125, event: 'workflow_dispatch', created_at: '2026-11-23T22:50:00Z' }];
  const mock = monitorFetch({ runs, stepsByRun: { 123: ['Health: WebDAV delivered'], 125: ['Health: test only'] } });
  globalThis.fetch = mock.fetch;
  try {
    await fireScheduled('0 23 * * *', '2026-11-23T23:00:00Z', { ...env, ALERT_WEBHOOK_URL: 'https://alerts.example/hook' });
    assert.equal(mock.alerts.length, 0);
    assert.deepEqual(mock.jobRequests, ['125', '123']);
  } finally { globalThis.fetch = original; }
});

for (const [status, steps, expected] of [
  ['completed', ['Health: WebDAV delivered'], 'delivered'],
  ['in_progress', [], 'running'],
]) {
  test(`delayed monitor observes a publication ${status} after scheduled time`, async () => {
    const original = globalThis.fetch, origLog = console.log, logs = [];
    const runs = [{ ...runToday, status, created_at: '2026-11-23T23:05:00Z' },
      { ...runToday, id: 124, created_at: '2026-11-23T23:11:00Z' }];
    const mock = monitorFetch({ runs, steps }); globalThis.fetch = mock.fetch;
    console.log = entry => logs.push(JSON.parse(entry));
    try {
      await fireScheduled('0 23 * * *', '2026-11-23T23:00:00Z',
        { ...env, ALERT_WEBHOOK_URL: 'https://alerts.example/hook' }, '2026-11-23T23:10:00Z');
      const record = logs.find(entry => entry.event === 'monitor');
      assert.equal(record.status, expected);
      assert.equal(record.scheduled_time, '2026-11-23T23:00:00.000Z');
      assert.equal(record.checked_time, '2026-11-23T23:10:00.000Z');
      assert.deepEqual(mock.jobRequests, ['123']);
    } finally { globalThis.fetch = original; console.log = origLog; }
  });
}

test('delay across midnight still checks the scheduled publication day', async () => {
  const original = globalThis.fetch;
  const runs = [{ ...runToday, created_at: '2026-11-23T23:05:00Z' },
    { ...runToday, id: 124, created_at: '2026-11-24T16:01:00Z' }];
  const mock = monitorFetch({ runs, steps: ['Health: WebDAV delivered'] }); globalThis.fetch = mock.fetch;
  try {
    await fireScheduled('0 23 * * *', '2026-11-23T23:00:00Z',
      { ...env, ALERT_WEBHOOK_URL: 'https://alerts.example/hook' }, '2026-11-24T16:05:00Z');
    assert.equal(mock.alerts.length, 0);
    assert.deepEqual(mock.jobRequests, ['123']);
  } finally { globalThis.fetch = original; }
});
