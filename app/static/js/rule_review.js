'use strict';

/* ── Utilities ─────────────────────────────────────────────────────────────── */
function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

/* ── State ─────────────────────────────────────────────────────────────────── */
let flows     = [];   // [{src, dst, service, comment}, ...]
let packages  = [];   // [{adom, name, path}, ...]
let results   = [];   // analysis results from server
let pkgPaths  = {};   // package display name → path

/* ── ADOM loader ────────────────────────────────────────────────────────────── */
async function loadAdoms() {
  const sel = document.getElementById('rrAdom');
  try {
    const resp = await fetch('/api/rule-review/adoms');
    if (resp.status === 401) { location.href = '/login'; return; }
    const adoms = await resp.json();
    if (!Array.isArray(adoms)) return;
    adoms.forEach(a => {
      const opt = document.createElement('option');
      opt.value = a; opt.textContent = a;
      sel.appendChild(opt);
    });
  } catch (_) {}
}

async function loadPackages(adom) {
  const sel = document.getElementById('rrPackage');
  sel.innerHTML = '<option value="">Loading…</option>';
  sel.disabled = true;
  pkgPaths = {};
  document.getElementById('rrAddPkgBtn').disabled = true;
  try {
    const resp = await fetch(`/api/rule-review/adoms/${encodeURIComponent(adom)}/packages`);
    if (resp.status === 401) { location.href = '/login'; return; }
    const pkgs = await resp.json();
    sel.innerHTML = '<option value="">— select package —</option>';
    if (Array.isArray(pkgs)) {
      pkgs.forEach(p => {
        pkgPaths[p.name] = p.path || p.name;
        const opt = document.createElement('option');
        opt.value = p.name; opt.textContent = p.name;
        sel.appendChild(opt);
      });
    }
    sel.disabled = false;
  } catch (_) {
    sel.innerHTML = '<option value="">Failed to load</option>';
  }
}

/* ── Zone-script status ─────────────────────────────────────────────────────── */
async function checkZoneStatus() {
  try {
    const resp = await fetch('/api/rule-review/zone-status');
    const data = await resp.json();
    const badge = document.getElementById('rrZoneStatus');
    badge.style.display = '';
    if (data.available) {
      badge.textContent = '✓ Zone policy database connected';
      badge.className   = 'rr-zone-badge rr-zone-ok';
    } else {
      badge.textContent = '⚠ Zone policy database not available';
      badge.className   = 'rr-zone-badge rr-zone-warn';
    }
  } catch (_) {}
}

/* ── Flow management ────────────────────────────────────────────────────────── */
function renderFlows() {
  const tbody = document.getElementById('rrFlowTbody');
  const wrap  = document.getElementById('rrFlowTableWrap');
  if (!flows.length) { wrap.style.display = 'none'; tbody.innerHTML = ''; updateReviewBtn(); return; }
  wrap.style.display = '';
  tbody.innerHTML = flows.map((f, i) => `
    <tr>
      <td style="color:var(--text-muted);font-size:.8rem">${i + 1}</td>
      <td><code>${esc(f.src)}</code></td>
      <td><code>${esc(f.dst)}</code></td>
      <td>${esc(f.service) || '<span class="text-muted">—</span>'}</td>
      <td style="color:var(--text-muted);font-size:.82rem">${esc(f.comment) || ''}</td>
      <td><button class="btn btn-sm btn-ghost rr-remove-btn" data-type="flow" data-idx="${i}" title="Remove">&#10005;</button></td>
    </tr>`).join('');
  updateReviewBtn();
}

function splitIPs(raw) {
  return raw.split(/[\n,]+/).map(s => s.trim()).filter(Boolean);
}

function addFlow(srcRaw, dstRaw, service, comment) {
  const srcs = splitIPs(srcRaw);
  const dsts = splitIPs(dstRaw);
  service = service.trim();
  comment = comment.trim();
  if (!srcs.length || !dsts.length) return;
  for (const src of srcs) {
    for (const dst of dsts) {
      flows.push({ src, dst, service, comment });
    }
  }
  renderFlows();
  clearFlowInputs();
}

function clearFlowInputs() {
  ['rrSrc','rrDst','rrSvc','rrComment'].forEach(id => {
    document.getElementById(id).value = '';
  });
}

/* ── Package management ─────────────────────────────────────────────────────── */
function renderPackages() {
  const tbody = document.getElementById('rrPkgTbody');
  const wrap  = document.getElementById('rrPkgTableWrap');
  if (!packages.length) { wrap.style.display = 'none'; tbody.innerHTML = ''; updateReviewBtn(); return; }
  wrap.style.display = '';
  tbody.innerHTML = packages.map((p, i) => `
    <tr>
      <td style="color:var(--text-muted);font-size:.8rem">${i + 1}</td>
      <td>${esc(p.adom)}</td>
      <td>${esc(p.name)}</td>
      <td><button class="btn btn-sm btn-ghost rr-remove-btn" data-type="pkg" data-idx="${i}" title="Remove">&#10005;</button></td>
    </tr>`).join('');
  updateReviewBtn();
}

function addPackage() {
  const adom    = document.getElementById('rrAdom').value;
  const pkgName = document.getElementById('rrPackage').value;
  if (!adom || !pkgName) return;
  const path = pkgPaths[pkgName] || pkgName;
  if (packages.some(p => p.adom === adom && p.path === path)) return;
  packages.push({ adom, name: pkgName, path });
  renderPackages();
}

function updateReviewBtn() {
  document.getElementById('rrReviewBtn').disabled = !(flows.length && packages.length);
}

/* ── CSV / XLSX import ──────────────────────────────────────────────────────── */
async function handleImport(file) {
  const statusEl = document.getElementById('rrImportStatus');
  statusEl.textContent = 'Parsing…';
  const fd = new FormData();
  fd.append('file', file);
  try {
    const resp = await fetch('/api/rule-review/parse-import', { method: 'POST', body: fd });
    const data = await resp.json();
    if (!resp.ok) { statusEl.textContent = data.error || 'Import failed'; return; }
    const imported = data.rows || [];
    imported.forEach(r => flows.push(r));
    renderFlows();
    const errs = data.errors || [];
    statusEl.textContent = `Imported ${imported.length} row${imported.length !== 1 ? 's' : ''}` +
      (errs.length ? ` (${errs.length} error${errs.length !== 1 ? 's' : ''}: ${errs[0]})` : '');
  } catch (e) {
    statusEl.textContent = 'Import error: ' + e.message;
  }
  document.getElementById('rrImportFile').value = '';
}

/* ── Analysis ───────────────────────────────────────────────────────────────── */
async function runReview() {
  const errEl = document.getElementById('rrError');
  errEl.style.display = 'none';
  document.getElementById('rrResults').style.display   = 'none';
  document.getElementById('rrCliPanel').style.display  = 'none';
  document.getElementById('rrReviewBtn').disabled = true;
  document.getElementById('rrRunning').style.display   = '';
  checkZoneStatus();

  try {
    const resp = await fetch('/api/rule-review/analyze', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ flows, packages }),
    });
    const data = await resp.json();
    if (!resp.ok) { showError(data.error || 'Analysis failed.'); return; }

    results = data.results || [];
    renderResults(data.zone_available);
    document.getElementById('rrResults').style.display = '';
    document.getElementById('rrStatusLine').textContent = `Last run: ${new Date().toLocaleString()}`;
  } catch (e) {
    showError(e.message);
  } finally {
    document.getElementById('rrReviewBtn').disabled = !(flows.length && packages.length);
    document.getElementById('rrRunning').style.display = 'none';
  }
}

function showError(msg) {
  const el = document.getElementById('rrError');
  el.textContent = msg;
  el.style.display = '';
}

/* ── Verdict / zone helpers ─────────────────────────────────────────────────── */
const VERDICT_LABEL = {
  PERMITTED:         'PERMITTED',
  EXPLICITLY_DENIED: 'EXPLICITLY DENIED',
  MODIFIABLE:        'MODIFIABLE',
  NEW_RULE_NEEDED:   'NEW RULE NEEDED',
};

function verdictClass(v) {
  return { PERMITTED: 'ALLOWED', EXPLICITLY_DENIED: 'BLOCKED',
           MODIFIABLE: 'UNKNOWN', NEW_RULE_NEEDED: 'UNKNOWN' }[v] || 'UNKNOWN';
}

function zoneClass(v) {
  return { ALLOWED: 'ALLOWED', BLOCKED: 'BLOCKED',
           UNKNOWN: 'UNKNOWN', UNAVAILABLE: 'UNKNOWN', ERROR: 'BLOCKED' }[v] || 'UNKNOWN';
}

function verdictLabel(v) {
  return VERDICT_LABEL[v] || v;
}

function zoneLabel(v) {
  if (v === 'UNKNOWN') return 'NO RULE';
  return v;
}

/* ── Governing rule HTML (matches zone-script style) ───────────────────────── */
function ruleRowHtml(p) {
  const svc = p.services && p.services.length
    ? `<span class="rr-rule-svc">[${esc(p.services.join(', '))}]</span>` : '';
  const sev = p.severity ? `<span class="rr-rule-sev">(${esc(p.severity)})</span>` : '';
  return `<div class="rr-rule-row">
    <span class="rr-rule-set">[${esc(p.policy_set || '')}]</span>
    ${esc(p.matched_from_zone || p.from_zone || '')} → ${esc(p.matched_to_zone || p.to_zone || '')}
    &nbsp;|&nbsp;
    <strong>${esc(p.access_type || '')}</strong>
    ${svc} ${sev}
  </div>`;
}

/* ── Path-relevance badge ───────────────────────────────────────────────────── */
function pathBadgeHtml(r) {
  const ip = r.path_in_path;
  if (ip === true)  return `<span class="rr-path-badge rr-path-yes">✓ In Path</span>`;
  if (ip === false) return `<span class="rr-path-badge rr-path-no">⚠ Not In Path</span>`;
  return `<span class="rr-path-badge rr-path-unknown">? Path Unknown</span>`;
}

/* ── Results rendering — zone-script card style ─────────────────────────────── */
function renderResults(zoneAvail) {
  const container = document.getElementById('rrResultCards');
  container.innerHTML = '';

  // Summary counts
  const vc = { PERMITTED: 0, EXPLICITLY_DENIED: 0, MODIFIABLE: 0, NEW_RULE_NEEDED: 0 };
  const zc = { ALLOWED: 0, BLOCKED: 0, UNKNOWN: 0 };
  results.forEach(r => {
    if (vc[r.verdict] !== undefined) vc[r.verdict]++;
    if (zc[r.zone_verdict] !== undefined) zc[r.zone_verdict]++;
  });

  const bar = document.getElementById('rrSummaryBar');
  let barHtml = `<span class="rr-summary-chip">${results.length} result${results.length !== 1 ? 's' : ''}</span>`;
  if (vc.PERMITTED)         barHtml += `<span class="rr-summary-chip chip-allowed">${vc.PERMITTED} Permitted</span>`;
  if (vc.NEW_RULE_NEEDED)   barHtml += `<span class="rr-summary-chip chip-unknown">${vc.NEW_RULE_NEEDED} New Rule Needed</span>`;
  if (vc.MODIFIABLE)        barHtml += `<span class="rr-summary-chip chip-warn">${vc.MODIFIABLE} Modifiable</span>`;
  if (vc.EXPLICITLY_DENIED) barHtml += `<span class="rr-summary-chip chip-blocked">${vc.EXPLICITLY_DENIED} Explicitly Denied</span>`;
  if (zoneAvail) {
    if (zc.BLOCKED)  barHtml += `<span class="rr-summary-chip chip-blocked">Zone: ${zc.BLOCKED} Blocked</span>`;
    if (zc.UNKNOWN)  barHtml += `<span class="rr-summary-chip chip-warn">Zone: ${zc.UNKNOWN} No Rule</span>`;
  }
  bar.innerHTML = barHtml;

  // One card per result
  results.forEach((r, idx) => {
    const vClass = verdictClass(r.verdict);
    const vLabel = verdictLabel(r.verdict);
    const zClass = zoneClass(r.zone_verdict);
    const zLabel = zoneLabel(r.zone_verdict);

    // Flow header
    const svcBadge = r.service
      ? `<span class="rr-flow-svc">${esc(r.service)}</span>` : '';
    const pathBadge = pathBadgeHtml(r);

    // Zone section
    let zoneHtml = '';
    if (r.zone_available) {
      const governing = r.zone_governing || [];
      const allPols   = r.zone_all_policies || [];
      let govHtml = '';
      if (governing.length) {
        govHtml = `<div class="rr-card-subsection">
          <div class="rr-subsection-label">Governing rule:</div>
          ${governing.map(ruleRowHtml).join('')}
        </div>`;
      } else if (r.zone_verdict === 'UNKNOWN') {
        govHtml = `<div class="rr-no-rule">No policy rule covers this zone pair — treat as implicitly blocked.</div>`;
      }

      let allPolsHtml = '';
      if (allPols.length > governing.length) {
        allPolsHtml = `<details class="rr-details">
          <summary class="rr-details-summary">All matching rules (${allPols.length})</summary>
          <div class="rr-details-body">${allPols.map(ruleRowHtml).join('')}</div>
        </details>`;
      }

      zoneHtml = `<div class="rr-card-zone-block">
        <div class="rr-card-row rr-zone-header">
          <span class="rr-zone-block-label">Zone Policy</span>
          <span class="verdict-${zClass} rr-zone-verdict">${esc(zLabel)}</span>
        </div>
        <div class="rr-card-row rr-zone-zones">
          <span>&#8599; Src zones: <strong>${esc((r.zone_src || []).join(', ') || '(none matched)')}</strong></span><br>
          <span>&#8600; Dst zones: <strong>${esc((r.zone_dst || []).join(', ') || '(none matched)')}</strong></span>
        </div>
        ${govHtml}
        ${allPolsHtml}
      </div>`;
    } else {
      zoneHtml = `<div class="rr-card-zone-block rr-zone-na">
        <span class="rr-zone-block-label">Zone Policy</span>
        <span class="text-muted" style="font-size:.8rem;margin-left:.5rem">not available</span>
      </div>`;
    }

    // FortiGate policy section
    let fgtHtml = '';
    if (r.matching_rules && r.matching_rules.length) {
      fgtHtml += `<div class="rr-card-subsection">
        <div class="rr-subsection-label">Matching rules:</div>
        ${r.matching_rules.map(m => `
        <div class="rr-rule-row">
          <span class="rr-rule-set">ID ${esc(m.id)}</span>
          ${m.name ? esc(m.name) : '<em>unnamed</em>'}
          &nbsp;|&nbsp;
          <strong style="color:${m.action === 'accept' ? 'var(--success)' : 'var(--danger)'}">${esc(m.action)}</strong>
        </div>`).join('')}
      </div>`;
    }
    if (r.modifiable_rules && r.modifiable_rules.length) {
      fgtHtml += `<div class="rr-card-subsection">
        <div class="rr-subsection-label">Modifiable rules:</div>
        ${r.modifiable_rules.map(m => `
        <div class="rr-rule-row">
          <span class="rr-rule-set">ID ${esc(m.id)}</span>
          ${m.name ? esc(m.name) : '<em>unnamed</em>'}
          &nbsp;|&nbsp; <span style="color:var(--warning)">${esc(m.suggestion)}</span>
        </div>`).join('')}
      </div>`;
    }

    // Path check section
    let pathHtml = '';
    if (r.path_notes && r.path_notes.length) {
      const routeInfo = [];
      if (r.path_src_iface) routeInfo.push(`Src → ${esc(r.path_src_iface)}`);
      if (r.path_dst_iface) routeInfo.push(`Dst → ${esc(r.path_dst_iface)}`);
      pathHtml = `<div class="rr-card-subsection rr-path-section rr-path-${r.path_in_path === true ? 'yes' : r.path_in_path === false ? 'no' : 'unknown'}">
        <div class="rr-subsection-label">Path Analysis (${esc(r.path_confidence || 'low')} confidence):</div>
        <div class="rr-path-note">${esc(r.path_notes[0] || '')}</div>
        ${routeInfo.length ? `<div class="rr-path-route">${routeInfo.join('  |  ')}</div>` : ''}
      </div>`;
    }

    // Notes
    const policyNotes = (r.notes || []).filter(n =>
      !n.startsWith('⚠ ZONE') && !n.startsWith('Zone policy:') &&
      !n.startsWith('⚠ PATH') && !n.startsWith('✓ PATH')
    );
    const notesHtml = policyNotes.length
      ? `<div class="rr-card-subsection">
          ${policyNotes.map(n => `<div class="rr-note">${esc(n)}</div>`).join('')}
        </div>` : '';

    const card = document.createElement('div');
    card.className = `rr-result-card result-card-${vClass}`;
    card.innerHTML = `
      <div class="rr-card-header">
        <div class="rr-card-flow">
          <code>${esc(r.src)}</code>
          <span class="rr-arrow">→</span>
          <code>${esc(r.dst)}</code>
          ${svcBadge}
          <span class="rr-pkg-label">${esc(r.adom)} / ${esc(r.pkg_name)}</span>
        </div>
        <div class="rr-card-badges">
          ${pathBadge}
          <span class="verdict-${vClass}">${esc(vLabel)}</span>
          <button class="btn btn-sm btn-secondary rr-detail-btn" data-idx="${idx}" title="Full details">⋯</button>
        </div>
      </div>

      ${zoneHtml}

      <div class="rr-card-fgt-block">
        <div class="rr-zone-block-label" style="margin-bottom:.4rem">FortiGate Policy</div>
        ${fgtHtml || '<div class="rr-no-rule">No matching rules found.</div>'}
        ${notesHtml}
        ${pathHtml}
      </div>
    `;
    container.appendChild(card);
  });

  if (!results.length) {
    container.innerHTML = '<div class="empty-state" style="padding:1.5rem">No results returned.</div>';
  }

  // CLI panel
  const cliSnippets = results.filter(r => r.fortios_cli).map(r => r.fortios_cli);
  const cliPanel  = document.getElementById('rrCliPanel');
  const cliOutput = document.getElementById('rrCliOutput');
  if (cliSnippets.length) {
    cliOutput.textContent = cliSnippets.join('\n\n' + '─'.repeat(60) + '\n\n');
    cliPanel.style.display = '';
  } else {
    cliPanel.style.display = 'none';
  }
}

/* ── Detail modal ───────────────────────────────────────────────────────────── */
function showDetail(idx) {
  const r = results[idx];
  if (!r) return;

  const vClass = verdictClass(r.verdict);
  const vLabel = verdictLabel(r.verdict);
  const zClass = zoneClass(r.zone_verdict);
  const zLabel = zoneLabel(r.zone_verdict);

  let html = `
    <div class="rr-detail-grid">
      <div class="rr-detail-row"><span class="rr-detail-label">Source</span><code>${esc(r.src)}</code></div>
      <div class="rr-detail-row"><span class="rr-detail-label">Destination</span><code>${esc(r.dst)}</code></div>
      <div class="rr-detail-row"><span class="rr-detail-label">Service</span>${esc(r.service) || '<em>any</em>'}</div>
      <div class="rr-detail-row"><span class="rr-detail-label">ADOM</span>${esc(r.adom)}</div>
      <div class="rr-detail-row"><span class="rr-detail-label">Package</span>${esc(r.pkg_name)}</div>
      ${r.device ? `<div class="rr-detail-row"><span class="rr-detail-label">Device</span>${esc(r.device)}</div>` : ''}
      <div class="rr-detail-row"><span class="rr-detail-label">FGT Verdict</span>
        <span class="verdict-${vClass}" style="font-weight:700">${esc(vLabel)}</span></div>
    </div>`;

  // Zone policy
  html += `<div class="rr-detail-section">
    <div class="rr-detail-section-title">Zone Segmentation Policy
      ${r.zone_available ? `<span class="verdict-${zClass}" style="margin-left:.5rem;font-weight:700">${esc(zLabel)}</span>` : '<span class="text-muted" style="margin-left:.5rem;font-size:.8rem">not available</span>'}
    </div>`;
  if (r.zone_available) {
    html += `<div class="rr-detail-row"><span class="rr-detail-label">Source Zones</span>
        ${esc((r.zone_src || []).join(', ') || '(none matched)')}</div>
      <div class="rr-detail-row"><span class="rr-detail-label">Dest Zones</span>
        ${esc((r.zone_dst || []).join(', ') || '(none matched)')}</div>`;
    if (r.zone_governing && r.zone_governing.length) {
      html += `<div style="margin-top:.5rem"><div class="rr-subsection-label">Governing rule:</div>
        ${r.zone_governing.map(ruleRowHtml).join('')}</div>`;
    } else if (r.zone_verdict === 'UNKNOWN') {
      html += `<div class="rr-no-rule">No policy rule covers this zone pair — treat as implicitly blocked.</div>`;
    }
    const allPols = r.zone_all_policies || [];
    if (allPols.length > (r.zone_governing || []).length) {
      html += `<details class="rr-details" style="margin-top:.4rem">
        <summary class="rr-details-summary">All matching rules (${allPols.length})</summary>
        <div class="rr-details-body">${allPols.map(ruleRowHtml).join('')}</div>
      </details>`;
    }
  }
  html += `</div>`;

  // Path analysis
  html += `<div class="rr-detail-section">
    <div class="rr-detail-section-title">Path Analysis</div>`;
  if (r.path_in_path === true)  html += `<div style="color:var(--success);font-weight:600;margin-bottom:.35rem">✓ Device is in the traffic path</div>`;
  if (r.path_in_path === false) html += `<div style="color:var(--warning);font-weight:600;margin-bottom:.35rem">⚠ Device may NOT be in the traffic path — proceed with caution</div>`;
  if (r.path_in_path === null)  html += `<div style="color:var(--text-muted);margin-bottom:.35rem">Path data unavailable</div>`;

  if (r.path_src_iface || r.path_src_route) {
    html += `<div class="rr-detail-row"><span class="rr-detail-label">Src Interface</span>${esc(r.path_src_iface || '—')}</div>`;
    if (r.path_src_route) {
      html += `<div class="rr-detail-row"><span class="rr-detail-label">Src Route</span>
        ${esc(r.path_src_route.network)} via ${esc(r.path_src_route.gateway || 'direct')} (${esc(r.path_src_route.interface || '?')})</div>`;
    }
  }
  if (r.path_dst_iface || r.path_dst_route) {
    html += `<div class="rr-detail-row"><span class="rr-detail-label">Dst Interface</span>${esc(r.path_dst_iface || '—')}</div>`;
    if (r.path_dst_route) {
      html += `<div class="rr-detail-row"><span class="rr-detail-label">Dst Route</span>
        ${esc(r.path_dst_route.network)} via ${esc(r.path_dst_route.gateway || 'direct')} (${esc(r.path_dst_route.interface || '?')})</div>`;
    }
  }
  (r.path_notes || []).forEach(n => {
    html += `<div class="rr-note" style="margin-top:.25rem">${esc(n)}</div>`;
  });
  html += `</div>`;

  // FortiGate matching rules
  if (r.matching_rules && r.matching_rules.length) {
    html += `<div class="rr-detail-section">
      <div class="rr-detail-section-title">Matching Rules</div>
      <table class="data-table" style="font-size:.82rem">
        <thead><tr><th>ID</th><th>Name</th><th>Action</th></tr></thead>
        <tbody>${r.matching_rules.map(m => `<tr>
          <td>${esc(m.id)}</td>
          <td>${esc(m.name || '—')}</td>
          <td style="font-weight:600;color:${m.action==='accept'?'var(--success)':'var(--danger)'}">${esc(m.action)}</td>
        </tr>`).join('')}</tbody>
      </table></div>`;
  }

  if (r.modifiable_rules && r.modifiable_rules.length) {
    html += `<div class="rr-detail-section">
      <div class="rr-detail-section-title">Rules That Could Be Modified</div>
      <table class="data-table" style="font-size:.82rem">
        <thead><tr><th>ID</th><th>Name</th><th>Suggestion</th></tr></thead>
        <tbody>${r.modifiable_rules.map(m => `<tr>
          <td>${esc(m.id)}</td><td>${esc(m.name || '—')}</td>
          <td style="color:var(--warning)">${esc(m.suggestion)}</td>
        </tr>`).join('')}</tbody>
      </table></div>`;
  }

  // All notes
  const allNotes = r.notes || [];
  if (allNotes.length) {
    html += `<div class="rr-detail-section">
      <div class="rr-detail-section-title">All Notes</div>
      ${allNotes.map(n => `<div class="rr-note">${esc(n)}</div>`).join('')}
    </div>`;
  }

  // CLI
  if (r.fortios_cli) {
    html += `<div class="rr-detail-section">
      <div class="rr-detail-section-title">FortiOS CLI</div>
      <pre class="rr-cli-block" style="margin-top:.5rem">${esc(r.fortios_cli)}</pre>
    </div>`;
  }

  document.getElementById('rrModalTitle').textContent =
    `${r.src} → ${r.dst}${r.service ? ' : ' + r.service : ''} — ${r.pkg_name}`;
  document.getElementById('rrModalBody').innerHTML = html;
  document.getElementById('rrDetailModal').style.display = '';
}

/* ── Clear all ──────────────────────────────────────────────────────────────── */
function clearAll() {
  flows    = [];
  packages = [];
  results  = [];
  renderFlows();
  renderPackages();
  document.getElementById('rrResults').style.display  = 'none';
  document.getElementById('rrCliPanel').style.display = 'none';
  document.getElementById('rrError').style.display    = 'none';
  document.getElementById('rrStatusLine').textContent = '';
  document.getElementById('rrZoneStatus').style.display = 'none';
  clearFlowInputs();
}

/* ── CLI copy / download ────────────────────────────────────────────────────── */
function copyCli() {
  const text = document.getElementById('rrCliOutput').textContent;
  navigator.clipboard.writeText(text).catch(() => {});
}

function downloadCli() {
  const text = document.getElementById('rrCliOutput').textContent;
  const a  = document.createElement('a');
  const bl = new Blob([text], { type: 'text/plain' });
  a.href   = URL.createObjectURL(bl);
  a.download = 'rule_review_cli.txt';
  a.click();
  URL.revokeObjectURL(a.href);
}

/* ── Event wiring ───────────────────────────────────────────────────────────── */
document.getElementById('rrAdom').addEventListener('change', function () {
  if (this.value) loadPackages(this.value);
  else {
    const sel = document.getElementById('rrPackage');
    sel.innerHTML = '<option value="">— select package —</option>';
    sel.disabled = true;
    document.getElementById('rrAddPkgBtn').disabled = true;
  }
});

document.getElementById('rrPackage').addEventListener('change', function () {
  document.getElementById('rrAddPkgBtn').disabled = !this.value;
});

document.getElementById('rrAddFlowBtn').addEventListener('click', () => {
  addFlow(
    document.getElementById('rrSrc').value,
    document.getElementById('rrDst').value,
    document.getElementById('rrSvc').value,
    document.getElementById('rrComment').value,
  );
});

document.getElementById('rrComment').addEventListener('keydown', e => {
  if (e.key === 'Enter') document.getElementById('rrAddFlowBtn').click();
});

document.getElementById('rrAddPkgBtn').addEventListener('click', addPackage);
document.getElementById('rrReviewBtn').addEventListener('click', runReview);
document.getElementById('rrClearBtn').addEventListener('click', clearAll);
document.getElementById('rrCopyCliBtn').addEventListener('click', copyCli);
document.getElementById('rrDownloadCliBtn').addEventListener('click', downloadCli);

document.getElementById('rrModalClose').addEventListener('click', () => {
  document.getElementById('rrDetailModal').style.display = 'none';
});
document.getElementById('rrDetailModal').addEventListener('click', e => {
  if (e.target === document.getElementById('rrDetailModal'))
    document.getElementById('rrDetailModal').style.display = 'none';
});

document.getElementById('rrFlowTbody').addEventListener('click', e => {
  const btn = e.target.closest('.rr-remove-btn');
  if (!btn || btn.dataset.type !== 'flow') return;
  flows.splice(parseInt(btn.dataset.idx, 10), 1);
  renderFlows();
});

document.getElementById('rrPkgTbody').addEventListener('click', e => {
  const btn = e.target.closest('.rr-remove-btn');
  if (!btn || btn.dataset.type !== 'pkg') return;
  packages.splice(parseInt(btn.dataset.idx, 10), 1);
  renderPackages();
});

document.getElementById('rrResultCards').addEventListener('click', e => {
  const btn = e.target.closest('.rr-detail-btn');
  if (btn) showDetail(parseInt(btn.dataset.idx, 10));
});

document.getElementById('rrImportFile').addEventListener('change', function () {
  if (this.files && this.files[0]) handleImport(this.files[0]);
});

/* ── Init ───────────────────────────────────────────────────────────────────── */
loadAdoms();
checkZoneStatus();
document.getElementById('rrZoneStatus').style.display = '';
