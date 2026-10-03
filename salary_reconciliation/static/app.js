// Salary Reconciliation page. Talks only to its own local server.
"use strict";

const STATUS = {
  match: "Matches",
  mismatch: "Does not match",
  not_in_gl: "Not in DetailBalances",
  gl_not_run: "Fund not in DetailBalances",
  gl_only: "Ledger only — no Labor Distribution",
  posted_elsewhere: "Explained — posted to another account",
  unchecked: "Not compared yet",
};
// Person view: a person's status, and the status of each account they were
// charged to (match / explained / mismatch / not_covered / unchecked).
const PERSON_STATUS = {
  match: "Matches",
  explained: "Explained — posted to another account",
  mismatch: "Does not match",
  not_in_ld: "Not in Labor Distribution",
  not_in_ledger: "Not in the ledger",
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

let state = null;
let filter = "all";
let view = "people";
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

// 10-1100001-106015-512100-210-0000-00-0000 with fund and GL account picked out
function comboHtml(combo) {
  const p = combo.split("-");
  if (p.length < 6) return `<span class="mono">${esc(combo)}</span>`;
  return `<span class="mono">${esc(p[0])}-<span class="seg-fund" title="Fund">${esc(p[1])}</span>-${esc(p[2])}-` +
    `<span class="seg-acct" title="GL account">${esc(p[3])}</span>-${esc(p.slice(4).join("-"))}</span>`;
}

async function api(path, opts) {
  const res = await fetch(path, opts);
  const body = await res.json();
  if (!res.ok) throw new Error(body.error || res.statusText);
  return body;
}

async function refresh() {
  state = await api("/api/state");
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
  const c = res.counts;
  const problems = (c.mismatch || 0) + (c.not_in_gl || 0) + (c.gl_only || 0);
  const diffTotal = hasGL ? res.totals.gl - res.totals.ld : null;
  $("#tiles").innerHTML = [
    tile("Account combinations", res.rows.length),
    tile(hasGL ? "Labor Distribution (compared)" : "Labor Distribution total", money(res.totals.ld)),
    hasGL ? tile("Match the ledger", c.match || 0, "good",
                 c.posted_elsewhere ? `+ ${c.posted_elsewhere} explained by lines posted to another account or period` : "")
          : tile("DetailBalances", "not loaded"),
    hasGL ? tile("Differences", problems, problems ? "bad" : "good",
                 problems ? `ledger − LD: ${money(diffTotal)}` : "everything ties out") : "",
  ].join("");

  const notes = [];
  if (!hasGL) {
    notes.push("Step 1 done: Labor Distribution sorted by account combination, then person. " +
               "Load DetailBalances to compare each combination with the ledger.");
  } else if (problems && !res.loaded.fund_line_items) {
    notes.push("Some combinations don't match. Load Fund Line Items to see which lines are missing on which side.");
  }
  if (c.gl_not_run) {
    const funds = [...new Set(res.rows.filter((r) => r.status === "gl_not_run").map((r) => r.segments.fund))];
    notes.push(`DetailBalances doesn't include fund${funds.length > 1 ? "s" : ""} ${funds.join(", ")}, ` +
               `so ${plural(c.gl_not_run, "combination", "combinations")} there aren't compared — run it for ` +
               `${funds.length > 1 ? "those funds" : "that fund"} too to check them.`);
  }
  if (res.people_not_in_ld) {
    notes.push(`The ledger has payroll lines for ${plural(res.people_not_in_ld, "person", "people")} who ` +
               `${res.people_not_in_ld === 1 ? "isn't" : "aren't"} in the Labor Distribution file at all, so their ` +
               `lines show as ledger-only. Run Labor Distribution for everyone paid from these funds to compare them.`);
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
  const c = res.people_counts;
  const hasFLI = res.loaded.fund_line_items;
  const compared = res.people.filter((p) => !["unchecked", "not_covered"].includes(p.status));
  const problems = res.people.filter((p) => !PERSON_OK.includes(p.status)).length;
  $("#tiles").innerHTML = [
    tile("People", res.people.length),
    tile(hasFLI ? "Labor Distribution (compared)" : "Labor Distribution total",
         money((hasFLI ? compared : res.people).reduce((a, p) => a + p.ld_total, 0))),
    hasFLI ? tile("Match the ledger", c.match || 0, "good",
                  c.explained ? `+ ${c.explained} explained by lines posted to another account or period` : "")
           : tile("Fund Line Items", "not loaded"),
    hasFLI ? tile("Differences", problems, problems ? "bad" : "good",
                  problems ? "people whose ledger doesn't line up" : "everyone lines up") : "",
  ].join("");

  const notes = [];
  if (!hasFLI) {
    notes.push("Each person's Labor Distribution, by account. Load Fund Line Items to compare each person " +
               "with what the ledger posted for them (DetailBalances only has account totals — see By account).");
  }
  if (c.not_in_ld) {
    notes.push(`The ledger has payroll lines for ${plural(c.not_in_ld, "person", "people")} who ` +
               `${c.not_in_ld === 1 ? "isn't" : "aren't"} in the Labor Distribution file at all — run Labor ` +
               `Distribution for everyone paid from these funds to compare them.`);
  }
  if (c.not_covered) {
    notes.push(`${plural(c.not_covered, "person is", "people are")} paid only from funds or periods the Fund Line ` +
               `Items report doesn't include, so ${c.not_covered === 1 ? "isn't" : "aren't"} compared.`);
  }
  const offAccounts = res.rows.filter((r) => r.fli && r.fli.covered && !r.fli.agrees_with_gl).length;
  if (offAccounts) {
    notes.push(`Fund Line Items doesn't add up to DetailBalances on ${plural(offAccounts, "account combination", "account combinations")} ` +
               `— it may have been run for a different date range (see By account).`);
  }
  if (res.unattributed.length) {
    notes.push("Ledger entries on salary accounts that aren't anyone's payroll line (journals, transfers) " +
               "are listed below the people.");
  }
  const outsidePeriods = [...new Set(res.outside_periods.map((l) => l.period))];
  if (outsidePeriods.length) {
    notes.push(`Labor Distribution lines in ${outsidePeriods.join(", ")} are outside the periods compared ` +
               `(${res.periods.compared.join(", ")}) and listed at the bottom.`);
  }
  return notes;
}

function personMatches(p, q) {
  if (filter === "problems" && PERSON_OK.includes(p.status)) return false;
  if (!q) return true;
  return p.name.toLowerCase().includes(q) || p.number.includes(q) ||
    p.cells.some((c) => c.combo.toLowerCase().includes(q) || c.period.includes(q));
}

function renderPeople() {
  const res = state.result;
  const hasFLI = res.loaded.fund_line_items;
  const q = $("#search").value.trim().toLowerCase();
  const people = res.people.filter((p) => personMatches(p, q));
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
      (hasFLI ? moneyCell(p.gl_total) + moneyCell(p.diff) : `<td class="num">—</td><td class="num">—</td>`) +
      `<td><span class="status ${p.status}">${esc(PERSON_STATUS[p.status])}</span></td></tr>` +
      (isOpen ? `<tr class="detail"><td colspan="6">${personDetailHtml(p)}</td></tr>` : "");
  }).join("") || `<tr><td colspan="6" class="small-muted">Nothing to show with this filter.</td></tr>`;

  const counted = people.filter((p) => !["unchecked", "not_covered"].includes(p.status) || !hasFLI);
  const sum = (f) => counted.reduce((a, p) => a + (f(p) || 0), 0);
  const skipped = people.length - counted.length;
  $("#people tfoot").innerHTML = `<tr><td></td><td>Total (${plural(people.length, "person", "people")} shown` +
    `${skipped ? `; ${skipped} not compared left out` : ""})</td>` + moneyCell(sum((p) => p.ld_total)) +
    (hasFLI ? moneyCell(sum((p) => p.gl_total)) + moneyCell(sum((p) => p.diff)) : `<td class="num">—</td><td class="num">—</td>`) +
    `<td></td></tr>`;
  $("#expand-all").textContent = people.length && people.every((p) => open.has(key(p))) ? "Collapse all" : "Expand all";
  renderUnattributed();
}

// One person, per account combination and period: the totals on each side,
// whether they line up, and the lines underneath.
function personDetailHtml(p) {
  const hasFLI = state.result.loaded.fund_line_items;
  const rows = [];
  for (const c of p.cells) {
    rows.push(`<tr class="cell-head"><td>${comboHtml(c.combo)}` +
      (c.account_name ? `<div class="small-muted">${esc(c.account_name)}</div>` : "") + `</td>` +
      `<td class="mono">${esc(c.period)}</td><td></td>` + moneyCell(c.ld_total) +
      (hasFLI && c.status !== "not_covered" ? moneyCell(c.gl_total) + moneyCell(c.diff) : `<td class="num">—</td><td class="num">—</td>`) +
      `<td>${CELL_STATUS[c.status] ? `<span class="status ${c.status}">${esc(CELL_STATUS[c.status])}</span>` : ""}</td></tr>`);
    for (const x of c.lines) rows.push(lineHtml(x, c));
  }
  return `<table class="person-cells"><thead><tr><th>Account combination / line</th><th>Period</th>` +
    `<th>Transaction</th><th class="num">Labor Distribution</th><th class="num">Ledger</th>` +
    `<th class="num">Difference</th><th>Status / note</th></tr></thead><tbody>${rows.join("")}</tbody></table>`;
}

function lineHtml(x, c) {
  const ld = x.ld, gl = x.gl;
  const what = ld ? `${ld.pay_element}` : (gl.person ? gl.assignment : (gl.text || gl.header_text));
  // a share of someone's pay split across funds (Line Percentage 50 = half)
  const share = ld && ld.percent && Number(ld.percent) !== 100 ? ` · ${ld.percent}% of this pay` : "";
  const when = (ld ? `${ld.pay_start}–${ld.pay_end}` : gl.posted) + share;
  const note = {
    pair: "",
    not_compared: "",
    amount_differs: "same transaction, different amount",
    ld_only: "not in the ledger",
    gl_only: "no Labor Distribution line",
    moved_out: () => `posted to ${x.other.split("-")[3]} ${x.other_name}` +
      (x.other_period !== c.period ? ` in ${x.other_period}` : ""),
    moved_in: () => `charged in Labor Distribution to ${x.other.split("-")[3]}` +
      (x.other_period !== c.period ? ` in ${x.other_period}` : ""),
  }[x.kind];
  const ldAmt = ld && x.kind !== "moved_in" ? ld.amount : null;
  const glAmt = gl && x.kind !== "moved_out" ? gl.amount : null;
  return `<tr class="line ${x.kind}"><td>${esc(what)}<div class="small-muted">${esc(when)}</div></td><td></td>` +
    `<td class="mono">${esc(ld ? ld.txn : gl.ref)}</td>` +
    (ldAmt === null ? `<td class="num">—</td>` : moneyCell(ldAmt)) +
    (glAmt === null ? `<td class="num">—</td>` : moneyCell(glAmt)) + `<td></td>` +
    `<td class="line-note">${esc(typeof note === "function" ? note() : note)}</td></tr>`;
}

// Ledger entries on salary accounts that aren't anyone's payroll line.
function renderUnattributed() {
  const groups = state.result.unattributed;
  if (!groups.length) { $("#unattributed").innerHTML = ""; return; }
  const q = $("#search").value.trim().toLowerCase();
  const shown = groups.filter((g) => !q || g.combo.toLowerCase().includes(q) || g.period.includes(q) ||
    g.lines.some((f) => (f.text + " " + f.header_text).toLowerCase().includes(q)));
  const rows = [];
  for (const g of shown) {
    rows.push(`<tr class="cell-head"><td>${comboHtml(g.combo)}` +
      (g.account_name ? `<div class="small-muted">${esc(g.account_name)}</div>` : "") + `</td>` +
      `<td class="mono">${esc(g.period)}</td><td></td><td></td><td></td>${moneyCell(g.total)}</tr>`);
    for (const f of g.lines) {
      rows.push(`<tr class="line"><td>${esc(f.text || f.header_text)}` +
        (f.text && f.header_text ? `<div class="small-muted">${esc(f.header_text)}</div>` : "") + `</td>` +
        `<td class="mono">${esc(f.posted)}</td><td>${esc(f.doc_type)}</td><td class="mono">${esc(f.ref)}</td>` +
        `<td>${esc(f.user)}</td>${moneyCell(f.amount)}</tr>`);
    }
  }
  $("#unattributed").innerHTML = `<details open><summary>Ledger entries on salary accounts that aren't anyone's ` +
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

function rowMatches(r, q) {
  if (filter === "problems" && ["match", "unchecked", "posted_elsewhere", "gl_not_run"].includes(r.status)) return false;
  if (!q) return true;
  if (r.combo.toLowerCase().includes(q) || r.period.includes(q)) return true;
  return r.ld_lines.some((l) => l.person.toLowerCase().includes(q) || l.person_number.includes(q));
}

function renderTable() {
  const res = state.result;
  const q = $("#search").value.trim().toLowerCase();
  const rows = res.rows.map((r, i) => [r, i]).filter(([r]) => rowMatches(r, q));
  const key = (r) => r.combo + "|" + r.period;
  $("#summary tbody").innerHTML = rows.map(([r]) => {
    const isOpen = open.has(key(r));
    return `<tr class="row${isOpen ? " open" : ""}" data-key="${esc(key(r))}">` +
      `<td class="chev">${isOpen ? "▾" : "▸"}</td>` +
      `<td>${comboHtml(r.combo)}<div class="small-muted">${plural(r.ld_people, "person", "people")}, ${plural(r.ld_lines.length, "line", "lines")}</div></td>` +
      `<td class="mono">${esc(r.period)}</td>` +
      moneyCell(r.ld_total) +
      `<td class="num">${res.loaded.detail_balances && r.status !== "gl_not_run" ? money(r.gl_total ?? 0) : "—"}</td>` +
      (r.diff === null ? `<td class="num">—</td>` : moneyCell(r.diff)) +
      `<td><span class="status ${r.status}">${esc(STATUS[r.status])}</span></td></tr>` +
      (isOpen ? `<tr class="detail"><td colspan="7">${detailHtml(r)}</td></tr>` : "");
  }).join("") || `<tr><td colspan="7" class="small-muted">Nothing to show with this filter.</td></tr>`;

  const shown = rows.map(([r]) => r);
  const compared = shown.filter((r) => r.status !== "gl_not_run");
  const sum = (f) => compared.reduce((a, r) => a + (f(r) || 0), 0);
  const skipped = shown.length - compared.length;
  $("#summary tfoot").innerHTML = `<tr><td></td><td>Total (${shown.length} shown` +
    `${skipped ? `; ${skipped} not compared left out` : ""})</td><td></td>` +
    moneyCell(sum((r) => r.ld_total)) +
    `<td class="num">${res.loaded.detail_balances ? money(sum((r) => r.gl_total)) : "—"}</td>` +
    (res.loaded.detail_balances ? moneyCell(sum((r) => r.diff)) : `<td class="num">—</td>`) +
    `<td></td></tr>`;
  $("#expand-all").textContent = rows.length && rows.every(([r]) => open.has(key(r))) ? "Collapse all" : "Expand all";
}

function detailHtml(r) {
  const parts = [];
  const res = state.result;
  if (r.status === "gl_not_run") {
    parts.push(`<p class="explain">The DetailBalances report that's loaded has no rows at all for fund ` +
      `<strong>${esc(r.segments.fund)}</strong>, department ${esc(r.segments.department)} in ${esc(r.period)} — ` +
      `it was run for other funds. Run it for this fund to compare these lines.</p>`);
  } else if (res.loaded.detail_balances && r.status !== "match") {
    parts.push(explainHtml(r));
  }
  parts.push(`<h3>Labor Distribution lines, by person</h3>` + ldTable(r.ld_lines, true));
  return parts.join("");
}

function explainHtml(r) {
  const fli = r.fli;
  const lead = r.status === "gl_only"
    ? `<p class="explain">The ledger shows <strong>${money(r.gl_total)}</strong> on this salary combination, but no Labor Distribution line was charged to it.</p>`
    : `<p class="explain">The ledger is <strong>${money(Math.abs(r.diff))} ${r.diff > 0 ? "higher" : "lower"}</strong> than Labor Distribution for ${esc(r.period)}.</p>`;
  if (!fli) {
    return lead + `<p class="explain">Load the <strong>Fund Line Items</strong> report for fund ${esc(r.segments.fund)} to see which lines account for this.</p>`;
  }
  if (!fli.covered) {
    return lead + `<p class="explain">The Fund Line Items report that's loaded has nothing for fund <strong>${esc(r.segments.fund)}</strong> in period ${esc(r.period)} — run it for this fund and period.</p>`;
  }
  const out = [lead];
  if (!fli.agrees_with_gl) {
    out.push(`<div class="note">Fund Line Items for this combination add up to ${money(fli.total)}, but DetailBalances shows ${money(r.gl_total ?? 0)} — it may have been run for a different date range, so the lists below may be incomplete.</div>`);
  }
  if (fli.gl_only.length) {
    const missing = fli.people_not_in_ld || [];
    out.push(`<h3>In the ledger, not in Labor Distribution (${fli.gl_only.length})</h3>` +
      (missing.length ? `<p class="explain">${plural(missing.length, "person", "people")} paid here in the ledger ` +
        `${missing.length === 1 ? "isn't" : "aren't"} in the Labor Distribution file at all — it was probably run for ` +
        `fewer people than are paid from this account.</p>` : "") +
      glTable(fli.gl_only));
  }
  if (fli.ld_only.length) {
    out.push(`<h3>In Labor Distribution, not in the ledger (${fli.ld_only.length})</h3>` + ldTable(fli.ld_only, false));
  }
  if (fli.amount_differs.length) {
    out.push(`<h3>Same transaction, different amount (${fli.amount_differs.length})</h3>` +
      `<table><thead><tr><th>Person</th><th>Pay element</th><th>Transaction</th><th class="num">Labor Distribution</th><th class="num">Ledger</th><th class="num">Difference</th></tr></thead><tbody>` +
      fli.amount_differs.map((m) => `<tr><td>${esc(m.ld.person)}</td><td>${esc(m.ld.pay_element)}</td><td class="mono">${esc(m.ld.txn)}</td>` +
        moneyCell(m.ld.amount) + moneyCell(m.gl.amount) + moneyCell(m.gl.amount - m.ld.amount) + `</tr>`).join("") +
      `</tbody></table>`);
  }
  if (fli.moved_out.length) {
    out.push(`<h3>Charged here in Labor Distribution, posted to another account or period in the ledger (${fli.moved_out.length})</h3>` +
      movedTable(fli.moved_out, (m) => esc(`${m.gl.key.split("-")[3]} ${m.gl.account_name}`) +
        (m.to_period !== r.period ? `, ${esc(m.to_period)}` : "") + `<div class="small-muted mono">${esc(m.to)}</div>`, "Posted to"));
  }
  if (fli.moved_in.length) {
    out.push(`<h3>In the ledger here, charged to another account or period in Labor Distribution (${fli.moved_in.length})</h3>` +
      movedTable(fli.moved_in, (m) => `<span class="mono">${esc(m.from)}</span>` +
        (m.from_period !== r.period ? `, ${esc(m.from_period)}` : ""), "Charged in LD to"));
  }
  if (fli.moved_out.length || fli.moved_in.length) {
    out.push(fli.unexplained === 0
      ? `<p class="explain">That accounts for the whole difference: accounting posted these lines to a different GL account (or period) than the one Labor Distribution shows.</p>`
      : `<p class="explain">Counting those, <strong>${money(Math.abs(fli.unexplained))}</strong> is still unexplained.</p>`);
  }
  if (!fli.gl_only.length && !fli.ld_only.length && !fli.amount_differs.length
      && !fli.moved_out.length && !fli.moved_in.length) {
    out.push(`<p class="explain">Every Fund Line Items line pairs with a Labor Distribution line, so the difference isn't in the individual lines — check that both reports were run for the same period.</p>`);
  }
  return out.join("");
}

// Lines whose two sides sit on different accounts; `where` describes the other side.
function movedTable(moves, where, whereLabel) {
  return `<table><thead><tr><th>Person</th><th>Pay element</th><th>Transaction</th><th>${esc(whereLabel)}</th>` +
    `<th class="num">Amount</th></tr></thead><tbody>` +
    moves.map((m) => `<tr><td>${esc(m.ld.person)}</td><td>${esc(m.ld.pay_element)}</td>` +
      `<td class="mono">${esc(m.ld.txn)}</td><td>${where(m)}</td>${moneyCell(m.ld.amount)}</tr>`).join("") +
    `</tbody></table>`;
}

function ldTable(lines, subtotals) {
  if (!lines.length) return `<p class="small-muted">None.</p>`;
  const rows = [];
  let i = 0;
  while (i < lines.length) {
    let j = i;
    while (j < lines.length && lines[j].person === lines[i].person) j++;
    for (const l of lines.slice(i, j)) {
      rows.push(`<tr><td>${esc(l.person)}</td><td>${esc(l.assignment)}</td><td>${esc(l.pay_element)}</td>` +
        `<td class="mono">${esc(l.pay_start)}–${esc(l.pay_end)}</td><td class="mono">${esc(l.txn)}</td>` +
        `<td class="num">${esc(l.percent)}</td>` + moneyCell(l.amount) + `</tr>`);
    }
    if (subtotals && j - i > 1) {
      const sub = lines.slice(i, j).reduce((a, l) => a + l.amount, 0);
      rows.push(`<tr class="person-sub"><td colspan="6">${esc(lines[i].person)} subtotal</td>${moneyCell(sub)}</tr>`);
    }
    i = j;
  }
  return `<table><thead><tr><th>Person</th><th>Assignment</th><th>Pay element</th><th>Payroll period</th>` +
    `<th>Transaction</th><th class="num">Line %</th><th class="num">Amount</th></tr></thead><tbody>${rows.join("")}</tbody></table>`;
}

// Ledger lines, grouped by whose pay they are (lines that aren't anyone's
// pay — journals, transfers — come first), with a subtotal per person.
function glTable(lines) {
  const rows = [];
  let i = 0;
  while (i < lines.length) {
    let j = i;
    while (j < lines.length && lines[j].person === lines[i].person) j++;
    for (const f of lines.slice(i, j)) {
      const what = f.person ? f.assignment : (f.text || f.header_text);
      const sub = f.person ? f.header_text : (f.text && f.header_text ? f.header_text : "");
      rows.push(`<tr><td>${f.person ? esc(f.person) : `<span class="small-muted">—</span>`}</td>` +
        `<td>${esc(what)}${sub ? `<div class="small-muted">${esc(sub)}</div>` : ""}</td>` +
        `<td class="mono">${esc(f.posted)}</td><td>${esc(f.doc_type)}</td><td class="mono">${esc(f.ref)}</td>` +
        `<td>${esc(f.user)}</td>${moneyCell(f.amount)}</tr>`);
    }
    if (lines[i].person && j - i > 1) {
      const total = lines.slice(i, j).reduce((a, f) => a + f.amount, 0);
      rows.push(`<tr class="person-sub"><td colspan="6">${esc(lines[i].person)} subtotal</td>${moneyCell(total)}</tr>`);
    }
    i = j;
  }
  return `<table><thead><tr><th>Person</th><th>Assignment / description</th><th>Entered</th><th>Document type</th>` +
    `<th>Reference</th><th>Entered by</th><th class="num">Amount</th></tr></thead><tbody>${rows.join("")}</tbody></table>`;
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

function leftoverTable(lines) {
  return `<table><thead><tr><th>Account combination</th><th>Period</th><th>Person</th><th>Pay element</th>` +
    `<th>Status</th><th class="num">Amount</th></tr></thead><tbody>` +
    lines.map((l) => `<tr><td>${comboHtml(l.combo)}</td><td class="mono">${esc(l.period)}</td><td>${esc(l.person)}</td>` +
      `<td>${esc(l.pay_element)}</td><td>${esc(l.status)}</td>${moneyCell(l.amount)}</tr>`).join("") +
    `</tbody></table>`;
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

$("#export-btn").addEventListener("click", () => { window.location = "/api/export"; });

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

$("#search").addEventListener("input", renderView);

$("#expand-all").addEventListener("click", () => {
  const q = $("#search").value.trim().toLowerCase();
  const keys = view === "people"
    ? state.result.people.filter((p) => personMatches(p, q)).map((p) => "p|" + p.name)
    : state.result.rows.filter((r) => rowMatches(r, q)).map((r) => r.combo + "|" + r.period);
  const allOpen = keys.every((k) => open.has(k));
  keys.forEach((k) => (allOpen ? open.delete(k) : open.add(k)));
  renderView();
});

loadFolder().catch((e) => renderNotes([e.message]));
