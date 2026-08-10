'use strict';

/* ── Utilities ─────────────────────────────────────────────────────────────── */
function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

/* ── Protocol badge rendering ───────────────────────────────────────────────── */
function protoBadgeHtml(proto) {
  const base = 'display:inline-block;padding:1px 6px;border-radius:3px;font-size:.75rem;font-weight:600;border:1px solid;margin:1px 2px';
  const label = proto.name.toUpperCase();
  if (proto.secure === false)
    return `<span style="${base};color:#dc3545;border-color:#dc3545;background:#fff5f5">${esc(label)}</span>`;
  if (proto.secure === true)
    return `<span style="${base};color:#2d6a2d;border-color:#5a9e5a;background:#f4faf4">${esc(label)}</span>`;
  return `<span style="${base};color:#555;border-color:#aaa;background:#f8f8f8">${esc(label)}</span>`;
}

function protoListHtml(protocols) {
  if (!protocols || !protocols.length) return '<span style="color:#888">—</span>';
  return protocols.map(protoBadgeHtml).join('');
}

/* ── Result badge rendering (CIS and interface checks unified) ──────────────── */
function resultBadgeHtml(result) {
  const base = 'display:inline-block;padding:2px 8px;border-radius:3px;font-size:.75rem;font-weight:700;border:1px solid';
  switch ((result || '').toUpperCase()) {
    case 'INSECURE':
      return `<span style="${base};color:#dc3545;border-color:#dc3545;background:#fff5f5">INSECURE</span>`;
    case 'FAIL':
      return `<span style="${base};color:#b91c1c;border-color:#fca5a5;background:#fee2e2">FAIL</span>`;
    case 'WARN':
      return `<span style="${base};color:#b45309;border-color:#fcd34d;background:#fffbeb">WARN</span>`;
    case 'CONFIG_MISSING':
      return `<span style="${base};color:#92400e;border-color:#fde68a;background:#fef3c7">CONFIG MISSING</span>`;
    case 'PASS':
      return `<span style="${base};color:#166534;border-color:#86efac;background:#dcfce7">PASS</span>`;
    case 'INFO':
      return `<span style="${base};color:#1d4ed8;border-color:#93c5fd;background:#eff6ff">INFO</span>`;
    default:
      return `<span style="${base};color:#555;border-color:#aaa;background:#f8f8f8">${esc(result || '?')}</span>`;
  }
}

/* ── State ──────────────────────────────────────────────────────────────────── */
let allRows       = [];
let lastMeta      = null;
let currentPage   = 1;
let pageSize      = 25;
let filterText    = '';
let filterDevice  = '';
let filterResult  = '';
let activeProtos  = new Set();
let _protoMeta    = {};
let _abortRun     = false;
let _knownDevices = [];

/* ── Error/clear ────────────────────────────────────────────────────────────── */
function showError(msg) {
  const el = document.getElementById('drError');
  el.textContent = msg;
  el.style.display = '';
}
function clearError() {
  const el = document.getElementById('drError');
  el.textContent = '';
  el.style.display = 'none';
}

/* ── Progress bar helpers ───────────────────────────────────────────────────── */
function showProgress(done, total, currentDevice) {
  const wrap = document.getElementById('drProgressWrap');
  const bar  = document.getElementById('drProgressBar');
  const lbl  = document.getElementById('drProgressLabel');
  if (!wrap) return;
  wrap.style.display = '';
  const pct = total > 0 ? Math.round((done / total) * 100) : 0;
  bar.style.width = pct + '%';
  bar.textContent  = pct + '%';
  const remaining  = total - done;
  lbl.textContent  = `Analysing… ${done} / ${total} devices` +
    (currentDevice ? ` — ${currentDevice}` : '') +
    (remaining > 0  ? ` (${remaining} remaining)` : '');
}

function hideProgress() {
  const wrap = document.getElementById('drProgressWrap');
  if (wrap) wrap.style.display = 'none';
}

/* ── Check parameter panel ──────────────────────────────────────────────────── */
function updateParamsPanel() {
  const checkedKeys = new Set(
    [...document.querySelectorAll('input[name="dr_check"]:checked')].map(cb => cb.value)
  );
  const panel  = document.getElementById('drParamsPanel');
  const fields = document.getElementById('drParamsFields');

  // Find all parameterised checks that are currently selected
  const active = (CHECK_DEFS || []).filter(
    c => checkedKeys.has(c.key) && c.params_schema && c.params_schema.length > 0
  );

  if (!active.length) {
    panel.style.display = 'none';
    return;
  }

  panel.style.display = '';

  // Preserve any values the user has already typed before rebuilding
  const savedValues = {};
  fields.querySelectorAll('.dr-param-input').forEach(inp => {
    savedValues[`${inp.dataset.checkKey}_${inp.dataset.paramKey}`] = inp.value;
  });

  fields.innerHTML = '';

  active.forEach(check => {
    check.params_schema.forEach(param => {
      const row = document.createElement('div');
      row.style.cssText = 'display:flex;align-items:center;gap:.6rem;margin-bottom:.5rem;flex-wrap:wrap';

      const lbl = document.createElement('label');
      lbl.style.cssText = 'min-width:180px;font-size:.88rem;font-weight:600;color:var(--text)';
      lbl.textContent = `${check.name} — ${param.label}:`;
      lbl.setAttribute('for', `drParam_${check.key}_${param.key}`);

      const inp = document.createElement('input');
      inp.type = 'text';
      inp.id   = `drParam_${check.key}_${param.key}`;
      inp.dataset.checkKey  = check.key;
      inp.dataset.paramKey  = param.key;
      inp.dataset.paramType = param.type || 'text';
      inp.placeholder = param.placeholder || '';
      inp.className   = 'form-control dr-param-input';
      inp.style.cssText = 'max-width:360px;font-size:.88rem';

      // Restore previously entered value when the panel is rebuilt
      const savedKey = `${check.key}_${param.key}`;
      if (savedValues[savedKey] !== undefined) inp.value = savedValues[savedKey];

      row.appendChild(lbl);
      row.appendChild(inp);
      fields.appendChild(row);
    });
  });
}

function collectCheckParams() {
  const params = {};
  document.querySelectorAll('.dr-param-input').forEach(inp => {
    const ck = inp.dataset.checkKey;
    const pk = inp.dataset.paramKey;
    const pt = inp.dataset.paramType || 'text';
    const val = (inp.value || '').trim();
    if (!params[ck]) params[ck] = {};
    if (pt === 'ip_list') {
      // IP lists: split on whitespace/commas into an array
      params[ck][pk] = val ? val.split(/[\s,]+/).map(s => s.trim()).filter(Boolean) : [];
    } else {
      // text / number: send as a plain string so Python can parse it directly
      params[ck][pk] = val;
    }
  });
  return params;
}

/* ── Device search helpers ──────────────────────────────────────────────────── */

// Parse the device search field into an array of trimmed tokens (lowercase).
// Returns [] if blank or "all".
function parseDeviceSearchTokens() {
  const raw = (document.getElementById('drDeviceSearch').value || '').trim();
  if (!raw || raw.toLowerCase() === 'all') return [];
  return raw.split(/[,\s]+/).map(s => s.trim().toLowerCase()).filter(Boolean);
}

// Return the subset of _knownDevices that match the current search field.
// Empty tokens (or "all") → all devices.
function resolveTargetDevices() {
  const tokens = parseDeviceSearchTokens();
  if (!tokens.length) return _knownDevices;
  return _knownDevices.filter(d => {
    const name = (d.name || '').toLowerCase();
    const ip   = (d.ip   || '').toLowerCase();
    return tokens.some(t => name === t || ip === t || name.includes(t) || ip.includes(t));
  });
}

function updateDeviceMatchCount() {
  const el = document.getElementById('drDeviceMatchCount');
  if (!_knownDevices.length) { el.textContent = ''; return; }
  const matched = resolveTargetDevices();
  const tokens  = parseDeviceSearchTokens();
  if (!tokens.length) {
    el.textContent = `${_knownDevices.length} device(s) in ADOM`;
  } else {
    el.textContent = `${matched.length} of ${_knownDevices.length} matched`;
  }
  document.getElementById('drRunBtn').disabled = matched.length === 0;
}

/* ── ADOM loader ────────────────────────────────────────────────────────────── */
async function loadAdoms() {
  const sel = document.getElementById('drAdom');
  try {
    const resp = await fetch('/api/adoms');
    if (resp.status === 401) { location.href = '/login'; return; }
    const adoms = await resp.json();
    if (!Array.isArray(adoms)) return;
    adoms.forEach(a => {
      const opt = document.createElement('option');
      opt.value = a.name; opt.textContent = a.name;
      sel.appendChild(opt);
    });
  } catch (_) {}
}

/* ── Fetch device list when ADOM changes ────────────────────────────────────── */
async function onAdomChange(adom) {
  _knownDevices = [];
  document.getElementById('drResults').style.display = 'none';
  document.getElementById('drRunBtn').disabled = true;
  document.getElementById('drDeviceMatchCount').textContent = '';
  clearError();
  allRows = [];

  if (!adom) return;

  document.getElementById('drDeviceLoading').style.display = '';
  try {
    const resp = await fetch(`/api/device-review/adoms/${encodeURIComponent(adom)}/devices`);
    if (resp.status === 401) { location.href = '/login'; return; }
    const data = await resp.json();
    if (Array.isArray(data)) {
      _knownDevices = data;
      updateDeviceMatchCount();
    }
  } catch (e) {
    showError('Could not load device list: ' + e.message);
  } finally {
    document.getElementById('drDeviceLoading').style.display = 'none';
  }
}

/* ── Run analysis (per-device loop with live progress) ──────────────────────── */
async function runAnalysis() {
  clearError();
  hideProgress();
  const adom = document.getElementById('drAdom').value;
  if (!adom) return;

  const checks = [...document.querySelectorAll('input[name="dr_check"]:checked')].map(cb => cb.value);
  if (!checks.length) { showError('Select at least one check.'); return; }

  const targetDevices = resolveTargetDevices();
  const deviceList    = targetDevices.map(d => d.name).filter(Boolean);
  if (!deviceList.length) { showError('No devices matched — check your firewall filter or select an ADOM.'); return; }

  const checkParams = collectCheckParams();

  _abortRun = false;
  document.getElementById('drRunBtn').disabled        = true;
  const cancelBtn = document.getElementById('drCancelBtn');
  cancelBtn.disabled    = false;
  cancelBtn.textContent = '⏹ Cancel';
  cancelBtn.style.display = '';
  document.getElementById('drRunning').style.display = '';
  document.getElementById('drResults').style.display = 'none';

  const collectedRows = [];
  const reviewed      = [];

  showProgress(0, deviceList.length, deviceList[0]);

  for (let i = 0; i < deviceList.length; i++) {
    if (_abortRun) break;

    const device = deviceList[i];
    showProgress(i, deviceList.length, device);

    try {
      const resp = await fetch('/api/device-review/run/device', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ adom, device, checks, check_params: checkParams }),
      });
      if (resp.status === 401) { location.href = '/login'; return; }
      const data = await resp.json();
      if (resp.ok && Array.isArray(data.rows)) {
        collectedRows.push(...data.rows);
        reviewed.push(device);
      }
    } catch (_) {
      // network error on one device — skip and continue
    }
  }

  showProgress(deviceList.length, deviceList.length, '');

  const runAt = new Date().toLocaleString();
  allRows  = collectedRows;
  lastMeta = {
    adom:         adom,
    run_at:       runAt,
    device_count: reviewed.length,
    checks_run:   checks,
    devices:      reviewed,
    check_params: checkParams,
  };

  // Reset filters
  filterText   = '';
  filterDevice = '';
  filterResult = '';
  currentPage  = 1;
  document.getElementById('drFilter').value       = '';
  document.getElementById('drDeviceFilter').value = '';
  document.getElementById('drResultFilter').value = '';

  populateDeviceFilter(reviewed);
  buildProtoCheckboxes();
  renderTable();

  document.getElementById('drResults').style.display = '';

  const failCount = allRows.filter(r => r.result === 'FAIL' || r.result === 'INSECURE').length;
  const passCount = allRows.filter(r => r.result === 'PASS').length;
  const warnCount = allRows.filter(r => r.result === 'WARN' || r.result === 'CONFIG_MISSING').length;
  let label = `Last run: ${runAt} — ${reviewed.length} device(s) · ${allRows.length} finding(s)`;
  if (failCount) label += ` · ${failCount} fail/insecure`;
  if (warnCount) label += ` · ${warnCount} warn/missing`;
  if (passCount) label += ` · ${passCount} pass`;
  if (_abortRun) label += ' (cancelled)';
  document.getElementById('drLastRunLabel').textContent = label;

  document.getElementById('drRunBtn').disabled        = false;
  cancelBtn.style.display  = 'none';
  cancelBtn.disabled       = false;
  cancelBtn.textContent    = '⏹ Cancel';
  document.getElementById('drRunning').style.display  = 'none';
  setTimeout(hideProgress, 2000);
}

/* ── Protocol checkbox panel (interface check only) ─────────────────────────── */
function buildProtoCheckboxes() {
  _protoMeta = {};
  allRows.forEach(row => {
    (row.protocols || []).forEach(p => {
      if (!(p.name in _protoMeta)) _protoMeta[p.name] = p.secure;
    });
  });

  const panel = document.getElementById('drProtoPanel');
  if (!Object.keys(_protoMeta).length) {
    panel.style.display = 'none';
    return;
  }
  panel.style.display = '';

  const sorted = Object.keys(_protoMeta).sort((a, b) => {
    const rank = v => v === false ? 0 : v === true ? 1 : 2;
    const ra = rank(_protoMeta[a]), rb = rank(_protoMeta[b]);
    return ra !== rb ? ra - rb : a.localeCompare(b);
  });

  activeProtos = new Set(sorted);

  const container = document.getElementById('drProtoChecks');
  container.innerHTML = '';

  if (!sorted.length) {
    container.innerHTML = '<span class="text-muted" style="font-size:.82rem">No protocols found.</span>';
    return;
  }

  sorted.forEach(name => {
    const secure = _protoMeta[name];
    const count  = allRows.filter(r => (r.protocols || []).some(p => p.name === name)).length;

    let badgeStyle = 'display:inline-block;padding:1px 5px;border-radius:3px;font-size:.72rem;font-weight:600;border:1px solid;margin-left:4px;vertical-align:middle';
    if (secure === false)  badgeStyle += ';color:#dc3545;border-color:#dc3545;background:#fff5f5';
    else if (secure === true) badgeStyle += ';color:#2d6a2d;border-color:#5a9e5a;background:#f4faf4';
    else                   badgeStyle += ';color:#555;border-color:#aaa;background:#f8f8f8';

    const lbl = document.createElement('label');
    lbl.className = 'checkbox-label';
    lbl.innerHTML =
      `<input type="checkbox" class="dr-proto-cb" value="${esc(name)}" checked />` +
      `<span style="${badgeStyle}">${esc(name.toUpperCase())}</span>` +
      `<span style="font-size:.75rem;color:var(--text-muted);margin-left:3px">(${count})</span>`;
    container.appendChild(lbl);
  });

  container.querySelectorAll('.dr-proto-cb').forEach(cb => {
    cb.addEventListener('change', () => {
      activeProtos = new Set(
        [...container.querySelectorAll('.dr-proto-cb:checked')].map(c => c.value)
      );
      currentPage = 1;
      renderTable();
    });
  });
}

function setAllProtos(checked) {
  document.querySelectorAll('.dr-proto-cb').forEach(cb => { cb.checked = checked; });
  activeProtos = checked ? new Set(Object.keys(_protoMeta)) : new Set();
  currentPage = 1;
  renderTable();
}

/* ── Device dropdown ────────────────────────────────────────────────────────── */
function populateDeviceFilter(deviceNames) {
  const sel = document.getElementById('drDeviceFilter');
  sel.innerHTML = '<option value="">All devices</option>';
  [...new Set(deviceNames)].sort().forEach(name => {
    const opt = document.createElement('option');
    opt.value = name; opt.textContent = name;
    sel.appendChild(opt);
  });
}

/* ── Filtering ──────────────────────────────────────────────────────────────── */
function filtered() {
  const q = filterText.toLowerCase();
  return allRows.filter(row => {
    if (filterDevice && row.device !== filterDevice) return false;
    if (filterResult && (row.result || '') !== filterResult) return false;

    // For interface-protocol rows, apply protocol visibility filter
    if (row.protocols && row.protocols.length > 0 && activeProtos.size > 0) {
      const rowProtos = new Set((row.protocols || []).map(p => p.name));
      if (![...activeProtos].some(p => rowProtos.has(p))) return false;
    }

    if (!q) return true;
    const protoNames = (row.protocols || []).map(p => p.name).join(' ').toLowerCase();
    return (
      (row.device    || '').toLowerCase().includes(q) ||
      (row.check     || '').toLowerCase().includes(q) ||
      (row.result    || '').toLowerCase().includes(q) ||
      (row.detail    || '').toLowerCase().includes(q) ||
      (row.interface || '').toLowerCase().includes(q) ||
      (row.ip        || '').toLowerCase().includes(q) ||
      protoNames.includes(q)
    );
  });
}

function visibleProtocols(row) {
  if (!row.protocols || !row.protocols.length) return [];
  return activeProtos.size > 0
    ? row.protocols.filter(p => activeProtos.has(p.name))
    : row.protocols;
}

/* ── Render table ───────────────────────────────────────────────────────────── */
function renderTable() {
  const rows  = filtered();
  const start = (currentPage - 1) * pageSize;
  const page  = rows.slice(start, start + pageSize);
  const tbody = document.getElementById('drTbody');

  if (!rows.length) {
    tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:var(--text-muted)">No results match the current filters.</td></tr>';
    document.getElementById('drPager').innerHTML    = '';
    document.getElementById('drSummary').textContent = buildSummary(rows);
    return;
  }

  tbody.innerHTML = page.map(row => {
    const isCis      = row.type === 'system';
    const resultBadge = resultBadgeHtml(row.result);

    // Scope column: interface + vdom for interface rows; "device-level" for CIS
    let scopeHtml;
    if (isCis) {
      scopeHtml = '<span style="color:var(--text-muted);font-size:.82rem">device-level</span>';
    } else {
      const vdom = row.vdom && row.vdom !== 'root' ? row.vdom : '';
      scopeHtml  =
        `<code>${esc(row.interface)}</code>` +
        (vdom ? ` <span class="dr-vdom-badge">${esc(vdom)}</span>` : '');
    }

    // Detail column: protocol badges for interface rows, plain text for CIS
    let detailHtml;
    if (isCis) {
      detailHtml = row.detail
        ? `<span style="font-size:.84rem">${esc(row.detail)}</span>`
        : '<span style="color:var(--text-muted)">—</span>';
    } else {
      const visProtos = visibleProtocols(row);
      detailHtml = protoListHtml(visProtos);
    }

    // Row highlight
    let rowStyle = '';
    const r = (row.result || '').toUpperCase();
    if (r === 'INSECURE' || r === 'FAIL') rowStyle = ' style="background:#fff8f8"';
    else if (r === 'PASS')                rowStyle = ' style="background:#f6fff8"';

    return `<tr${rowStyle}>
      <td><strong>${esc(row.device)}</strong></td>
      <td style="font-size:.85rem">${esc(row.check || '')}</td>
      <td>${resultBadge}</td>
      <td>${scopeHtml}</td>
      <td><code>${esc(row.ip || '—')}</code></td>
      <td>${detailHtml}</td>
    </tr>`;
  }).join('');

  document.getElementById('drSummary').textContent = buildSummary(rows);
  renderPager(rows.length);
}

function buildSummary(rows) {
  const fail    = rows.filter(r => r.result === 'FAIL' || r.result === 'INSECURE').length;
  const pass    = rows.filter(r => r.result === 'PASS').length;
  const missing = rows.filter(r => r.result === 'CONFIG_MISSING').length;
  const devices = new Set(rows.map(r => r.device)).size;
  let s = `${rows.length} finding(s) · ${devices} device(s)`;
  if (fail)    s += ` · ${fail} fail/insecure`;
  if (missing) s += ` · ${missing} config missing`;
  if (pass)    s += ` · ${pass} pass`;
  return s;
}

/* ── Pagination ─────────────────────────────────────────────────────────────── */
function renderPager(total) {
  const pages = Math.ceil(total / pageSize);
  const pager = document.getElementById('drPager');
  if (pages <= 1) { pager.innerHTML = ''; return; }

  const range = [1];
  for (let p = Math.max(2, currentPage - 2); p <= Math.min(pages - 1, currentPage + 2); p++) range.push(p);
  if (!range.includes(pages)) range.push(pages);

  let html = `<button ${currentPage === 1 ? 'disabled' : ''} data-page="1">&laquo;</button>`;
  html    += `<button ${currentPage === 1 ? 'disabled' : ''} data-page="${currentPage - 1}">&lsaquo;</button>`;
  let prev = 0;
  range.forEach(p => {
    if (p - prev > 1) html += `<span class="pagination-ellipsis">…</span>`;
    html += `<button ${p === currentPage ? 'class="active"' : ''} data-page="${p}">${p}</button>`;
    prev = p;
  });
  html += `<button ${currentPage === pages ? 'disabled' : ''} data-page="${currentPage + 1}">&rsaquo;</button>`;
  html += `<button ${currentPage === pages ? 'disabled' : ''} data-page="${pages}">&raquo;</button>`;
  pager.innerHTML = html;
}

/* ── Exports ────────────────────────────────────────────────────────────────── */
function exportCsv() {
  const rows = filtered();
  if (!rows.length) { showError('No filtered results to export.'); return; }
  clearError();
  const meta = lastMeta || {};
  const header = [
    `ADOM,${meta.adom || ''}`,
    `Date/Time,${meta.run_at || ''}`,
    `Devices Reviewed,${meta.device_count ?? ''}`,
    `Total Findings,${rows.length}`,
    '',
  ].join('\r\n');
  const cols = ['Device', 'Check', 'Result', 'Interface/Scope', 'IP Address', 'Detail/Protocols'];
  const body = [cols.join(','), ...rows.map(r => [
    r.device,
    r.check || '',
    r.result || '',
    r.type === 'system' ? 'device-level' : r.interface,
    r.ip || '',
    r.type === 'system'
      ? (r.detail || '')
      : visibleProtocols(r).map(p => p.name).join(' '),
  ].map(v => `"${String(v ?? '').replace(/"/g, '""')}"`).join(','))].join('\r\n');
  download(`device-review-${meta.adom || 'export'}.csv`, header + body, 'text/csv');
}

function exportJson() {
  const meta = lastMeta || {};
  const rows = filtered();
  if (!rows.length) { showError('No filtered results to export.'); return; }
  clearError();
  download(
    `device-review-${meta.adom || 'export'}.json`,
    JSON.stringify({ meta, rows }, null, 2),
    'application/json',
  );
}

function exportPdf() {
  const rows = filtered();
  if (!rows.length) { showError('No filtered results to export.'); return; }
  clearError();
  const meta        = lastMeta || {};
  const ts          = meta.run_at || new Date().toLocaleString();
  const title       = `Device Review — ADOM: ${meta.adom || ''}`;
  const failCount   = rows.filter(r => r.result === 'FAIL' || r.result === 'INSECURE').length;
  const passCount   = rows.filter(r => r.result === 'PASS').length;
  const deviceCount = new Set(rows.map(r => r.device)).size;

  const resultCell = result => {
    switch ((result || '').toUpperCase()) {
      case 'INSECURE':      return '<span style="color:#b91c1c;font-weight:700;background:#fee2e2;padding:0 5px;border-radius:2px">INSECURE</span>';
      case 'FAIL':          return '<span style="color:#b91c1c;font-weight:700;background:#fee2e2;padding:0 5px;border-radius:2px">FAIL</span>';
      case 'WARN':          return '<span style="color:#92400e;background:#fef3c7;padding:0 5px;border-radius:2px">WARN</span>';
      case 'CONFIG_MISSING':return '<span style="color:#92400e;background:#fef3c7;padding:0 5px;border-radius:2px">CONFIG MISSING</span>';
      case 'PASS':          return '<span style="color:#166534;background:#dcfce7;padding:0 5px;border-radius:2px">PASS</span>';
      case 'INFO':          return '<span style="color:#1d4ed8;background:#eff6ff;padding:0 5px;border-radius:2px">INFO</span>';
      default: return esc(result || '?');
    }
  };

  const tableRows = rows.map(r => {
    const rowStyle  = (r.result === 'FAIL' || r.result === 'INSECURE') ? 'background:#fff5f5' : '';
    const scope     = r.type === 'system' ? 'device-level' : r.interface;
    const detailStr = r.type === 'system'
      ? esc(r.detail || '—')
      : visibleProtocols(r).map(p => {
          const s = p.secure === false
            ? 'color:#b91c1c;font-weight:700;background:#fee2e2;padding:0 4px;border-radius:2px;margin:0 1px'
            : p.secure === true
              ? 'color:#166534;font-weight:600;background:#dcfce7;padding:0 4px;border-radius:2px;margin:0 1px'
              : 'color:#374151;background:#f3f4f6;padding:0 4px;border-radius:2px;margin:0 1px';
          return `<span style="${s}">${esc(p.name.toUpperCase())}</span>`;
        }).join('') || '—';
    return `<tr style="${rowStyle}">
      <td>${esc(r.device)}</td>
      <td style="font-size:9px">${esc(r.check || '')}</td>
      <td>${resultCell(r.result)}</td>
      <td><code>${esc(scope)}</code></td>
      <td><code>${esc(r.ip || '—')}</code></td>
      <td>${detailStr}</td>
    </tr>`;
  }).join('');

  const html = `<!DOCTYPE html><html><head><meta charset="UTF-8">
<title>${esc(title)}</title>
<style>
  body{font-family:Arial,sans-serif;font-size:11px;color:#1a2133;margin:1.5cm}
  h1{font-size:16px;margin-bottom:6px}
  .meta{background:#f3f4f6;border-left:4px solid #3b82f6;padding:8px 12px;border-radius:3px;margin-bottom:14px;font-size:10px}
  .meta table{border:none;width:auto}
  .meta td{padding:1px 14px 1px 0;border:none;background:none}
  .meta td:first-child{font-weight:700;color:#1f2937}
  .stats{display:flex;gap:12px;margin-bottom:14px}
  .stat{padding:4px 12px;border-radius:4px;font-size:11px;font-weight:700}
  .stat-total{background:#f3f4f6;color:#374151}
  .stat-fail{background:#fee2e2;color:#991b1b}
  .stat-pass{background:#dcfce7;color:#166534}
  table.main{width:100%;border-collapse:collapse}
  th{background:#eef1f5;text-align:left;padding:5px 8px;font-size:10px;text-transform:uppercase;border-bottom:2px solid #d0d7e2}
  td{padding:4px 8px;border-bottom:1px solid #e5e7eb;vertical-align:middle}
  code{font-family:monospace;font-size:10px}
  @media print{body{margin:.8cm}.stats{display:block}}
</style></head><body>
<h1>${esc(title)}</h1>
<div class="meta">
  <table>
    <tr><td>ADOM</td><td>${esc(meta.adom || '')}</td></tr>
    <tr><td>Date / Time</td><td>${esc(ts)}</td></tr>
    <tr><td>Devices Reviewed</td><td>${meta.device_count ?? 0}</td></tr>
    <tr><td>Devices in Report</td><td>${deviceCount}</td></tr>
    <tr><td>Findings in Report</td><td>${rows.length}</td></tr>
  </table>
</div>
<div class="stats">
  <span class="stat stat-total">Findings: ${rows.length}</span>
  ${failCount ? `<span class="stat stat-fail">&#9888; Fail/Insecure: ${failCount}</span>` : ''}
  ${passCount ? `<span class="stat stat-pass">&#10003; Pass: ${passCount}</span>` : ''}
</div>
<table class="main">
  <thead><tr><th>Device</th><th>Check</th><th>Result</th><th>Interface/Scope</th><th>IP</th><th>Detail / Protocols</th></tr></thead>
  <tbody>${tableRows}</tbody>
</table>
</body></html>`;

  const win = window.open('', '_blank');
  if (win) { win.document.write(html); win.document.close(); win.focus(); win.print(); }
}

function download(filename, content, mime) {
  const a  = document.createElement('a');
  const bl = new Blob([content], { type: mime });
  a.href   = URL.createObjectURL(bl);
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
}

/* ── Event wiring ───────────────────────────────────────────────────────────── */
document.getElementById('drAdom').addEventListener('change', e => onAdomChange(e.target.value));

document.getElementById('drDeviceSearch').addEventListener('input', updateDeviceMatchCount);

document.getElementById('drChecksAll').addEventListener('click', () => {
  document.querySelectorAll('input[name="dr_check"]').forEach(cb => { cb.checked = true; });
  updateParamsPanel();
});

document.getElementById('drChecksNone').addEventListener('click', () => {
  document.querySelectorAll('input[name="dr_check"]').forEach(cb => { cb.checked = false; });
  updateParamsPanel();
});

document.getElementById('drRunBtn').addEventListener('click', runAnalysis);

document.getElementById('drCancelBtn').addEventListener('click', function () {
  _abortRun = true;
  this.disabled    = true;
  this.textContent = 'Cancelling…';
});

document.getElementById('drFilter').addEventListener('input', e => {
  filterText  = e.target.value;
  currentPage = 1;
  renderTable();
});

document.getElementById('drDeviceFilter').addEventListener('change', e => {
  filterDevice = e.target.value;
  currentPage  = 1;
  renderTable();
});

document.getElementById('drResultFilter').addEventListener('change', e => {
  filterResult = e.target.value;
  currentPage  = 1;
  renderTable();
});

document.getElementById('drPageSize').addEventListener('change', e => {
  pageSize    = parseInt(e.target.value, 10);
  currentPage = 1;
  renderTable();
});

document.getElementById('drPager').addEventListener('click', e => {
  const btn = e.target.closest('button[data-page]');
  if (!btn || btn.disabled) return;
  currentPage = parseInt(btn.dataset.page, 10);
  renderTable();
});

document.getElementById('drProtoAll').addEventListener('click',  () => setAllProtos(true));
document.getElementById('drProtoNone').addEventListener('click', () => setAllProtos(false));

document.getElementById('drExportCsv').addEventListener('click', exportCsv);
document.getElementById('drExportJson').addEventListener('click', exportJson);
document.getElementById('drExportPdf').addEventListener('click', exportPdf);

// Update params panel whenever a check checkbox changes
document.getElementById('drChecks').addEventListener('change', updateParamsPanel);

/* ── Init ───────────────────────────────────────────────────────────────────── */
loadAdoms();
updateParamsPanel();
