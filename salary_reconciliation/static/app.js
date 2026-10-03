// Salary Reconciliation page. Talks only to its own local server.
"use strict";

const STATUS = {
  match: "Matches",
  mismatch: "Does not match",
  not_in_gl: "Not in DetailBalances",
  gl_only: "Ledger only — no Labor Distribution",
  unchecked: "Not compared yet",
};
const KIND_NAMES = {
  labor_distribution: "Labor Distribution",
  detail_balances: "DetailBalances",
  fund_line_items: "Fund Line Items",
};

let state = null;
let filter = "all";
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
  if (p.length !== 8) return `<span class="mono">${esc(combo)}</span>`;
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

  const hasGL = res.loaded.detail_balances;
  const c = res.counts;
  const problems = (c.mismatch || 0) + (c.not_in_gl || 0) + (c.gl_only || 0);
  const diffTotal = hasGL ? res.totals.gl - res.totals.ld : null;
  $("#tiles").innerHTML = [
    tile("Account combinations", res.rows.length),
    tile(hasGL ? "Labor Distribution (compared)" : "Labor Distribution total", money(res.totals.ld)),
    hasGL ? tile("Match the ledger", c.match || 0, "good") : tile("DetailBalances", "not loaded"),
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
  const outsidePeriods = [...new Set(res.outside_periods.map((l) => l.period))];
  if (hasGL && outsidePeriods.length) {
    notes.push(`DetailBalances covers ${res.periods.gl.join(", ") || "no periods"}; ` +
               `Labor Distribution lines in ${outsidePeriods.join(", ")} are listed at the bottom and not compared.`);
  }
  $("#period-note").innerHTML = notes.map(esc).join("<br>");
  $("#period-note").className = "note info";

  renderTable();
  renderLeftovers();
}

function tile(label, value, cls = "", sub = "") {
  return `<div class="tile ${cls}"><div class="label">${esc(label)}</div>` +
    `<div class="value">${esc(value)}</div>${sub ? `<div class="small-muted">${esc(sub)}</div>` : ""}</div>`;
}

function rowMatches(r, q) {
  if (filter === "problems" && (r.status === "match" || r.status === "unchecked")) return false;
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
      `<td class="num">${res.loaded.detail_balances ? money(r.gl_total ?? 0) : "—"}</td>` +
      (r.diff === null ? `<td class="num">—</td>` : moneyCell(r.diff)) +
      `<td><span class="status ${r.status}">${esc(STATUS[r.status])}</span></td></tr>` +
      (isOpen ? `<tr class="detail"><td colspan="7">${detailHtml(r)}</td></tr>` : "");
  }).join("") || `<tr><td colspan="7" class="small-muted">Nothing to show with this filter.</td></tr>`;

  const shown = rows.map(([r]) => r);
  const sum = (f) => shown.reduce((a, r) => a + (f(r) || 0), 0);
  $("#summary tfoot").innerHTML = `<tr><td></td><td>Total (${shown.length} shown)</td><td></td>` +
    moneyCell(sum((r) => r.ld_total)) +
    `<td class="num">${res.loaded.detail_balances ? money(sum((r) => r.gl_total)) : "—"}</td>` +
    (res.loaded.detail_balances ? moneyCell(sum((r) => r.diff)) : `<td class="num">—</td>`) +
    `<td></td></tr>`;
  $("#expand-all").textContent = rows.length && rows.every(([r]) => open.has(key(r))) ? "Collapse all" : "Expand all";
}

function detailHtml(r) {
  const parts = [];
  const res = state.result;
  if (res.loaded.detail_balances && r.status !== "match") {
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
    out.push(`<h3>In the ledger, not in Labor Distribution (${fli.gl_only.length})</h3>` + glTable(fli.gl_only));
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
  if (!fli.gl_only.length && !fli.ld_only.length && !fli.amount_differs.length) {
    out.push(`<p class="explain">Every Fund Line Items line pairs with a Labor Distribution line, so the difference isn't in the individual lines — check that both reports were run for the same period.</p>`);
  }
  if (fli.matched_by_amount.length) {
    out.push(`<p class="small-muted">${fli.matched_by_amount.length} ledger line(s) were paired with Labor Distribution by amount because their reference didn't match a transaction number.</p>`);
  }
  return out.join("");
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

function glTable(lines) {
  return `<table><thead><tr><th>Entered</th><th>Description</th><th>Document type</th><th>Reference</th>` +
    `<th>Entered by</th><th class="num">Amount</th></tr></thead><tbody>` +
    lines.map((f) => `<tr><td class="mono">${esc(f.posted)}</td><td>${esc(f.text || f.header_text)}` +
      (f.text && f.header_text ? `<div class="small-muted">${esc(f.header_text)}</div>` : "") +
      `</td><td>${esc(f.doc_type)}</td><td class="mono">${esc(f.ref)}</td><td>${esc(f.user)}</td>` +
      moneyCell(f.amount) + `</tr>`).join("") + `</tbody></table>`;
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

$("#clear-btn").addEventListener("click", async () => {
  await api("/api/clear", { method: "POST" });
  open.clear();
  renderNotes([]);
  refresh();
});

$("#export-btn").addEventListener("click", () => { window.location = "/api/export"; });

$("#summary tbody").addEventListener("click", (e) => {
  const tr = e.target.closest("tr.row");
  if (!tr) return;
  const k = tr.dataset.key;
  open.has(k) ? open.delete(k) : open.add(k);
  renderTable();
});

document.querySelector(".seg").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  filter = b.dataset.filter;
  document.querySelectorAll(".seg button").forEach((x) => x.classList.toggle("on", x === b));
  renderTable();
});

$("#search").addEventListener("input", renderTable);

$("#expand-all").addEventListener("click", () => {
  const q = $("#search").value.trim().toLowerCase();
  const keys = state.result.rows.filter((r) => rowMatches(r, q)).map((r) => r.combo + "|" + r.period);
  const allOpen = keys.every((k) => open.has(k));
  keys.forEach((k) => (allOpen ? open.delete(k) : open.add(k)));
  renderTable();
});

refresh().catch((e) => renderNotes([e.message]));
