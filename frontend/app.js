/* FlowPilot - local automation control center */

const ICON = {
  overview: '<svg viewBox="0 0 20 20" width="17" height="17" fill="none" stroke="currentColor" stroke-width="1.7"><rect x="2.5" y="2.5" width="6" height="6" rx="1.5"/><rect x="11.5" y="2.5" width="6" height="6" rx="1.5"/><rect x="2.5" y="11.5" width="6" height="6" rx="1.5"/><rect x="11.5" y="11.5" width="6" height="6" rx="1.5"/></svg>',
  studio: '<svg viewBox="0 0 20 20" width="17" height="17" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M4 5h12M4 10h8M4 15h5" stroke-linecap="round"/><circle cx="15.5" cy="13.5" r="3"/></svg>',
  queue: '<svg viewBox="0 0 20 20" width="17" height="17" fill="none" stroke="currentColor" stroke-width="1.7"><rect x="2.5" y="4" width="15" height="12" rx="2"/><path d="m3 6 7 5 7-5" stroke-linecap="round"/></svg>',
  live: '<svg viewBox="0 0 20 20" width="17" height="17" fill="none" stroke="currentColor" stroke-width="1.7"><rect x="2.5" y="3.5" width="15" height="13" rx="2"/><path d="M2.5 7h15" /><circle cx="10" cy="12" r="2.2"/></svg>',
  history: '<svg viewBox="0 0 20 20" width="17" height="17" fill="none" stroke="currentColor" stroke-width="1.7"><circle cx="10" cy="10" r="7.5"/><path d="M10 6v4.3l2.8 1.7" stroke-linecap="round"/></svg>',
  review: '<svg viewBox="0 0 20 20" width="17" height="17" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M10 2.8 17.5 16H2.5L10 2.8Z" stroke-linejoin="round"/><path d="M10 8v3.2M10 13.6v.2" stroke-linecap="round"/></svg>',
  analytics: '<svg viewBox="0 0 20 20" width="17" height="17" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M3 16.5V11M7.7 16.5V5M12.3 16.5V8.5M17 16.5v-9" stroke-linecap="round"/></svg>',
  integrations: '<svg viewBox="0 0 20 20" width="17" height="17" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M8 3.5 4 7.5l4 4M12 16.5l4-4-4-4" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  settings: '<svg viewBox="0 0 20 20" width="17" height="17" fill="none" stroke="currentColor" stroke-width="1.7"><circle cx="10" cy="10" r="2.6"/><path d="M10 2.5v2M10 15.5v2M17.5 10h-2M4.5 10h-2M15.3 4.7l-1.4 1.4M6.1 13.9l-1.4 1.4M15.3 15.3l-1.4-1.4M6.1 6.1 4.7 4.7" stroke-linecap="round"/></svg>',
  check: '<svg viewBox="0 0 20 20" width="19" height="19" fill="none" stroke="currentColor" stroke-width="2"><path d="m4.5 10.5 3.5 3.5 7.5-8" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  clock: '<svg viewBox="0 0 20 20" width="19" height="19" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="10" cy="10" r="7.5"/><path d="M10 6v4.3l2.8 1.7" stroke-linecap="round"/></svg>',
  db: '<svg viewBox="0 0 20 20" width="19" height="19" fill="none" stroke="currentColor" stroke-width="1.8"><ellipse cx="10" cy="5" rx="6.5" ry="2.5"/><path d="M3.5 5v10c0 1.4 2.9 2.5 6.5 2.5s6.5-1.1 6.5-2.5V5M3.5 10c0 1.4 2.9 2.5 6.5 2.5s6.5-1.1 6.5-2.5"/></svg>',
  alert: '<svg viewBox="0 0 20 20" width="19" height="19" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M10 2.8 17.5 16H2.5L10 2.8Z" stroke-linejoin="round"/><path d="M10 8v3.2M10 13.6v.2" stroke-linecap="round"/></svg>',
  info: '<svg viewBox="0 0 20 20" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="10" cy="10" r="7.5"/><path d="M10 9v4.5M10 6.6v.2" stroke-linecap="round"/></svg>',
};

const PAGES = [
  { id: 'overview', label: 'Overview', icon: 'overview' },
  { id: 'studio', label: 'Automation Studio', icon: 'studio' },
  { id: 'queue', label: 'Record Queue', icon: 'queue' },
  { id: 'live', label: 'Live Browser', icon: 'live' },
  { id: 'history', label: 'Run History', icon: 'history' },
  { id: 'review', label: 'Review Center', icon: 'review', badge: 'review' },
  { id: 'analytics', label: 'Analytics', icon: 'analytics' },
  { id: 'integrations', label: 'Integrations', icon: 'integrations' },
  { id: 'settings', label: 'Settings', icon: 'settings' },
];

const state = {
  page: location.hash.slice(1) || 'overview',
  engine: { status: 'idle', current: {} },
  logs: [],
  badges: {},
  queueFilter: { status: 'all', search: '' },
  reviewFilter: 'all',
  openRun: null,
};

const $ = (sel, root = document) => root.querySelector(sel);
const view = $('#view');

// -- helpers -----------------------------------------------------------------

async function api(path, options = {}) {
  const res = await fetch(`/api${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch {}
    throw new Error(detail);
  }
  return res.status === 204 ? null : res.json();
}

function toast(message, kind = '') {
  const el = document.createElement('div');
  el.className = `toast ${kind}`;
  el.textContent = message;
  $('#toasts').appendChild(el);
  setTimeout(() => el.remove(), 5200);
}

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const fmtTime = (ts) => ts ? new Date(ts * 1000).toLocaleString(undefined,
  { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '-';

const fmtDur = (s) => {
  if (!s && s !== 0) return '-';
  const m = Math.floor(s / 60), r = Math.round(s % 60);
  return m ? `${m}m ${r}s` : `${r}s`;
};

const chip = (status) => `<span class="chip ${esc(status)}">${esc(status)}</span>`;

function statTile(icon, tone, value, label) {
  return `<div class="card"><div class="stat">
    <div class="stat-icon ${tone}">${ICON[icon]}</div>
    <div><div class="stat-value">${value}</div><div class="stat-label">${label}</div></div>
  </div></div>`;
}

function pageHead(title, sub, actions = '') {
  return `<div class="page-head"><div><h1>${title}</h1><p>${sub}</p></div>
    <div class="actions">${actions}</div></div>`;
}

function emptyState(title, body) {
  return `<div class="empty"><h3>${title}</h3><p>${body}</p></div>`;
}

// -- shell -------------------------------------------------------------------

function renderNav() {
  $('#nav').innerHTML = PAGES.map((p) => {
    const count = state.badges[p.badge];
    return `<button data-page="${p.id}" class="${p.id === state.page ? 'active' : ''}">
      ${ICON[p.icon]}<span>${p.label}</span>
      ${count ? `<span class="badge">${count}</span>` : ''}
    </button>`;
  }).join('');
  $('#nav').querySelectorAll('button').forEach((b) =>
    b.onclick = () => navigate(b.dataset.page));
}

function renderEnginePill() {
  const pill = $('#enginePill');
  const s = state.engine.status;
  pill.className = `engine-pill ${s === 'running' || s === 'stopping' ? 'running' : s === 'paused' ? 'paused' : ''}`;
  const c = state.engine.current || {};
  $('#engineLabel').textContent =
    s === 'idle' ? 'Idle'
    : s === 'paused' ? 'Paused'
    : c.index ? `Running ${c.index}/${c.total}` : 'Running';
}

function navigate(page) {
  state.page = page;
  location.hash = page;
  renderNav();
  render();
}

const RENDERERS = {};

async function render() {
  const fn = RENDERERS[state.page] || RENDERERS.overview;
  view.innerHTML = `<div class="empty"><p>Loading...</p></div>`;
  try {
    await fn();
  } catch (err) {
    view.innerHTML = pageHead('Something went wrong', esc(err.message));
  }
}

// -- Overview ----------------------------------------------------------------

RENDERERS.overview = async () => {
  const d = await api('/overview');
  state.badges.review = d.counts.review || 0;
  renderNav();

  const wf = d.workflow;
  const pct = d.counts.total ? Math.round(d.counts.completed / d.counts.total * 100) : 0;
  const busy = state.engine.status !== 'idle';

  view.innerHTML = pageHead(
    'Overview',
    'Monitor and control automation runs against targets you are authorized to use.',
    `<button class="btn primary" id="startBtn" ${busy ? 'disabled' : ''}>
       ${busy ? 'Run in progress' : 'Start new run'}</button>`
  ) + `
  <div class="grid cols-4" style="margin-bottom:16px">
    ${statTile('db', '', d.counts.total, 'Records in queue')}
    ${statTile('check', 'green', d.counts.completed, 'Completed')}
    ${statTile('clock', 'amber', d.counts.pending, 'Pending')}
    ${statTile('alert', 'red', d.counts.review + d.counts.failed, 'Needs attention')}
  </div>

  <div class="grid split">
    <div class="card">
      <div class="card-head"><div><h2>Automation control</h2>
        <p>Active workflow and batch configuration.</p></div></div>
      <div class="card-body">
        <div class="kv"><span>Workflow</span><span>${esc(wf.name)}</span></div>
        <div class="kv"><span>Target</span><span class="mono">${esc(wf.target_domain)}</span></div>
        <div class="kv"><span>Steps</span><span>${wf.steps.length}</span></div>
        <div class="kv"><span>Authorization</span><span class="chip completed">on record</span></div>

        <div class="field" style="margin-top:16px">
          <label for="batchSize">Batch size</label>
          <input type="number" id="batchSize" min="1" max="500" value="${Math.min(25, d.counts.pending || 25)}">
          <div class="hint">How many pending records this run will process.</div>
        </div>

        <div style="display:flex;gap:8px;margin-top:4px">
          <button class="btn" id="pauseBtn" ${state.engine.status !== 'running' ? 'disabled' : ''}>Pause</button>
          <button class="btn" id="resumeBtn" ${state.engine.status !== 'paused' ? 'disabled' : ''}>Resume</button>
          <button class="btn danger" id="stopBtn" ${!busy ? 'disabled' : ''}>Stop</button>
        </div>

        <div class="notice" style="margin-top:16px">${ICON.info}
          <div><strong>${esc(wf.target_domain)} is allowlisted</strong>
          Authorized by: ${esc(wf.authorized_by)}</div></div>
      </div>
    </div>

    <div>
      <div class="card" style="margin-bottom:16px">
        <div class="card-head"><div><h2>Live progress</h2>
          <p>Current run status.</p></div></div>
        <div class="card-body" id="liveProgress">${liveProgressHTML()}</div>
      </div>

      <div class="card">
        <div class="card-head"><div><h2>Recent activity</h2></div>
          <div class="actions"><button class="btn sm" data-goto="history">View all</button></div></div>
        <div class="table-wrap">
          ${d.recent_activity.length ? `<table><tbody>
            ${d.recent_activity.map((a) => `<tr>
              <td><div class="truncate">${esc(a.data.email || a.data.name || `#${a.id}`)}</div></td>
              <td style="text-align:right">${chip(a.status)}</td>
              <td class="muted" style="text-align:right;white-space:nowrap">${fmtTime(a.finished_at)}</td>
            </tr>`).join('')}
          </tbody></table>` : emptyState('No activity yet', 'Start a run to see results here.')}
        </div>
      </div>
    </div>
  </div>`;

  $('#startBtn').onclick = startRun;
  $('#pauseBtn').onclick = () => control('pause');
  $('#resumeBtn').onclick = () => control('resume');
  $('#stopBtn').onclick = () => control('stop');
  view.querySelectorAll('[data-goto]').forEach((b) =>
    b.onclick = () => navigate(b.dataset.goto));
};

function liveProgressHTML() {
  const c = state.engine.current || {};
  if (state.engine.status === 'idle' || !c.index) {
    return `<div class="empty" style="padding:28px 10px"><p>Engine idle. No run in progress.</p></div>`;
  }
  const pct = Math.round((c.index / c.total) * 100);
  return `
    <div style="display:flex;justify-content:space-between;margin-bottom:8px;font-size:13px">
      <span class="muted">Processing ${c.index} of ${c.total}</span><strong>${pct}%</strong>
    </div>
    <div class="progress"><div style="width:${pct}%"></div></div>
    <div class="kv" style="margin-top:14px"><span>Current record</span>
      <span class="truncate">${esc(c.label || '-')}</span></div>
    <div class="kv"><span>Step</span><span>${esc(c.step || '-')}
      ${c.step_count ? `<span class="muted">(${c.step_number}/${c.step_count})</span>` : ''}</span></div>
    <div class="kv"><span>Status</span>${chip(state.engine.status)}</div>`;
}

async function startRun() {
  const batch = parseInt($('#batchSize')?.value || '25', 10);
  try {
    const r = await api('/run/start', {
      method: 'POST',
      body: JSON.stringify({ batch_size: batch }),
    });
    toast(`Run #${r.run_id} started`, 'success');
    navigate('live');
  } catch (e) { toast(e.message, 'error'); }
}

async function control(action) {
  try {
    state.engine = await api(`/run/${action}`, { method: 'POST' });
    renderEnginePill();
    render();
  } catch (e) { toast(e.message, 'error'); }
}

// -- Automation Studio -------------------------------------------------------

RENDERERS.studio = async () => {
  const workflows = await api('/workflows');

  view.innerHTML = pageHead(
    'Automation Studio',
    'Define the steps a run executes. Every workflow records who authorized automating its target.'
  ) + `
  <div class="grid split">
    <div class="card">
      <div class="card-head"><div><h2>Workflows</h2>
        <p>${workflows.length} configured.</p></div></div>
      <div class="table-wrap"><table>
        <thead><tr><th>Name</th><th>Target</th><th>Steps</th><th>Authorized by</th></tr></thead>
        <tbody>${workflows.map((w) => `<tr>
          <td><strong>${esc(w.name)}</strong><div class="muted" style="font-size:12px">${esc(w.description)}</div></td>
          <td class="mono">${esc(w.target_domain)}</td>
          <td>${w.steps.length}</td>
          <td class="muted"><div class="truncate">${esc(w.authorized_by)}</div></td>
        </tr>`).join('')}</tbody>
      </table></div>
    </div>

    <div class="card">
      <div class="card-head"><div><h2>New workflow</h2>
        <p>Steps are JSON. Supported types: goto, wait_for, fill, click, assert_text, extract.</p></div></div>
      <div class="card-body">
        <div class="field"><label>Name</label><input type="text" id="wfName" placeholder="Staging smoke test"></div>
        <div class="field"><label>Target domain</label><input type="text" id="wfDomain" placeholder="staging.example.com">
          <div class="hint">Must also be added to Settings allowed domains.</div></div>
        <div class="field"><label>Who authorized this?</label><input type="text" id="wfAuth" placeholder="Jane Doe, CTO at Example Ltd, 2026-10-29">
          <div class="hint">Required. Stored with the workflow as your record of permission.</div></div>
        <div class="field"><label>Steps (JSON array)</label>
          <textarea id="wfSteps" rows="9">[
  {"type": "goto", "url": "https://staging.example.com/login", "label": "Open login"},
  {"type": "fill", "selector": "#email", "value": "{{email}}", "label": "Fill email"},
  {"type": "click", "selector": "button[type=submit]", "label": "Submit"},
  {"type": "assert_text", "selector": ".flash", "value": "Welcome", "label": "Verify"}
]</textarea></div>
        <button class="btn primary" id="saveWf">Save workflow</button>
      </div>
    </div>
  </div>`;

  $('#saveWf').onclick = async () => {
    let steps;
    try { steps = JSON.parse($('#wfSteps').value); }
    catch { return toast('Steps must be valid JSON.', 'error'); }
    try {
      await api('/workflows', {
        method: 'POST',
        body: JSON.stringify({
          name: $('#wfName').value.trim(),
          target_domain: $('#wfDomain').value.trim(),
          authorized_by: $('#wfAuth').value.trim(),
          description: '',
          steps,
        }),
      });
      toast('Workflow saved', 'success');
      render();
    } catch (e) { toast(e.message, 'error'); }
  };
};

// -- Record Queue ------------------------------------------------------------

RENDERERS.queue = async () => {
  const { status, search } = state.queueFilter;
  const d = await api(`/records?status=${status}&search=${encodeURIComponent(search)}&limit=200`);
  const c = d.counts;

  view.innerHTML = pageHead(
    'Record Queue',
    'Import, filter and manage the records a run will process.',
    `<button class="btn" id="importToggle">Import CSV</button>`
  ) + `
  <div class="grid cols-4" style="margin-bottom:16px">
    ${statTile('db', '', c.total || 0, 'Total records')}
    ${statTile('check', 'green', c.completed || 0, 'Completed')}
    ${statTile('clock', 'amber', c.pending || 0, 'Pending')}
    ${statTile('alert', 'red', (c.review || 0) + (c.failed || 0), 'Needs attention')}
  </div>

  <div class="card" id="importPanel" style="display:none;margin-bottom:16px">
    <div class="card-head"><div><h2>Import records</h2>
      <p>Paste CSV with a header row. Duplicates are skipped automatically.</p></div></div>
    <div class="card-body">
      <textarea id="csvText" rows="6" placeholder="name,tag&#10;Love tag,love&#10;Humor tag,humor"></textarea>
      <button class="btn primary" id="doImport" style="margin-top:12px">Import</button>
    </div>
  </div>

  <div class="card">
    <div class="toolbar">
      <div class="search"><input id="qSearch" placeholder="Search records" value="${esc(search)}"></div>
      <select id="qStatus">
        ${['all', 'pending', 'completed', 'failed', 'review', 'processing']
          .map((s) => `<option value="${s}" ${s === status ? 'selected' : ''}>${s[0].toUpperCase() + s.slice(1)}</option>`).join('')}
      </select>
      <div class="spacer"></div>
      <span class="muted">${d.records.length} shown</span>
    </div>
    <div class="table-wrap">
      ${d.records.length ? `<table>
        <thead><tr><th>#</th><th>Record</th><th>Status</th><th>Source</th><th>Last run</th><th></th></tr></thead>
        <tbody>${d.records.map((r) => `<tr>
          <td class="muted">${r.id}</td>
          <td><div class="truncate">${esc(r.data.email || r.data.name || JSON.stringify(r.data))}</div>
            <div class="mono muted truncate" style="font-size:11px">${esc(r.dedupe_key)}</div></td>
          <td>${chip(r.status)}</td>
          <td class="muted">${esc(r.source)}</td>
          <td class="muted">${fmtTime(r.last_run_at)}</td>
          <td style="text-align:right;white-space:nowrap">
            <button class="btn sm" data-reset="${r.id}">Reset</button>
            <button class="btn sm" data-del="${r.id}">Delete</button>
          </td>
        </tr>`).join('')}</tbody>
      </table>` : emptyState('No records match', 'Adjust the filter or import more records.')}
    </div>
  </div>`;

  $('#importToggle').onclick = () => {
    const p = $('#importPanel');
    p.style.display = p.style.display === 'none' ? 'block' : 'none';
  };
  $('#doImport').onclick = async () => {
    try {
      const r = await api('/records/import', {
        method: 'POST',
        body: JSON.stringify({ csv_text: $('#csvText').value }),
      });
      toast(`Imported ${r.inserted}, skipped ${r.skipped_duplicates} duplicate(s)`, 'success');
      render();
    } catch (e) { toast(e.message, 'error'); }
  };
  $('#qStatus').onchange = (e) => { state.queueFilter.status = e.target.value; render(); };
  let t;
  $('#qSearch').oninput = (e) => {
    clearTimeout(t);
    t = setTimeout(() => { state.queueFilter.search = e.target.value; render(); }, 280);
  };
  view.querySelectorAll('[data-reset]').forEach((b) => b.onclick = async () => {
    await api(`/records/${b.dataset.reset}/reset`, { method: 'POST' });
    toast('Record reset to pending'); render();
  });
  view.querySelectorAll('[data-del]').forEach((b) => b.onclick = async () => {
    await api(`/records/${b.dataset.del}`, { method: 'DELETE' });
    toast('Record deleted'); render();
  });
};

// -- Live Browser ------------------------------------------------------------

RENDERERS.live = async () => {
  const d = await api('/overview');
  const wf = d.workflow;

  view.innerHTML = pageHead(
    'Live Browser',
    'Watch each step as it executes and intervene when needed.',
    `<button class="btn" id="pauseBtn2" ${state.engine.status !== 'running' ? 'disabled' : ''}>Pause</button>
     <button class="btn" id="resumeBtn2" ${state.engine.status !== 'paused' ? 'disabled' : ''}>Resume</button>
     <button class="btn danger" id="stopBtn2" ${state.engine.status === 'idle' ? 'disabled' : ''}>Stop</button>`
  ) + `
  <div class="grid split">
    <div class="card">
      <div class="card-head"><div><h2>Browser view</h2>
        <p>Screenshot captured after each step.</p></div>
        <div class="actions" id="liveChip">${chip(state.engine.status)}</div></div>
      <div class="card-body">
        <div class="browser-frame">
          <div class="browser-chrome">
            <div class="browser-dots"><i></i><i></i><i></i></div>
            <div class="browser-url mono" id="liveUrl">${esc(wf.target_domain)}</div>
          </div>
          <div class="browser-screen" id="liveScreen">
            <div class="browser-empty">No run in progress.<br>Start a run from the Overview screen.</div>
          </div>
        </div>
        <div class="steps" id="liveSteps">${stepsHTML(wf.steps)}</div>
      </div>
    </div>

    <div>
      <div class="card" style="margin-bottom:16px">
        <div class="card-head"><div><h2>Current record</h2></div></div>
        <div class="card-body" id="liveMeta">${liveProgressHTML()}</div>
      </div>
      <div class="card">
        <div class="card-head"><div><h2>Event log</h2></div></div>
        <div class="card-body"><div class="log" id="liveLog">${logHTML()}</div></div>
      </div>
    </div>
  </div>`;

  state.wfSteps = wf.steps;
  $('#pauseBtn2').onclick = () => control('pause');
  $('#resumeBtn2').onclick = () => control('resume');
  $('#stopBtn2').onclick = () => control('stop');
  paintLive();
};

function stepsHTML(steps) {
  const c = state.engine.current || {};
  return steps.map((s, i) => {
    const n = i + 1;
    const cls = c.step_number > n ? 'done' : c.step_number === n ? 'active' : '';
    return `<div class="step-node ${cls}">
      <div class="bubble">${c.step_number > n ? '&#10003;' : n}</div>
      <div class="label">${esc(s.label || s.type)}</div></div>`;
  }).join('');
}

function logHTML() {
  if (!state.logs.length) return `<div class="muted">Waiting for events...</div>`;
  return state.logs.slice(-200).map((l) =>
    `<div><span class="t">${new Date(l.ts * 1000).toLocaleTimeString()}</span>
     <span class="${esc(l.level || '')}">${esc(l.message)}</span></div>`).join('');
}

function paintLive() {
  if (state.page !== 'live') return;
  const c = state.engine.current || {};
  if (c.screenshot) state.lastShot = c.screenshot;

  const screen = $('#liveScreen');
  if (screen) {
    const shot = c.screenshot || state.lastShot;
    if (!shot) {
      screen.innerHTML = `<div class="browser-empty">${state.engine.status === 'idle'
        ? 'No run in progress.<br>Start a run from the Overview screen.'
        : 'Waiting for first screenshot...'}</div>`;
    } else {
      // Swap src on the existing element. Rebuilding it restarts the download and
      // leaves the pane blank for most of the run.
      let img = screen.querySelector('img');
      if (!img) {
        screen.innerHTML = '<img alt="Browser view">';
        img = screen.querySelector('img');
      }
      if (img.getAttribute('src') !== shot) img.setAttribute('src', shot);
    }
  }
  const url = $('#liveUrl');
  if (url && c.label) url.textContent = c.label;

  const steps = $('#liveSteps');
  if (steps && state.wfSteps) steps.innerHTML = stepsHTML(state.wfSteps);

  const liveChip = $('#liveChip');
  if (liveChip) liveChip.innerHTML = chip(state.engine.status);

  const meta = $('#liveMeta');
  if (meta) meta.innerHTML = liveProgressHTML();
  const log = $('#liveLog');
  if (log) {
    const atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 40;
    log.innerHTML = logHTML();
    if (atBottom) log.scrollTop = log.scrollHeight;
  }
}

// -- Run History -------------------------------------------------------------

RENDERERS.history = async () => {
  if (state.openRun) return renderRunDetail(state.openRun);
  const runs = await api('/runs');
  const ok = runs.filter((r) => r.status === 'completed').length;
  const partial = runs.filter((r) => r.failed || r.review).length;
  const bad = runs.filter((r) => r.status === 'failed').length;

  view.innerHTML = pageHead('Run History', 'Every batch, its outcome and exportable results.') + `
  <div class="grid cols-4" style="margin-bottom:16px">
    ${statTile('history', '', runs.length, 'Total runs')}
    ${statTile('check', 'green', ok, 'Successful')}
    ${statTile('alert', 'amber', partial, 'With exceptions')}
    ${statTile('alert', 'red', bad, 'Failed')}
  </div>
  <div class="card"><div class="table-wrap">
    ${runs.length ? `<table>
      <thead><tr><th>#</th><th>Started</th><th>Batch</th><th>Completed</th><th>Review</th>
        <th>Failed</th><th>Duration</th><th>Status</th><th></th></tr></thead>
      <tbody>${runs.map((r) => `<tr>
        <td class="muted">${r.id}</td>
        <td>${fmtTime(r.started_at)}</td>
        <td>${r.total}</td>
        <td><strong>${r.completed}</strong></td>
        <td>${r.review || 0}</td>
        <td>${r.failed || 0}</td>
        <td class="muted">${fmtDur(r.duration_s)}</td>
        <td>${chip(r.status)}</td>
        <td style="text-align:right;white-space:nowrap">
          <button class="btn sm" data-run="${r.id}">Details</button>
          <a class="btn sm" href="/api/runs/${r.id}/export">Export</a>
        </td>
      </tr>`).join('')}</tbody></table>`
      : emptyState('No runs yet', 'Completed runs will be listed here.')}
  </div></div>`;

  view.querySelectorAll('[data-run]').forEach((b) =>
    b.onclick = () => { state.openRun = b.dataset.run; render(); });
};

async function renderRunDetail(runId) {
  const d = await api(`/runs/${runId}`);
  view.innerHTML = pageHead(
    `Run #${d.run.id}`,
    `${d.run.total} record(s), started ${fmtTime(d.run.started_at)}`,
    `<button class="btn" id="backBtn">Back to history</button>
     <a class="btn primary" href="/api/runs/${runId}/export">Export CSV</a>`
  ) + `
  <div class="grid cols-4" style="margin-bottom:16px">
    ${statTile('check', 'green', d.run.completed, 'Completed')}
    ${statTile('alert', 'amber', d.run.review || 0, 'Needs review')}
    ${statTile('alert', 'red', d.run.failed || 0, 'Failed')}
    ${statTile('clock', '', fmtDur(d.run.finished_at ? d.run.finished_at - d.run.started_at : null), 'Duration')}
  </div>
  <div class="card"><div class="table-wrap"><table>
    <thead><tr><th>#</th><th>Record</th><th>Status</th><th>Issue</th>
      <th>Duration</th><th>Extracted</th><th>Evidence</th></tr></thead>
    <tbody>${d.items.map((i) => {
      const shot = (i.evidence.steps || []).slice(-1)[0]?.screenshot;
      const ext = i.evidence.extracted || {};
      return `<tr>
        <td class="muted">${i.id}</td>
        <td><div class="truncate">${esc(i.data.email || i.data.name || `#${i.record_id}`)}</div></td>
        <td>${chip(i.status)}</td>
        <td class="muted"><div class="truncate">${esc(i.issue || i.error || '')}</div></td>
        <td class="muted">${i.duration_ms ? (i.duration_ms / 1000).toFixed(1) + 's' : '-'}</td>
        <td class="mono" style="font-size:11px"><div class="truncate">${esc(Object.values(ext).join(' | '))}</div></td>
        <td>${shot ? `<a href="${esc(shot)}" target="_blank"><img class="thumb" src="${esc(shot)}"></a>` : '<span class="muted">-</span>'}</td>
      </tr>`;
    }).join('')}</tbody>
  </table></div></div>`;

  $('#backBtn').onclick = () => { state.openRun = null; render(); };
}

// -- Review Center -----------------------------------------------------------

RENDERERS.review = async () => {
  const d = await api(`/review?issue=${state.reviewFilter}`);
  state.badges.review = d.items.length;
  renderNav();

  const issues = ['all', ...Object.keys(d.counts)];

  view.innerHTML = pageHead(
    'Review Center',
    'Records the engine could not confirm. Approve or reject each one by hand.'
  ) + `
  <div class="notice warn" style="margin-bottom:16px">${ICON.alert}
    <div><strong>Nothing here is retried automatically</strong>
    An uncertain outcome stays pending until you decide, so a record is never submitted twice by accident.</div>
  </div>
  <div class="card">
    <div class="tabs">${issues.map((i) => `<button data-issue="${i}" class="${i === state.reviewFilter ? 'active' : ''}">
      ${i === 'all' ? 'All' : esc(i.replace(/_/g, ' '))} ${i !== 'all' ? `(${d.counts[i]})` : `(${d.items.length})`}</button>`).join('')}</div>
    <div class="table-wrap">
      ${d.items.length ? `<table>
        <thead><tr><th>#</th><th>Record</th><th>Issue</th><th>Detail</th><th>Evidence</th><th></th></tr></thead>
        <tbody>${d.items.map((i) => {
          const shot = (i.evidence.steps || []).slice(-1)[0]?.screenshot;
          return `<tr>
            <td class="muted">${i.id}</td>
            <td><div class="truncate">${esc(i.data.email || i.data.name || `#${i.record_id}`)}</div></td>
            <td><span class="chip review">${esc((i.issue || 'unknown').replace(/_/g, ' '))}</span></td>
            <td class="muted"><div class="truncate">${esc(i.error || '')}</div></td>
            <td>${shot ? `<a href="${esc(shot)}" target="_blank"><img class="thumb" src="${esc(shot)}"></a>` : '<span class="muted">-</span>'}</td>
            <td style="text-align:right;white-space:nowrap">
              <button class="btn sm primary" data-approve="${i.id}">Approve</button>
              <button class="btn sm" data-reject="${i.id}">Requeue</button>
            </td>
          </tr>`;
        }).join('')}</tbody></table>`
        : emptyState('Nothing needs review', 'Uncertain outcomes will appear here for a human decision.')}
    </div>
  </div>`;

  view.querySelectorAll('[data-issue]').forEach((b) =>
    b.onclick = () => { state.reviewFilter = b.dataset.issue; render(); });
  view.querySelectorAll('[data-approve]').forEach((b) => b.onclick = () => resolve(b.dataset.approve, 'approve'));
  view.querySelectorAll('[data-reject]').forEach((b) => b.onclick = () => resolve(b.dataset.reject, 'reject'));
};

async function resolve(id, resolution) {
  try {
    await api(`/review/${id}/resolve`, { method: 'POST', body: JSON.stringify({ resolution }) });
    toast(resolution === 'approve' ? 'Marked completed' : 'Returned to pending', 'success');
    render();
  } catch (e) { toast(e.message, 'error'); }
}

// -- Analytics ---------------------------------------------------------------

RENDERERS.analytics = async () => {
  const a = await api('/analytics');
  const processed = a.completed + a.failed + a.review;
  const rate = processed ? Math.round(a.completed / processed * 100) : 0;
  const max = Math.max(1, ...a.daily.map((d) => d.completed + d.failed + d.review));

  view.innerHTML = pageHead('Analytics', 'Outcomes, error categories and processing time.') + `
  <div class="grid cols-4" style="margin-bottom:16px">
    ${statTile('check', 'green', a.completed, 'Completed all time')}
    ${statTile('alert', 'amber', a.review, 'Sent to review')}
    ${statTile('alert', 'red', a.failed, 'Failed')}
    ${statTile('clock', '', rate + '%', 'Success rate')}
  </div>

  <div class="grid split">
    <div class="card">
      <div class="card-head"><div><h2>Daily outcomes</h2>
        <p>Last ${a.daily.length} active day(s).</p></div></div>
      <div class="card-body">
        ${a.daily.length ? `<div class="bars">
          ${a.daily.map((d) => {
            const h = (v) => `height:${(v / max) * 100}%`;
            const day = new Date(d.day * 1000).toLocaleDateString(undefined, { weekday: 'short' });
            return `<div class="bar-col"><div class="bar-stack">
              ${d.failed ? `<i class="f" style="${h(d.failed)}"></i>` : ''}
              ${d.review ? `<i class="r" style="${h(d.review)}"></i>` : ''}
              ${d.completed ? `<i class="c" style="${h(d.completed)}"></i>` : ''}
            </div><span>${day}</span></div>`;
          }).join('')}
        </div>
        <div class="legend">
          <div><i style="background:var(--green)"></i>Completed</div>
          <div><i style="background:var(--blue)"></i>Review</div>
          <div><i style="background:var(--red)"></i>Failed</div>
        </div>` : emptyState('No data yet', 'Run a batch to populate analytics.')}
      </div>
    </div>

    <div>
      <div class="card" style="margin-bottom:16px">
        <div class="card-head"><div><h2>Outcome split</h2></div></div>
        <div class="card-body donut-wrap">
          ${donut([
            { v: a.completed, c: 'var(--green)' },
            { v: a.review, c: 'var(--blue)' },
            { v: a.failed, c: 'var(--red)' },
          ], processed)}
          <div class="donut-legend">
            <div class="kv"><span>Completed</span><span>${a.completed}</span></div>
            <div class="kv"><span>Review</span><span>${a.review}</span></div>
            <div class="kv"><span>Failed</span><span>${a.failed}</span></div>
          </div>
        </div>
      </div>

      <div class="card">
        <div class="card-head"><div><h2>Error categories</h2></div></div>
        <div class="table-wrap">
          ${a.issues.length ? `<table><tbody>${a.issues.map((i) => `<tr>
            <td>${esc(i.issue.replace(/_/g, ' '))}</td>
            <td style="text-align:right"><strong>${i.n}</strong></td></tr>`).join('')}</tbody></table>`
            : emptyState('No errors recorded', 'Failures are grouped here by cause.')}
        </div>
      </div>
    </div>
  </div>

  <div class="card" style="margin-top:16px">
    <div class="card-body">
      <div class="kv"><span>Average processing time per record</span>
        <span>${a.avg_ms ? (a.avg_ms / 1000).toFixed(1) + 's' : '-'}</span></div>
      <div class="kv"><span>Total runs</span><span>${a.runs}</span></div>
    </div>
  </div>`;
};

function donut(slices, total) {
  const r = 52, circ = 2 * Math.PI * r;
  let offset = 0;
  const arcs = slices.filter((s) => s.v > 0).map((s) => {
    const len = total ? (s.v / total) * circ : 0;
    const el = `<circle cx="64" cy="64" r="${r}" fill="none" stroke="${s.c}"
      stroke-width="18" stroke-dasharray="${len} ${circ - len}"
      stroke-dashoffset="${-offset}" transform="rotate(-90 64 64)"/>`;
    offset += len;
    return el;
  }).join('');
  const pct = total ? Math.round((slices[0].v / total) * 100) : 0;
  return `<svg viewBox="0 0 128 128" width="128" height="128">
    <circle cx="64" cy="64" r="${r}" fill="none" stroke="var(--border)" stroke-width="18"/>
    ${arcs}
    <text x="64" y="60" text-anchor="middle" font-size="22" font-weight="700" fill="var(--text)">${pct}%</text>
    <text x="64" y="78" text-anchor="middle" font-size="10" fill="var(--muted)">success</text>
  </svg>`;
}

// -- Integrations ------------------------------------------------------------

RENDERERS.integrations = async () => {
  const d = await api('/integrations');

  view.innerHTML = pageHead(
    'Integrations',
    'Which targets this workspace may automate, and where its data lives.'
  ) + `
  <div class="notice" style="margin-bottom:16px">${ICON.info}
    <div><strong>Allowlist is enforced at run time</strong>
    A workflow whose target is not listed below is refused before the browser opens.
    Add a domain only if you are authorized to automate it.</div>
  </div>

  <div class="grid cols-2">
    <div class="card">
      <div class="card-head"><div><h2>Allowed domains</h2>
        <p>${d.allowed_domains.length} entries. Edit in Settings.</p></div></div>
      <div class="card-body">
        ${d.allowed_domains.map((x) => `<div class="kv"><span class="mono">${esc(x)}</span>
          <span class="chip completed">allowed</span></div>`).join('')}
      </div>
    </div>

    <div class="card">
      <div class="card-head"><div><h2>Workflow authorization</h2>
        <p>Each workflow carries a record of who approved it.</p></div></div>
      <div class="card-body">
        ${d.workflows.map((w) => `<div class="kv">
          <span>${esc(w.name)} <span class="mono muted">${esc(w.target_domain)}</span></span>
          <span class="chip ${w.allowlisted ? 'completed' : 'failed'}">
            ${w.allowlisted ? 'allowlisted' : 'blocked'}</span></div>`).join('')}
      </div>
    </div>
  </div>

  <div class="card" style="margin-top:16px">
    <div class="card-head"><div><h2>Local storage</h2>
      <p>Everything stays on this machine. Nothing is uploaded.</p></div></div>
    <div class="card-body">
      <div class="kv"><span>Database</span><span class="mono truncate">${esc(d.database)}</span></div>
      <div class="kv"><span>Evidence folder</span><span class="mono truncate">${esc(d.evidence_dir)}</span></div>
      <div class="kv"><span>robots.txt enforcement</span>
        <span class="chip ${d.respect_robots_txt ? 'completed' : 'pending'}">
          ${d.respect_robots_txt ? 'on' : 'off'}</span></div>
    </div>
  </div>`;
};

// -- Settings ----------------------------------------------------------------

RENDERERS.settings = async () => {
  const s = await api('/settings');
  const sw = (key, title, desc) => `<label class="switch">
    <input type="checkbox" data-set="${key}" ${s[key] === '1' ? 'checked' : ''}>
    <span class="track"></span>
    <span class="switch-text"><strong>${title}</strong><span>${desc}</span></span></label>`;

  view.innerHTML = pageHead('Settings', 'Workspace, pacing and safety controls.',
    `<button class="btn primary" id="saveSettings">Save changes</button>`) + `
  <div class="grid split">
    <div class="card">
      <div class="card-head"><div><h2>Automation</h2></div></div>
      <div class="card-body">
        <div class="field"><label>Workspace name</label>
          <input type="text" data-set="workspace_name" value="${esc(s.workspace_name)}"></div>
        <div class="field"><label>Default batch size</label>
          <input type="number" data-set="default_batch_size" value="${esc(s.default_batch_size)}"></div>
        <div class="grid cols-2">
          <div class="field"><label>Min delay (seconds)</label>
            <input type="number" data-set="min_delay_seconds" value="${esc(s.min_delay_seconds)}">
            <div class="hint">Courtesy gap between records so you do not overload the target.</div></div>
          <div class="field"><label>Max delay (seconds)</label>
            <input type="number" data-set="max_delay_seconds" value="${esc(s.max_delay_seconds)}"></div>
        </div>
        <div class="field"><label>Step timeout (seconds)</label>
          <input type="number" data-set="step_timeout_seconds" value="${esc(s.step_timeout_seconds)}"></div>
        <div class="field"><label>Stop after N consecutive failures</label>
          <input type="number" data-set="stop_on_consecutive_failures" value="${esc(s.stop_on_consecutive_failures)}">
          <div class="hint">Circuit breaker. Halts the run so a broken selector cannot burn the whole queue.</div></div>
        <div class="field"><label>Browser</label>
          <select data-set="browser_channel">
            ${[['chrome', 'Google Chrome (installed)'],
               ['msedge', 'Microsoft Edge (installed)'],
               ['', 'Playwright bundled Chromium']]
              .map(([v, l]) => `<option value="${v}" ${s.browser_channel === v ? 'selected' : ''}>${l}</option>`).join('')}
          </select>
          <div class="hint">Falls back automatically if the chosen browser will not start.</div></div>
      </div>
    </div>

    <div>
      <div class="card" style="margin-bottom:16px">
        <div class="card-head"><div><h2>Safety</h2></div></div>
        <div class="card-body">
          ${sw('respect_robots_txt', 'Respect robots.txt', 'Refuse any URL the site disallows for crawlers.')}
          ${sw('capture_screenshots', 'Capture evidence', 'Save a screenshot after every step.')}
          ${sw('skip_duplicates', 'Skip duplicates', 'Reject records whose key already exists.')}
          ${sw('headless', 'Run headless', 'Hide the browser window. Leave off to watch the run.')}
        </div>
      </div>

      <div class="card">
        <div class="card-head"><div><h2>Allowed domains</h2>
          <p>One per line. Runs against anything else are refused.</p></div></div>
        <div class="card-body">
          <textarea data-set="allowed_domains" rows="7">${esc(s.allowed_domains)}</textarea>
          <div class="notice warn" style="margin-top:12px">${ICON.alert}
            <div><strong>Only add targets you are permitted to automate</strong>
            Your own systems, a client's with written approval, or a site whose terms allow it.</div></div>
        </div>
      </div>
    </div>
  </div>`;

  $('#saveSettings').onclick = async () => {
    const values = {};
    view.querySelectorAll('[data-set]').forEach((el) => {
      values[el.dataset.set] = el.type === 'checkbox' ? (el.checked ? '1' : '0') : el.value;
    });
    try {
      await api('/settings', { method: 'PUT', body: JSON.stringify({ values }) });
      toast('Settings saved', 'success');
    } catch (e) { toast(e.message, 'error'); }
  };
};

// -- websocket ---------------------------------------------------------------

function connect() {
  const ws = new WebSocket(`ws://${location.host}/ws`);

  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);

    if (msg.kind === 'state') {
      state.engine = { status: msg.status, current: msg.current || {}, run_id: msg.run_id };
      renderEnginePill();
      if (state.page === 'live') paintLive();
      if (state.page === 'overview') {
        const lp = $('#liveProgress');
        if (lp) lp.innerHTML = liveProgressHTML();
      }
    }

    if (msg.kind === 'log') {
      state.logs.push(msg);
      if (state.logs.length > 400) state.logs.shift();
      if (state.page === 'live') paintLive();
      if (msg.level === 'error') toast(msg.message, 'error');
    }

    if (msg.kind === 'run_finished') {
      toast(`Run #${msg.run_id} finished: ${msg.status}`,
        msg.status === 'completed' ? 'success' : '');
      render();
    }

    if (msg.kind === 'item_finished' && ['overview', 'queue'].includes(state.page)) {
      render();
    }
  };

  ws.onclose = () => setTimeout(connect, 1800);
}

// -- boot --------------------------------------------------------------------

$('#themeToggle').onclick = () => {
  const dark = document.documentElement.dataset.theme === 'dark';
  document.documentElement.dataset.theme = dark ? 'light' : 'dark';
  localStorage.setItem('fp-theme', dark ? 'light' : 'dark');
};
document.documentElement.dataset.theme = localStorage.getItem('fp-theme') || 'light';

window.addEventListener('hashchange', () => {
  const p = location.hash.slice(1);
  if (p && p !== state.page) { state.page = p; state.openRun = null; renderNav(); render(); }
});

$('#globalSearch').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') {
    state.queueFilter.search = e.target.value;
    state.queueFilter.status = 'all';
    navigate('queue');
  }
});

renderNav();
render();
connect();
