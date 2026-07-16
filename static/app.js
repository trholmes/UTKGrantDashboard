/* UTKGrantDashboard front-end. Vanilla JS, no external dependencies.
   All rendering is DOM-built (no innerHTML with data) so names from the
   CSVs can never inject markup. */
'use strict';

let DATA = null;   // payload from /api/data
let CFG = null;    // { people:[], assignments:[], overrides:{} }
let saveTimer = null;

/* ---------- tiny DOM + format helpers ---------- */

const $ = (sel) => document.querySelector(sel);

function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === 'class') node.className = v;
    else if (k === 'dataset') Object.assign(node.dataset, v);
    else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
    else if (v !== undefined && v !== null && v !== false) node.setAttribute(k, v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined) continue;
    node.append(c.nodeType ? c : document.createTextNode(c));
  }
  return node;
}

const fmtUSD = new Intl.NumberFormat('en-US',
  { style: 'currency', currency: 'USD', maximumFractionDigits: 0 });
const fmt$ = (v) => fmtUSD.format(Math.round(v));
const fmtK = (v) => {
  const a = Math.abs(v), sign = v < 0 ? '-' : '';
  if (a >= 1e6) return sign + '$' + (a / 1e6).toFixed(1) + 'M';
  if (a >= 1000) return sign + '$' + Math.round(a / 1000) + 'k';
  return sign + '$' + Math.round(a);
};
const fmtPct = (v) => (v * 100).toFixed(0) + '%';

const MONTH_NAMES = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
function fmtMonth(m) {  // '2026-07' -> 'Jul 2026'
  if (!m) return '—';
  return MONTH_NAMES[+m.slice(5, 7) - 1] + ' ' + m.slice(0, 4);
}
function monthAdd(m, n) {
  let y = +m.slice(0, 4), mo = +m.slice(5, 7) - 1 + n;
  y += Math.floor(mo / 12); mo = ((mo % 12) + 12) % 12;
  return `${y}-${String(mo + 1).padStart(2, '0')}`;
}
function monthDiff(a, b) {  // whole months from a to b
  return (+b.slice(0, 4) - +a.slice(0, 4)) * 12 + (+b.slice(5, 7) - +a.slice(5, 7));
}
function monthRange(a, b) {
  const out = [];
  for (let m = a; m <= b; m = monthAdd(m, 1)) out.push(m);
  return out;
}
const uid = () => 'id' + Math.random().toString(36).slice(2, 9);

/* ---------- tooltip ---------- */

const tooltip = () => $('#tooltip');
function showTip(text, x, y) {
  const t = tooltip();
  t.textContent = text;
  t.hidden = false;
  const pad = 12;
  t.style.left = Math.min(x + pad, window.innerWidth - t.offsetWidth - 8) + 'px';
  t.style.top = (y - t.offsetHeight - pad < 0 ? y + pad : y - t.offsetHeight - pad) + 'px';
}
function hideTip() { tooltip().hidden = true; }

/* ---------- data loading & config ---------- */

async function load() {
  try {
    const res = await fetch('/api/data');
    const payload = await res.json();
    if (payload.error) throw new Error(payload.error);
    DATA = payload;
    initConfig();
    renderAll();
  } catch (err) {
    const panel = $('#error-panel');
    panel.hidden = false;
    panel.replaceChildren(el('b', {}, 'Could not load data: '), String(err.message || err));
  }
}

function initConfig() {
  CFG = DATA.config && typeof DATA.config === 'object' ? DATA.config : {};
  CFG.people = Array.isArray(CFG.people) ? CFG.people : [];
  CFG.assignments = Array.isArray(CFG.assignments) ? CFG.assignments : [];
  CFG.overrides = CFG.overrides && typeof CFG.overrides === 'object' ? CFG.overrides : {};
  // merge in newly-detected payroll people (matched by name)
  const known = new Set(CFG.people.map((p) => p.name));
  for (const det of DATA.people) {
    if (known.has(det.name)) continue;
    CFG.people.push({
      id: uid(),
      name: det.name,
      monthlySalary: det.monthlySalary || 0,
      fringeRate: det.fringeRate ?? 0,
      annualFees: det.annualFees || 0,
      source: 'payroll',
    });
  }
}

function save() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(async () => {
    try {
      await fetch('/api/config', { method: 'POST', body: JSON.stringify(CFG) });
      const ind = $('#save-indicator');
      ind.hidden = false;
      setTimeout(() => { ind.hidden = true; }, 1500);
    } catch { /* server gone; nothing to do */ }
  }, 600);
}

/* ---------- rendering ---------- */

function renderAll() {
  renderStatus();
  renderFlags();
  renderPortfolio();
  renderPeople();
  renderAssignments();
  renderSim();
}

function renderStatus() {
  const recognized = DATA.files.filter((f) => f.type !== 'unrecognized');
  const parts = recognized.length
    ? `${recognized.length} file${recognized.length === 1 ? '' : 's'} · ` +
      recognized.map((f) => f.name).join(', ')
    : 'No CSV exports found in the data/ folder yet';
  $('#data-status').textContent = `As of ${DATA.generated} · ${parts}`;
}

function severityLabel(s) {
  return { critical: 'Critical', serious: 'Serious', warning: 'Warning', info: 'Note' }[s] || s;
}

function renderFlags() {
  const box = $('#flags');
  box.replaceChildren();
  if (!DATA.flags.length) {
    box.append(el('div', { class: 'flag-empty' }, 'No issues flagged. \u{1F389}'));
    return;
  }
  for (const f of DATA.flags) {
    box.append(el('div', { class: 'flag' },
      el('span', { class: `sev sev-${f.severity}` }, severityLabel(f.severity)),
      el('span', {},
        el('span', { class: 'title' }, f.title + ' '),
        el('span', { class: 'detail' }, f.detail))));
  }
}

/* ----- portfolio ----- */

function timeFrac(p) {
  if (!p.start || !p.end) return null;
  const s = Date.parse(p.start), e = Date.parse(p.end), t = Date.parse(DATA.today);
  if (e <= s) return null;
  return Math.min(1, Math.max(0, (t - s) / (e - s)));
}

function meter(label, frac, valText, cls) {
  const width = frac === null ? 0 : Math.min(1, Math.max(0, frac)) * 100;
  return el('div', { class: 'meter' },
    el('span', { class: 'lbl' }, label),
    el('div', { class: 'track' },
      el('div', { class: `fill ${cls || ''}${frac > 1 ? ' over' : ''}`, style: `width:${width}%` })),
    el('span', { class: 'val' }, valText));
}

function renderPortfolio() {
  const grid = $('#portfolio');
  grid.replaceChildren();
  const showClosed = $('#show-closed').checked;
  const projects = DATA.projects.filter(
    (p) => p.inDashboard && (showClosed || p.status.toLowerCase() === 'active'));

  // shared axes for all sparklines: same month range and same $ scale,
  // so the little plots are comparable across awards
  const monthSet = new Set();
  for (const p of projects) Object.keys(p.monthly || {}).forEach((m) => monthSet.add(m));
  let sparkDomain = [...monthSet].sort();
  if (sparkDomain.length) {
    sparkDomain = monthRange(sparkDomain[0], sparkDomain[sparkDomain.length - 1]).slice(-24);
  }
  let sparkMax = 1;
  for (const p of projects) {
    for (const m of sparkDomain) sparkMax = Math.max(sparkMax, Math.abs((p.monthly || {})[m] || 0));
  }

  for (const p of projects) {
    const tf = timeFrac(p);
    const sf = p.totals.budget > 0 ? p.totals.spent / p.totals.budget : null;
    const active = p.status.toLowerCase() === 'active';

    const card = el('div', { class: 'card' });
    card.append(el('div', { class: 'card-head' },
      el('div', {},
        el('h3', {}, p.shortName),
        el('div', { class: 'proj-id' },
          `${p.id} · ${p.start ?? '?'} → ${p.end ?? '?'}`,
          p.faRate != null ? ` · F&A ${fmtPct(p.faRate)}${p.faSource === 'inferred' ? ' (est.)' : ''}` : '')),
      el('span', { class: 'status-chip' + (active ? '' : ' closed') }, p.status)));

    if (tf !== null) {
      const daysLeft = Math.max(0, Math.round((Date.parse(p.end) - Date.parse(DATA.today)) / 86400000));
      card.append(meter('Time elapsed', tf, active ? `${fmtPct(tf)} · ${Math.round(daysLeft / 30.44)} mo left` : fmtPct(tf), 'time'));
    }
    if (sf !== null) {
      card.append(meter('Budget spent', sf,
        `${fmtPct(sf)} · ${fmt$(p.totals.remaining)} left`));
    }

    // category table
    const tbl = el('table', { class: 'cats' },
      el('tr', {}, el('th', {}, 'Category'), el('th', {}, 'Budget'),
        el('th', {}, 'Spent'), el('th', {}, 'Left'), el('th', {})));
    for (const c of p.categories) {
      const frac = c.budget > 0 ? c.spent / c.budget : (c.spent > 0 ? 1.01 : 0);
      tbl.append(el('tr', {},
        el('td', {}, c.category),
        el('td', {}, fmtK(c.budget)),
        el('td', {}, fmtK(c.spent)),
        el('td', { class: c.remaining < -0.5 ? 'neg' : '' }, fmtK(c.remaining)),
        el('td', { class: 'catbar' },
          el('div', { class: 'track' },
            el('div', {
              class: 'fill' + (frac > 1 ? ' over' : ''),
              style: `width:${Math.min(100, frac * 100)}%`,
            })))));
    }
    card.append(tbl);

    // monthly spending sparkline (shared axes across all cards)
    const hasMonthly = Object.keys(p.monthly || {}).length > 0;
    if (hasMonthly && sparkDomain.length) {
      const hasFac = sparkDomain.some((m) => ((p.monthlyFaculty || {})[m] || 0) > 0);
      const block = el('div', { class: 'spark-block' },
        el('div', { class: 'spark-title' }, 'Monthly spend (detail export)'));
      block.append(barSpark(p.monthly, p.monthlyFaculty || {}, sparkDomain, sparkMax));
      if (hasFac) {
        block.append(el('div', { class: 'mini-legend' },
          el('span', { class: 'key' }, el('span', { class: 'dot', style: 'background:var(--series-5)' }), 'PI summer salary'),
          el('span', { class: 'key' }, el('span', { class: 'dot', style: 'background:var(--series-1)' }), 'all other spending')));
      }
      card.append(block);
    }

    // burn / runway line
    const burn = p.burn.avg12 ?? p.burn.recent ?? p.burn.linear;
    if (active && burn && burn > 0) {
      const src = p.burn.avg12 != null ? '12-mo avg'
        : p.burn.recent != null ? `avg of ${p.burn.recentMonths.map(fmtMonth).join(', ')}`
        : 'linear average over the award';
      const runway = p.totals.remaining / burn;
      const monthsLeft = p.end ? monthDiff(DATA.today.slice(0, 7), p.end.slice(0, 7)) : null;
      let runTxt = `runway ≈ ${runway.toFixed(0)} mo`;
      if (monthsLeft !== null) runTxt += ` (award has ${monthsLeft} mo left)`;
      const extra = (p.burn.avg12 != null && p.burn.recent != null)
        ? ` · last 3 mo ${fmt$(p.burn.recent)}/mo` : '';
      card.append(el('div', { class: 'burn-line' },
        'Burn ≈ ', el('b', {}, fmt$(burn) + '/mo'), ` (${src})${extra} · ${runTxt}`));
    } else if (active && !hasMonthly) {
      card.append(el('div', { class: 'burn-line' },
        'No transaction detail loaded for this award — add an RPT_GMS_007 export for real burn rates.'));
    }

    // projection of current spending to the end of the award
    if (active && burn && burn > 0 && p.end) {
      const curMonth = DATA.today.slice(0, 7);
      const endMonth = p.end.slice(0, 7);
      if (endMonth > curMonth) {
        const projMonths = monthRange(monthAdd(curMonth, 1), endMonth);
        let bal = p.totals.remaining - p.totals.committed;
        const values = projMonths.map(() => (bal -= burn));
        const block = el('div', { class: 'spark-block' },
          el('div', { class: 'spark-title' }, `Projected balance at current burn (${fmt$(burn)}/mo)`));
        block.append(lineChart(projMonths, [{ name: 'projected balance', values, endLabel: true }],
          { W: 340, H: 110, padL: 46 }));
        card.append(block);
      }
    }

    grid.append(card);
  }
}

function barSpark(monthly, monthlyFac, domain, maxV) {
  // domain and maxV are shared across all cards so the sparklines compare.
  const W = 340, H = 48, pad = 2;
  const NS = 'http://www.w3.org/2000/svg';
  const vals = domain.map((k) => monthly[k] || 0);
  const zero = vals.some((v) => v < 0) ? H * 0.7 : H;
  const slot = (W - pad * 2) / domain.length;
  const bw = Math.max(2, slot - 2);

  const svg = document.createElementNS(NS, 'svg');
  svg.setAttribute('viewBox', `0 0 ${W} ${H + 14}`);
  svg.setAttribute('width', '100%');
  svg.style.display = 'block';

  const rect = (x, y, w, h, fill) => {
    const r = document.createElementNS(NS, 'rect');
    r.setAttribute('x', x); r.setAttribute('y', y);
    r.setAttribute('width', w); r.setAttribute('height', h);
    r.setAttribute('rx', 1.5);
    r.setAttribute('fill', fill);
    return r;
  };

  domain.forEach((k, i) => {
    const v = monthly[k] || 0;
    const fac = Math.max(0, Math.min(monthlyFac[k] || 0, Math.max(0, v)));
    const x = pad + i * slot;
    const h = Math.abs(v) / maxV * (zero - 4);
    const tip = (e) => showTip(
      `${fmtMonth(k)}: ${fmt$(v)}` + (fac > 0.5 ? ` (PI summer salary ${fmt$(fac)})` : ''),
      e.clientX, e.clientY);

    // full net bar in blue, then the faculty-salary portion re-drawn in
    // violet from the baseline up (positive months only)
    const bars = [rect(x, v >= 0 ? zero - h : zero, bw, Math.max(1, h), 'var(--series-1)')];
    if (v > 0 && fac > 0) {
      bars.push(rect(x, zero - fac / maxV * (zero - 4), bw, Math.max(1, fac / maxV * (zero - 4)), 'var(--series-5)'));
    }
    for (const b of bars) {
      b.addEventListener('mousemove', tip);
      b.addEventListener('mouseleave', hideTip);
      svg.append(b);
    }
  });

  const label = (x, anchor, text) => {
    const t = document.createElementNS(NS, 'text');
    t.setAttribute('x', x); t.setAttribute('y', H + 11);
    t.setAttribute('text-anchor', anchor);
    t.textContent = text;
    return t;
  };
  svg.append(label(pad, 'start', fmtMonth(domain[0])),
             label(W - pad, 'end', fmtMonth(domain[domain.length - 1])));
  return svg;
}

/* ----- people ----- */

function renderPeople() {
  const box = $('#people');
  const tbl = el('table', { class: 'people' },
    el('tr', {},
      el('th', {}, 'Name'), el('th', { class: 'num' }, 'Monthly salary ($)'),
      el('th', { class: 'num' }, 'Fringe (%)'), el('th', { class: 'num' }, 'Fees+tuition ($/yr)'),
      el('th', {}, 'Source'), el('th', {})));

  for (const person of CFG.people) {
    const det = DATA.people.find((d) => d.name === person.name);
    const numIn = (key, scale, step) => el('input', {
      type: 'number', step: step || 1,
      value: scale ? Math.round(person[key] * scale * 100) / 100 : Math.round(person[key] * 100) / 100,
      oninput: (e) => {
        const v = parseFloat(e.target.value);
        person[key] = isNaN(v) ? 0 : (scale ? v / scale : v);
        save(); renderSim();
      },
    });
    tbl.append(el('tr', {},
      el('td', {}, el('input', {
        type: 'text', value: person.name,
        oninput: (e) => { person.name = e.target.value; save(); renderSim(); },
      })),
      el('td', { class: 'num' }, numIn('monthlySalary', 0, 50)),
      el('td', { class: 'num' }, numIn('fringeRate', 100, 0.1)),
      el('td', { class: 'num' }, numIn('annualFees', 0, 100)),
      el('td', {},
        el('span', { class: 'badge' }, person.source === 'payroll' ? 'from payroll' : 'manual'),
        det && det.facultySalary ? el('span', {
          class: 'badge', style: 'color:var(--series-5);border-color:var(--series-5)',
          title: 'faculty salary — only charged in the months listed, not year-round',
        }, ' summer salary · ' + (det.paidMonthNums || []).map((n) => MONTH_NAMES[n - 1]).join('/')) : null,
        det && det.lastPaid ? el('span', { class: 'badge', title: 'most recent salary month in the detail export' }, ' last paid ' + fmtMonth(det.lastPaid)) : null),
      el('td', {}, el('button', {
        class: 'btn danger',
        onclick: () => {
          CFG.people = CFG.people.filter((p) => p !== person);
          CFG.assignments = CFG.assignments.filter((a) => a.personId !== person.id);
          save(); renderPeople(); renderAssignments(); renderSim();
        },
      }, 'Remove'))));
  }
  box.replaceChildren(tbl);
}

/* ----- simulator ----- */

function activeProjects() {
  return DATA.projects.filter((p) => p.inDashboard && p.status.toLowerCase() === 'active');
}

function renderAssignments() {
  const box = $('#assignments');
  box.replaceChildren();
  const curMonth = DATA.today.slice(0, 7);

  for (const a of CFG.assignments) {
    const personSel = el('select', {
      onchange: (e) => { a.personId = e.target.value; save(); renderSim(); },
    }, CFG.people.map((p) => el('option', { value: p.id, selected: p.id === a.personId || null }, p.name || '(unnamed)')));

    const projSel = el('select', {
      onchange: (e) => { a.projectId = e.target.value; save(); renderSim(); },
    }, activeProjects().map((p) => el('option', { value: p.id, selected: p.id === a.projectId || null }, `${p.shortName} (${p.id})`)));

    box.append(el('div', { class: 'assignment-row' },
      personSel,
      el('span', { class: 'sep' }, 'on'), projSel,
      el('span', { class: 'sep' }, 'at'),
      el('input', {
        type: 'number', min: 0, max: 200, step: 5, value: a.effort,
        oninput: (e) => { a.effort = parseFloat(e.target.value) || 0; save(); renderSim(); },
      }),
      el('span', { class: 'sep' }, '% of their salary, from'),
      el('input', {
        type: 'month', value: a.startMonth || curMonth,
        oninput: (e) => { a.startMonth = e.target.value; save(); renderSim(); },
      }),
      el('span', { class: 'sep' }, 'to'),
      el('input', {
        type: 'month', value: a.endMonth || '',
        oninput: (e) => { a.endMonth = e.target.value; save(); renderSim(); },
      }),
      el('label', { class: 'check' },
        el('input', {
          type: 'checkbox', checked: a.chargeFees || null,
          onchange: (e) => { a.chargeFees = e.target.checked; save(); renderSim(); },
        }), ' charge fees/tuition'),
      el('button', {
        class: 'btn danger',
        onclick: () => {
          CFG.assignments = CFG.assignments.filter((x) => x !== a);
          save(); renderAssignments(); renderSim();
        },
      }, 'Remove')));
  }
}

function assignmentCost(a, person, faRate) {
  const salary = (person.monthlySalary || 0) * (a.effort || 0) / 100;
  const fringe = salary * (person.fringeRate || 0);
  const fees = a.chargeFees ? (person.annualFees || 0) / 12 : 0;
  const idc = (salary + fringe) * (faRate || 0);
  return { salary, fringe, fees, idc, total: salary + fringe + fees + idc };
}

function renderSim() {
  const box = $('#sim-results');
  box.replaceChildren();
  const curMonth = DATA.today.slice(0, 7);

  const byProject = new Map();
  for (const a of CFG.assignments) {
    const person = CFG.people.find((p) => p.id === a.personId);
    const project = DATA.projects.find((p) => p.id === a.projectId);
    if (!person || !project || !(a.effort > 0)) continue;
    if (!byProject.has(project.id)) byProject.set(project.id, []);
    byProject.get(project.id).push({ a, person });
  }
  if (!byProject.size) {
    if (CFG.assignments.length === 0) {
      box.append(el('p', { class: 'hint' }, 'No assignments yet — add one above to project balances.'));
    }
    return;
  }

  for (const [pid, items] of byProject) {
    const project = DATA.projects.find((p) => p.id === pid);
    const ov = CFG.overrides[pid] || (CFG.overrides[pid] = {});
    const faRate = ov.faRate ?? project.faRate ?? 0;
    const defaultBaseline = project.burn.avg12 ?? project.burn.recent ?? project.burn.linear ?? 0;
    const baseline = ov.baselineBurn ?? defaultBaseline;

    const startBal = project.totals.remaining - project.totals.committed;
    const endMonth = project.end ? project.end.slice(0, 7)
      : items.reduce((m, it) => (it.a.endMonth > m ? it.a.endMonth : m), curMonth);
    const firstMonth = monthAdd(curMonth, 1);

    const card = el('div', { class: 'sim-card' });
    card.append(el('div', { class: 'sim-head' },
      el('h3', {}, `${project.shortName} (${project.id})`),
      el('span', { class: 'hint' }, `award ends ${fmtMonth(endMonth)}`)));

    // per-assignment cost breakdown
    for (const { a, person } of items) {
      const c = assignmentCost(a, person, faRate);
      const from = a.startMonth || curMonth;
      const to = a.endMonth || endMonth;
      const nMonths = Math.max(0, monthDiff(from, to) + 1);
      card.append(el('div', { class: 'burn-line' },
        el('b', {}, `${person.name} at ${a.effort}%: `),
        `${fmt$(c.salary)} salary + ${fmt$(c.fringe)} fringe` +
        (c.fees ? ` + ${fmt$(c.fees)} fees` : '') +
        ` + ${fmt$(c.idc)} F&A = `,
        el('b', {}, fmt$(c.total) + '/mo'),
        ` · ${fmtMonth(from)}–${fmtMonth(to)} (${nMonths} mo, ${fmt$(c.total * nMonths)} total)`));
    }

    if (endMonth < firstMonth) {
      card.append(el('p', { class: 'hint' }, 'This award ends before next month — nothing to project.'));
      box.append(card);
      continue;
    }

    // projection
    const months = monthRange(firstMonth, endMonth);
    let balBase = startBal, balWith = startBal;
    const seriesBase = [], seriesWith = [];
    let runsOut = null;
    for (const m of months) {
      balBase -= baseline;
      let extra = 0;
      for (const { a, person } of items) {
        const from = a.startMonth || curMonth;
        const to = a.endMonth || endMonth;
        if (m >= from && m <= to) extra += assignmentCost(a, person, faRate).total;
      }
      balWith -= baseline + extra;
      if (runsOut === null && balWith < 0) runsOut = m;
      seriesBase.push(balBase);
      seriesWith.push(balWith);
    }
    const final = seriesWith[seriesWith.length - 1];

    const statVal = (v) => el('span', { class: 'stat-value ' + (v >= 0 ? 'ok' : 'bad') },
      (v >= 0 ? '✓ ' : '✕ ') + fmt$(v));
    card.append(el('div', { class: 'sim-stats' },
      el('div', { class: 'stat' },
        el('div', { class: 'stat-label' }, 'Balance now'),
        el('div', { class: 'stat-value' }, fmt$(startBal)),
        project.totals.committed ? el('div', { class: 'stat-note' }, `after ${fmt$(project.totals.committed)} committed`) : null),
      el('div', { class: 'stat' },
        el('div', { class: 'stat-label' }, `Projected at award end (${fmtMonth(endMonth)})`),
        statVal(final),
        el('div', { class: 'stat-note' }, runsOut ? `runs out ${fmtMonth(runsOut)}` : 'stays in the black')),
      el('div', { class: 'baseline-ctl' },
        'Baseline burn (existing spending): $',
        el('input', {
          type: 'number', step: 100, value: Math.round(baseline),
          oninput: (e) => {
            const v = parseFloat(e.target.value);
            ov.baselineBurn = isNaN(v) ? null : v;
            save(); renderSim();
          },
        }), '/mo',
        el('span', { class: 'sep' }, '· F&A'),
        el('input', {
          type: 'number', step: 0.5, value: Math.round(faRate * 1000) / 10,
          oninput: (e) => {
            const v = parseFloat(e.target.value);
            ov.faRate = isNaN(v) ? null : v / 100;
            save(); renderSim();
          },
        }), '%')));

    card.append(el('div', { class: 'legend' },
      el('span', { class: 'key' }, el('span', { class: 'swatch' }), ' With new assignments'),
      el('span', { class: 'key' }, el('span', { class: 'swatch dashed' }), ' Current spending only')));
    card.append(lineChart(months, [
      { name: 'current only', values: seriesBase, dashed: true },
      { name: 'with assignments', values: seriesWith, dashed: false },
    ]));
    box.append(card);
  }
}

/* ----- projection line chart (SVG) ----- */

function lineChart(months, series, opts) {
  const { W = 640, H = 220, padL = 56 } = opts || {};
  const padR = 14, padT = 10, padB = 24;
  const NS = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(NS, 'svg');
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  svg.setAttribute('width', '100%');
  svg.style.display = 'block';

  const all = series.flatMap((s) => s.values);
  const lo = Math.min(0, ...all), hi = Math.max(0, ...all);
  const span = (hi - lo) || 1;
  const x = (i) => padL + (months.length === 1 ? 0 : i / (months.length - 1) * (W - padL - padR));
  const y = (v) => padT + (hi - v) / span * (H - padT - padB);

  // horizontal gridlines + $ labels (4 ticks)
  const step = niceStep(span / 3);
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1; v += step) {
    const line = document.createElementNS(NS, 'line');
    line.setAttribute('x1', padL); line.setAttribute('x2', W - padR);
    line.setAttribute('y1', y(v)); line.setAttribute('y2', y(v));
    line.setAttribute('class', Math.abs(v) < step / 100 ? 'zeroline' : 'gridline');
    svg.append(line);
    const label = document.createElementNS(NS, 'text');
    label.setAttribute('x', padL - 6); label.setAttribute('y', y(v) + 3.5);
    label.setAttribute('text-anchor', 'end');
    label.textContent = fmtK(v);
    svg.append(label);
  }

  // x labels: ~6 month ticks (fewer on small charts); the last month always
  // gets a label, and a modulo tick too close to it is dropped
  const every = Math.max(1, Math.ceil(months.length / (W < 400 ? 4 : 6)));
  months.forEach((m, i) => {
    if (i % every !== 0 && i !== months.length - 1) return;
    if (i !== months.length - 1 && months.length - 1 - i < every * 0.6) return;
    const label = document.createElementNS(NS, 'text');
    label.setAttribute('x', x(i)); label.setAttribute('y', H - 6);
    label.setAttribute('text-anchor', i === months.length - 1 ? 'end' : 'middle');
    label.textContent = fmtMonth(m);
    svg.append(label);
  });

  for (const s of series) {
    const path = document.createElementNS(NS, 'path');
    path.setAttribute('d', s.values.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(''));
    path.setAttribute('fill', 'none');
    path.setAttribute('stroke', s.dashed ? 'var(--muted)' : 'var(--series-1)');
    path.setAttribute('stroke-width', '2');
    if (s.dashed) path.setAttribute('stroke-dasharray', '4 4');
    svg.append(path);
    if (s.endLabel) {
      const last = s.values[s.values.length - 1];
      const t = document.createElementNS(NS, 'text');
      t.setAttribute('x', W - padR);
      t.setAttribute('y', Math.max(padT + 9, Math.min(H - padB - 3, y(last) - 5)));
      t.setAttribute('text-anchor', 'end');
      t.setAttribute('font-weight', '600');
      t.setAttribute('fill', last < 0 ? 'var(--critical)' : 'var(--good-text)');
      t.textContent = fmtK(last) + ' at end';
      svg.append(t);
    }
  }

  // hover crosshair + tooltip
  const cross = document.createElementNS(NS, 'line');
  cross.setAttribute('class', 'gridline');
  cross.setAttribute('y1', padT); cross.setAttribute('y2', H - padB);
  cross.setAttribute('visibility', 'hidden');
  svg.append(cross);
  svg.addEventListener('mousemove', (e) => {
    const rect = svg.getBoundingClientRect();
    const fx = (e.clientX - rect.left) / rect.width * W;
    const i = Math.round((fx - padL) / ((W - padL - padR) / Math.max(1, months.length - 1)));
    if (i < 0 || i >= months.length) { cross.setAttribute('visibility', 'hidden'); hideTip(); return; }
    cross.setAttribute('x1', x(i)); cross.setAttribute('x2', x(i));
    cross.setAttribute('visibility', 'visible');
    const parts = series.map((s) => (series.length > 1 ? `${s.name}: ` : '') + fmt$(s.values[i]));
    showTip(`${fmtMonth(months[i])} — ${parts.join(' · ')}`, e.clientX, e.clientY);
  });
  svg.addEventListener('mouseleave', () => { cross.setAttribute('visibility', 'hidden'); hideTip(); });
  return svg;
}

function niceStep(raw) {
  const mag = Math.pow(10, Math.floor(Math.log10(Math.max(1, raw))));
  for (const m of [1, 2, 2.5, 5, 10]) if (raw <= m * mag) return m * mag;
  return 10 * mag;
}

/* ---------- wiring ---------- */

$('#reload-btn').addEventListener('click', load);
$('#show-closed').addEventListener('change', renderPortfolio);
$('#add-person').addEventListener('click', () => {
  CFG.people.push({ id: uid(), name: '', monthlySalary: 0, fringeRate: 0.1, annualFees: 0, source: 'manual' });
  save(); renderPeople();
});
$('#add-assignment').addEventListener('click', () => {
  if (!CFG.people.length) { alert('Add a person first.'); return; }
  const proj = activeProjects()[0];
  const cur = DATA.today.slice(0, 7);
  CFG.assignments.push({
    id: uid(), personId: CFG.people[0].id, projectId: proj ? proj.id : null,
    effort: 100, startMonth: monthAdd(cur, 1),
    endMonth: proj && proj.end ? proj.end.slice(0, 7) : monthAdd(cur, 12),
    chargeFees: false,
  });
  save(); renderAssignments(); renderSim();
});

load();
