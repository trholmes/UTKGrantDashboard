// Salary Reconciliation page. Talks only to its own local server.
"use strict";

// By account: each combination's Labor Distribution total against
// DetailBalances.
const STATUS = {
  match: "Matches",
  mismatch: "Does not match",
  not_in_gl: "Not in DetailBalances",
  gl_not_run: "Fund not in DetailBalances",
  gl_only: "DetailBalances only — no Labor Distribution",
  posted_elsewhere: "Explained — posted to another account",
  unchecked: "Not compared yet",
};
// By person: a person's status, and the status of each account they were
// charged to (match / explained / mismatch / not_covered / unchecked).
const PERSON_STATUS = {
  match: "Matches",
  explained: "Explained — posted to another account",
  mismatch: "Does not match",
  not_in_ld: "Not in Labor Distribution",
  not_in_ledger: "Not in Fund Line Items",
  accounts_differ: "Total matches, accounts differ",
  not_covered: "Fund not in Fund Line Items",
  unchecked: "Not compared yet",
};
const PERSON_OK = ["match", "explained", "unchecked", "not_covered"];
// The same, shorter, for each account inside a person's breakdown.
const CELL_STATUS = {
  match: "Matches",
  explained: "Explained",
  mismatch: "Does not match",
  not_covered: "Not in Fund Line Items",
  unchecked: "",
};
const KIND_NAMES = {
  labor_distribution: "Labor Distribution",
  detail_balances: "DetailBalances",
  fund_line_items: "Fund Line Items",
};

// The segments of 10-1100001-106015-512100-210-0000-00-0000, in order,
// with their usual widths (Fund Line Items has the first six).
const SEGMENT_NAMES = ["Entity", "Fund", "Department", "GL account", "Program", "Activity", "InterCo", "Future"];
const WIDTHS = [2, 7, 6, 6, 3, 4, 2, 4];
const SEGMENT_CLASS = { 1: "seg-fund", 3: "seg-acct", 5: "seg-activity" };

let state = null;
let filter = "all";
let view = "people";
let period = "";  // "" = all periods
let hideCancelled = false;  // leave out pairs of lines that cancel each other out
// The filter boxes: what was typed in each segment's box and the person box.
const filters = { segments: WIDTHS.map(() => ""), person: "" };
let segmentTerms = WIDTHS.map(() => []);
let personTerms = [];
// "first six segments|period" -> [{p, c}]: everyone with lines on that
// account combination, with their account cell (built once per load).
let cellsOn = new Map();
const open = new Set();

const $ = (sel) => document.querySelector(sel);
const plural = (n, one, many) => `${n.toLocaleString()} ${n === 1 ? one : many}`;
const esc = (s) => String(s ?? "").replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function money(cents) {
  if (cents === null || cents === undefined) return "—";
  const neg = cents < 0;
  const abs = Math.abs(cents);
  const s = Math.floor(abs / 100).toLocaleString("en-US") + "." + String(abs % 100).padStart(2, "0");
  return neg ? "−" + s : s;
}
const moneyCell = (c) => `<td class="num${c < 0 ? " neg" : ""}">${money(c)}</td>`;
const DASH = `<td class="num">—</td>`;
const BLANK = `<td class="num"></td>`;
const key6 = (combo) => combo.split("-").slice(0, 6).join("-");

// An account combination with fund, GL account and activity code picked
// out, and each segment named on hover.
function comboHtml(combo) {
  const p = combo.split("-");
  if (p.length < 6) return `<span class="mono">${esc(combo)}</span>`;
  return `<span class="mono">` + p.map((v, i) =>
    `<span class="${SEGMENT_CLASS[i] || ""}" title="${SEGMENT_NAMES[i]}">${esc(v)}</span>`).join("-") + `</span>`;
}

// "Fund 1100001 · Account 512100 · Activity 0053" — the parts that tell
// salary lines apart.
function segmentsText(combo) {
  const p = combo.split("-");
  return p.length < 6 ? "" : `Fund ${p[1]} · Account ${p[3]} · Activity ${p[5]}`;
}

// --- filters ----------------------------------------------------------------

// What was typed in a segment box: "512100", "512" (a prefix), "512100-512400"
// (a range) or several of those separated by commas or spaces.
function parseTerms(text) {
  return text.split(/[\s,;]+/).map((t) => t.toLowerCase()).filter(Boolean).map((t) => {
    const m = /^([^-]+)-([^-]+)$/.exec(t);
    return m ? { lo: m[1], hi: m[2] } : { term: t };
  });
}
const digits = (s) => /^\d+$/.test(s);

// Whether one segment's value is picked by the terms typed for it. A range
// typed short is widened to the segment's width — 512-513 on a GL account
// is 512000 to 513999.
function segmentMatches(value, terms, width) {
  if (!terms.length) return true;
  const v = value.toLowerCase();
  return terms.some((t) => {
    if (t.term !== undefined) return v.startsWith(t.term);
    if (digits(t.lo) && digits(t.hi) && digits(v)) {
      const w = Math.max(width, v.length);
      return Number(t.lo.padEnd(w, "0")) <= Number(v) && Number(v) <= Number(t.hi.padEnd(w, "9"));
    }
    return t.lo <= v && v <= t.hi;
  });
}
function comboMatches(combo) {
  const parts = combo.split("-");
  return segmentTerms.every((terms, i) => segmentMatches(parts[i] || "", terms, WIDTHS[i]));
}
function personMatches(p) {
  return !personTerms.length ||
    personTerms.some((t) => p.name.toLowerCase().includes(t) || p.number.includes(t));
}
function readFilters() {
  document.querySelectorAll("#segment-filters input").forEach((el) => {
    filters.segments[Number(el.dataset.seg)] = el.value.trim();
    el.classList.toggle("active", !!el.value.trim());
  });
  filters.person = $("#person-filter").value.trim();
  $("#person-filter").classList.toggle("active", !!filters.person);
  segmentTerms = filters.segments.map(parseTerms);
  // names hold commas ("Rivera, Ana"), so a list of people is separated by ;
  personTerms = filters.person.split(";").map((t) => t.trim().toLowerCase()).filter(Boolean);
}

// Totals and status over some of a person's account cells — the same rule
// as reconcile.py's _person_summary, for whatever the filters leave.
function summarize(cells, checked) {
  const compared = cells.filter((c) => c.status !== "not_covered");
  const ld = compared.reduce((a, c) => a + c.ld_total, 0);
  const gl = compared.reduce((a, c) => a + c.gl_total, 0);
  let status;
  if (!checked) status = "unchecked";
  else if (!compared.length) status = "not_covered";
  else if (compared.every((c) => c.status === "match")) status = "match";
  else if (compared.every((c) => c.status === "match" || c.status === "explained")) status = "explained";
  else if (!cells.some((c) => c.ld_lines)) status = "not_in_ld";
  else if (gl === 0 && !compared.some((c) => c.gl_lines)) status = "not_in_ledger";
  else if (gl === ld) status = "accounts_differ";
  else status = "mismatch";
  return { status, ld_total: ld, gl_total: checked ? gl : null, diff: checked ? gl - ld : null };
}

// A person as seen through the period and segment filters: the accounts
// that match, with totals and status over just those, or null if none do.
function personView(p) {
  const cells = p.cells.filter((c) => (!period || c.period === period) && comboMatches(c.combo));
  if (!cells.length) return null;
  return { ...p, ...summarize(cells, state.result.loaded.fund_line_items), cells };
}
const visiblePeople = () => state.result.people.map(personView).filter((p) => p && personMatches(p));
const peopleOn = (r) => cellsOn.get(key6(r.combo) + "|" + r.period) || [];
const visibleRows = () => state.result.rows.filter((r) =>
  (!period || r.period === period) && comboMatches(r.combo) &&
  (!personTerms.length || peopleOn(r).some(({ p }) => personMatches(p))));

function indexCells() {
  cellsOn = new Map();
  for (const p of state.result.people) {
    for (const c of p.cells) {
      const k = key6(c.combo) + "|" + c.period;
      if (!cellsOn.has(k)) cellsOn.set(k, []);
      cellsOn.get(k).push({ p, c });
    }
  }
}

// Indices of the items that another item in the same group cancels out
// exactly — 306.98 and −306.98 for the same person on the same account and
// period, a payment reversed and issued again. `amounts` gives each item's
// amounts, one per side shown (null for a side it has nothing on); two items
// cancel when every side is the exact opposite and at least one isn't zero.
// Each item pairs with one other at most, so hiding a pair changes no total.
function cancelledIndices(items, group, amounts) {
  const amts = items.map(amounts);
  const keys = items.map(group);
  const hidden = new Set();
  for (let i = 0; i < items.length; i++) {
    if (hidden.has(i) || !amts[i].some((a) => a)) continue;
    for (let j = i + 1; j < items.length; j++) {
      if (hidden.has(j) || keys[j] !== keys[i]) continue;
      if (amts[i].every((a, k) => (a === null) === (amts[j][k] === null) && (a === null || amts[j][k] === -a))) {
        hidden.add(i).add(j);
        break;
      }
    }
  }
  return hidden;
}

// The items to show under the "hide lines that cancel out" toggle, and how
// many were left out.
function withoutCancelled(items, group, amounts) {
  if (!hideCancelled) return { shown: items, hidden: 0 };
  const hidden = cancelledIndices(items, group, amounts);
  return { shown: items.filter((_, i) => !hidden.has(i)), hidden: hidden.size };
}
const hiddenText = (n) => `${plural(n, "line", "lines")} that cancel each other out hidden`;
const hiddenNote = (n) => (n ? `<p class="hidden-note">${esc(hiddenText(n))}</p>` : "");
const hiddenRow = (n, cls) => `<tr class="line"><td colspan="6" class="hidden-note ${cls}">${esc(hiddenText(n))}</td></tr>`;

async function api(path, opts) {
  const res = await fetch(path, opts);
  const body = await res.json();
  if (!res.ok) throw new Error(body.error || res.statusText);
  return body;
}

async function refresh() {
  state = await api("/api/state");
  if (state.result) indexCells();
  render();
}

async function loadFolder() {
  $("#busy").hidden = false;
  $("#busy").textContent = "Reading the data folder…";
  try {
    const r = await api("/api/load-folder", { method: "POST" });
    open.clear();
    await refresh();
    renderNotes(r.notes);
  } finally {
    $("#busy").hidden = true;
  }
}

async function addFiles(files) {
  if (!files.length) return;
  $("#busy").hidden = false;
  const notes = [];
  try {
    for (const f of files) {
      $("#busy").textContent = `Reading ${f.name}…`;
      try {
        const r = await api("/api/file?name=" + encodeURIComponent(f.name), { method: "POST", body: f });
        notes.push(...r.notes);
      } catch (e) {
        notes.push(`${f.name}: ${e.message}`);
      }
    }
    await refresh();
    renderNotes(notes);
  } finally {
    $("#busy").hidden = true;
  }
}

function renderNotes(notes) {
  $("#notes").innerHTML = notes.map((n) => `<div class="note">${esc(n)}</div>`).join("");
}

function renderSlots() {
  $("#data-dir").textContent = state.data_dir || "";
  document.querySelectorAll(".slot").forEach((el) => {
    const f = state.files[el.dataset.kind];
    el.classList.toggle("loaded", !!f);
    el.querySelector(".slot-file").innerHTML = f
      ? `✓ ${esc(f.name)} <span class="small-muted">(${plural(f.lines, "line", "lines")})</span>` +
        `<button class="x" data-kind="${el.dataset.kind}" title="Remove this report">✕</button>`
      : `<span class="small-muted">not loaded</span>`;
  });
}

function render() {
  renderSlots();
  const res = state.result;
  $("#export-btn").disabled = !res;
  $("#results").hidden = !res;
  if (!res) return;

  readFilters();
  const periods = [...new Set([...res.periods.compared, ...res.people.flatMap((p) => p.cells.map((c) => c.period))])].sort();
  if (period && !periods.includes(period)) period = "";
  $("#period-select").innerHTML = `<option value="">All</option>` +
    periods.map((p) => `<option value="${esc(p)}"${p === period ? " selected" : ""}>${esc(p)}</option>`).join("");
  $("#people-view").hidden = view !== "people";
  $("#accounts-view").hidden = view !== "accounts";
  document.querySelectorAll("#view-tabs button").forEach((b) => b.classList.toggle("on", b.dataset.view === view));
  const notes = view === "people" ? peopleHeader(res) : accountsHeader(res);
  $("#period-note").innerHTML = notes.map(esc).join("<br>");
  $("#period-note").className = "note info";
  renderView();
  renderLeftovers();
}

function renderView() {
  view === "people" ? renderPeople() : renderTable();
}

// Tiles for the account view; returns its notes.
function accountsHeader(res) {
  const hasGL = res.loaded.detail_balances;
  const rows = visibleRows();
  const c = {};
  rows.forEach((r) => { c[r.status] = (c[r.status] || 0) + 1; });
  const compared = rows.filter((r) => r.status !== "gl_not_run");
  const ldTotal = compared.reduce((a, r) => a + r.ld_total, 0);
  const problems = (c.mismatch || 0) + (c.not_in_gl || 0) + (c.gl_only || 0);
  const diffTotal = hasGL ? compared.reduce((a, r) => a + (r.gl_total || 0), 0) - ldTotal : null;
  $("#tiles").innerHTML = [
    tile("Account combinations", rows.length),
    tile(hasGL ? "Labor Distribution (compared)" : "Labor Distribution total", money(ldTotal)),
    hasGL ? tile("Match DetailBalances", c.match || 0, "good",
                 c.posted_elsewhere ? `+ ${c.posted_elsewhere} explained by lines posted to another account or period` : "")
          : tile("DetailBalances", "not loaded"),
    hasGL ? tile("Differences", problems, problems ? "bad" : "good",
                 problems ? `DetailBalances − Labor Distribution: ${money(diffTotal)}` : "everything ties out") : "",
  ].join("");

  const notes = [];
  if (!hasGL) {
    notes.push("Step 1 done: Labor Distribution sorted by account combination, then person. " +
               "Load DetailBalances to compare each combination with what was posted.");
  } else if (problems && !res.loaded.fund_line_items) {
    notes.push("Some combinations don't match. Load Fund Line Items to see which lines are missing on which side.");
  }
  if (c.gl_not_run) {
    const funds = [...new Set(rows.filter((r) => r.status === "gl_not_run").map((r) => r.segments.fund))];
    notes.push(`DetailBalances doesn't include fund${funds.length > 1 ? "s" : ""} ${funds.join(", ")}, ` +
               `so ${plural(c.gl_not_run, "combination", "combinations")} there aren't compared — run it for ` +
               `${funds.length > 1 ? "those funds" : "that fund"} too to check them.`);
  }
  if (res.people_not_in_ld) {
    notes.push(`Fund Line Items has payroll lines for ${plural(res.people_not_in_ld, "person", "people")} who ` +
               `${res.people_not_in_ld === 1 ? "isn't" : "aren't"} in the Labor Distribution file at all, so their ` +
               `lines have no Labor Distribution line. Run Labor Distribution for everyone paid from these funds to compare them.`);
  }
  const outsidePeriods = [...new Set(res.outside_periods.map((l) => l.period))];
  if (hasGL && outsidePeriods.length) {
    notes.push(`DetailBalances covers ${res.periods.gl.join(", ") || "no periods"}; ` +
               `Labor Distribution lines in ${outsidePeriods.join(", ")} are listed at the bottom and not compared.`);
  }
  return notes;
}

// Tiles for the person view; returns its notes.
function peopleHeader(res) {
  const people = visiblePeople();
  const c = {};
  people.forEach((p) => { c[p.status] = (c[p.status] || 0) + 1; });
  const hasFLI = res.loaded.fund_line_items;
  const compared = people.filter((p) => !["unchecked", "not_covered"].includes(p.status));
  const problems = people.filter((p) => !PERSON_OK.includes(p.status)).length;
  $("#tiles").innerHTML = [
    tile("People", people.length),
    tile(hasFLI ? "Labor Distribution (compared)" : "Labor Distribution total",
         money((hasFLI ? compared : people).reduce((a, p) => a + p.ld_total, 0))),
    hasFLI ? tile("Match Fund Line Items", c.match || 0, "good",
                  c.explained ? `+ ${c.explained} explained by lines posted to another account or period` : "")
           : tile("Fund Line Items", "not loaded"),
    hasFLI ? tile("Differences", problems, problems ? "bad" : "good",
                  problems ? "people whose Fund Line Items don't line up" : "everyone lines up") : "",
  ].join("");

  const notes = [];
  if (!hasFLI) {
    notes.push("Each person's Labor Distribution, by account. Load Fund Line Items to compare each person " +
               "with what was posted for them (DetailBalances only has account totals — see By account).");
  }
  if (c.not_in_ld) {
    notes.push(`Fund Line Items has payroll lines for ${plural(c.not_in_ld, "person", "people")} who ` +
               `${c.not_in_ld === 1 ? "isn't" : "aren't"} in the Labor Distribution file at all — run Labor ` +
               `Distribution for everyone paid from these funds to compare them.`);
  }
  if (c.not_covered) {
    notes.push(`${plural(c.not_covered, "person is", "people are")} paid only from funds or periods the Fund Line ` +
               `Items report doesn't include, so ${c.not_covered === 1 ? "isn't" : "aren't"} compared.`);
  }
  const offAccounts = visibleRows().filter((r) => r.fli && r.fli.covered && !r.fli.agrees_with_gl).length;
  if (offAccounts) {
    notes.push(`Fund Line Items doesn't add up to DetailBalances on ${plural(offAccounts, "account combination", "account combinations")} ` +
               `— it may have been run for a different date range (see By account).`);
  }
  if (visibleUnattributed().length) {
    notes.push("Fund Line Items entries on salary accounts that aren't anyone's payroll line (journals, transfers) " +
               "are listed below the people.");
  }
  const outsidePeriods = [...new Set(res.outside_periods.map((l) => l.period))];
  if (outsidePeriods.length) {
    notes.push(`Labor Distribution lines in ${outsidePeriods.join(", ")} are outside the periods compared ` +
               `(${res.periods.compared.join(", ")}) and listed at the bottom.`);
  }
  return notes;
}

const statusPill = (s, text) => (text ? `<span class="status ${s}">${esc(text)}</span>` : "");

function renderPeople() {
  const res = state.result;
  const hasFLI = res.loaded.fund_line_items;
  const people = visiblePeople().filter((p) => filter !== "problems" || !PERSON_OK.includes(p.status));
  const key = (p) => "p|" + p.name;
  $("#people tbody").innerHTML = people.map((p) => {
    const isOpen = open.has(key(p));
    const accounts = new Set(p.cells.map((c) => c.combo)).size;
    return `<tr class="row${isOpen ? " open" : ""}" data-key="${esc(key(p))}">` +
      `<td class="chev">${isOpen ? "▾" : "▸"}</td>` +
      `<td><strong>${esc(p.name)}</strong>${p.number ? ` <span class="small-muted mono">${esc(p.number)}</span>` : ""}` +
      `<div class="small-muted">${plural(accounts, "account", "accounts")}, ` +
      `${[...new Set(p.cells.map((c) => c.period))].join(", ")}</div></td>` +
      moneyCell(p.ld_total) +
      (hasFLI ? moneyCell(p.gl_total) + moneyCell(p.diff) : DASH + DASH) +
      `<td>${statusPill(p.status, PERSON_STATUS[p.status])}</td></tr>` +
      (isOpen ? `<tr class="detail"><td colspan="6">${personDetailHtml(p)}</td></tr>` : "");
  }).join("") || `<tr><td colspan="6" class="small-muted">Nothing to show with this filter.</td></tr>`;

  const counted = people.filter((p) => !["unchecked", "not_covered"].includes(p.status) || !hasFLI);
  const sum = (f) => counted.reduce((a, p) => a + (f(p) || 0), 0);
  const skipped = people.length - counted.length;
  $("#people tfoot").innerHTML = `<tr><td></td><td>Total (${plural(people.length, "person", "people")} shown` +
    `${skipped ? `; ${skipped} not compared left out` : ""})</td>` + moneyCell(sum((p) => p.ld_total)) +
    (hasFLI ? moneyCell(sum((p) => p.gl_total)) + moneyCell(sum((p) => p.diff)) : DASH + DASH) +
    `<td></td></tr>`;
  $("#expand-all").textContent = people.length && people.every((p) => open.has(key(p))) ? "Collapse all" : "Expand all";
  renderUnattributed();
}

// One person: period by period, each account combination they were charged
// to, and under each the lines from each report with a subtotal per report.
function personDetailHtml(p) {
  const hasFLI = state.result.loaded.fund_line_items;
  const rows = [];
  for (const per of [...new Set(p.cells.map((c) => c.period))]) {  // cells come sorted by period, then account
    const cells = p.cells.filter((c) => c.period === per);
    const sum = summarize(cells, hasFLI);
    const shown = hasFLI && sum.status !== "not_covered";
    rows.push(`<tr class="period-head"><td colspan="2">${esc(per)}</td>` + moneyCell(sum.ld_total) +
      (shown ? moneyCell(sum.gl_total) + moneyCell(sum.diff) : DASH + DASH) +
      `<td>${sum.status === "unchecked" ? "" : statusPill(sum.status, CELL_STATUS[sum.status] || PERSON_STATUS[sum.status])}</td></tr>`);
    for (const c of cells) {
      rows.push(`<tr class="cell-head"><td class="in1">${comboHtml(c.combo)}` +
        `<div class="small-muted">${esc([c.account_name, segmentsText(c.combo)].filter(Boolean).join(" — "))}</div></td><td></td>` +
        moneyCell(c.ld_total) +
        (hasFLI && c.status !== "not_covered" ? moneyCell(c.gl_total) + moneyCell(c.diff) : DASH + DASH) +
        `<td>${statusPill(c.status, CELL_STATUS[c.status])}</td></tr>`);
      rows.push(reportGroupsHtml(c, hasFLI, 2));
    }
  }
  return `<table class="person-cells"><thead><tr><th>Period / account combination / report / line</th>` +
    `<th>Transaction</th><th class="num">Labor Distribution</th><th class="num">Fund Line Items</th>` +
    `<th class="num">Difference</th><th>Status / note</th></tr></thead><tbody>${rows.join("")}</tbody></table>`;
}

// One account cell's lines, under the report each comes from — Labor
// Distribution, then Fund Line Items — each report with its own subtotal.
// `depth` is how far in the report rows sit (their lines sit one further).
function reportGroupsHtml(c, hasFLI, depth) {
  const rows = [];
  const group = (x) => `${x.kind}|${x.other || ""}|${x.other_period || ""}`;
  const head = (name, n) => `<tr class="report-head"><td class="in${depth}" colspan="2">${esc(name)} ` +
    `<span class="small-muted">(${plural(n, "line", "lines")})</span></td><td></td><td></td><td></td><td></td></tr>`;
  const subtotal = (name, ld, gl) => `<tr class="subtotal"><td class="in${depth + 1}" colspan="2">${esc(name)} subtotal</td>` +
    (ld === null ? BLANK : moneyCell(ld)) + (gl === null ? BLANK : moneyCell(gl)) + `<td></td><td></td></tr>`;
  const none = (what) => `<tr class="line"><td class="in${depth + 1} small-muted" colspan="6">No ${what} line here.</td></tr>`;

  const ld = c.lines.filter((x) => x.ld && x.kind !== "moved_in");
  rows.push(head("Labor Distribution", ld.length));
  const ldShown = withoutCancelled(ld, group, (x) => [x.ld.amount]);
  for (const x of ldShown.shown) rows.push(ldLineHtml(x, c, depth + 1));
  if (ldShown.hidden) rows.push(hiddenRow(ldShown.hidden, `in${depth + 1}`));
  if (!ld.length) rows.push(none("Labor Distribution"));
  rows.push(subtotal("Labor Distribution", c.ld_total, null));

  const gl = c.lines.filter((x) => x.gl && x.kind !== "moved_out");
  if (hasFLI && (c.status !== "not_covered" || gl.length)) {
    rows.push(head("Fund Line Items", gl.length));
    const glShown = withoutCancelled(gl, group, (x) => [x.gl.amount]);
    for (const x of glShown.shown) rows.push(glLineHtml(x, c, depth + 1));
    if (glShown.hidden) rows.push(hiddenRow(glShown.hidden, `in${depth + 1}`));
    if (!gl.length) rows.push(none("Fund Line Items"));
    rows.push(subtotal("Fund Line Items", null, c.gl_total));
  }
  return rows.join("");
}

const otherAccount = (x, c) => `${x.other.split("-")[3]}` +
  (x.other_period !== c.period ? ` in ${x.other_period}` : "") +
  (x.by_amount ? " (same amount, no reference)" : "");

// A Labor Distribution line, with how it fared in Fund Line Items.
function ldLineHtml(x, c, depth) {
  const l = x.ld;
  // a share of someone's pay split across funds (Line Percentage 50 = half)
  const share = l.percent && Number(l.percent) !== 100 ? `${l.percent}% of this pay` : "";
  const sub = [`${l.pay_start}–${l.pay_end}`, l.assignment, share].filter(Boolean).join(" · ");
  const note = {
    pair: "",
    not_compared: "",
    amount_differs: () => `Fund Line Items has ${money(x.gl.amount)} on this transaction`,
    ld_only: "not in Fund Line Items",
    moved_out: () => `posted to ${x.other.split("-")[3]} ${x.other_name}` +
      (x.other_period !== c.period ? ` in ${x.other_period}` : "") +
      (x.by_amount ? " (same amount, no reference)" : ""),
  }[x.kind];
  return `<tr class="line ${x.kind}"><td class="in${depth}">${esc(l.pay_element)}<div class="small-muted">${esc(sub)}</div></td>` +
    `<td class="mono">${esc(l.txn)}</td>${moneyCell(l.amount)}${BLANK}<td></td>` +
    `<td class="line-note">${esc(typeof note === "function" ? note() : note)}</td></tr>`;
}

// A Fund Line Items line, with how it fared against Labor Distribution.
function glLineHtml(x, c, depth) {
  const f = x.gl;
  const what = f.person ? f.assignment : (f.text || f.header_text);
  const sub = [f.posted, f.doc_type, f.person || !f.text ? f.header_text : "",
               f.user ? `entered by ${f.user}` : ""].filter(Boolean).join(" · ");
  const note = {
    pair: "",
    amount_differs: () => `Labor Distribution has ${money(x.ld.amount)} on this transaction`,
    gl_only: "no Labor Distribution line",
    moved_in: () => `charged in Labor Distribution to ${otherAccount(x, c)}`,
  }[x.kind];
  return `<tr class="line ${x.kind}"><td class="in${depth}">${esc(what)}<div class="small-muted">${esc(sub)}</div></td>` +
    `<td class="mono">${esc(f.ref)}</td>${BLANK}${moneyCell(f.amount)}<td></td>` +
    `<td class="line-note">${esc(typeof note === "function" ? note() : note)}</td></tr>`;
}

// Fund Line Items entries on salary accounts that aren't anyone's payroll
// line, as the filters leave them (a person filter leaves none: they're
// nobody's).
function visibleUnattributed() {
  if (personTerms.length) return [];
  return state.result.unattributed.filter((g) => (!period || g.period === period) && comboMatches(g.combo));
}

function renderUnattributed() {
  const groups = visibleUnattributed();
  if (!groups.length) { $("#unattributed").innerHTML = ""; return; }
  const rows = [];
  for (const g of groups) {
    rows.push(`<tr class="cell-head"><td>${comboHtml(g.combo)}` +
      (g.account_name ? `<div class="small-muted">${esc(g.account_name)}</div>` : "") + `</td>` +
      `<td class="mono">${esc(g.period)}</td><td></td><td></td><td></td><td></td></tr>`);
    const { shown, hidden } = withoutCancelled(g.lines, () => "", (f) => [f.amount]);
    for (const f of shown) {
      rows.push(`<tr class="line"><td class="in1">${esc(f.text || f.header_text)}` +
        (f.text && f.header_text ? `<div class="small-muted">${esc(f.header_text)}</div>` : "") + `</td>` +
        `<td class="mono">${esc(f.posted)}</td><td>${esc(f.doc_type)}</td><td class="mono">${esc(f.ref)}</td>` +
        `<td>${esc(f.user)}</td>${moneyCell(f.amount)}</tr>`);
    }
    if (hidden) rows.push(hiddenRow(hidden, "in1"));
    rows.push(`<tr class="subtotal"><td class="in1" colspan="5">Fund Line Items subtotal</td>${moneyCell(g.total)}</tr>`);
  }
  $("#unattributed").innerHTML = `<details open><summary>Fund Line Items entries on salary accounts that aren't anyone's ` +
    `payroll line (${plural(groups.reduce((a, g) => a + g.lines.length, 0), "line", "lines")})</summary>` +
    `<p class="small-muted">Journals, salary transfers and the like: they move money on these accounts without a ` +
    `person's name, so they can't be put under a person. They explain account totals that differ.</p>` +
    `<table class="person-cells"><thead><tr><th>Account combination / entry</th><th>Entered</th><th>Document type</th>` +
    `<th>Reference</th><th>Entered by</th><th class="num">Amount</th></tr></thead><tbody>${rows.join("")}</tbody></table></details>`;
}

function tile(label, value, cls = "", sub = "") {
  return `<div class="tile ${cls}"><div class="label">${esc(label)}</div>` +
    `<div class="value">${esc(value)}</div>${sub ? `<div class="small-muted">${esc(sub)}</div>` : ""}</div>`;
}

const rowShown = (r) => filter !== "problems" || !["match", "unchecked", "posted_elsewhere", "gl_not_run"].includes(r.status);

function renderTable() {
  const res = state.result;
  const rows = visibleRows().filter(rowShown);
  const key = (r) => r.combo + "|" + r.period;
  $("#summary tbody").innerHTML = rows.map((r) => {
    const isOpen = open.has(key(r));
    return `<tr class="row${isOpen ? " open" : ""}" data-key="${esc(key(r))}">` +
      `<td class="chev">${isOpen ? "▾" : "▸"}</td>` +
      `<td>${comboHtml(r.combo)}<div class="small-muted">${esc(segmentsText(r.combo))}</div>` +
      `<div class="small-muted">${plural(peopleOn(r).length, "person", "people")}, ` +
      `${plural(r.ld_lines.length, "Labor Distribution line", "Labor Distribution lines")}</div></td>` +
      `<td class="mono">${esc(r.period)}</td>` +
      moneyCell(r.ld_total) +
      `<td class="num">${res.loaded.detail_balances && r.status !== "gl_not_run" ? money(r.gl_total ?? 0) : "—"}</td>` +
      (r.diff === null ? DASH : moneyCell(r.diff)) +
      `<td>${statusPill(r.status, STATUS[r.status])}</td></tr>` +
      (isOpen ? `<tr class="detail"><td colspan="7">${detailHtml(r)}</td></tr>` : "");
  }).join("") || `<tr><td colspan="7" class="small-muted">Nothing to show with this filter.</td></tr>`;

  const compared = rows.filter((r) => r.status !== "gl_not_run");
  const sum = (f) => compared.reduce((a, r) => a + (f(r) || 0), 0);
  const skipped = rows.length - compared.length;
  $("#summary tfoot").innerHTML = `<tr><td></td><td>Total (${rows.length} shown` +
    `${skipped ? `; ${skipped} not compared left out` : ""})</td><td></td>` +
    moneyCell(sum((r) => r.ld_total)) +
    `<td class="num">${res.loaded.detail_balances ? money(sum((r) => r.gl_total)) : "—"}</td>` +
    (res.loaded.detail_balances ? moneyCell(sum((r) => r.diff)) : DASH) +
    `<td></td></tr>`;
  $("#expand-all").textContent = rows.length && rows.every((r) => open.has(key(r))) ? "Collapse all" : "Expand all";
}

// One account combination and period: why it differs, then each person
// paid from it with their lines from each report, then the entries that
// aren't anyone's payroll line.
function detailHtml(r) {
  const res = state.result;
  const hasFLI = res.loaded.fund_line_items;
  const parts = [];
  if (r.status === "gl_not_run") {
    parts.push(`<p class="explain">The DetailBalances report that's loaded has no rows at all for fund ` +
      `<strong>${esc(r.segments.fund)}</strong>, department ${esc(r.segments.department)} in ${esc(r.period)} — ` +
      `it was run for other funds. Run it for this fund to compare these lines.</p>`);
  } else if (res.loaded.detail_balances && r.status !== "match") {
    parts.push(explainHtml(r));
  }
  const rows = [];
  for (const { p, c } of peopleOn(r).filter(({ p }) => personMatches(p))) {
    const compared = hasFLI && c.status !== "not_covered";
    rows.push(`<tr class="person-head"><td><strong>${esc(p.name)}</strong>` +
      `${p.number ? ` <span class="small-muted mono">${esc(p.number)}</span>` : ""}</td><td></td>` +
      moneyCell(c.ld_total) + (compared ? moneyCell(c.gl_total) + moneyCell(c.diff) : DASH + DASH) +
      `<td>${statusPill(c.status, CELL_STATUS[c.status])}</td></tr>`);
    rows.push(reportGroupsHtml(c, hasFLI, 1));
  }
  if (!personTerms.length) {
    for (const g of res.unattributed.filter((g) => key6(g.combo) === key6(r.combo) && g.period === r.period)) {
      rows.push(`<tr class="person-head"><td><strong>Not anyone's payroll line</strong> ` +
        `<span class="small-muted">journals, transfers and the like</span></td><td></td>${BLANK}` +
        moneyCell(g.total) + `<td></td><td></td></tr>`);
      rows.push(`<tr class="report-head"><td class="in1" colspan="2">Fund Line Items ` +
        `<span class="small-muted">(${plural(g.lines.length, "line", "lines")})</span></td><td></td><td></td><td></td><td></td></tr>`);
      const { shown, hidden } = withoutCancelled(g.lines, () => "", (f) => [f.amount]);
      for (const f of shown) {
        const sub = [f.posted, f.doc_type, f.text ? f.header_text : "", f.user ? `entered by ${f.user}` : ""].filter(Boolean).join(" · ");
        rows.push(`<tr class="line gl_only"><td class="in2">${esc(f.text || f.header_text)}<div class="small-muted">${esc(sub)}</div></td>` +
          `<td class="mono">${esc(f.ref)}</td>${BLANK}${moneyCell(f.amount)}<td></td><td class="line-note">no Labor Distribution line</td></tr>`);
      }
      if (hidden) rows.push(hiddenRow(hidden, "in2"));
      rows.push(`<tr class="subtotal"><td class="in2" colspan="2">Fund Line Items subtotal</td>${BLANK}${moneyCell(g.total)}<td></td><td></td></tr>`);
    }
  }
  parts.push(rows.length
    ? `<table class="person-cells"><thead><tr><th>Person / report / line</th><th>Transaction</th>` +
      `<th class="num">Labor Distribution</th><th class="num">Fund Line Items</th><th class="num">Difference</th>` +
      `<th>Status / note</th></tr></thead><tbody>${rows.join("")}</tbody></table>`
    : `<p class="small-muted">No lines to show with this filter.</p>`);
  return parts.join("");
}

function explainHtml(r) {
  const fli = r.fli;
  const lead = r.status === "gl_only"
    ? `<p class="explain">DetailBalances shows <strong>${money(r.gl_total)}</strong> on this salary combination, but no Labor Distribution line was charged to it.</p>`
    : `<p class="explain">DetailBalances is <strong>${money(Math.abs(r.diff))} ${r.diff > 0 ? "higher" : "lower"}</strong> than Labor Distribution for ${esc(r.period)}.</p>`;
  if (!fli) {
    return lead + `<p class="explain">Load the <strong>Fund Line Items</strong> report for fund ${esc(r.segments.fund)} to see which lines account for this.</p>`;
  }
  if (!fli.covered) {
    return lead + `<p class="explain">The Fund Line Items report that's loaded has nothing for fund <strong>${esc(r.segments.fund)}</strong> in period ${esc(r.period)} — run it for this fund and period.</p>`;
  }
  const out = [lead];
  if (!fli.agrees_with_gl) {
    out.push(`<div class="note">Fund Line Items for this combination add up to ${money(fli.total)}, but DetailBalances shows ${money(r.gl_total ?? 0)} — it may have been run for a different date range, so the lines below may be incomplete.</div>`);
  }
  const missing = fli.people_not_in_ld || [];
  if (missing.length) {
    out.push(`<p class="explain">${plural(missing.length, "person", "people")} paid here in Fund Line Items ` +
      `${missing.length === 1 ? "isn't" : "aren't"} in the Labor Distribution file at all — it was probably run for ` +
      `fewer people than are paid from this account.</p>`);
  }
  const what = [];
  if (fli.gl_only.length) what.push(`${plural(fli.gl_only.length, "Fund Line Items line", "Fund Line Items lines")} with no Labor Distribution line`);
  if (fli.ld_only.length) what.push(`${plural(fli.ld_only.length, "Labor Distribution line", "Labor Distribution lines")} not in Fund Line Items`);
  if (fli.amount_differs.length) what.push(`${plural(fli.amount_differs.length, "transaction", "transactions")} with a different amount in each report`);
  if (fli.moved_out.length) what.push(`${plural(fli.moved_out.length, "line", "lines")} posted to another account or period`);
  if (fli.moved_in.length) what.push(`${plural(fli.moved_in.length, "line", "lines")} charged to another account or period in Labor Distribution`);
  if (what.length) out.push(`<p class="explain">${what.join("; ")} — marked on the lines below.</p>`);
  if (fli.moved_out.length || fli.moved_in.length) {
    out.push(fli.unexplained === 0
      ? `<p class="explain">That accounts for the whole difference: accounting posted these lines to a different GL account (or period) than the one Labor Distribution shows.</p>`
      : `<p class="explain">Counting those, <strong>${money(Math.abs(fli.unexplained))}</strong> is still unexplained.</p>`);
  }
  if (!what.length) {
    out.push(`<p class="explain">Every Fund Line Items line pairs with a Labor Distribution line, so the difference isn't in the individual lines — check that both reports were run for the same period.</p>`);
  }
  return out.join("");
}

function renderLeftovers() {
  const res = state.result;
  const blocks = [];
  if (res.outside_periods.length) {
    blocks.push(`<details><summary>Labor Distribution lines in periods DetailBalances doesn't cover (${res.outside_periods.length})</summary>` +
      leftoverTable(res.outside_periods) + `</details>`);
  }
  if (res.excluded.length) {
    blocks.push(`<details><summary>Labor Distribution lines not compared because their status isn't Success (${res.excluded.length})</summary>` +
      leftoverTable(res.excluded) + `</details>`);
  }
  $("#leftovers").innerHTML = blocks.join("");
}

function leftoverTable(all) {
  const { shown, hidden } = withoutCancelled(all, (l) => `${l.combo}|${l.period}|${l.person}`, (l) => [l.amount]);
  return `<table><thead><tr><th>Account combination</th><th>Period</th><th>Person</th><th>Pay element</th>` +
    `<th>Status</th><th class="num">Amount</th></tr></thead><tbody>` +
    shown.map((l) => `<tr><td>${comboHtml(l.combo)}</td><td class="mono">${esc(l.period)}</td><td>${esc(l.person)}</td>` +
      `<td>${esc(l.pay_element)}</td><td>${esc(l.status)}</td>${moneyCell(l.amount)}</tr>`).join("") +
    `</tbody></table>` + hiddenNote(hidden);
}

// --- events ---------------------------------------------------------------

$("#file-input").addEventListener("change", (e) => {
  addFiles([...e.target.files]);
  e.target.value = "";
});

let dragDepth = 0;
window.addEventListener("dragenter", (e) => { e.preventDefault(); dragDepth++; $("#drop-overlay").hidden = false; });
window.addEventListener("dragleave", () => { if (--dragDepth <= 0) { dragDepth = 0; $("#drop-overlay").hidden = true; } });
window.addEventListener("dragover", (e) => e.preventDefault());
window.addEventListener("drop", (e) => {
  e.preventDefault();
  dragDepth = 0;
  $("#drop-overlay").hidden = true;
  addFiles([...e.dataTransfer.files]);
});

document.querySelector(".slots").addEventListener("click", async (e) => {
  const x = e.target.closest(".x");
  if (!x) return;
  await api("/api/clear?kind=" + encodeURIComponent(x.dataset.kind), { method: "POST" });
  renderNotes([]);
  refresh();
});

$("#reload-btn").addEventListener("click", () => loadFolder().catch((e) => renderNotes([e.message])));

$("#clear-btn").addEventListener("click", async () => {
  await api("/api/clear", { method: "POST" });
  open.clear();
  renderNotes([]);
  refresh();
});

$("#export-btn").addEventListener("click", () => {
  window.location = "/api/export" + (hideCancelled ? "?hide_cancelled=1" : "");
});

$("#hide-cancelled").addEventListener("change", (e) => {
  hideCancelled = e.target.checked;
  render();
});

$("#people tbody").addEventListener("click", (e) => {
  const tr = e.target.closest("tr.row");
  if (!tr) return;
  const k = tr.dataset.key;
  open.has(k) ? open.delete(k) : open.add(k);
  renderPeople();
});

$("#view-tabs").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (!b || b.dataset.view === view) return;
  view = b.dataset.view;
  render();
});

$("#summary tbody").addEventListener("click", (e) => {
  const tr = e.target.closest("tr.row");
  if (!tr) return;
  const k = tr.dataset.key;
  open.has(k) ? open.delete(k) : open.add(k);
  renderTable();
});

$("#filter-seg").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  filter = b.dataset.filter;
  document.querySelectorAll("#filter-seg button").forEach((x) => x.classList.toggle("on", x === b));
  renderView();
});

// The filter boxes narrow the tiles too, so the whole view is re-rendered.
$("#segment-filters").addEventListener("input", render);
$("#person-filter").addEventListener("input", render);
$("#clear-filters").addEventListener("click", () => {
  document.querySelectorAll("#segment-filters input").forEach((el) => { el.value = ""; });
  $("#person-filter").value = "";
  render();
});

$("#period-select").addEventListener("change", (e) => {
  period = e.target.value;
  render();
});

$("#expand-all").addEventListener("click", () => {
  const keys = view === "people"
    ? visiblePeople().filter((p) => filter !== "problems" || !PERSON_OK.includes(p.status)).map((p) => "p|" + p.name)
    : visibleRows().filter(rowShown).map((r) => r.combo + "|" + r.period);
  const allOpen = keys.every((k) => open.has(k));
  keys.forEach((k) => (allOpen ? open.delete(k) : open.add(k)));
  renderView();
});

loadFolder().catch((e) => renderNotes([e.message]));
