/* UTKGrantDashboard front-end. Vanilla JS, no external dependencies.
   All rendering is DOM-built (no innerHTML with data) so names from the
   CSVs can never inject markup. */
'use strict';

let DATA = null;   // payload from /api/data
let CFG = null;    // { people:[], assignments:[], overrides:{} }
let saveTimer = null;
let personColors = new Map();  // name -> color, assigned by the summary chart
let cardModel = null;          // per-award projection model, built by renderSummary

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
const fmtBytes = (n) => (n >= 1e6 ? (n / 1e6).toFixed(0) + ' MB'
  : n >= 1e3 ? (n / 1e3).toFixed(0) + ' kB' : n + ' B');

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
  CFG.escalation = CFG.escalation && typeof CFG.escalation === 'object'
    ? CFG.escalation : { ut: 0.03, gra: 0.05, fees: 0.02 };
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
  if (collapsed.getdata === undefined) {
    // wide open on a first run with no exports yet; tucked away once there
    // is data to look at (the header button re-opens it)
    collapsed.getdata = DATA.files.some((f) => f.type !== 'unrecognized');
  }
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
  renderCharges();
  renderGetData();
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
  // "past its end date but still active" is exactly what a kept-active late
  // renewal looks like — no point flagging what the user already marked
  const flags = DATA.flags.filter((f) =>
    !(f.kind === 'past_end' && (CFG.overrides[f.project] || {}).forceActive));
  const shown = flags.filter((f) => showNotes || f.severity !== 'info');
  const hidden = flags.length - shown.length;
  if (!flags.length) {
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
  // the award selection scoping the summary, cards, and people graying;
  // isActive honours the per-award "treat as active" override, and
  // effectiveEnd supplies the month its money lasts through
  const all = DATA.projects.filter((p) =>
    p.inDashboard && isActive(p) && effectiveEnd(p));
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
  const missingNote = missingAwardsNote(filter);
  if (!filter.all.length) {
    if (missingNote) box.append(missingNote);
    return;
  }

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
  if (missingNote) box.append(missingNote);

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
  // plus manual people given planned support (future hires) — minus anyone
  // whose expected end has passed or whose support is all on unselected awards
  const team = [];
  for (const person of CFG.people) {
    const det = DATA.people.find((d) => d.name === person.name);
    if (person.endMonth && person.endMonth < curMonth) continue;
    if (det && det.lastPaid && monthDiff(det.lastPaid, curMonth) <= 2
        && shareMults(det).frac > 0) {
      team.push({ person, det });
    } else if (!det && (person.plannedSupport || []).length) {
      const synth = {
        facultySalary: false, paidMonthNums: [], salaryByProject: {},
        gra: (person.fringeRate || 0) < 0.13,  // grad-level fringe => GRA raises
        support: { shares: person.plannedSupport.map((s) =>
          ({ project: s.project, pct: (s.pct || 0) / 100 })) },
      };
      if (shareMults(synth).frac > 0) team.push({ person, det: synth });
    }
  }
  // escalation: raises compound each August in projected months (UT and
  // GRA salary rates, plus a fees/tuition rate — editable in People).
  // A scheduled pay change resets the escalation reference: its amount is
  // taken at face value for its month and escalates from there.
  const esc = CFG.escalation;
  const augustsBetween = (fromM, toM) => {
    if (toM <= fromM) return 0;
    let n = 0;
    for (let y = +fromM.slice(0, 4); y <= +toM.slice(0, 4); y++) {
      const aug = `${y}-08`;
      if (aug > fromM && aug <= toM) n++;
    }
    return n;
  };
  const escFactor = (rate, fromM, toM) => Math.pow(1 + (rate || 0), augustsBetween(fromM, toM));
  const salaryAt = (person, det, m) => {
    let base = person.monthlySalary || 0;
    let ref = curMonth;
    if (person.payChangeMonth && person.payChangeSalary != null
        && m >= person.payChangeMonth) {
      base = person.payChangeSalary;
      ref = person.payChangeMonth;
    }
    return base * escFactor(det && det.gra ? esc.gra : esc.ut, ref, m);
  };
  const feesAt = (person, m) => (person.annualFees || 0) / 12 * escFactor(esc.fees, curMonth, m);
  const personnelFor = (m) => {
    const mm = +m.slice(5, 7);
    let sum = 0;
    for (const { person, det } of team) {
      if (person.endMonth && m > person.endMonth) continue;
      if (person.startMonth && m < person.startMonth) continue;
      const { frac, mult } = shareMults(det);
      const loaded = salaryAt(person, det, m) * (1 + (person.fringeRate || 0)) * mult;
      const fees = feesAt(person, m) * frac;
      if (!det.facultySalary || (det.paidMonthNums || []).includes(mm)) sum += loaded;
      sum += fees;
    }
    return sum;
  };

  // non-personnel spending trend: 12-month average of the "other" component
  // (projects without transaction detail contribute their whole burn here)
  // per-award non-personnel trend: calendar-month average (quiet months
  // count as zero) anchored at the last complete month, with the F&A
  // generated by personnel removed — that travels with each person instead
  const projectOtherTrend = (p) => {
    const parts = p.monthlyParts || {};
    const keys = Object.keys(parts).filter((m) => m < curMonth).sort();
    if (!keys.length) return p.burn.avg12 ?? p.burn.linear ?? 0;
    const fa = faOf(p.id);
    const lastComplete = monthAdd(curMonth, -1);
    const window = [];
    for (let i = 0; i < 12; i++) {
      const m = monthAdd(lastComplete, -i);
      if (m >= keys[0]) window.push(m);
    }
    if (!window.length) return 0;
    const net = window.reduce((a, m) => {
      const pt = parts[m] || {};
      const personnelIdc = ((pt.personnel || 0) + (pt.fac || 0)) * fa;
      return a + Math.max(0, (pt.other || 0) - personnelIdc);
    }, 0);
    return net / window.length;
  };
  let otherTrend = 0;
  const otherBreakdown = [];
  for (const p of active) {
    const v = projectOtherTrend(p);
    otherTrend += v;
    otherBreakdown.push(`${p.shortName}: ${fmt$(v)}/mo`);
  }

  // one person's monthly cost attributable to ONE award — the same rules
  // the summary uses, so card projections agree with the portfolio view
  const personCostOn = (pid, t, m) => {
    const { person, det } = t;
    if (person.endMonth && m > person.endMonth) return 0;
    if (person.startMonth && m < person.startMonth) return 0;
    const sh = ((det.support && det.support.shares) || [])
      .find((s) => s.project === pid);
    const pct = sh ? Math.max(0, sh.pct || 0) : 0;
    if (pct <= 0) return 0;
    const mm = +m.slice(5, 7);
    const loaded = salaryAt(person, det, m) * (1 + (person.fringeRate || 0)) * pct * (1 + faOf(pid));
    const fees = feesAt(person, m) * pct;
    return (!det.facultySalary || (det.paidMonthNums || []).includes(mm) ? loaded : 0) + fees;
  };
  cardModel = { team, personCostOn, projectOtherTrend };

  // month-by-month projection: spend from the soonest-ending award first;
  // an award's leftover balance disappears when it ends. Manually entered
  // "expected additional funding" (with its new end date) joins the pool —
  // an extended end also lets the existing balance carry forward. The
  // CURRENT month is projected, not treated as actuals (its data is
  // incomplete), so each pool starts from the last complete month's
  // balance: today's balance plus the partial spend added back.
  let extraTotal = 0;
  const pools = active
    .map((p) => {
      const ov = CFG.overrides[p.id] || {};
      const extra = ov.expectedExtra || 0;
      extraTotal += extra;
      const end = effectiveEnd(p);
      const partial = (p.monthly || {})[curMonth] || 0;
      return { end, bal: Math.max(0, p.totals.remaining - p.totals.committed + partial) + extra };
    })
    .sort((a, b) => (a.end < b.end ? -1 : 1));
  const horizon = pools[pools.length - 1].end;
  const months = monthRange(curMonth, horizon);
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
  // future hires (start month ahead) count in the projection, not in the
  // "current team" headline
  const startedNow = team.filter((t) => !t.person.startMonth || t.person.startMonth <= curMonth);
  const yearRound = startedNow.filter((t) => !t.det.facultySalary);
  const summerFolk = startedNow.filter((t) => t.det.facultySalary);
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
      [(extraTotal > 0 ? `+ ${fmt$(extraTotal)} expected (entered manually) · ` : ''),
       (expired > 0
         ? el('span', { class: 'expires-warn' }, `${fmt$(expired)} expires unspent at this pace`)
         : 'across all active awards')]),
    stat('Current team', fmt$(baseMonthly) + '/mo',
      `${yearRound.length} people year-round — salary+fringe+fees+their F&A`
        + (summerFolk.length
          ? ` · ${fmt$(peakMonthly)}/mo in ${summerMonths} (${summerFolk.map((t) => t.person.name).join(', ')} summer salary)`
          : '')
        + (team.some((t) => t.person.endMonth || t.person.payChangeMonth)
          ? ' · scheduled departures/pay changes applied' : '')),
    (() => {
      const s = stat('Other spending', fmt$(otherTrend) + '/mo',
        '12-mo trend: travel, supplies, F&A on non-salary costs — personnel F&A and fees count with each person (hover for per-award breakdown)');
      s.title = otherBreakdown.join('\n');
      return s;
    })(),
    stat('Funded through', fmtMonth(fundedThrough),
      runsOut === null
        ? 'to the end of your last award'
        : `bring in new money by then (${fmt$(unmet)} short through ${fmtMonth(horizon)})`,
      runwayMonths >= 12 ? 'ok' : 'bad')));

  // ground the projection with reconstructed history: total available funds
  // over the last ~18 months, rebuilt backwards from today's balances the
  // same way the per-award charts are
  // history stops at the last COMPLETE month; the current (partial) month
  // belongs to the projection
  let histMonths = [];
  const lastComplete = monthAdd(curMonth, -1);
  const detailMonths = [...new Set(active.flatMap((p) =>
    Object.keys(p.monthly || {}).filter((m) => m < curMonth)))].sort();
  if (detailMonths.length && detailMonths[0] <= lastComplete) {
    histMonths = monthRange(detailMonths[0], lastComplete).slice(-18);
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
    // join at the last complete month's actual balance
    if (i === histMonths.length - 1) return histTotals[histMonths.length - 1];
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
    const feesByProj = det.feesByProject || {};
    const values = timeline.map((m, i) => {
      if (i < projStart) {
        // history from the per-award breakdown, restricted to the selected
        // awards, with each award's own F&A folded in and the person's
        // posted fees included (gray history is a residual of actual
        // totals, so the stacks stay conserved)
        let sum = 0;
        for (const pid of Object.keys(byProj)) {
          if (!selectedSet.has(pid)) continue;
          sum += (byProj[pid][m] || 0) * (1 + histFringe) * (1 + faOf(pid));
        }
        for (const pid of Object.keys(feesByProj)) {
          if (!selectedSet.has(pid)) continue;
          sum += feesByProj[pid][m] || 0;
        }
        return sum;
      }
      if (!inTeam) return 0;
      const { person, det: d } = inTeam;
      if (person.endMonth && m > person.endMonth) return 0;
      if (person.startMonth && m < person.startMonth) return 0;
      const mm = +m.slice(5, 7);
      const { frac, mult } = shareMults(d);
      const loaded = salaryAt(person, d, m) * (1 + (person.fringeRate || 0)) * mult;
      const fees = feesAt(person, m) * frac;
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

  //card.append(el('div', { class: 'spark-title', style: 'margin-top:6px' },
  //  'Top: available funds (history, then projection; balances expire as awards end). '
  //  + 'Bottom: monthly costs by person — lighter bars are projected.'));
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
    active.slice().sort((a, b) => (effectiveEnd(a) < effectiveEnd(b) ? -1 : 1))
      .map((p) => `${p.shortName} ${fmtMonth(effectiveEnd(p))}`
        + (effectiveEnd(p) !== (p.end || '').slice(0, 7) ? ' (expected)' : ''))
      .join(' · ')));
  box.append(card);
}

function missingAwardsNote(filter) {
  // why an award someone can see elsewhere (the charge lookup, the export)
  // is absent from the summary — with the one-click fix where there is one
  const shown = new Set(filter.all.map((p) => p.id));
  const missing = DATA.projects.filter((p) => !shown.has(p.id));
  if (!missing.length) return null;
  const curMonth = DATA.today.slice(0, 7);
  const items = missing.map((p) => {
    const row = el('div', { class: 'missing-row' },
      el('b', {}, p.id),
      p.shortName && p.shortName !== p.id ? ` ${p.shortName}` : '', ' — ');
    if (!p.inDashboard) {
      row.append('only in the transaction detail export; without budget rows '
        + 'there are no balances to project. Re-export the PI Dashboard with '
        + 'this project included.');
    } else {
      const why = p.status.toLowerCase() !== 'active'
        ? `the export marks it “${p.status}”`
        : (p.end && p.end.slice(0, 7) < curMonth)
          ? `its end date (${p.end}) has passed`
          : 'the export gives it no end date';
      row.append(why + '. ', el('button', {
        class: 'btn btn-x',
        title: 'Include this award everywhere as if it were active — for late '
          + 'year-by-year renewals. The projection extends a year past the '
          + 'recorded end; adjust that with “new end” on its card.',
        onclick: () => setForceActive(p, true),
      }, 'Treat as active'));
    }
    return row;
  });
  return el('details', { class: 'missing-awards' },
    el('summary', {}, `${missing.length} award${missing.length === 1 ? '' : 's'}`
      + ' not in this summary — why?'),
    items);
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
    (p) => p.inDashboard && !hidden.has(p.id) && (showClosed || isActive(p)));

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
    const active = isActive(p);
    const forced = !!(CFG.overrides[p.id] || {}).forceActive;

    const card = el('div', { class: 'card' });
    card.append(el('div', { class: 'card-head' },
      el('h3', {}, p.shortName),
      forced
        ? el('span', {
            class: 'status-chip kept',
            title: `The export says “${p.status}”`
              + (p.end && p.end < DATA.today ? `, ended ${p.end}` : '')
              + ' — kept active by you (untick "Treat as active" below to undo)',
          }, 'Kept active')
        : el('span', { class: 'status-chip' + (active ? '' : ' closed') }, p.status)));
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


    // combined figure: balance line (top) over monthly spend by person
    // (bottom) — same construction as the portfolio summary, shared bar
    // scale across all cards
    const hasMonthly = Object.keys(p.monthly || {}).length > 0;
    const burn = p.burn.avg12 ?? p.burn.recent ?? p.burn.linear;
    const curMonth = DATA.today.slice(0, 7);
    const endMonth = p.end ? p.end.slice(0, 7) : null;
    // an expected extension (NCE / new money) stretches the projection to
    // the new end and adds the funds, mirroring the summary's treatment
    const ov = CFG.overrides[p.id] || (CFG.overrides[p.id] = {});
    const extra = active ? (ov.expectedExtra || 0) : 0;
    const effEnd = active ? effectiveEnd(p) : endMonth;
    const canProject = active && effEnd && effEnd > curMonth && cardModel;
    if ((hasMonthly && sparkDomain.length) || canProject) {
      // when projecting, the current (partial) month is modeled rather than
      // shown as actuals; history ends at the last complete month
      const histEnd = canProject ? monthAdd(curMonth, -1) : curMonth;
      const histMonths = sparkDomain.length ? monthRange(sparkDomain[0], histEnd) : [];
      const projMonths = canProject ? monthRange(curMonth, effEnd) : [];
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

      // bars: per-person history on this award, plus each person's projected
      // cost attributable to it — the exact model the summary uses
      const otherT = canProject ? cardModel.projectOtherTrend(p) : 0;
      const fa = (CFG.overrides[p.id] || {}).faRate ?? p.faRate ?? 0;
      const cfgByName = new Map(CFG.people.map((cp) => [cp.name, cp]));
      const detByName = new Map(DATA.people.map((d) => [d.name, d]));
      const teamByName = new Map((cardModel ? cardModel.team : []).map((t) => [t.person.name, t]));
      const names = new Set();
      for (const d of DATA.people) {
        if ((d.salaryByProject || {})[p.id] || (d.feesByProject || {})[p.id]) names.add(d.name);
      }
      if (canProject) {
        for (const t of cardModel.team) {
          const shares = (t.det.support && t.det.support.shares) || [];
          if (shares.some((s) => s.project === p.id && (s.pct || 0) > 0)) names.add(t.person.name);
        }
      }
      const barSeries = [];
      for (const name of names) {
        const d = detByName.get(name);
        const hist = (d && (d.salaryByProject || {})[p.id]) || {};
        const feesHist = (d && (d.feesByProject || {})[p.id]) || {};
        const fr = ((cfgByName.get(name) || d || {}).fringeRate) || 0;
        const t = teamByName.get(name);
        const values = timeline.map((m, i) => {
          if (i < projStart) return (hist[m] || 0) * (1 + fr) * (1 + fa) + (feesHist[m] || 0);
          return (canProject && t) ? cardModel.personCostOn(p.id, t, m) : 0;
        });
        if (values.some((v) => v > 1)) {
          barSeries.push({ name, color: personColors.get(name) || 'var(--ink-2)', values });
        }
      }
      barSeries.push({
        name: 'non-personnel', color: 'var(--muted)',
        values: timeline.map((m, i) => {
          if (i >= projStart) return otherT;
          const tot = (p.monthly || {})[m] || 0;
          return Math.max(0, tot - barSeries.reduce((a, s) => a + s.values[i], 0));
        }),
      });

      // projection line integrates exactly what the bars show, anchored at
      // the last complete month's balance (today's balance plus the current
      // month's partial spend added back — the full month is then modeled);
      // expected additional funding arrives as a step, like the summary
      const balAnchor = balNow + ((p.monthly || {})[curMonth] || 0);
      let balProj = balAnchor + extra;
      const trend = timeline.map((m, i) => {
        if (!canProject) return null;
        if (i === projStart - 1) return balAnchor;  // join at last complete month
        if (i < projStart) return null;
        balProj -= barSeries.reduce((a, s) => a + s.values[i], 0);
        return balProj;
      });

      const hasExpected = canProject && (extra > 0 || effEnd !== endMonth);
      const block = el('div', { class: 'spark-block' },
        el('div', { class: 'spark-title' },
          'Balance & monthly spend'
          + (canProject ? ` — projected to ${hasExpected ? 'expected end (incl. new funding)' : 'award end'}` : '')));
      block.append(summaryChart(timeline, projStart, [
        { name: 'projected', values: trend, dashed: true, endLabel: canProject },
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
      // months left to the effective end — the recorded end, an expected
      // extension, or "kept active" — matching what the projection uses
      const monthsLeft = effEnd ? monthDiff(curMonth, effEnd) : null;
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

    // late year-by-year renewals: the export can say "closed" (or carry a
    // past end date) while the next increment is simply late — one tick
    // keeps the award in the summary, projections, and defaults
    const looksClosed = p.status.toLowerCase() !== 'active'
      || (p.end && p.end.slice(0, 7) < curMonth);
    if (looksClosed) {
      card.append(el('label', { class: 'check keep-active' },
        el('input', {
          type: 'checkbox', checked: forced || null,
          onchange: (e) => setForceActive(p, e.target.checked),
        }),
        ' Treat as active — the export '
        + (p.status.toLowerCase() !== 'active'
          ? `marks this “${p.status}”` : `says it ended ${p.end}`)
        + ', but a late renewal looks exactly like this.'));
    }

    // manually entered future funding (persists in config.json; feeds the
    // portfolio summary's funded-through projection)
    if (active) {
      const endInput = monthInput(ov.expectedEnd,
        (v) => { ov.expectedEnd = v; save(); renderSummary(); });
      // redraw the card chart once the user leaves the field (re-rendering
      // per keystroke would steal focus from these very inputs)
      endInput.addEventListener('blur', () => renderPortfolio());
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
          onblur: () => renderPortfolio(),
        }),
        el('span', { class: 'sep' }, 'new end'),
        endInput));
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

function plannedSupportCell(person) {
  // manual people: one grant that will pay them, feeding the summary
  // projection. Timing comes from the other columns: start someone later
  // with salary 0 + a Pay change; stop them with Expected end.
  const cell = el('td', { class: 'support-edit' });
  const activeAwards = grantFilter().all;
  const current = (person.plannedSupport || [])[0];
  cell.append(el('select', {
    title: 'Grant that will pay this person (100%). To start them later, set '
      + 'salary 0 and schedule a Pay change; use Expected end to stop them.',
    onchange: (e) => {
      person.plannedSupport = e.target.value
        ? [{ project: e.target.value, pct: 100 }] : [];
      save(); renderSummary(); renderPortfolio(); renderPeople();
    },
  },
    el('option', { value: '' }, '— pick a grant —'),
    activeAwards.map((a) => el('option', {
      value: a.id, selected: (current && a.id === current.project) || null,
    }, a.shortName))));
  return cell;
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
  const onSelected = (person, det) => {
    const shares = (det && det.support && det.support.shares)
      || (person.plannedSupport || []).map((s) => ({ project: s.project }));
    return shares.some((sh) => filter.selectedSet.has(sh.project));
  };

  for (const person of CFG.people) {
    const det = DATA.people.find((d) => d.name === person.name);
    const grayedOut = filter.filterActive && !onSelected(person, det);
    const numIn = (key, scale, step) => el('input', {
      type: 'number', step: step || 1,
      value: scale ? Math.round(person[key] * scale * 100) / 100 : Math.round(person[key] * 100) / 100,
      oninput: (e) => {
        const v = parseFloat(e.target.value);
        person[key] = isNaN(v) ? 0 : (scale ? v / scale : v);
        save(); renderSummary(); renderPortfolio();
      },
    });
    tbl.append(el('tr', {
      class: grayedOut ? 'filtered-out' : '',
      title: grayedOut ? 'not supported by any selected award' : '',
    },
      el('td', { class: 'name-cell' },
        el('input', {
          type: 'text', value: person.name, title: person.name,
          oninput: (e) => { person.name = e.target.value; save(); },
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
          (v) => { person.endMonth = v; save(); renderSummary(); renderPortfolio(); });
        inp.title = 'expected graduation / rotation off your funding — the summary projection drops them after this month';
        return inp;
      })()),
      el('td', { class: 'paychange' },
        (() => {
          const inp = monthInput(person.payChangeMonth,
            (v) => { person.payChangeMonth = v; save(); renderSummary(); renderPortfolio(); });
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
            save(); renderSummary(); renderPortfolio();
          },
        })),
      det
        ? el('td', {
            class: 'muted-cell support-cell',
            title: supportLabel(det.support),
          }, supportLabel(det.support))
        : plannedSupportCell(person),
      el('td', {}, el('button', {
        class: 'btn danger btn-x', title: 'Remove person',
        onclick: () => {
          CFG.people = CFG.people.filter((p) => p !== person);
          save(); renderSummary(); renderPortfolio(); renderPeople();
        },
      }, '✕'))));
  }
  const esc = CFG.escalation;
  const escIn = (key, title) => el('input', {
    type: 'number', step: 0.5, class: 'esc-in', title,
    value: Math.round(esc[key] * 1000) / 10,
    oninput: (e) => {
      const v = parseFloat(e.target.value);
      esc[key] = isNaN(v) ? 0 : v / 100;
      save(); renderSummary(); renderPortfolio();
    },
  });
  const escRow = el('div', { class: 'esc-row' },
    'Escalation each August (projections only): UT salaries ',
    escIn('ut', 'annual raise for faculty, postdocs, and staff'), '% · GRA salaries ',
    escIn('gra', 'annual raise for graduate assistants (detected from payroll type)'), '% · fees/tuition ',
    escIn('fees', 'annual increase applied to fees and tuition'), '%');

  box.replaceChildren(el('div', { class: 'people-wrap' }, tbl), escRow);
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
      // hovering one segment shows that series alone, not the whole stack
      r.addEventListener('mousemove', (e) => {
        e.stopPropagation();
        cross.setAttribute('x1', x(i)); cross.setAttribute('x2', x(i));
        cross.setAttribute('visibility', 'visible');
        showTip(`${fmtMonth(m)} — ${s.name}: ${fmt$(v)} of ${fmt$(stackTot[i])} out`
          + (i >= projStart ? ' (projected)' : ''), e.clientX, e.clientY);
      });
      r.addEventListener('mouseleave', hideTip);
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

function niceStep(raw) {
  const mag = Math.pow(10, Math.floor(Math.log10(Math.max(1, raw))));
  for (const m of [1, 2, 2.5, 5, 10]) if (raw <= m * mag) return m * mag;
  return 10 * mag;
}

/* ----- charge lookup ----- */
/* Pick an award and a date window; /api/charges returns every transaction
   from the detail exports that posted to it. Rendered two ways: a
   month-by-type grid (a charge you expected but don't see is a visible gap)
   and the full line-by-line list. */

let CHARGES = null;       // last /api/charges response, tagged with its inputs
let chargesText = '';     // client-side text filter (not persisted)
let chargesShowAll = false;
let chargesSeq = 0;       // drop out-of-order fetch responses

const fmtCents = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' });

function chargesState() {
  const ui = CFG.ui;
  ui.charges = ui.charges && typeof ui.charges === 'object' ? ui.charges : {};
  const st = ui.charges;
  const withDetail = DATA.projects.filter((p) => p.hasDetail);
  // migrate the single-award field this state used to hold
  if (!Array.isArray(st.projects) && typeof st.project === 'string' && st.project) {
    st.projects = [st.project];
  }
  delete st.project;
  if (Array.isArray(st.projects)) {
    st.projects = st.projects.filter((id) => withDetail.some((p) => p.id === id));
  } else {
    // first visit: start with one active award; an emptied selection stays empty
    const first = withDetail.find(isActive) || withDetail[0];
    st.projects = first ? [first.id] : [];
  }
  // "the last few months" is the common question, so that's the default
  if (typeof st.from !== 'string' || !st.from) {
    st.from = monthAdd(DATA.today.slice(0, 7), -3) + '-01';
  }
  if (typeof st.to !== 'string') st.to = '';
  return st;
}

function renderCharges() {
  const box = $('#charges');
  box.replaceChildren();
  const withDetail = DATA.projects.filter((p) => p.hasDetail);
  if (!withDetail.length) {
    box.append(el('p', { class: 'hint' },
      'No transaction detail loaded yet — download the expenditure detail '
      + 'report (under Get fresh data) and every charge becomes searchable here.'));
    return;
  }
  const st = chargesState();
  const card = el('div', { class: 'charges-card' });
  const chips = el('div', { class: 'chips', style: 'margin-top:0' });
  for (const p of withDetail) {
    const on = st.projects.includes(p.id);
    chips.append(el('label', { class: 'chip' + (on ? ' on' : ''), title: p.name },
      el('input', {
        type: 'checkbox', checked: on || null,
        onchange: (e) => {
          st.projects = e.target.checked
            ? st.projects.concat([p.id])
            : st.projects.filter((id) => id !== p.id);
          save(); renderCharges();
        },
      }),
      p.id,
      el('span', { class: 'chip-name' }, p.shortName || ''),
      isActive(p) ? null : el('span', { class: 'chip-closed' }, 'closed')));
  }
  card.append(chips);
  card.append(el('div', { class: 'getdata-row' },
    el('label', { class: 'check' }, 'from ',
      el('input', {
        type: 'date', value: st.from,
        onchange: (e) => { st.from = e.target.value || ''; save(); refreshCharges(); },
      })),
    el('label', { class: 'check' }, 'through ',
      el('input', {
        type: 'date', value: st.to || DATA.today,
        title: 'leave at today to include everything since the from-date',
        onchange: (e) => { st.to = e.target.value || ''; save(); refreshCharges(); },
      })),
    el('label', { class: 'check' }, 'filter ',
      el('input', {
        type: 'search', class: 'charges-filter',
        placeholder: 'type, person, trx #…', value: chargesText,
        oninput: (e) => { chargesText = e.target.value; chargesShowAll = false; renderChargesResults(); },
      }))));
  card.append(el('div', { id: 'charges-results' }));
  box.append(card);
  refreshCharges();
}

async function refreshCharges() {
  const out = $('#charges-results');
  if (!out) return;
  const st = chargesState();
  if (!st.projects.length) {
    CHARGES = null;
    out.replaceChildren(el('p', { class: 'hint' }, 'Pick at least one award.'));
    return;
  }
  // same awards, window, and underlying data as last time: just re-render
  const wanted = JSON.stringify([st.projects, st.from, st.to, DATA.generated]);
  if (CHARGES && CHARGES.wanted === wanted) { renderChargesResults(); return; }
  const seq = ++chargesSeq;
  chargesShowAll = false;
  const params = new URLSearchParams({ project: st.projects.join(' ') });
  if (st.from) params.set('from', st.from);
  if (st.to) params.set('to', st.to);
  out.replaceChildren(el('p', { class: 'hint' }, 'Loading charges…'));
  try {
    const res = await fetch('/api/charges?' + params.toString());
    const payload = await res.json();
    if (payload.error) throw new Error(payload.error);
    if (seq !== chargesSeq) return;   // a newer request superseded this one
    CHARGES = payload;
    CHARGES.wanted = wanted;
  } catch (err) {
    if (seq !== chargesSeq) return;
    CHARGES = null;
    out.replaceChildren(el('p', { class: 'hint' },
      'Could not load charges: ' + (err.message || err)));
    return;
  }
  renderChargesResults();
}

function chargeMatches(c, q) {
  const hay = `${c.project} ${c.date || ''} ${c.category} ${c.type} ${c.person} `
    + `${c.trx} ${c.desc || ''} ${c.vendor || ''} ${c.amount}`;
  return q.split(/\s+/).every((w) => !w || hay.toLowerCase().includes(w));
}

function renderChargesResults() {
  const out = $('#charges-results');
  if (!out || !CHARGES) return;
  out.replaceChildren();
  const st = chargesState();
  const multi = CHARGES.projects.length > 1;
  const shortOf = new Map(DATA.projects.map((p) => [p.id, p.shortName]));

  // an empty month can mean "nothing charged" or "the export doesn't cover
  // it" — very different answers when checking that everything is there
  const uncovered = CHARGES.projects
    .map((id) => DATA.projects.find((p) => p.id === id))
    .filter((p) => p && p.detailWindow
      && ((st.from && st.from < p.detailWindow[0])
          || (st.to || DATA.today) > p.detailWindow[1]));
  if (uncovered.length) {
    // group awards sharing the same export window into one clause
    const byWin = new Map();
    for (const p of uncovered) {
      const w = `${p.detailWindow[0]} → ${p.detailWindow[1]}`;
      byWin.set(w, (byWin.get(w) || []).concat([p.id]));
    }
    out.append(el('p', { class: 'charges-coverage' },
      '⚠ The loaded detail export covers '
      + [...byWin.entries()].map(([w, ids]) =>
        `${w} for ${multi ? ids.join(', ') : 'this award'}`).join('; ')
      + '. Months outside that window look empty even if charges exist — '
      + 'download a wider export under Get fresh data to see them.'));
  }

  const q = chargesText.trim().toLowerCase();
  const charges = q ? CHARGES.charges.filter((c) => chargeMatches(c, q)) : CHARGES.charges;
  if (!charges.length) {
    out.append(el('p', { class: 'hint' },
      (CHARGES.count ? 'No charges match the filter'
        : `No charges found on ${CHARGES.projects.join(', ')} in this window`)
      + '.'));
    return;
  }

  const total = charges.reduce((a, c) => a + c.amount, 0);
  const credits = charges.filter((c) => c.amount < 0);
  out.append(el('div', { class: 'charges-headline' },
    el('b', {}, `${charges.length} charge${charges.length === 1 ? '' : 's'}`),
    ` · net ${fmtCents.format(total)}`,
    q ? ` · filtered from ${CHARGES.count}` : '',
    credits.length
      ? ` · includes ${credits.length} credit${credits.length === 1 ? '' : 's'} `
        + `(${fmtCents.format(credits.reduce((a, c) => a + c.amount, 0))})`
      : '',
    CHARGES.undated
      ? ` · ${CHARGES.undated} line${CHARGES.undated === 1 ? '' : 's'} with no date (always shown)`
      : ''));

  // ---- month-by-type grid: rows are category → type (per person for
  // payroll), columns are every month of the window — gaps stay visible ----
  const dated = charges.filter((c) => c.date);
  const present = [...new Set(dated.map((c) => c.date.slice(0, 7)))].sort();
  let cols = [];
  if (present.length) {
    let a = present[0], b = present[present.length - 1];
    if (st.from && st.from.slice(0, 7) < a) a = st.from.slice(0, 7);
    const wantTo = (st.to || DATA.today).slice(0, 7);
    if (wantTo > b) b = wantTo;
    cols = monthRange(a, b);
  }
  const hasUndated = charges.some((c) => !c.date);
  if (hasUndated) cols = cols.concat(['undated']);

  const rows = new Map();  // category \0 type \0 person (\0 project) -> row
  for (const c of charges) {
    const key = `${c.category} ${c.type} ${c.person}`
      + (multi ? ` ${c.project}` : '');
    let r = rows.get(key);
    if (!r) {
      r = { category: c.category, type: c.type, person: c.person,
            project: c.project, byMonth: {}, total: 0 };
      rows.set(key, r);
    }
    const m = c.date ? c.date.slice(0, 7) : 'undated';
    r.byMonth[m] = (r.byMonth[m] || 0) + c.amount;
    r.total += c.amount;
  }
  const rowList = [...rows.values()].sort((a, b) =>
    a.category.localeCompare(b.category) || a.type.localeCompare(b.type)
    || a.person.localeCompare(b.person) || a.project.localeCompare(b.project));

  const moLabel = (m) => (m === 'undated' ? 'no date'
    : MONTH_NAMES[+m.slice(5, 7) - 1] + ' ’' + m.slice(2, 4));
  const cell = (v) => (v === undefined
    ? el('td', { class: 'cell-empty' }, '—')
    : el('td', { class: v < -0.005 ? 'neg' : '' }, fmt$(v)));

  if (cols.length && rowList.length) {
    const grid = el('table', { class: 'cats charges-grid' });
    grid.append(el('tr', {},
      el('th', {}, 'Charge'),
      cols.map((m) => el('th', { title: m === 'undated' ? '' : fmtMonth(m) }, moLabel(m))),
      el('th', {}, 'Total')));
    let lastCat = null;
    for (const r of rowList) {
      if (r.category !== lastCat) {
        lastCat = r.category;
        grid.append(el('tr', { class: 'cat-row' },
          el('td', { colspan: cols.length + 2 }, r.category || '(no category)')));
      }
      const award = multi ? (shortOf.get(r.project) || r.project) : '';
      grid.append(el('tr', {},
        el('td', {
          class: 'charge-label',
          title: r.type + (r.person ? ' — ' + r.person : '') + (award ? ` (${r.project})` : ''),
        },
          r.type || '(no type)',
          r.person ? el('span', { class: 'muted-cell' }, ' — ' + r.person) : '',
          award ? el('span', { class: 'muted-cell' }, ' · ' + award) : ''),
        cols.map((m) => cell(r.byMonth[m])),
        el('td', { class: r.total < -0.005 ? 'neg' : '' }, fmt$(r.total))));
    }
    grid.append(el('tr', { class: 'total-row' },
      el('td', {}, 'Total'),
      cols.map((m) => {
        const v = rowList.reduce((a, r) => a + (r.byMonth[m] || 0), 0);
        return el('td', { class: v < -0.005 ? 'neg' : '' },
          rowList.some((r) => r.byMonth[m] !== undefined) ? fmt$(v) : '');
      }),
      el('td', {}, fmt$(total))));
    out.append(el('div', { class: 'spark-title', style: 'margin-top:10px' },
      'What landed each month — a blank cell means nothing posted'));
    out.append(el('div', { class: 'charges-wrap' }, grid));
  }

  // ---- every line, newest first ----
  const CAP = 200;
  const shown = chargesShowAll ? charges : charges.slice(0, CAP);
  const hasDesc = charges.some((c) => c.desc);
  const hasVend = charges.some((c) => c.vendor);
  const tbl = el('table', { class: 'cats charges-lines' },
    el('tr', {},
      el('th', {}, 'Date'),
      multi ? el('th', {}, 'Award') : null,
      el('th', {}, 'Category'), el('th', {}, 'Type'),
      hasDesc ? el('th', {}, 'Description') : null,
      hasVend ? el('th', {}, 'Vendor') : null,
      el('th', {}, 'Person'), el('th', {}, 'Trx #'), el('th', {}, 'Amount')));
  for (const c of shown) {
    tbl.append(el('tr', {},
      el('td', {}, c.date || el('span', { class: 'muted-cell' }, 'no date')),
      multi ? el('td', { class: 'muted-cell' }, c.project) : null,
      el('td', {}, c.category),
      el('td', {}, c.type),
      hasDesc ? el('td', { class: 'desc-cell', title: c.desc || '' }, c.desc || '') : null,
      hasVend ? el('td', { class: 'desc-cell', title: c.vendor || '' }, c.vendor || '') : null,
      el('td', {}, c.person),
      el('td', { class: 'muted-cell' }, c.trx),
      el('td', { class: c.amount < -0.005 ? 'neg' : '' }, fmtCents.format(c.amount))));
  }
  out.append(el('div', { class: 'spark-title', style: 'margin-top:14px' }, 'Every charge line'));
  out.append(el('div', { class: 'charges-wrap' }, tbl));
  if (charges.length > shown.length) {
    out.append(el('button', {
      class: 'btn', style: 'margin-top:8px',
      onclick: () => { chargesShowAll = true; renderChargesResults(); },
    }, `Show all ${charges.length} lines`));
  }
}

/* ----- get fresh data ----- */
/* Builds the download link for the detail report (the URL itself comes from
   spn_reports.py via /api/report-links, so there is one source of truth), then
   watches the Downloads folder so the CSV lands in data/ without a trip
   through Finder. */

let LINKS = null;          // last /api/report-links response
let INBOX = null;          // last /api/inbox response
let inboxTimer = null;     // poll handle, live for a minute after a download
let watchingSince = null;  // epoch seconds; files newer than this are "new"
let lastImport = null;     // {names, at} — the "imported ✓" note

function fetchState() {
  const ui = CFG.ui;
  ui.fetch = ui.fetch && typeof ui.fetch === 'object' ? ui.fetch : {};
  const st = ui.fetch;
  if (!Array.isArray(st.excluded)) {
    // closed awards start out unchecked; their history is already in hand
    st.excluded = DATA.projects.filter((p) => !isActive(p)).map((p) => p.id);
  }
  if (typeof st.extra !== 'string') st.extra = '';
  if (typeof st.autoImport !== 'boolean') st.autoImport = true;
  if (st.mode !== 'per-project') st.mode = 'combined';
  if (!st.from) st.from = defaultFrom();
  return st;
}

function isActive(p) {
  // the per-award "treat as active" override wins: year-by-year grants often
  // look closed in the export while the next increment is late
  if (((CFG && CFG.overrides) || {})[p.id]?.forceActive) return true;
  return p.status.toLowerCase() === 'active'
    && (!p.end || p.end.slice(0, 7) >= DATA.today.slice(0, 7));
}

function effectiveEnd(p) {
  // the month an award's money can be spent through: the report's end date,
  // extended by the "new end" override; an award kept active past its
  // recorded end lasts at least through the current month
  const ov = ((CFG && CFG.overrides) || {})[p.id] || {};
  let end = p.end ? p.end.slice(0, 7) : null;
  if (ov.expectedEnd && (!end || ov.expectedEnd > end)) end = ov.expectedEnd;
  if (ov.forceActive && (!end || end < DATA.today.slice(0, 7))) {
    end = DATA.today.slice(0, 7);
  }
  return end;
}

function setForceActive(p, on) {
  const ov = CFG.overrides[p.id] || (CFG.overrides[p.id] = {});
  ov.forceActive = on || null;
  // a late renewal usually means another year: default the projection end a
  // year past the recorded end — visible and editable as "new end" on the card
  if (on && !ov.expectedEnd && p.end && p.end.slice(0, 7) < DATA.today.slice(0, 7)) {
    ov.expectedEnd = monthAdd(p.end.slice(0, 7), 12);
  }
  save(); renderAll();
}

function defaultFrom() {
  // the widest window worth asking for: back to the oldest award we know of
  const starts = DATA.projects.map((p) => p.start).filter(Boolean).sort();
  return starts[0] || (+DATA.today.slice(0, 4) - 3) + '-01-01';
}

function fetchProjects() {
  // ticked awards plus whatever is typed in the "also include" box, left as
  // typed — the server canonicalizes it (bare numbers, case, duplicates)
  const st = fetchState();
  const excluded = new Set(st.excluded);
  const known = DATA.projects.filter((p) => !excluded.has(p.id)).map((p) => p.id);
  return known.concat(st.extra.trim() ? [st.extra] : []);
}

function renderGetData() {
  const box = $('#getdata');
  const st = fetchState();
  box.replaceChildren();

  // step 1 — the budget export, which has to be driven by hand
  const dash = el('div', { class: 'getdata-step' },
    el('div', { class: 'step-head' }, el('span', { class: 'step-num' }, '1'),
      'PI Dashboard export — budgets and balances'),
    el('div', { class: 'hint' },
      'Project Summary → your name in Project PI / Manager → export the table '
      + 'as CSV. This one needs a few clicks in the reporting tool; the link '
      + 'opens it on the right page.'));
  if (DATA.reportSource && DATA.reportSource.piDashboardUrl) {
    dash.append(el('a', {
      class: 'btn', href: DATA.reportSource.piDashboardUrl,
      target: '_blank', rel: 'noopener',
    }, 'Open PI Dashboard ↗'));
  }
  box.append(dash);

  // step 2 — the detail export, which is one click
  const projects = fetchProjects();
  const chips = el('div', { class: 'chips' });
  for (const p of DATA.projects) {
    const on = !st.excluded.includes(p.id);
    chips.append(el('label', { class: 'chip' + (on ? ' on' : ''), title: p.name },
      el('input', {
        type: 'checkbox', checked: on || null,
        onchange: (e) => {
          st.excluded = e.target.checked
            ? st.excluded.filter((id) => id !== p.id)
            : st.excluded.concat([p.id]);
          save(); renderGetData();
        },
      }),
      p.id,
      el('span', { class: 'chip-name' }, p.shortName || ''),
      isActive(p) ? null : el('span', { class: 'chip-closed' }, 'closed')));
  }

  const known = DATA.projects.length;
  const allOn = st.excluded.length === 0;
  const detail = el('div', { class: 'getdata-step' },
    el('div', { class: 'step-head' }, el('span', { class: 'step-num' }, '2'),
      'Expenditure detail report — transactions, salaries, F&A'),
    el('div', { class: 'hint' }, known
      ? 'Pick the projects and the window. Wider is better: burn rates and '
        + 'seasonality come from this history.'
      : 'Type your project numbers — they are the SPN codes on your award '
        + 'notices, and every one of them appears in the PI Dashboard export '
        + 'above. Once that export is in, they show up here as checkboxes.'),
    known ? chips : null,
    el('div', { class: 'getdata-row' },
      known ? el('button', {
        class: 'btn btn-x',
        onclick: () => {
          st.excluded = allOn ? DATA.projects.map((p) => p.id) : [];
          save(); renderGetData();
        },
      }, allOn ? 'Select none' : 'Select all') : null,
      el('label', { class: 'check' }, known ? 'Also include: ' : 'Project numbers: ',
        el('input', {
          type: 'text', class: 'extra-in', placeholder: 'SPN107048 SPN107049',
          value: st.extra, size: known ? 24 : 40,
          oninput: (e) => { st.extra = e.target.value; save(); refreshLinks(); },
        }))),
    el('div', { class: 'getdata-row' },
      el('label', { class: 'check' }, 'From ',
        el('input', {
          type: 'date', value: st.from,
          onchange: (e) => { st.from = e.target.value || defaultFrom(); save(); refreshLinks(); },
        })),
      el('label', { class: 'check' }, 'through ',
        el('input', {
          type: 'date', value: st.to || DATA.today,
          onchange: (e) => { st.to = e.target.value || null; save(); refreshLinks(); },
        }))));

  // no href until the URL is built, which the stylesheet shows as disabled
  const link = el('a', {
    class: 'btn primary', id: 'dl-link', target: '_blank', rel: 'noopener',
    onclick: () => { if (LINKS) startWatching(); },
  }, '⬇ Download detail report CSV');
  const extraLinks = el('div', { class: 'dl-extra', id: 'dl-extra' });
  detail.append(el('div', { class: 'getdata-row dl-row' }, link,
    el('button', {
      class: 'btn',
      onclick: (e) => copyLink(e.target),
    }, 'Copy link'),
    el('span', { class: 'hint dl-status', id: 'dl-status' }, 'Building link…')),
    extraLinks,
    troubleshooting(st));
  box.append(detail);

  // step 3 — the file has to end up in data/
  box.append(el('div', { class: 'getdata-step', id: 'inbox-step' },
    el('div', { class: 'step-head' }, el('span', { class: 'step-num' }, '3'),
      'Import what you downloaded'),
    el('div', { id: 'inbox' })));

  refreshLinks();
  refreshInbox();
}

function troubleshooting(st) {
  const urlBox = el('input', { type: 'text', class: 'url-box', id: 'url-box', readonly: '' });
  const bm = el('a', { class: 'btn bookmarklet', id: 'bm-link', href: '#' },
    '⬇ Download detail report');
  return el('details', { class: 'getdata-more' },
    el('summary', {}, 'Nothing downloaded, or using Safari?'),
    el('div', { class: 'hint' },
      'The link only works while you have a live session in the reporting '
      + 'system — open it and log in, then click again. Safari can also '
      + 'withhold the session on a link from another page; in that case paste '
      + 'this URL into the address bar of the logged-in tab:'),
    urlBox,
    el('div', { class: 'hint' },
      'Or install this bookmarklet: drag it onto your Favorites bar '
      + '(View › Show Favorites Bar), then click it from any page of the '
      + 'reporting system. It always pulls through today, so it keeps '
      + 'working next month.'),
    bm,
    el('div', { class: 'getdata-row' },
      el('label', { class: 'check' },
        el('input', {
          type: 'checkbox', checked: st.mode === 'per-project' || null,
          onchange: (e) => {
            st.mode = e.target.checked ? 'per-project' : 'combined';
            save(); renderGetData();
          },
        }), ' One file per project (if the report rejects a multi-project run)'),
      el('label', { class: 'check' }, 'Report layout name: ',
        el('input', {
          type: 'text', class: 'tpl-in', size: 8,
          value: st.template || (DATA.reportSource || {}).template || '',
          title: 'The label on the report’s output tab. If the link opens the '
            + 'report viewer instead of downloading a file, this is the value '
            + 'to correct.',
          onchange: (e) => { st.template = e.target.value.trim() || null; save(); refreshLinks(); },
        }))));
}

async function refreshLinks() {
  const st = fetchState();
  const status = $('#dl-status');
  if (!status) return;
  const projects = fetchProjects();
  if (!projects.length) {
    LINKS = null;
    status.textContent = 'Pick at least one project.';
    $('#dl-link').removeAttribute('href');
    return;
  }
  const params = new URLSearchParams({ projects: projects.join(' '), from: st.from, mode: st.mode });
  if (st.to) params.set('to', st.to);
  if (st.template) params.set('template', st.template);
  try {
    const res = await fetch('/api/report-links?' + params.toString());
    const payload = await res.json();
    if (payload.error) throw new Error(payload.error);
    LINKS = payload;
  } catch (err) {
    LINKS = null;
    status.textContent = 'Could not build the link: ' + (err.message || err);
    return;
  }
  const first = LINKS.links[0];
  $('#dl-link').setAttribute('href', first.url);
  $('#url-box').value = first.url;
  $('#bm-link').setAttribute('href', LINKS.bookmarklet);
  status.textContent = `${LINKS.projects.length} project`
    + `${LINKS.projects.length === 1 ? '' : 's'} · ${LINKS.from} → ${LINKS.to}`
    + (LINKS.links.length > 1 ? ` · ${LINKS.links.length} files` : '');
  // per-project mode: the rest of the downloads, one link each
  const extra = $('#dl-extra');
  extra.replaceChildren();
  if (LINKS.links.length > 1) {
    extra.append(el('div', { class: 'hint' },
      'One file per project — the button starts the first, then use these:'));
    for (const l of LINKS.links.slice(1)) {
      extra.append(el('a', {
        class: 'btn btn-x', href: l.url, target: '_blank', rel: 'noopener',
        onclick: () => startWatching(),
      }, l.label));
    }
  }
}

function copyLink(btn) {
  if (!LINKS) return;
  const url = LINKS.links[0].url;
  const done = () => {
    const was = btn.textContent;
    btn.textContent = 'Copied ✓';
    setTimeout(() => { btn.textContent = was; }, 1500);
  };
  if (navigator.clipboard) {
    navigator.clipboard.writeText(url).then(done, () => selectUrlBox());
  } else {
    selectUrlBox();
  }
}

function selectUrlBox() {
  // clipboard blocked: open the details and select the URL so ⌘C works
  const box = $('#url-box');
  box.closest('details').open = true;
  box.focus();
  box.select();
}

/* ----- the Downloads folder ----- */

function startWatching() {
  // a download was just started; watch for the file for a minute
  watchingSince = Date.now() / 1000 - 5;
  clearInterval(inboxTimer);
  const until = Date.now() + 60000;
  inboxTimer = setInterval(() => {
    if (Date.now() > until) { clearInterval(inboxTimer); inboxTimer = null; }
    refreshInbox();
  }, 2000);
  refreshInbox();
}

function isNewFile(f) {
  return watchingSince !== null && f.mtime >= watchingSince && !f.imported;
}

function isReady(f) {
  // a big export may still be streaming into the folder; wait for it
  return f.settled !== false;
}

async function refreshInbox() {
  if (!$('#inbox')) return;
  try {
    const res = await fetch('/api/inbox');
    INBOX = await res.json();
  } catch {
    INBOX = null;
  }
  const fresh = (INBOX && INBOX.files || []).filter((f) => isNewFile(f) && isReady(f));
  if (fresh.length && fetchState().autoImport) {
    await importFiles(fresh.map((f) => f.name));
    return;
  }
  renderInbox();
}

function renderInbox() {
  const box = $('#inbox');
  if (!box) return;
  const st = fetchState();
  box.replaceChildren();
  if (!INBOX || !INBOX.dir) {
    box.append(el('div', { class: 'hint' },
      'Not watching a downloads folder. Move the downloaded CSV into the '
      + 'data/ folder yourself, then click Reload data. (Point the dashboard '
      + 'at your downloads folder with --downloads to get one-click imports.)'));
    return;
  }
  const files = INBOX.files || [];
  const pending = files.filter((f) => !f.imported);
  if (lastImport && Date.now() - lastImport.at < 60000) {
    box.append(el('div', { class: 'imported-note' },
      'Imported ' + lastImport.names.join(', ') + ' into the data folder ✓'));
  }
  box.append(el('div', { class: 'hint' },
    inboxTimer ? 'Watching ' + INBOX.dir + ' for the download…'
      : 'Exports found in ' + INBOX.dir + ':'));
  if (!pending.length) {
    box.append(el('div', { class: 'flag-empty' },
      files.length ? 'Everything there is already in your data folder.'
        : 'Nothing to import yet.'));
  }
  for (const f of pending) {
    box.append(el('div', { class: 'inbox-row' + (isNewFile(f) ? ' fresh' : '') },
      el('span', { class: 'inbox-name' }, f.name),
      el('span', { class: 'badge' },
        f.type === 'detail' ? 'detail report' : 'PI Dashboard'),
      el('span', { class: 'muted-cell' }, f.modified + ' · ' + fmtBytes(f.size)),
      isReady(f)
        ? el('button', { class: 'btn btn-x', onclick: () => importFiles([f.name]) },
          'Import')
        : el('span', { class: 'muted-cell' }, 'still downloading…')));
  }
  const ready = pending.filter(isReady);
  const row = el('div', { class: 'getdata-row' });
  if (ready.length > 1) {
    row.append(el('button', {
      class: 'btn', onclick: () => importFiles(ready.map((f) => f.name)),
    }, 'Import all ' + ready.length));
  }
  row.append(el('button', { class: 'btn', onclick: refreshInbox }, 'Check again'));
  row.append(el('label', { class: 'check' },
    el('input', {
      type: 'checkbox', checked: st.autoImport || null,
      onchange: (e) => { st.autoImport = e.target.checked; save(); },
    }), ' Import new exports automatically'));
  box.append(row);
}

async function importFiles(names) {
  const box = $('#inbox');
  try {
    const res = await fetch('/api/import', {
      method: 'POST', body: JSON.stringify({ names }),
    });
    const result = await res.json();
    if (result.error) throw new Error(result.error);
    if (result.imported.length) {
      clearInterval(inboxTimer);
      inboxTimer = null;
      watchingSince = null;
      lastImport = { names: result.imported, at: Date.now() };
      await load();   // re-parse and redraw everything, including this section
      return;
    }
  } catch (err) {
    if (box) {
      box.prepend(el('div', { class: 'hint' }, 'Import failed: ' + (err.message || err)));
    }
  }
  refreshInbox();
}

/* ---------- wiring ---------- */

$('#getdata-btn').addEventListener('click', () => {
  const sec = $('#getdata-section');
  sec.classList.remove('collapsed');
  CFG.ui.collapsed = CFG.ui.collapsed || {};
  CFG.ui.collapsed.getdata = false;
  save();
  sec.scrollIntoView({ behavior: 'smooth', block: 'start' });
});
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

load();
