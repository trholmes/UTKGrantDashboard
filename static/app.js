/* UTKGrantDashboard front-end. Vanilla JS, no external dependencies.
   All rendering is DOM-built (no innerHTML with data) so names from the
   CSVs can never inject markup. */
'use strict';

let DATA = null;   // payload from /api/data
let CFG = null;    // { people:[], assignments:[], overrides:{} }
let saveTimer = null;
let personColors = new Map();  // name -> color, assigned by the summary chart

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
  CFG.ui = CFG.ui && typeof CFG.ui === 'object' ? CFG.ui : {};
  $('#show-notes').checked = !!CFG.ui.showNotes;
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

let sectionsWired = false;

function setupSections() {
  // every section header toggles its body; state persists in the config
  if (!sectionsWired) {
    sectionsWired = true;
    document.querySelectorAll('section.collapsible > h2').forEach((h) => {
      h.addEventListener('click', () => {
        const sec = h.parentElement;
        sec.classList.toggle('collapsed');
        CFG.ui.collapsed = CFG.ui.collapsed || {};
        CFG.ui.collapsed[sec.dataset.key] = sec.classList.contains('collapsed');
        save();
      });
    });
  }
  const collapsed = CFG.ui.collapsed || {};
  document.querySelectorAll('section.collapsible').forEach((sec) => {
    sec.classList.toggle('collapsed', !!collapsed[sec.dataset.key]);
  });
}

function renderAll() {
  setupSections();
  renderStatus();
  renderFlags();
  renderSummary();
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
  const showNotes = $('#show-notes').checked;
  const shown = DATA.flags.filter((f) => showNotes || f.severity !== 'info');
  const hidden = DATA.flags.length - shown.length;
  if (!DATA.flags.length) {
    box.append(el('div', { class: 'flag-empty' }, 'No issues flagged. \u{1F389}'));
    return;
  }
  for (const f of shown) {
    box.append(el('div', { class: 'flag' },
      el('span', { class: `sev sev-${f.severity}` }, severityLabel(f.severity)),
      el('span', {},
        el('span', { class: 'title' }, f.title + ' '),
        el('span', { class: 'detail' }, f.detail))));
  }
  if (hidden > 0) {
    box.append(el('div', { class: 'flag-empty' },
      `${hidden} note-level flag${hidden === 1 ? '' : 's'} hidden.`));
  }
}

/* ----- portfolio summary ----- */

function grantFilter() {
  // the award selection scoping the summary, cards, and people graying
  const curMonth = DATA.today.slice(0, 7);
  const all = DATA.projects.filter((p) =>
    p.inDashboard && p.status.toLowerCase() === 'active'
    && p.end && p.end.slice(0, 7) >= curMonth);
  const excluded = new Set((CFG.ui && CFG.ui.excluded) || []);
  const selected = all.filter((p) => !excluded.has(p.id));
  return {
    all, selected,
    selectedSet: new Set(selected.map((p) => p.id)),
    filterActive: selected.length < all.length,
  };
}

function renderSummary() {
  const box = $('#summary');
  box.replaceChildren();
  const curMonth = DATA.today.slice(0, 7);
  const filter = grantFilter();
  if (!filter.all.length) return;

  // checkbox chips: which awards feed this summary (and show as cards)
  box.append(el('div', { class: 'grant-filter' },
    filter.all.map((p) => el('label', { class: 'check' },
      el('input', {
        type: 'checkbox', checked: filter.selectedSet.has(p.id) || null,
        onchange: (e) => {
          const ex = new Set((CFG.ui && CFG.ui.excluded) || []);
          if (e.target.checked) ex.delete(p.id); else ex.add(p.id);
          CFG.ui.excluded = [...ex];
          save(); renderAll();
        },
      }), ` ${p.shortName}`))));

  const active = filter.selected;
  const selectedSet = filter.selectedSet;
  if (!active.length) {
    box.append(el('p', { class: 'hint' }, 'No awards selected.'));
    personColors = new Map();
    return;
  }

  const totBudget = active.reduce((a, p) => a + p.totals.budget, 0);
  const totSpent = active.reduce((a, p) => a + p.totals.spent, 0);
  const available = active.reduce((a, p) => a + p.totals.remaining - p.totals.committed, 0);

  // Per-person cost = salary + fringe, times (1 + their F&A rate), plus
  // fees/tuition (/12, excluded from F&A per MTDC). Costs are scoped to the
  // selected awards via each person's support split: `frac` is the fraction
  // of their support on selected awards (scales fees), `mult` additionally
  // folds in each selected award's F&A rate (scales salary+fringe). With
  // everything selected, frac = 1 and this matches the unfiltered model.
  const faOf = (pid) => {
    const ov = CFG.overrides[pid] || {};
    const proj = DATA.projects.find((p) => p.id === pid);
    return ov.faRate ?? (proj ? proj.faRate : null) ?? 0;
  };
  const shareMults = (det) => {
    let frac = 0, mult = 0;
    const s = det && det.support;
    for (const sh of (s && s.shares) || []) {
      if (!selectedSet.has(sh.project)) continue;
      const pct = Math.max(0, sh.pct || 0);
      frac += pct;
      mult += pct * (1 + faOf(sh.project));
    }
    return { frac, mult };
  };

  // current team = people with a salary in the last 2 months of detail data,
  // minus anyone whose expected end has passed or whose support is entirely
  // on unselected awards
  const team = [];
  for (const person of CFG.people) {
    const det = DATA.people.find((d) => d.name === person.name);
    if (person.endMonth && person.endMonth < curMonth) continue;
    if (det && det.lastPaid && monthDiff(det.lastPaid, curMonth) <= 2
        && shareMults(det).frac > 0) {
      team.push({ person, det });
    }
  }
  const salaryAt = (person, m) => {
    if (person.payChangeMonth && person.payChangeSalary != null
        && m >= person.payChangeMonth) {
      return person.payChangeSalary;
    }
    return person.monthlySalary || 0;
  };
  const personnelFor = (m) => {
    const mm = +m.slice(5, 7);
    let sum = 0;
    for (const { person, det } of team) {
      if (person.endMonth && m > person.endMonth) continue;
      const { frac, mult } = shareMults(det);
      const loaded = salaryAt(person, m) * (1 + (person.fringeRate || 0)) * mult;
      const fees = (person.annualFees || 0) / 12 * frac;
      if (!det.facultySalary || (det.paidMonthNums || []).includes(mm)) sum += loaded;
      sum += fees;
    }
    return sum;
  };

  // non-personnel spending trend: 12-month average of the "other" component
  // (projects without transaction detail contribute their whole burn here)
  let otherTrend = 0;
  for (const p of active) {
    const parts = p.monthlyParts || {};
    const keys = Object.keys(parts).filter((m) => m < curMonth).sort();
    if (keys.length) {
      // average over calendar months (quiet months count as zero),
      // anchored at the last complete month. The F&A generated by
      // personnel (rate × salary+fringe) is estimated per month and
      // removed — it is carried with each person instead.
      const fa = faOf(p.id);
      const lastComplete = monthAdd(curMonth, -1);
      const window = [];
      for (let i = 0; i < 12; i++) {
        const m = monthAdd(lastComplete, -i);
        if (m >= keys[0]) window.push(m);
      }
      if (window.length) {
        const net = window.reduce((a, m) => {
          const pt = parts[m] || {};
          const personnelIdc = ((pt.personnel || 0) + (pt.fac || 0)) * fa;
          return a + Math.max(0, (pt.other || 0) - personnelIdc);
        }, 0);
        otherTrend += net / window.length;
      }
    } else {
      otherTrend += p.burn.avg12 ?? p.burn.linear ?? 0;
    }
  }

  // month-by-month projection: spend from the soonest-ending award first;
  // an award's leftover balance disappears when it ends. Manually entered
  // "expected additional funding" (with its new end date) joins the pool —
  // an extended end also lets the existing balance carry forward.
  let extraTotal = 0;
  const pools = active
    .map((p) => {
      const ov = CFG.overrides[p.id] || {};
      const extra = ov.expectedExtra || 0;
      extraTotal += extra;
      let end = p.end.slice(0, 7);
      if (ov.expectedEnd && ov.expectedEnd > end) end = ov.expectedEnd;
      return { end, bal: Math.max(0, p.totals.remaining - p.totals.committed) + extra };
    })
    .sort((a, b) => (a.end < b.end ? -1 : 1));
  const horizon = pools[pools.length - 1].end;
  const months = monthRange(monthAdd(curMonth, 1), horizon);
  const series = [];
  let runsOut = null, expired = 0, unmet = 0;
  for (const m of months) {
    for (const pool of pools) {
      if (pool.end < m && pool.bal > 0) { expired += pool.bal; pool.bal = 0; }
    }
    let need = personnelFor(m) + otherTrend;
    for (const pool of pools) {
      if (pool.end < m || pool.bal <= 0) continue;
      const take = Math.min(pool.bal, need);
      pool.bal -= take; need -= take;
      if (need <= 0) break;
    }
    if (need > 0) {
      unmet += need;
      if (runsOut === null) runsOut = m;
    }
    series.push(pools.reduce((a, pool) => a + (pool.end >= m ? pool.bal : 0), 0) - unmet);
  }

  // team cost decomposition: year-round people vs. PI/faculty summer salary
  const loadedOf = (t) => (t.person.monthlySalary || 0) * (1 + (t.person.fringeRate || 0))
    * shareMults(t.det).mult;
  const feesOf = (t) => (t.person.annualFees || 0) / 12 * shareMults(t.det).frac;
  const yearRound = team.filter((t) => !t.det.facultySalary);
  const summerFolk = team.filter((t) => t.det.facultySalary);
  const baseMonthly = yearRound.reduce((a, t) => a + loadedOf(t) + feesOf(t), 0)
    + summerFolk.reduce((a, t) => a + feesOf(t), 0);
  const peakMonthly = baseMonthly + summerFolk.reduce((a, t) => a + loadedOf(t), 0);
  const summerMonths = [...new Set(summerFolk.flatMap((t) => t.det.paidMonthNums || []))]
    .sort((a, b) => a - b).map((n) => MONTH_NAMES[n - 1]).join('/');

  const fundedThrough = runsOut === null ? horizon : monthAdd(runsOut, -1);
  const runwayMonths = monthDiff(curMonth, fundedThrough);

  const card = el('div', { class: 'sim-card' });
  const stat = (label, value, note, cls) => el('div', { class: 'stat' },
    el('div', { class: 'stat-label' }, label),
    el('div', { class: 'stat-value' + (cls ? ' ' + cls : '') }, value),
    note ? el('div', { class: 'stat-note' }, note) : null);

  card.append(el('div', { class: 'sim-stats' },
    stat('Active awards', String(active.length),
      `${fmt$(totBudget)} total · ${fmt$(totSpent)} spent`),
    stat('Available now', fmt$(available),
      (extraTotal > 0 ? `+ ${fmt$(extraTotal)} expected (entered manually) · ` : '')
        + (expired > 0 ? `${fmt$(expired)} expires unspent at this pace` : 'across all active awards')),
    stat('Current team', fmt$(baseMonthly) + '/mo',
      `${yearRound.length} people year-round — salary+fringe+fees+their F&A`
        + (summerFolk.length
          ? ` · ${fmt$(peakMonthly)}/mo in ${summerMonths} (${summerFolk.map((t) => t.person.name).join(', ')} summer salary)`
          : '')
        + (team.some((t) => t.person.endMonth || t.person.payChangeMonth)
          ? ' · scheduled departures/pay changes applied' : '')),
    stat('Other spending', fmt$(otherTrend) + '/mo',
      '12-mo trend: travel, supplies, F&A on non-salary costs — personnel F&A and fees count with each person'),
    stat('Funded through', fmtMonth(fundedThrough),
      runsOut === null
        ? 'to the end of your last award'
        : `bring in new money by then (${fmt$(unmet)} short through ${fmtMonth(horizon)})`,
      runwayMonths >= 12 ? 'ok' : 'bad')));

  // ground the projection with reconstructed history: total available funds
  // over the last ~18 months, rebuilt backwards from today's balances the
  // same way the per-award charts are
  let histMonths = [];
  const detailMonths = [...new Set(active.flatMap((p) =>
    Object.keys(p.monthly || {}).filter((m) => m <= curMonth)))].sort();
  if (detailMonths.length) {
    histMonths = monthRange(detailMonths[0], curMonth).slice(-18);
  }
  const histTotals = histMonths.map(() => 0);
  for (const p of active) {
    let bal = p.totals.remaining - p.totals.committed;
    const balBy = { [curMonth]: bal };
    let cursor = curMonth;
    if (histMonths.length) {
      for (let m = monthAdd(curMonth, -1); m >= histMonths[0]; m = monthAdd(m, -1)) {
        bal += (p.monthly || {})[cursor] || 0;
        balBy[m] = bal;
        cursor = m;
      }
    }
    // an award contributes nothing before it started — new money shows as a
    // step up in the history, which is exactly what "bringing money in" looks like
    const startMonth = p.start ? p.start.slice(0, 7) : null;
    histMonths.forEach((m, i) => {
      if (!startMonth || m >= startMonth) histTotals[i] += balBy[m];
    });
  }

  const timeline = histMonths.concat(months);
  const actualSeries = timeline.map((m, i) => (i < histMonths.length ? histTotals[i] : null));
  const projSeries = timeline.map((m, i) => {
    if (i < histMonths.length - 1) return null;
    if (i === histMonths.length - 1) return available;  // join at "now"
    return series[i - histMonths.length];
  });

  // stacked monthly-cost bars under the balance line: one color per person
  // (history from payroll, projection from the People table incl. expected
  // ends and pay changes), gray for spending not tied to a person
  const projStart = histMonths.length;
  const cfgByName = new Map(CFG.people.map((p) => [p.name, p]));
  const teamByName = new Map(team.map((t) => [t.person.name, t]));
  const candidates = new Map();  // name -> detected record
  for (const d of DATA.people) {
    if (Object.keys(d.salaryHistory || {}).some((m) => m >= (histMonths[0] || curMonth) && m <= curMonth)) {
      candidates.set(d.name, d);
    }
  }
  for (const t of team) candidates.set(t.person.name, t.det);

  const persons = [...candidates.entries()].map(([name, det]) => {
    const cfg = cfgByName.get(name);
    const histFringe = (cfg ? cfg.fringeRate : det.fringeRate) || 0;
    const inTeam = teamByName.get(name);
    const byProj = det.salaryByProject || {};
    const values = timeline.map((m, i) => {
      if (i < projStart) {
        // history from the per-award breakdown, restricted to the selected
        // awards, with each award's own F&A folded in (gray history is a
        // residual of actual totals, so the stacks stay conserved)
        let sum = 0;
        for (const pid of Object.keys(byProj)) {
          if (!selectedSet.has(pid)) continue;
          sum += (byProj[pid][m] || 0) * (1 + histFringe) * (1 + faOf(pid));
        }
        return sum;
      }
      if (!inTeam) return 0;
      const { person, det: d } = inTeam;
      if (person.endMonth && m > person.endMonth) return 0;
      const mm = +m.slice(5, 7);
      const { frac, mult } = shareMults(d);
      const loaded = salaryAt(person, m) * (1 + (person.fringeRate || 0)) * mult;
      const fees = (person.annualFees || 0) / 12 * frac;
      return (!d.facultySalary || (d.paidMonthNums || []).includes(mm) ? loaded : 0) + fees;
    });
    return {
      name,
      values,
      total: values.reduce((a, b) => a + b, 0),
      // rank on history only: config edits (pay changes, expected ends)
      // must never reshuffle everyone's colors mid-experiment
      hist: values.slice(0, projStart).reduce((a, b) => a + b, 0),
    };
  }).filter((p) => p.total > 1);
  persons.sort((a, b) => (b.hist - a.hist) || (b.total - a.total)
    || a.name.localeCompare(b.name));

  const PERSON_COLORS = ['var(--series-2)', 'var(--series-3)', 'var(--series-5)',
    'var(--series-6)', 'var(--series-7)', 'var(--series-8)'];
  const barSeries = persons.slice(0, PERSON_COLORS.length)
    .map((p, i) => ({ name: p.name, color: PERSON_COLORS[i], values: p.values }));
  const rest = persons.slice(PERSON_COLORS.length);
  if (rest.length) {
    barSeries.push({
      name: `${rest.length} others`, color: 'var(--series-4)',
      values: timeline.map((_, i) => rest.reduce((a, p) => a + p.values[i], 0)),
    });
  }
  // publish the color assignment so the People table can match it
  personColors = new Map();
  persons.slice(0, PERSON_COLORS.length).forEach((p, i) => personColors.set(p.name, PERSON_COLORS[i]));
  rest.forEach((p) => personColors.set(p.name, 'var(--series-4)'));
  barSeries.push({
    name: 'non-personnel', color: 'var(--muted)',
    values: timeline.map((m, i) => {
      if (i < projStart) {
        const total = active.reduce((a, p) => a + ((p.monthly || {})[m] || 0), 0);
        const ppl = persons.reduce((a, p) => a + p.values[i], 0);
        return Math.max(0, total - ppl);
      }
      return otherTrend;
    }),
  });

  card.append(el('div', { class: 'spark-title', style: 'margin-top:6px' },
    'Top: available funds (history, then projection; balances expire as awards end). '
    + 'Bottom: monthly costs by person — lighter bars are projected.'));
  card.append(summaryChart(timeline, projStart, [
    { name: 'projected', values: projSeries, dashed: true, endLabel: true },
    { name: 'actual', values: actualSeries },
  ], barSeries));
  const legend = el('div', { class: 'mini-legend', style: 'margin-top:4px' },
    el('span', { class: 'key' }, el('span', { class: 'swatch' }), ' available (actual)'),
    el('span', { class: 'key' }, el('span', { class: 'swatch dashed' }), ' available (projected)'));
  for (const s of barSeries) {
    legend.append(el('span', { class: 'key' },
      el('span', { class: 'dot', style: `background:${s.color}` }), s.name));
  }
  card.append(legend);
  card.append(el('div', { class: 'burn-line' }, 'Awards end: ',
    active.slice().sort((a, b) => (a.end < b.end ? -1 : 1))
      .map((p) => `${p.shortName} ${fmtMonth(p.end.slice(0, 7))}`).join(' · '),
    '. Hypothetical simulator assignments are not included here.'));
  box.append(card);
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
  const hidden = new Set((CFG.ui && CFG.ui.excluded) || []);
  const projects = DATA.projects.filter(
    (p) => p.inDashboard && !hidden.has(p.id)
      && (showClosed || p.status.toLowerCase() === 'active'));

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
    for (const m of sparkDomain) {
      const parts = (p.monthlyParts || {})[m];
      if (!parts) continue;
      const vals = [parts.fac || 0, parts.personnel || 0, (parts.other || 0) + (parts.fees || 0)];
      const pos = vals.filter((v) => v > 0).reduce((a, b) => a + b, 0);
      const neg = -vals.filter((v) => v < 0).reduce((a, b) => a + b, 0);
      sparkMax = Math.max(sparkMax, pos, neg);
    }
  }

  for (const p of projects) {
    const tf = timeFrac(p);
    const sf = p.totals.budget > 0 ? p.totals.spent / p.totals.budget : null;
    const active = p.status.toLowerCase() === 'active';

    const card = el('div', { class: 'card' });
    card.append(el('div', { class: 'card-head' },
      el('h3', {}, p.shortName),
      el('span', { class: 'status-chip' + (active ? '' : ' closed') }, p.status)));
    // full-width so it can run under the status chip without wrapping
    card.append(el('div', { class: 'proj-id' },
      `${p.id} · ${p.start ?? '?'} → ${p.end ?? '?'}`,
      p.faRate != null ? ` · F&A ${fmtPct(p.faRate)}${p.faSource === 'inferred' ? ' (est.)' : ''}` : ''));

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
    const CAT_SHORT = {
      'Salaries & Wages': 'Salaries', 'Fringe Benefits': 'Fringe',
      'Materials & Supplies': 'Materials', 'Other Direct Costs': 'Other Direct',
      'Indirect Costs': 'Indirect (F&A)',
    };
    for (const c of p.categories) {
      const frac = c.budget > 0 ? c.spent / c.budget : (c.spent > 0 ? 1.01 : 0);
      tbl.append(el('tr', {},
        el('td', { title: c.category }, CAT_SHORT[c.category] || c.category),
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
    tbl.append(el('tr', { class: 'total-row' },
      el('td', {}, 'Total award'),
      el('td', {}, fmt$(p.totals.budget)),
      el('td', {}, fmt$(p.totals.spent)),
      el('td', { class: p.totals.remaining < -0.5 ? 'neg' : '' }, fmt$(p.totals.remaining)),
      el('td', {})));
    card.append(tbl);

    // who's on this grant (salary lines from the detail export)
    if ((p.personnel || []).length) {
      const curMonth = DATA.today.slice(0, 7);
      const section = el('div', { class: 'spark-block' },
        el('div', { class: 'spark-title' }, "Who's on this grant"));
      const tbl = el('table', { class: 'cats' });
      for (const person of p.personnel) {
        const stale = monthDiff(person.lastPaid, curMonth) > 2;
        tbl.append(el('tr', { class: stale ? 'stale' : '' },
          el('td', {},
            el('span', {
              class: 'dot',
              style: `background:${personColors.get(person.name) || 'var(--muted)'};margin-right:6px`,
              title: personColors.has(person.name) ? 'color matches the summary chart' : '',
            }),
            person.name,
            person.faculty ? el('span', { class: 'badge', style: 'margin-left:6px' }, 'PI summer') : null),
          el('td', {}, stale ? '—' : fmt$(person.monthly) + '/mo'),
          el('td', { class: 'muted-cell' }, 'last paid ' + fmtMonth(person.lastPaid))));
      }
      section.append(tbl);
      card.append(section);
    } else if (p.hasDetail) {
      card.append(el('div', { class: 'spark-block' },
        el('div', { class: 'spark-title' }, "Who's on this grant"),
        el('div', { class: 'burn-line' }, 'No salaries charged in the export window.')));
    }

    // combined figure: balance line (top) over monthly spend by person
    // (bottom) — same construction as the portfolio summary, shared bar
    // scale across all cards
    const hasMonthly = Object.keys(p.monthly || {}).length > 0;
    const burn = p.burn.avg12 ?? p.burn.recent ?? p.burn.linear;
    const curMonth = DATA.today.slice(0, 7);
    const endMonth = p.end ? p.end.slice(0, 7) : null;
    const canProject = active && burn && burn > 0 && endMonth && endMonth > curMonth;
    if ((hasMonthly && sparkDomain.length) || canProject) {
      const histMonths = sparkDomain.length
        ? monthRange(sparkDomain[0], curMonth) : [curMonth];
      const projMonths = canProject ? monthRange(monthAdd(curMonth, 1), endMonth) : [];
      const timeline = histMonths.concat(projMonths);
      const projStart = histMonths.length;

      // actual balance walked backwards from today
      const balNow = p.totals.remaining - p.totals.committed;
      const balBy = { [curMonth]: balNow };
      let bal = balNow, cursor = curMonth;
      for (const m of histMonths.filter((mm) => mm < curMonth).reverse()) {
        bal += (p.monthly || {})[cursor] || 0;
        balBy[m] = bal; cursor = m;
      }
      const actual = timeline.map((m, i) => (i < projStart ? balBy[m] : null));
      const trend = timeline.map((m) =>
        (canProject && m >= curMonth ? balNow - burn * monthDiff(curMonth, m) : null));

      // per-person history on this award, colored like the summary
      const fa = (CFG.overrides[p.id] || {}).faRate ?? p.faRate ?? 0;
      const cfgByName = new Map(CFG.people.map((cp) => [cp.name, cp]));
      const barSeries = [];
      for (const d of DATA.people) {
        const hist = (d.salaryByProject || {})[p.id];
        if (!hist) continue;
        const fr = ((cfgByName.get(d.name) || d).fringeRate) || 0;
        const values = timeline.map((m, i) =>
          (i < projStart ? (hist[m] || 0) * (1 + fr) * (1 + fa) : 0));
        if (values.some((v) => v > 1)) {
          barSeries.push({ name: d.name, color: personColors.get(d.name) || 'var(--ink-2)', values });
        }
      }
      barSeries.push({
        name: 'non-personnel', color: 'var(--muted)',
        values: timeline.map((m, i) => {
          if (i >= projStart) return 0;
          const tot = (p.monthly || {})[m] || 0;
          return Math.max(0, tot - barSeries.reduce((a, s) => a + s.values[i], 0));
        }),
      });

      const block = el('div', { class: 'spark-block' },
        el('div', { class: 'spark-title' },
          'Balance & monthly spend' + (canProject ? ' — trend to award end' : '')));
      block.append(summaryChart(timeline, projStart, [
        { name: 'trend', values: trend, dashed: true, endLabel: canProject },
        { name: 'balance', values: actual },
      ], barSeries, { W: 340, padL: 46, lineH: 84, barH: 44, gap: 20, barMax: sparkMax, maxTicks: 4 }));
      card.append(block);
    }

    // burn / runway line
    if (active && burn && burn > 0) {
      const src = p.burn.avg12 != null ? '12-mo avg'
        : p.burn.recent != null ? `avg of ${p.burn.recentMonths.map(fmtMonth).join(', ')}`
        : 'linear average over the award';
      const runway = p.totals.remaining / burn;
      const monthsLeft = p.end ? monthDiff(curMonth, p.end.slice(0, 7)) : null;
      let runTxt = `runway ≈ ${runway.toFixed(0)} mo`;
      if (monthsLeft !== null) runTxt += ` (award has ${monthsLeft} mo left)`;
      const extra = (p.burn.avg12 != null && p.burn.recent != null)
        ? ` · last 3 mo ${fmt$(p.burn.recent)}/mo` : '';
      card.append(el('div', { class: 'burn-line' },
        'Burn ≈ ', el('b', {}, fmt$(burn) + '/mo'), ` (${src})${extra} · ${runTxt}`));
    } else if (active && !hasMonthly) {
      card.append(el('div', { class: 'burn-line' },
        'No transaction detail loaded for this award — add an expenditure detail export (RPT…) for real burn rates.'));
    }

    // manually entered future funding (persists in config.json; feeds the
    // portfolio summary's funded-through projection)
    if (active) {
      const ov = CFG.overrides[p.id] || (CFG.overrides[p.id] = {});
      card.append(el('div', { class: 'baseline-ctl' },
        'Expected additional funding: $',
        el('input', {
          type: 'number', step: 1000, min: 0, placeholder: '0',
          value: ov.expectedExtra ?? '',
          oninput: (e) => {
            const v = parseFloat(e.target.value);
            ov.expectedExtra = isNaN(v) || v <= 0 ? null : v;
            save(); renderSummary();
          },
        }),
        el('span', { class: 'sep' }, 'new end'),
        monthInput(ov.expectedEnd, (v) => { ov.expectedEnd = v; save(); renderSummary(); })));
    }

    grid.append(card);
  }
}

/* ----- people ----- */

function monthInput(value, onSet, placeholder) {
  // text input that accepts YYYY-MM (placeholder shows the format, which
  // native month inputs can't do in Safari); snaps to the saved value on blur
  let saved = value || '';
  const input = el('input', {
    type: 'text', class: 'month-in', placeholder: placeholder || 'YYYY-MM',
    maxlength: 7, value: saved,
    oninput: (e) => {
      const v = e.target.value.trim();
      if (v === '') { saved = ''; onSet(null); return; }
      const m = v.match(/^(\d{4})-(\d{1,2})$/);
      if (m && +m[2] >= 1 && +m[2] <= 12) {
        saved = `${m[1]}-${String(+m[2]).padStart(2, '0')}`;
        onSet(saved);
      }
    },
    onchange: (e) => { e.target.value = saved; },
  });
  return input;
}

function supportLabel(support) {
  // "Jun 2026: DOE DE-SC0023122 50% · Princeton SUB0000919 50%"
  if (!support || !support.shares || !support.shares.length) return '—';
  const parts = support.shares.map((s) => {
    const proj = DATA.projects.find((p) => p.id === s.project);
    const name = proj ? proj.shortName : s.project;
    return support.shares.length > 1 ? `${name} ${Math.round(s.pct * 100)}%` : name;
  });
  return `${fmtMonth(support.month)}: ${parts.join(' · ')}`;
}

function renderPeople() {
  const box = $('#people');
  const tbl = el('table', { class: 'people' },
    el('tr', {},
      el('th', {}, 'Name'), el('th', { class: 'num' }, 'Salary ($/mo)'),
      el('th', { class: 'num' }, 'Fringe (%)'), el('th', { class: 'num' }, 'Fees ($/yr)'),
      el('th', {}, 'Expected end'), el('th', {}, 'Pay change'),
      el('th', {}, 'Current support'), el('th', {})));

  const filter = grantFilter();
  const onSelected = (det) => {
    const shares = (det && det.support && det.support.shares) || [];
    return shares.some((sh) => filter.selectedSet.has(sh.project));
  };

  for (const person of CFG.people) {
    const det = DATA.people.find((d) => d.name === person.name);
    const grayedOut = filter.filterActive && !onSelected(det);
    const numIn = (key, scale, step) => el('input', {
      type: 'number', step: step || 1,
      value: scale ? Math.round(person[key] * scale * 100) / 100 : Math.round(person[key] * 100) / 100,
      oninput: (e) => {
        const v = parseFloat(e.target.value);
        person[key] = isNaN(v) ? 0 : (scale ? v / scale : v);
        save(); renderSim(); renderSummary();
      },
    });
    tbl.append(el('tr', {
      class: grayedOut ? 'filtered-out' : '',
      title: grayedOut ? 'not supported by any selected award' : '',
    },
      el('td', { class: 'name-cell' },
        el('input', {
          type: 'text', value: person.name, title: person.name,
          oninput: (e) => { person.name = e.target.value; save(); renderSim(); },
        }),
        el('span', {
          class: 'dot',
          style: `background:${personColors.get(person.name) || 'var(--muted)'};margin-left:5px`,
          title: (personColors.has(person.name)
            ? 'color matches this person in the summary chart'
            : 'gray: not part of the summary projection (no recent payroll match)')
            + (det && det.facultySalary
              ? ' — faculty summer salary, only charged '
                + (det.paidMonthNums || []).map((n) => MONTH_NAMES[n - 1]).join('/')
              : ''),
        }),
        person.source !== 'payroll' ? el('span', { class: 'badge', style: 'margin-left:5px' }, 'manual') : null),
      el('td', { class: 'num' }, numIn('monthlySalary', 0, 50)),
      el('td', { class: 'num' }, numIn('fringeRate', 100, 0.1)),
      el('td', { class: 'num' }, numIn('annualFees', 0, 100)),
      el('td', {}, (() => {
        const inp = monthInput(person.endMonth,
          (v) => { person.endMonth = v; save(); renderSummary(); });
        inp.title = 'expected graduation / rotation off your funding — the summary projection drops them after this month';
        return inp;
      })()),
      el('td', { class: 'paychange' },
        (() => {
          const inp = monthInput(person.payChangeMonth,
            (v) => { person.payChangeMonth = v; save(); renderSummary(); });
          inp.title = 'month a scheduled pay change takes effect';
          return inp;
        })(),
        ' → $',
        el('input', {
          type: 'number', step: 50, placeholder: 'new /mo', class: 'newpay',
          value: person.payChangeSalary ?? '',
          title: 'new monthly salary from that month on',
          oninput: (e) => {
            const v = parseFloat(e.target.value);
            person.payChangeSalary = isNaN(v) ? null : v;
            save(); renderSummary();
          },
        })),
      el('td', {
        class: 'muted-cell support-cell',
        title: supportLabel(det && det.support),
      }, supportLabel(det && det.support)),
      el('td', {}, el('button', {
        class: 'btn danger btn-x', title: 'Remove person',
        onclick: () => {
          CFG.people = CFG.people.filter((p) => p !== person);
          CFG.assignments = CFG.assignments.filter((a) => a.personId !== person.id);
          save(); renderSummary(); renderPeople(); renderAssignments(); renderSim();
        },
      }, '✕'))));
  }
  box.replaceChildren(el('div', { class: 'people-wrap' }, tbl));
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

/* ----- portfolio summary figure: balance line over stacked cost bars ----- */

function summaryChart(timeline, projStart, lineSeries, barSeries, opts) {
  const { W = 960, padL = 56, lineH = 150, barH = 88, gap = 24,
          barMax = 0, maxTicks = 7 } = opts || {};
  const padR = 14, padT = 8, xLabH = 18;
  const H = padT + lineH + gap + barH + xLabH;
  const NS = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(NS, 'svg');
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  svg.setAttribute('width', '100%');
  svg.style.display = 'block';

  const slot = (W - padL - padR) / timeline.length;
  const x = (i) => padL + (i + 0.5) * slot;

  const mkLine = (x1, x2, y1, y2, cls) => {
    const l = document.createElementNS(NS, 'line');
    l.setAttribute('x1', x1); l.setAttribute('x2', x2);
    l.setAttribute('y1', y1); l.setAttribute('y2', y2);
    l.setAttribute('class', cls);
    return l;
  };
  const mkText = (tx, ty, anchor, str) => {
    const t = document.createElementNS(NS, 'text');
    t.setAttribute('x', tx); t.setAttribute('y', ty);
    t.setAttribute('text-anchor', anchor);
    t.textContent = str;
    return t;
  };

  // ---- top panel: available funds ----
  const lv = lineSeries.flatMap((s) => s.values).filter((v) => v !== null && isFinite(v));
  const lo = Math.min(0, ...lv), hi = Math.max(0, ...lv);
  const lspan = (hi - lo) || 1;
  const ly = (v) => padT + (hi - v) / lspan * lineH;
  const lstep = niceStep(lspan / 3);
  for (let v = Math.ceil(lo / lstep) * lstep; v <= hi + 1; v += lstep) {
    svg.append(mkLine(padL, W - padR, ly(v), ly(v),
      Math.abs(v) < lstep / 100 ? 'zeroline' : 'gridline'));
    svg.append(mkText(padL - 6, ly(v) + 3.5, 'end', fmtK(v)));
  }
  for (const s of lineSeries) {
    const path = document.createElementNS(NS, 'path');
    let d = '', pen = false;
    s.values.forEach((v, i) => {
      if (v === null || !isFinite(v)) { pen = false; return; }
      d += `${pen ? 'L' : 'M'}${x(i).toFixed(1)},${ly(v).toFixed(1)}`;
      pen = true;
    });
    path.setAttribute('d', d);
    path.setAttribute('fill', 'none');
    path.setAttribute('stroke', s.dashed ? 'var(--muted)' : 'var(--series-1)');
    path.setAttribute('stroke-width', '2');
    if (s.dashed) path.setAttribute('stroke-dasharray', '4 4');
    svg.append(path);
    if (s.endLabel) {
      const nonNull = s.values.filter((v) => v !== null && isFinite(v));
      const last = nonNull[nonNull.length - 1];
      if (last === undefined) continue;
      const offset = ly(last) < (padT + lineH) / 2 ? 13 : -7;
      const t = mkText(W - padR, Math.max(padT + 9, Math.min(padT + lineH - 3, ly(last) + offset)),
        'end', fmtK(last) + ' at end');
      t.setAttribute('font-weight', '600');
      t.setAttribute('fill', last < 0 ? 'var(--critical)' : 'var(--good-text)');
      svg.append(t);
    }
  }

  // ---- bottom panel: stacked monthly costs ----
  const barTop = padT + lineH + gap, barBot = barTop + barH;

  // broken y-axis: one vertical axis segment per panel, with a visible gap
  // between them signalling that the scale changes
  svg.append(mkLine(padL, padL, padT, padT + lineH, 'axis'));
  svg.append(mkLine(padL, padL, barTop, barBot, 'axis'));
  const caption = (y, str) => {
    const t = mkText(W - padR, y, 'end', str);
    t.setAttribute('font-size', '10');
    t.setAttribute('font-style', 'italic');
    svg.append(t);
  };
  caption(padT + 9, 'available funds');
  caption(barTop - 4, 'monthly spend');
  const stackTot = timeline.map((_, i) => barSeries.reduce((a, s) => a + Math.max(0, s.values[i]), 0));
  const bmax = Math.max(1, barMax, ...stackTot);
  const by = (v) => barBot - v / bmax * barH;
  const bstep = niceStep(bmax / 2);
  for (let v = 0; v <= bmax + 1; v += bstep) {
    svg.append(mkLine(padL, W - padR, by(v), by(v), v === 0 ? 'zeroline' : 'gridline'));
    svg.append(mkText(padL - 6, by(v) + 3.5, 'end', fmtK(v)));
  }
  const bw = Math.max(2, slot - 2);
  timeline.forEach((m, i) => {
    let cum = 0;
    for (const s of barSeries) {
      const v = Math.max(0, s.values[i]);
      if (v < 0.5) continue;
      const r = document.createElementNS(NS, 'rect');
      r.setAttribute('x', x(i) - bw / 2);
      r.setAttribute('y', by(cum + v));
      r.setAttribute('width', bw);
      r.setAttribute('height', Math.max(0.5, by(cum) - by(cum + v)));
      r.setAttribute('fill', s.color);
      r.setAttribute('fill-opacity', i >= projStart ? '0.45' : '0.9');
      svg.append(r);
      cum += v;
    }
  });

  // ---- shared x labels ----
  const every = Math.max(1, Math.ceil(timeline.length / maxTicks));
  timeline.forEach((m, i) => {
    if (i % every !== 0 && i !== timeline.length - 1) return;
    if (i !== timeline.length - 1 && timeline.length - 1 - i < every) return;
    svg.append(mkText(x(i), H - 4, i === timeline.length - 1 ? 'end' : 'middle', fmtMonth(m)));
  });

  // ---- crosshair + tooltip across both panels ----
  const cross = mkLine(0, 0, padT, barBot, 'gridline');
  cross.setAttribute('visibility', 'hidden');
  svg.append(cross);
  svg.addEventListener('mousemove', (e) => {
    const rect = svg.getBoundingClientRect();
    const fx = (e.clientX - rect.left) / rect.width * W;
    const i = Math.floor((fx - padL) / slot);
    if (i < 0 || i >= timeline.length) { cross.setAttribute('visibility', 'hidden'); hideTip(); return; }
    cross.setAttribute('x1', x(i)); cross.setAttribute('x2', x(i));
    cross.setAttribute('visibility', 'visible');
    const bal = lineSeries.map((s) => s.values[i]).find((v) => v !== null && isFinite(v));
    const parts = barSeries
      .map((s) => ({ name: s.name, v: Math.max(0, s.values[i]) }))
      .filter((p) => p.v > 0.5)
      .sort((a, b) => b.v - a.v);
    const top = parts.slice(0, 3).map((p) => `${p.name} ${fmtK(p.v)}`).join(' · ');
    showTip(`${fmtMonth(timeline[i])} — available ${bal !== undefined ? fmt$(bal) : '—'}`
      + ` · out ${fmt$(stackTot[i])}${top ? ` (${top}${parts.length > 3 ? ', …' : ''})` : ''}`,
      e.clientX, e.clientY);
  });
  svg.addEventListener('mouseleave', () => { cross.setAttribute('visibility', 'hidden'); hideTip(); });
  return svg;
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

  const all = series.flatMap((s) => s.values).filter((v) => v !== null && isFinite(v));
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
    if (i !== months.length - 1 && months.length - 1 - i < every) return;
    const label = document.createElementNS(NS, 'text');
    label.setAttribute('x', x(i)); label.setAttribute('y', H - 6);
    label.setAttribute('text-anchor', i === months.length - 1 ? 'end' : 'middle');
    label.textContent = fmtMonth(m);
    svg.append(label);
  });

  for (const s of series) {
    const path = document.createElementNS(NS, 'path');
    let d = '', pen = false;
    s.values.forEach((v, i) => {
      if (v === null || !isFinite(v)) { pen = false; return; }
      d += `${pen ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
      pen = true;
    });
    path.setAttribute('d', d);
    path.setAttribute('fill', 'none');
    path.setAttribute('stroke', s.dashed ? 'var(--muted)' : 'var(--series-1)');
    path.setAttribute('stroke-width', '2');
    if (s.dashed) path.setAttribute('stroke-dasharray', '4 4');
    svg.append(path);
    if (s.endLabel) {
      const nonNull = s.values.filter((v) => v !== null && isFinite(v));
      const last = nonNull[nonNull.length - 1];
      if (last === undefined) continue;
      // place the label below the line end when the line ends high, above when low
      const offset = y(last) < (H - padB) / 2 ? 13 : -7;
      const t = document.createElementNS(NS, 'text');
      t.setAttribute('x', W - padR);
      t.setAttribute('y', Math.max(padT + 9, Math.min(H - padB - 3, y(last) + offset)));
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
    const parts = series
      .filter((s) => s.values[i] !== null && isFinite(s.values[i]))
      .map((s) => (series.length > 1 ? `${s.name}: ` : '') + fmt$(s.values[i]));
    if (!parts.length) { cross.setAttribute('visibility', 'hidden'); hideTip(); return; }
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
$('#show-notes').addEventListener('change', () => {
  CFG.ui.showNotes = $('#show-notes').checked;
  save();
  renderFlags();
});
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
