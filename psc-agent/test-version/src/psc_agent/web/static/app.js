"use strict";
// PSC Agent frontend: vanilla JS over the Flask API (/api/...). Everything that comes from the server
// (file names, model output, skill text) goes through esc() before it reaches innerHTML.

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const val = (sel) => $(sel).value.trim();
const num = (sel) => Number($(sel).value) || undefined;

// Full class names (Tailwind only generates classes it can see written out).
const BADGE = {
  done: "bg-emerald-100 text-emerald-800", running: "bg-sky-100 text-sky-800", queued: "bg-slate-100 text-slate-700",
  failed: "bg-rose-100 text-rose-800", cancelled: "bg-amber-100 text-amber-800", interrupted: "bg-amber-100 text-amber-800",
  accept: "bg-emerald-100 text-emerald-800", verify_more: "bg-amber-100 text-amber-800", revise: "bg-rose-100 text-rose-800",
  match: "bg-emerald-100 text-emerald-800", mismatch: "bg-rose-100 text-rose-800", unit_mismatch: "bg-orange-100 text-orange-800",
  missing_in_record: "bg-violet-100 text-violet-800", not_found: "bg-slate-100 text-slate-700", needs_si: "bg-sky-100 text-sky-800", review: "bg-amber-100 text-amber-800",
  core: "bg-emerald-100 text-emerald-800", context: "bg-sky-100 text-sky-800", provenance: "bg-slate-100 text-slate-700",
  drop: "bg-rose-100 text-rose-800",
  not_checked: "bg-slate-200 text-slate-700", project: "bg-indigo-100 text-indigo-800", generated: "bg-teal-100 text-teal-800",
  role: "bg-slate-100 text-slate-600", other: "bg-slate-100 text-slate-700",
};
const badge = (text, kind = text) => `<span class="badge ${BADGE[kind] || BADGE.other}">${esc(text)}</span>`;
const FINDING_ORDER = ["mismatch", "unit_mismatch", "missing_in_record", "not_found", "needs_si", "review", "not_checked", "match"];
const TABS = ["overview", "dashboard", "recheck", "extract", "analyze", "columns", "skills", "jobs"];

const state = { status: null, pairs: null, pdfs: [], pdfChoice: null, selected: new Set(), skills: [], roles: [],
  skill: null, jobs: [], job: null, rows: [], filter: "", timers: {} };

async function api(path, options = {}) {
  const json = options.body && !(options.body instanceof FormData);
  const res = await fetch(path, { ...options, headers: { ...(json ? { "Content-Type": "application/json" } : {}), ...(options.headers || {}) } });
  let data = {};
  try { data = await res.json(); } catch { /* a non-JSON error page */ }
  if (!res.ok) throw new Error(data.error || `${res.status} ${res.statusText}`);
  return data;
}

function toast(message, ok = true) {
  const t = $("#toast");
  t.textContent = message;
  t.className = `fixed right-4 bottom-4 z-50 max-w-md rounded-lg px-4 py-3 text-sm text-white shadow-lg ${ok ? "bg-slate-900" : "bg-rose-600"}`;
  clearTimeout(state.timers.toast);
  state.timers.toast = setTimeout(() => t.classList.add("hidden"), 6000);
}

async function guard(fn) {
  try { await fn(); } catch (e) { toast(e.message, false); }
}

const fileUrl = (jobId, path, download = false) =>
  `/api/jobs/${encodeURIComponent(jobId)}/files/${path.split("/").map(encodeURIComponent).join("/")}${download ? "?download=1" : ""}`;

// ---------------------------------------------------------------- markdown (escaped first, then a small subset)

function md(text) {
  const inline = (s) => s.replace(/`([^`]+)`/g, "<code>$1</code>").replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  let html = "", list = null, para = [], table = [];
  const flushPara = () => { if (para.length) { html += `<p>${inline(para.join(" "))}</p>`; para = []; } };
  const flushList = () => { if (list) { html += `<${list.tag}>${list.items.map((i) => `<li>${inline(i)}</li>`).join("")}</${list.tag}>`; list = null; } };
  const flushTable = () => {
    if (!table.length) return;
    const rows = table.filter((r) => !/^\|?\s*:?-{2,}/.test(r)).map((r) => r.replace(/^\||\|$/g, "").split("|").map((c) => c.trim()));
    html += `<table>${rows.map((r, i) => `<tr>${r.map((c) => (i ? `<td>${inline(c)}</td>` : `<th>${inline(c)}</th>`)).join("")}</tr>`).join("")}</table>`;
    table = [];
  };
  for (const line of esc(text).split(/\r?\n/)) {
    let m;
    if (/^\s*\|/.test(line)) { flushPara(); flushList(); table.push(line.trim()); continue; }
    flushTable();
    if ((m = line.match(/^(#{1,3})\s+(.*)$/))) { flushPara(); flushList(); html += `<h${m[1].length}>${inline(m[2])}</h${m[1].length}>`; }
    else if ((m = line.match(/^\s*[-*]\s+(.*)$/))) { flushPara(); if (list?.tag !== "ul") { flushList(); list = { tag: "ul", items: [] }; } list.items.push(m[1]); }
    else if ((m = line.match(/^\s*\d+[.)]\s+(.*)$/))) { flushPara(); if (list?.tag !== "ol") { flushList(); list = { tag: "ol", items: [] }; } list.items.push(m[1]); }
    else if (!line.trim()) { flushPara(); flushList(); }
    else { flushList(); para.push(line.trim()); }
  }
  flushPara(); flushList(); flushTable();
  return html;
}

// ---------------------------------------------------------------- routing

function route() {
  const [tab, id] = (location.hash.slice(1) || "overview").split("/");
  const active = TABS.includes(tab) ? tab : "overview";
  $$("#tabs [data-tab]").forEach((a) => a.classList.toggle("tab-active", a.dataset.tab === active));
  $$(".tab-panel").forEach((p) => p.classList.toggle("hidden", p.id !== `tab-${active}`));
  if (active !== "jobs") stopPolling();
  LOADERS[active](id && decodeURIComponent(id));
}

function stopPolling() {
  clearInterval(state.timers.job);
  clearInterval(state.timers.jobs);
}

// ---------------------------------------------------------------- dashboard: accuracy against the hand-entered workbooks

const pctTxt = (v, d = 0) => (v == null ? "–" : `${(v * 100).toFixed(d)}%`);
const tone = (v) => (v == null ? "#94a3b8" : v >= 0.7 ? "#16a34a" : v >= 0.4 ? "#d97706" : "#dc2626");
const COLOR_A = "#475569", COLOR_B = "#0284c7";
const enc = encodeURIComponent;
const pbar = (v, color) => `<div style="height:.45rem;border-radius:.25rem;background:#e2e8f0;overflow:hidden"><div style="height:100%;width:${v == null ? 0 : Math.round(v * 100)}%;background:${color || tone(v)}"></div></div>`;
const delta = (a, b) => (a == null || b == null ? "" : `<span style="color:${b >= a ? "#16a34a" : "#dc2626"}">${b >= a ? "+" : "−"}${Math.abs((b - a) * 100).toFixed(0)} pts</span>`);
const rateCell = (vals) => (vals.length === 1
  ? `<div>${pctTxt(vals[0])}</div>${pbar(vals[0])}`
  : vals.map((v, i) => `<div style="display:flex;gap:.5rem;align-items:center"><span style="width:2.6rem">${pctTxt(v)}</span><div style="flex:1">${pbar(v, i ? COLOR_B : COLOR_A)}</div></div>`).join("") +
    `<div class="text-xs">${delta(vals[0], vals[1])}</div>`);
const share = (part, whole) => (whole ? `${part} of ${whole}` : "–");

// [label, what it means, rate, "x of y"]: every tile of the headline row
const DASH_METRICS = [
  ["Values that match", "Of all the hand-entered values, the share the model extracted correctly. It also counts values that only the supporting information holds, which a PDF cannot give.",
    (s) => s.values.match_rate, (s) => share(s.values.match, s.values.n)],
  ["…of those in the PDF text", "The fair measure. Only hand-entered values that really appear in the PDF text are counted, so what the paper itself does not say is not held against the model.",
    (s) => s.values.match_rate_likely, (s) => (s.values.likely ? share(Math.round(s.values.match_rate_likely * s.values.likely), s.values.likely) : "–")],
  ["Correct when given", "When the model gave a value, how often it was right (precision).",
    (s) => s.values.accuracy_when_found, (s) => share(s.values.match, s.values.found)],
  ["Values the model gave", "The share of hand-entered values for which the model gave any value (coverage). Low coverage is the usual reason for a low score.",
    (s) => s.values.found_rate, (s) => share(s.values.found, s.values.n)],
  ["Core fields", "Champion PCE, Voc, Jsc and FF, and the materials of the HTL, its treatment, the perovskite and the ETL: the cells an analysis cannot do without.",
    (s) => s.core.match_rate, (s) => share(s.core.match, s.core.n)],
  ["Rows found", "A layer, material, process step, property or test of the record that the model also listed (whatever its values).",
    (s) => s.rows.match_rate, (s) => share(s.rows.match, s.rows.n)],
];

async function loadDashboard() {
  await getPairs().catch(() => null); // the run form lists the papers that have workbooks
  const { runs } = await api("/api/dashboard/runs");
  const d = (state.dash = state.dash || {});
  d.runs = runs;
  const label = (r) => `${r.label} · ${r.model || r.provider || "?"} · ${r.papers} paper${r.papers > 1 ? "s" : ""} · ${(r.modified || "").slice(0, 16).replace("T", " ")}`;
  const prevA = $("#dash-a").value, prevB = $("#dash-b").value;
  $("#dash-a").innerHTML = runs.map((r) => `<option value="${esc(r.id)}">${esc(label(r))}</option>`).join("");
  $("#dash-b").innerHTML = `<option value="">— no comparison —</option>` + runs.map((r) => `<option value="${esc(r.id)}">${esc(label(r))}</option>`).join("");
  $("#dash-a").value = runs.some((r) => r.id === prevA) ? prevA : (runs[0]?.id || "");
  $("#dash-b").value = runs.some((r) => r.id === prevB) ? prevB : "";
  renderDashPapers();
  await drawDashboard();
}

async function drawDashboard() {
  const a = $("#dash-a").value, b = $("#dash-b").value;
  const body = $("#dash-body");
  if (!a) {
    body.innerHTML = `<div class="card text-sm text-slate-500">No evaluation has finished yet. Choose papers below and press Evaluate: when the first paper is done it appears here.
      Runs made on the command line (folders out/ and evaluate_out/) are listed too.</div>`;
    return;
  }
  body.innerHTML = `<div class="card text-sm text-slate-500">Reading the run…</div>`;
  try {
    if (b && b !== a) {
      const c = await api(`/api/dashboard/compare?a=${enc(a)}&b=${enc(b)}`);
      state.dash.view = { labels: c.labels, meta: c.meta, sums: [c.a, c.b], totals: c.totals, papers: c.papers, agree: c.agree,
        compared: c.cells_compared, differing: c.differing, differingTotal: c.differing_total,
        perPaper: c.papers.map((k) => ({ key: k, values: [c.by_paper[k].a, c.by_paper[k].b] })) };
    } else {
      const r = await api(`/api/dashboard/run?id=${enc(a)}`);
      state.dash.view = { labels: [r.label], meta: [r.meta], sums: [r.summary], totals: [r.totals], papers: r.papers.map((p) => p.key),
        perPaper: r.papers.map((p) => ({ key: p.key, pdf: p.pdf, values: [p.values], devices: `${p.paired_devices} of ${p.record_devices}`, errors: p.errors })) };
    }
  } catch (e) {
    body.innerHTML = `<div class="card text-sm text-rose-700">${esc(e.message)}</div>`;
    return;
  }
  renderDashBody();
  await drawDashCells();
}

function tileHtml(m, sums) {
  const [label, hint, rate, of] = m;
  const vals = sums.map(rate);
  const inner = sums.length === 1
    ? `<div style="font-size:2rem;font-weight:700;line-height:1.1;color:${tone(vals[0])}">${pctTxt(vals[0])}</div>${pbar(vals[0])}<div class="text-xs text-slate-500" style="margin-top:.25rem">${of(sums[0])}</div>`
    : sums.map((s, i) => `<div style="display:flex;justify-content:space-between;align-items:baseline"><span class="text-xs text-slate-500">${i ? "B" : "A"}</span>
        <span style="font-size:1.5rem;font-weight:700;color:${i ? COLOR_B : COLOR_A}">${pctTxt(vals[i])}</span></div>${pbar(vals[i], i ? COLOR_B : COLOR_A)}
        <div class="text-xs text-slate-500" style="margin:.15rem 0 .4rem">${of(s)}</div>`).join("") + `<div class="text-xs">B − A: ${delta(vals[0], vals[1])}</div>`;
  return `<div class="card" title="${esc(hint)}" style="flex:1 1 11rem;min-width:11rem"><div class="text-xs text-slate-500" style="margin-bottom:.35rem">${esc(label)} <span title="${esc(hint)}" style="cursor:help">ⓘ</span></div>${inner}</div>`;
}

function renderDashBody() {
  const v = state.dash.view, two = v.sums.length === 2;
  const heads = v.labels.map((l, i) => `<div><b>${two ? (i ? "B" : "A") + ": " : ""}${esc(l)}</b> <span class="text-slate-500">${esc([v.meta[i]?.provider, v.meta[i]?.model].filter(Boolean).join(" · "))}</span></div>`).join("");
  const tot = v.totals.map((t, i) => `<div class="text-xs text-slate-600">${two ? (i ? "B" : "A") + ": " : ""}${t.calls} model calls · ${Number(t.tokens_in).toLocaleString()} tokens in, ${Number(t.tokens_out).toLocaleString()} out ·
      ${t.cost > 0 ? "$" + Number(t.cost).toFixed(2) : "cost not priced"} · ${Math.round(t.seconds)} s${t.errors ? ` · <span style="color:#dc2626">${t.errors} error(s)</span>` : ""}</div>`).join("");
  const sheets = Object.keys(v.sums[0].by_sheet).filter((s) => v.sums.some((x) => x.by_sheet[s].values.n || x.by_sheet[s].rows.n));
  const cell = (stats, key) => stats?.[key];
  const sheetRows = sheets.map((s) => {
    const vs = v.sums.map((x) => x.by_sheet[s].values);
    return `<tr><td class="td font-medium">${esc(s)}</td><td class="td tabular-nums">${vs[0].n}</td><td class="td" style="min-width:11rem">${rateCell(vs.map((x) => x.match_rate))}</td>
      <td class="td">${rateCell(vs.map((x) => x.found_rate))}</td><td class="td">${rateCell(vs.map((x) => x.accuracy_when_found))}</td>
      <td class="td">${rateCell(vs.map((x) => x.match_rate_likely))}</td></tr>`;
  }).join("");
  const groups = [...new Set(v.sums.flatMap((x) => Object.keys(x.core_groups)))].sort();
  const coreRows = groups.map((g) => {
    const cs = v.sums.map((x) => x.core_groups[g]);
    return `<tr><td class="td font-medium">${esc(g)}</td><td class="td tabular-nums">${(cs[0] || cs[1]).n}</td><td class="td" style="min-width:11rem">${rateCell(cs.map((x) => cell(x, "match_rate")))}</td></tr>`;
  }).join("");
  const paperRows = v.perPaper.map((p) => `<tr data-dashpaper="${esc(p.key)}" style="cursor:pointer" title="show this paper's values that did not match">
      <td class="td font-mono text-xs">${esc(p.key)}${p.pdf ? `<div class="text-slate-400" style="font-family:inherit">${esc(p.pdf.length > 60 ? p.pdf.slice(0, 58) + "…" : p.pdf)}</div>` : ""}</td>
      <td class="td tabular-nums text-xs">${p.values[0].n}${p.devices ? `<div class="text-slate-400">devices matched ${esc(p.devices)}</div>` : ""}</td>
      <td class="td" style="min-width:11rem">${rateCell(p.values.map((x) => x.match_rate))}</td>
      <td class="td">${rateCell(p.values.map((x) => x.match_rate_likely))}</td>
      <td class="td text-xs">${(p.errors || []).length ? `<span style="color:#dc2626">${esc(p.errors[0].slice(0, 80))}</span>` : ""}</td></tr>`).join("");
  const th = (t, hint) => `<th class="th" title="${esc(hint || "")}">${t}</th>`;
  const agree = two ? `<div class="card space-y-2"><h3 class="card-title">Cell by cell (${v.compared} hand-entered values in both runs)</h3>
      <div style="display:flex;gap:.75rem;flex-wrap:wrap">${[["Both match", v.agree.both], ["Only A matches", v.agree.only_a], ["Only B matches", v.agree.only_b], ["Neither matches", v.agree.neither]]
        .map(([k, n]) => `<div style="flex:1 1 9rem"><div class="stat">${n}</div><div class="text-xs text-slate-500">${k}</div></div>`).join("")}</div>
      <details><summary class="cursor-pointer text-sm font-medium">Where they disagree (${Math.min(v.differing.length, 300)} of ${v.differingTotal})</summary>
      <div style="max-height:24rem;overflow:auto"><table class="w-full"><thead><tr>${["Paper", "Cell", "Hand-entered", "A gave", "B gave"].map((h) => th(h)).join("")}</tr></thead><tbody>
      ${v.differing.map((d) => `<tr><td class="td font-mono text-xs">${esc(d.paper)}</td><td class="td text-xs">${esc(d.sheet)} · ${esc(d.row)}<div class="text-slate-400">${esc(d.column)}</div></td>
        <td class="td text-xs">${esc(d.record)} ${esc(d.record_unit)}</td><td class="td text-xs">${badge(d.status)} ${esc(d.extracted)}</td><td class="td text-xs">${badge(d.other_status)} ${esc(d.other_extracted)}</td></tr>`).join("")}
      </tbody></table></div></details></div>` : "";
  const sheetOpts = sheets.map((s) => `<option value="${esc(s)}">${esc(s)}</option>`).join("");
  const paperOpts = v.papers.map((k) => `<option value="${esc(k)}">${esc(k)}</option>`).join("");
  $("#dash-body").innerHTML = `
    <div class="card space-y-1">${heads}${tot}<div class="text-xs text-slate-500">${v.papers.length} paper${v.papers.length > 1 ? "s" : ""}${two ? " present in both runs" : ""}. Hover a tile for what it measures.</div></div>
    <div style="display:flex;gap:1rem;flex-wrap:wrap">${DASH_METRICS.map((m) => tileHtml(m, v.sums)).join("")}</div>
    ${agree}
    <div class="card space-y-2"><h3 class="card-title">By sheet</h3><div style="overflow:auto"><table class="w-full"><thead><tr>${th("Sheet")}${th("Values in record")}${th("Match", "Share of the hand-entered values that match")}
      ${th("Model gave", "Share of hand-entered values the model gave any value for")}${th("Correct when given")}${th("Match, of those in the PDF text", "The fair measure")}</tr></thead><tbody>${sheetRows}</tbody></table></div></div>
    <div class="card space-y-2"><h3 class="card-title">Core fields</h3><div style="overflow:auto"><table class="w-full"><thead><tr>${th("Field")}${th("In record")}${th("Match")}</tr></thead><tbody>${coreRows}</tbody></table></div></div>
    <div class="card space-y-2"><h3 class="card-title">By paper <span class="text-xs text-slate-500" style="font-weight:400">(click a row to see its values that did not match)</span></h3>
      <div style="max-height:28rem;overflow:auto"><table class="w-full"><thead><tr>${th("Paper")}${th("Values")}${th("Match")}${th("Match, of those in the PDF text")}${th("Errors")}</tr></thead><tbody>${paperRows}</tbody></table></div></div>
    <div class="card space-y-3"><h3 class="card-title">Values that did not match${two ? " (run A)" : ""}</h3>
      <div style="display:flex;gap:.75rem;flex-wrap:wrap;align-items:end">
        <div><label class="label" for="dash-cstatus">Status</label><select id="dash-cstatus" class="input"><option value="">All that did not match</option><option value="mismatch">mismatch (a different value)</option>
          <option value="unit_mismatch">unit_mismatch</option><option value="review">review (text that may mean the same)</option><option value="not_found">not_found (the model gave nothing)</option><option value="match">match</option></select></div>
        <div><label class="label" for="dash-csheet">Sheet</label><select id="dash-csheet" class="input"><option value="">All</option>${sheetOpts}</select></div>
        <div><label class="label" for="dash-cpaper">Paper</label><select id="dash-cpaper" class="input"><option value="">All</option>${paperOpts}</select></div>
        <label class="text-sm" style="display:flex;gap:.4rem;align-items:center;padding-bottom:.5rem"><input id="dash-ccore" type="checkbox"> core fields only</label></div>
      <div id="dash-cells" style="max-height:28rem;overflow:auto"></div></div>`;
}

async function drawDashCells() {
  const box = $("#dash-cells");
  if (!box) return;
  const q = new URLSearchParams({ id: $("#dash-a").value, status: $("#dash-cstatus").value, sheet: $("#dash-csheet").value, paper: $("#dash-cpaper").value, limit: "200" });
  if ($("#dash-ccore").checked) q.set("core", "1");
  const r = await api(`/api/dashboard/cells?${q}`);
  box.innerHTML = `<div class="text-xs text-slate-500" style="margin-bottom:.25rem">${r.cells.length} of ${r.total} values</div>
    <table class="w-full"><thead><tr>${["Status", "Paper", "Cell", "Hand-entered", "Model gave", "Detail"].map((h) => `<th class="th">${h}</th>`).join("")}</tr></thead><tbody>
    ${r.cells.map((c) => `<tr><td class="td">${badge(c.status)}${c.core ? `<div class="text-xs text-slate-400">core</div>` : ""}</td><td class="td font-mono text-xs">${esc(c.paper)}</td>
      <td class="td text-xs">${esc(c.sheet)} · ${esc(c.row)}<div class="text-slate-400">${esc(c.column)}</div></td><td class="td text-xs">${esc(c.record)} ${esc(c.record_unit)}</td>
      <td class="td text-xs">${esc(c.extracted)} ${esc(c.extracted_unit)}</td><td class="td text-xs text-slate-500">${esc(c.detail)}${c.likely ? "" : `<div class="text-slate-400">not in the PDF text</div>`}</td></tr>`).join("")}</tbody></table>
    ${r.cells.length ? "" : `<p class="p-4 text-sm text-slate-500">Nothing here.</p>`}`;
}

// the run form: the papers that have hand-entered workbooks
function dashPaperRows() {
  const q = ($("#dash-search")?.value || "").trim().toLowerCase();
  const key = q.replace(/[^0-9a-z]/g, "");
  return ((state.pairs && state.pairs.pairs) || []).filter((p) => p.workbooks && (!q || (key && p.key.includes(key)) || p.pdf.toLowerCase().includes(q)));
}

function renderDashPapers() {
  state.dashSel = state.dashSel || new Set();
  $("#dash-papers").innerHTML = `<table class="w-full"><thead><tr><th class="th"></th><th class="th">DOI key</th><th class="th">PDF</th><th class="th">Devices</th><th class="th">SI</th></tr></thead><tbody>
    ${dashPaperRows().map((p) => `<tr><td class="td"><input type="checkbox" data-dashdoi="${esc(p.key)}" ${state.dashSel.has(p.key) ? "checked" : ""}></td>
      <td class="td font-mono text-xs">${esc(p.key)}</td><td class="td text-xs">${esc(p.pdf)}</td><td class="td tabular-nums">${p.workbooks}</td><td class="td text-xs">${p.si ? `${p.si} file(s)` : ""}</td></tr>`).join("")}</tbody></table>`;
  updateDashButton();
}

function updateDashButton() {
  const n = state.dashSel.size;
  $("#dash-run").disabled = n === 0;
  $("#dash-run").textContent = n ? `Evaluate ${n} paper${n > 1 ? "s" : ""}` : "Select papers to evaluate";
}

// ---------------------------------------------------------------- columns

const RELEVANCE = ["core", "context", "provenance", "drop"];

async function loadColumns() {
  if (!state.columns) {
    $("#columns-table-body").innerHTML = `<p class="text-slate-500">Reading the tables…</p>`;
    state.columns = await api("/api/columns");
    state.colFilter = { table: "", relevance: "", q: "" };
    const tables = Object.keys(state.columns.summary.tables);
    $("#columns-table").innerHTML = `<option value="">All tables (${state.columns.summary.total.columns} columns)</option>` +
      tables.map((t) => `<option value="${esc(t)}">${esc(t)} (${state.columns.summary.tables[t].columns})</option>`).join("");
  }
  renderColumns();
}

function renderColumns() {
  const { summary, columns } = state.columns, f = state.colFilter;
  const chip = (key, label, n) => `<button data-colrel="${esc(key)}" class="btn-ghost ${f.relevance === key ? "ring-2 ring-slate-400" : ""}">${esc(label)} ${n}</button>`;
  $("#columns-chips").innerHTML = chip("", "all", summary.total.columns) +
    RELEVANCE.map((r) => chip(r, r, summary.total[r])).join("") + (summary.total.unrated ? chip("none", "not rated", summary.total.unrated) : "");
  const q = f.q.toLowerCase();
  const rows = columns.filter((c) => (!f.table || c.table === f.table)
    && (!f.relevance || (f.relevance === "none" ? !c.relevance : c.relevance === f.relevance))
    && (!q || `${c.table} ${c.column} ${c.description} ${c.catalog_note} ${c.why}`.toLowerCase().includes(q)));
  $("#columns-table-body").innerHTML = `<div class="mb-2 text-xs text-slate-500">${rows.length} of ${columns.length} columns</div>
    <table class="w-full"><thead><tr><th class="th">Column</th><th class="th">Relevance</th><th class="th">Filled</th><th class="th">Meaning</th><th class="th">Why</th></tr></thead><tbody>
    ${rows.map((c) => `<tr class="align-top">
      <td class="td"><div class="font-mono text-xs font-semibold">${esc(c.column)}</div><div class="text-xs text-slate-400">${esc(c.table)} · ${esc(c.dtype)}${c.role ? " · " + esc(c.role) : ""}</div>
        ${c.examples.length ? `<div class="mt-1 text-xs text-slate-400">e.g. ${c.examples.map(esc).join(" · ")}</div>` : ""}</td>
      <td class="td">${c.relevance ? badge(c.relevance) : `<span class="text-xs text-slate-400">not rated</span>`}</td>
      <td class="td tabular-nums text-xs">${c.pct}%<div class="text-slate-400">${c.papers == null ? "" : c.papers + " papers"}${c.distinct ? " · " + c.distinct + " values" : ""}</div></td>
      <td class="td text-xs">${esc(c.description || c.catalog_note)}</td>
      <td class="td text-xs text-slate-600">${esc(c.why)}</td></tr>`).join("")}</tbody></table>`;
}

// ---------------------------------------------------------------- status + overview

async function loadStatus() {
  const s = (state.status = await api("/api/status"));
  $("#status-chips").innerHTML = (s.key_set ? badge("API key set", "done") : badge("API key missing", "failed")) +
    " " + badge(`default model: ${s.models.default}`, "queued");
  const m = s.models;
  const defaults = {
    "#recheck-model": m.default, "#recheck-judge": m.judge || m.default, "#extract-model": m.default,
    "#extract-mapper": m.mapper || m.default, "#extract-decision": m.decision || m.default, "#analyze-model": m.default,
    "#analyze-analyst": m.analyst || m.default, "#analyze-decision": m.decision || m.default,
    "#dash-model": m.default, "#dash-mapper": m.mapper || m.default, "#extract-simodel": m.si_finder || m.default,
  };
  for (const [sel, v] of Object.entries(defaults)) if (!$(sel).value) $(sel).value = v;
}

async function getPairs(force = false) {
  if (!state.pairs || force) state.pairs = await api("/api/pairs");
  return state.pairs;
}

async function loadOverview() {
  $("#overview-stats").innerHTML = `<div class="card col-span-full text-sm text-slate-500">Reading papers and workbooks…</div>`;
  const [pairs, tables] = await Promise.all([getPairs(), api("/api/data/tables").catch((e) => ({ error: e.message }))]);
  const stats = [
    ["Papers paired", pairs.pairs.length], ["Workbooks", pairs.pairs.reduce((n, p) => n + p.workbooks, 0)],
    ["PDFs without workbooks", pairs.unmatched_pdfs.length], ["DOIs without PDF", pairs.workbooks_without_pdf.length],
    ["Data tables", tables.tables ? tables.tables.length : "–"],
  ];
  $("#overview-stats").innerHTML = stats.map(([k, v]) =>
    `<div class="card"><div class="stat">${esc(v)}</div><div class="mt-1 text-xs text-slate-500">${esc(k)}</div></div>`).join("");
  const s = state.status;
  $("#overview-setup").innerHTML = `
    <div>${s.key_set ? badge("OPENROUTER_API_KEY set", "done") : badge("OPENROUTER_API_KEY missing", "failed")}</div>
    ${s.key_set ? "" : `<p class="text-xs text-rose-700">Set it in the server's environment and restart. The key never reaches this page.</p>`}
    <div class="space-y-1">${Object.entries(s.models).map(([role, model]) =>
      `<div class="flex justify-between gap-2"><span class="text-slate-500">${esc(role)}</span><code class="text-xs">${esc(model)}</code></div>`).join("")}</div>
    <details class="text-xs text-slate-500"><summary class="cursor-pointer">Folders</summary>
      ${Object.entries(s.paths).map(([k, v]) => `<div class="mt-1 break-all"><b>${esc(k)}</b> ${esc(v)}</div>`).join("")}</details>`;
  await refreshJobs();
  $("#overview-jobs").innerHTML = jobsTable(state.jobs.slice(0, 8));
}

function jobsTable(jobs) {
  if (!jobs.length) return `<p class="text-sm text-slate-500">No jobs yet. Start one from Recheck, Extract or Analyze.</p>`;
  return `<table class="w-full"><thead><tr><th class="th">Job</th><th class="th">Status</th><th class="th">Cost</th></tr></thead><tbody>
    ${jobs.map((j) => `<tr data-job="${esc(j.id)}" class="cursor-pointer hover:bg-slate-50 ${state.job?.id === j.id ? "bg-indigo-50" : ""}">
      <td class="td"><div class="font-medium">${esc(j.kind)}</div><div class="text-xs text-slate-500">${esc(j.id)}</div></td>
      <td class="td">${badge(j.status)}</td><td class="td tabular-nums">$${Number(j.cost_usd).toFixed(4)}</td></tr>`).join("")}
  </tbody></table>`;
}

// ---------------------------------------------------------------- recheck

async function loadRecheck() {
  if (!state.pairs) $("#recheck-papers").innerHTML = `<p class="p-4 text-sm text-slate-500">Reading papers and workbooks…</p>`;
  await getPairs();
  renderRecheck();
}

function recheckRows() {
  const q = val("#recheck-search").toLowerCase();
  const key = q.replace(/[^0-9a-z]/g, "");
  return state.pairs.pairs.filter((p) => !q || (key && p.key.includes(key)) || p.pdf.toLowerCase().includes(q));
}

function renderRecheck() {
  const rows = recheckRows();
  $("#recheck-papers").innerHTML = `<table class="w-full"><thead><tr><th class="th w-8"></th><th class="th">DOI key</th><th class="th">PDF</th><th class="th">Devices</th><th class="th">SI</th><th class="th">Flags</th></tr></thead><tbody>
    ${rows.map((p) => `<tr class="hover:bg-slate-50">
      <td class="td"><input type="checkbox" data-doi="${esc(p.key)}" ${state.selected.has(p.key) ? "checked" : ""}></td>
      <td class="td font-mono text-xs">${esc(p.key)}</td><td class="td text-xs">${esc(p.pdf)}</td>
      <td class="td tabular-nums">${p.workbooks}</td>
      <td class="td text-xs">${(p.si_files || []).map((n) => `<div title="${esc(n)}">${esc(n.length > 22 ? n.slice(0, 20) + "…" : n)}
        <button data-sidelete="${esc(n)}" class="text-rose-600" title="remove this file">✕</button></div>`).join("")}
        ${siUploadControl(p.key, p.si ? "Add another SI" : "Upload SI")}</td>
      <td class="td">${p.typo_pdf_doi ? badge("DOI typo in file names", "unit_mismatch") : ""} ${p.duplicates.length ? badge("duplicate PDF", "queued") : ""}</td>
    </tr>`).join("")}</tbody></table>`;
  updateRecheckButton();
}

function updateRecheckButton() {
  const n = state.selected.size;
  $("#recheck-run").disabled = n === 0;
  $("#recheck-run").textContent = n ? `Recheck ${n} paper${n > 1 ? "s" : ""}` : "Select papers to recheck";
}

// ---------------------------------------------------------------- extract

async function loadExtract() {
  state.pdfs = (await api("/api/pdfs")).pdfs;
  renderExtract();
  if (state.pdfChoice && !state.si) await loadSiPlan();
}

function renderExtract() {
  const onlyNew = $("#extract-only-new").checked;
  const rows = state.pdfs.filter((p) => !onlyNew || !p.has_workbooks);
  $("#extract-pdfs").innerHTML = rows.length ? `<table class="w-full"><tbody>${rows.map((p) => `<tr class="hover:bg-slate-50">
      <td class="td w-8"><input type="radio" name="pdf" value="${esc(p.id)}" ${state.pdfChoice === p.id ? "checked" : ""}></td>
      <td class="td text-sm">${esc(p.name)}</td>
      <td class="td text-right">${p.uploaded ? badge("uploaded", "project") : ""} ${p.has_workbooks ? badge("has workbooks", "done") : ""}</td>
    </tr>`).join("")}</tbody></table>` : `<p class="p-4 text-sm text-slate-500">No PDFs here.</p>`;
  renderSi();
}

// step 2: what to do about the paper's SI, decided before the paper is read
async function selectPdf(id) {
  state.pdfChoice = id;
  state.si = null;
  state.siJob = null;
  renderSi();
  await loadSiPlan();
}

async function loadSiPlan() {
  if (!state.pdfChoice) return;
  const id = state.pdfChoice;
  const plan = await api(`/api/si/plan?pdf=${encodeURIComponent(id)}`);
  if (state.pdfChoice !== id) return; // another paper was chosen meanwhile
  const prev = state.si || {};
  state.si = { ...plan, status: plan.status === "have" ? "have" : prev.verdict || plan.status, verdict: prev.verdict,
               agentLinks: prev.agentLinks || [], agentNote: prev.agentNote || "" };
  renderSi();
}

const SI_TEXT = {
  have: "SI file(s) are already saved for this paper. They will be read with it.",
  not_referenced: "The paper never mentions supporting information, so there is nothing to find. You can still upload a file if you have one.",
  pattern: "The paper refers to its SI. Open the publisher's supplementary page, download the file, then upload it here.",
  printed: "The model read the paper: it prints the address of its SI. Open it, download the file, then upload it here.",
  agent_needed: "The paper refers to its SI, but this publisher's page cannot be worked out from the DOI. Let the model read the paper for the address (it searches the web if the paper prints none), or find the file yourself.",
};

function renderSi() {
  const box = $("#extract-si"), btn = $("#extract-run"), note = $("#extract-si-note"), si = state.si;
  $("#extract-si-agent").style.display = "none";
  if (!state.pdfChoice) {
    box.textContent = "Choose a paper first.";
    btn.disabled = true; btn.textContent = "Choose a paper first"; note.textContent = "";
    return;
  }
  if (!si) {
    box.textContent = "Checking the paper for references to its supporting information…";
    btn.disabled = true; btn.textContent = "Checking the SI…"; note.textContent = "";
    return;
  }
  const links = [...si.agentLinks, ...si.links.filter((u) => !si.agentLinks.includes(u))];
  const files = si.files.map((f) => `<div style="display:flex;gap:.5rem;align-items:center">✔ <span class="font-mono text-xs">${esc(f.name)}</span>
      <span class="text-xs text-slate-400">${Math.round(f.size / 1024).toLocaleString()} KB</span><button data-sidel-ex="${esc(f.name)}" class="text-rose-600" title="remove this file">✕</button></div>`).join("");
  const busy = state.siJob ? "disabled" : "", srv = state.agentServer, open = si.status !== "have";
  const agentBtn = open
    ? `<button id="si-agent-run" class="btn-ghost" ${busy}>${state.siJob === "running" ? "The model is reading…" : "Read the paper for its SI (model)"}</button>` : "";
  // the agent server (outside Docker) drives your own Chrome, signed in as you: it can fetch what the web agent gets 403 for
  const chromeBtn = open && srv && srv.reachable
    ? `<button id="si-chrome-run" class="btn-ghost" ${busy}>${state.siJob === "chrome" ? "Chrome is downloading…" : "Download the SI with my Chrome"}</button>` : "";
  const chromeNote = open && srv && srv.reachable
    ? `<div class="text-xs text-slate-500">A Chrome window opens on your computer: sign in or pass a check there if asked. The file is saved for this paper.</div>`
    : open && srv && srv.configured ? `<div class="text-xs text-slate-500">Agent server not reachable: ${esc(srv.error || "")}</div>` : "";
  box.innerHTML = `<div class="space-y-3">
    <div>${badge(si.status === "have" ? "SI loaded" : si.status.replace("_", " "), si.status === "have" ? "done" : si.status === "not_referenced" ? "queued" : "review")}
      <span class="text-slate-700">${esc(SI_TEXT[si.status] || "")}</span></div>
    ${links.length ? `<div><div class="text-xs text-slate-500" style="margin-bottom:.15rem">Where to get it:</div>${links.map((u) =>
        `<div class="break-all"><a style="color:#0369a1;text-decoration:underline" href="${esc(u)}" target="_blank" rel="noopener noreferrer">${esc(u)}</a></div>`).join("")}</div>` : ""}
    ${si.agentNote ? `<div class="text-xs text-slate-500">Agent: ${esc(si.agentNote)}</div>` : ""}
    ${files ? `<div><div class="text-xs text-slate-500" style="margin-bottom:.15rem">Saved for this paper:</div>${files}</div>` : ""}
    <div style="display:flex;gap:.75rem;flex-wrap:wrap;align-items:center">${agentBtn}${chromeBtn}
      ${siUploadControl(si.key, si.files.length ? "Upload another SI file" : "Upload the SI file")}
      <span class="text-xs text-slate-400">saved as <code>${esc(si.save_as)}</code> · optional</span></div>${chromeNote}</div>`;
  $("#extract-si-agent").style.display = si.status === "agent_needed" ? "flex" : "none";
  btn.disabled = false;
  const referenced = ["pattern", "printed", "agent_needed"].includes(si.status);
  if (si.files.length) {
    btn.textContent = `Extract (paper + ${si.files.length} SI file${si.files.length > 1 ? "s" : ""})`;
    note.textContent = "The paper and its SI are read together, once.";
  } else if (referenced) {
    btn.textContent = "Extract without the SI";
    note.textContent = "The SI is not loaded: what only it holds (recipes, suppliers) will be missing and flagged. Upload it in step 2 and extract again to fill it in.";
  } else {
    btn.textContent = "Extract the selected paper";
    note.textContent = "";
  }
}

// the SI agent: a job that answers with addresses; poll it until it is done
async function findSiLink() {
  const id = state.pdfChoice;
  state.siJob = "running";
  renderSi();
  try {
    const job = await api("/api/jobs", { method: "POST", body: JSON.stringify({ kind: "si", params: { pdf: id, si_finder_model: val("#extract-simodel"), budget: 1 } }) });
    toast(`SI agent started: ${job.id}`);
    for (let i = 0; i < 150; i += 1) {
      await new Promise((r) => setTimeout(r, 2000));
      if (state.pdfChoice !== id) return;
      const j = await api(`/api/jobs/${encodeURIComponent(job.id)}`);
      if (j.status === "done") {
        // the model's verdict replaces the text rule's: it can find that the paper never refers to an SI, or prints its address
        const verdict = ["not_referenced", "printed", "pattern"].includes(j.summary.status) ? j.summary.status : state.si.status;
        state.si = { ...state.si, status: verdict, verdict: ["not_referenced", "printed", "pattern"].includes(j.summary.status) ? verdict : undefined, agentLinks: j.summary.links || [], agentNote: j.summary.notes || "" };
        break;
      }
      if (["failed", "cancelled", "interrupted"].includes(j.status)) {
        state.si = { ...state.si, agentNote: `the search ${j.status}: ${j.error || ""}` };
        break;
      }
    }
  } finally {
    state.siJob = null;
    if (state.pdfChoice === id) renderSi();
  }
}

async function loadAgentServer() {
  try {
    state.agentServer = await api("/api/agent-server");
  } catch (e) {
    state.agentServer = null;
  }
  renderSi();
}

// the agent server downloads the SI through your own Chrome; a bot check can keep its window open for minutes
async function downloadWithChrome() {
  const id = state.pdfChoice;
  state.siJob = "chrome";
  renderSi();
  try {
    const job = await api("/api/jobs", { method: "POST", body: JSON.stringify({ kind: "si_download", params: { pdf: id, si_finder_model: val("#extract-simodel") } }) });
    toast(`Chrome download started: ${job.id}`);
    for (let i = 0; i < 700; i += 1) {
      await new Promise((r) => setTimeout(r, 3000));
      if (state.pdfChoice !== id) return;
      const j = await api(`/api/jobs/${encodeURIComponent(job.id)}`);
      if (j.status === "done") {
        const s = j.summary || {};
        state.si = { ...state.si, agentLinks: s.links || [], agentNote: `Chrome ${s.status}${s.notes ? `: ${s.notes}` : ""}` };
        break;
      }
      if (["failed", "cancelled", "interrupted"].includes(j.status)) {
        state.si = { ...state.si, agentNote: `Chrome download ${j.status}: ${j.error || ""}` };
        break;
      }
    }
  } finally {
    state.siJob = null;
    if (state.pdfChoice === id) await loadSiPlan(); // a saved file now shows as "SI loaded"
  }
}

async function uploadPdfs() {
  const files = $("#extract-upload").files;
  if (!files.length) return toast("Choose a PDF first", false);
  const form = new FormData();
  [...files].forEach((f) => form.append("files", f));
  const res = await api("/api/uploads", { method: "POST", body: form });
  toast(`Uploaded ${res.files.length} file(s)`);
  $("#extract-only-new").checked = true;
  await loadExtract();
  await selectPdf(res.files[0].id);
}

// ---------------------------------------------------------------- analyze

async function loadAnalyze() {
  try {
    const t = await api("/api/data/tables");
    $("#analyze-tables").innerHTML = `<table class="w-full"><thead><tr><th class="th">Table</th><th class="th">Rows</th><th class="th">Papers</th></tr></thead><tbody>
      ${t.tables.map((x) => `<tr><td class="td font-mono text-xs">${esc(x.table)}</td><td class="td tabular-nums">${x.rows}</td><td class="td tabular-nums">${esc(x.papers ?? "")}</td></tr>`).join("")}
      </tbody></table>
      <p class="hint">Main table <code>${esc(t.main_table)}</code>. Treatment: ${esc(t.treatment)}</p>
      <button id="btn-show-map" class="btn-ghost mt-3">Show data map</button>
      <pre id="map-text" class="mt-3 hidden max-h-96 overflow-auto rounded-lg bg-slate-900 p-3 text-xs whitespace-pre-wrap text-slate-100"></pre>`;
  } catch (e) {
    $("#analyze-tables").innerHTML = `<p class="text-sm text-rose-700">${esc(e.message)}</p>`;
  }
}

// ---------------------------------------------------------------- skills

async function loadSkills() {
  const res = await api("/api/skills");
  state.skills = res.skills;
  state.roles = res.roles;
  const groups = [["project", "Project skills"], ["default", "Default skills (package)"]];
  $("#skills-list").innerHTML = groups.map(([source, title]) => {
    const items = state.skills.filter((s) => s.source === source);
    if (!items.length) return "";
    return `<div class="mt-3 mb-1 text-xs font-semibold tracking-wide text-slate-400 uppercase">${title}</div>` + items.map((s) => `
      <button data-skill="${esc(s.name)}" class="block w-full rounded-lg px-3 py-2 text-left hover:bg-slate-50 ${state.skill === s.name ? "bg-indigo-50" : ""}">
        <div class="flex items-center justify-between gap-2"><span class="text-sm font-medium">${esc(s.name)}</span><span class="text-xs text-slate-400">order ${s.order}</span></div>
        <div class="mt-1 flex flex-wrap gap-1">${s.roles.map((r) => badge(r, "role")).join("")}
          ${s.overrides_default ? badge("overrides default", "project") : ""}${s.read_only && s.source === "project" ? badge("generated", "generated") : ""}</div>
      </button>`).join("");
  }).join("");
  if (!$("#prompt-role").options.length) {
    $("#prompt-role").innerHTML = state.roles.map((r) => `<option value="${esc(r)}">${esc(r)}</option>`).join("");
    $("#prompt-role").value = "analyst";
  }
  await loadPrompt();
}

async function openSkill(name) {
  const meta = state.skills.find((s) => s.name === name);
  const res = await api(`/api/skills/${encodeURIComponent(name)}`);
  state.skill = name;
  $("#skill-title").innerHTML = `${esc(name)} ${badge(meta.source, meta.source === "project" ? "project" : "role")}`;
  $("#skill-text").value = res.text;
  $("#skill-text").readOnly = meta.read_only;
  $("#skill-save").classList.toggle("hidden", meta.read_only);
  $("#skill-override").classList.toggle("hidden", meta.source !== "default");
  $("#skill-delete").classList.toggle("hidden", meta.source !== "project" || (meta.read_only && meta.source === "project"));
  $$("#skills-list [data-skill]").forEach((b) => b.classList.toggle("bg-indigo-50", b.dataset.skill === name));
}

function newSkill() {
  const name = (window.prompt("New skill name (lowercase letters, digits, dashes):") || "").trim();
  if (!name) return;
  state.skill = name;
  $("#skill-title").innerHTML = `${esc(name)} ${badge("new", "project")}`;
  $("#skill-text").readOnly = false;
  $("#skill-text").value = `---\nname: ${name}\ndescription: What this skill tells the model\nroles: [analyst]\norder: 70\n---\nInstructions for the model.\n`;
  $("#skill-save").classList.remove("hidden");
  $("#skill-override").classList.add("hidden");
  $("#skill-delete").classList.add("hidden");
}

async function saveSkill() {
  await api(`/api/skills/${encodeURIComponent(state.skill)}`, { method: "PUT", body: JSON.stringify({ text: $("#skill-text").value }) });
  toast(`Saved ${state.skill} into the project skills`);
  await loadSkills();
  await openSkill(state.skill);
}

async function deleteSkill() {
  if (!window.confirm(`Delete the project copy of ${state.skill}?`)) return;
  await api(`/api/skills/${encodeURIComponent(state.skill)}`, { method: "DELETE" });
  toast(`Deleted ${state.skill}`);
  const name = state.skill;
  await loadSkills();
  if (state.skills.some((s) => s.name === name)) await openSkill(name);
}

async function loadPrompt() {
  const res = await api(`/api/prompt?role=${encodeURIComponent($("#prompt-role").value)}`);
  $("#prompt-meta").textContent = `${res.skills.join(" → ")} · ${res.chars.toLocaleString()} characters`;
  $("#prompt-text").textContent = res.prompt;
}

// ---------------------------------------------------------------- jobs

async function refreshJobs() {
  state.jobs = (await api("/api/jobs")).jobs;
  $("#jobs-list").innerHTML = jobsTable(state.jobs);
}

async function loadJobs(id) {
  stopPolling();
  await refreshJobs();
  if (id) await openJob(id);
  state.timers.jobs = setInterval(() => {
    if (state.jobs.some((j) => ["queued", "running"].includes(j.status))) guard(refreshJobs);
  }, 5000);
}

async function runJob(kind, params, button) {
  button.disabled = true;
  try {
    const job = await api("/api/jobs", { method: "POST", body: JSON.stringify({ kind, params }) });
    toast(`Started ${kind}: ${job.id}`);
    location.hash = `jobs/${job.id}`;
  } finally {
    button.disabled = false;
  }
}

async function openJob(id) {
  clearInterval(state.timers.job);
  const job = (state.job = await api(`/api/jobs/${encodeURIComponent(id)}`));
  renderJob(job);
  if (["queued", "running"].includes(job.status)) {
    state.timers.job = setInterval(() => guard(async () => {
      const fresh = await api(`/api/jobs/${encodeURIComponent(id)}`);
      if (fresh.status !== state.job.status) { clearInterval(state.timers.job); await refreshJobs(); return openJob(id); }
      state.job = fresh;
      $("#job-cost").textContent = `$${Number(fresh.cost_usd).toFixed(4)}`;
      updateProgress(fresh);
      const log = $("#job-log");
      log.textContent = fresh.log.join("\n");
      log.scrollTop = log.scrollHeight;
    }), 2000);
  }
}

function fmtEta(s) {
  if (s == null) return "estimating the time left…";
  if (s < 60) return `about ${Math.max(5, Math.round(s / 5) * 5)} s left`;
  const m = Math.round(s / 60);
  return m < 90 ? `about ${m} min left` : `about ${(s / 3600).toFixed(1)} h left`;
}

// A bar and an estimate of the time left. The estimate scales the time so far by what is left, so it is rough.
function progressHtml(job) {
  if (!["queued", "running"].includes(job.status)) return "";
  return `<div id="job-progress" class="flex items-center gap-3 text-xs text-slate-600">
      <progress id="job-bar" class="w-64 max-w-full" max="100" value="${Math.round((job.progress?.fraction || 0) * 100)}"></progress>
      <span id="job-eta" class="tabular-nums"></span></div>`;
}

function updateProgress(job) {
  const bar = $("#job-bar"), eta = $("#job-eta");
  if (!bar || !eta) return;
  const p = job.progress || {};
  bar.value = Math.round((p.fraction || 0) * 100);
  const running = p.elapsed_seconds != null ? ` · running ${Math.max(1, Math.round(p.elapsed_seconds / 60))} min` : "";
  eta.textContent = job.status === "queued" ? "waiting for a free worker" : `${bar.value}% · ${fmtEta(p.eta_seconds)}${running}`;
}

function renderJob(job) {
  const running = ["queued", "running"].includes(job.status);
  const time = (t) => (t ? esc(t.replace("T", " ")) : "–");
  const summary = Object.entries(job.summary || {}).filter(([, v]) => v !== null && !(Array.isArray(v) && !v.length));
  $("#job-detail").innerHTML = `
    <div class="card space-y-3">
      <div class="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div class="text-xs tracking-wide text-slate-500 uppercase">${esc(job.kind)}</div>
          <h2 class="text-base font-semibold break-all">${esc(job.id)}</h2>
          <div class="mt-1 text-xs text-slate-500">created ${time(job.created)} · started ${time(job.started)} · finished ${time(job.finished)}</div>
        </div>
        <div class="flex flex-wrap items-center gap-2">
          ${badge(job.status)}<span id="job-cost" class="text-sm tabular-nums">$${Number(job.cost_usd).toFixed(4)}</span>
          ${running ? `<button id="job-cancel" class="btn-danger" ${job.cancel_requested ? "disabled" : ""}>Cancel</button>`
                    : `<button id="job-trace" class="btn-ghost">Trace digest</button>${job.kind !== "learn" ? `<button id="job-learn" class="btn-ghost">Learn from this run</button>` : ""}`}
        </div>
      </div>
      ${job.error ? `<div class="rounded-lg bg-rose-50 p-3 text-sm text-rose-800">${esc(job.error)}</div>` : ""}
      ${summary.length ? `<dl class="grid gap-3 text-sm md:grid-cols-2">${summary.map(([k, v]) => `<div><dt class="text-xs text-slate-500">${esc(k)}</dt>
        <dd class="mt-1 flex flex-wrap gap-1 break-words">${summaryValue(v)}</dd></div>`).join("")}</dl>` : ""}
      ${progressHtml(job)}
      <details ${running ? "open" : ""}><summary class="cursor-pointer text-sm font-medium">Log</summary>
        <pre id="job-log" class="mt-2 max-h-72 overflow-auto rounded-lg bg-slate-900 p-3 text-xs whitespace-pre-wrap text-slate-100">${esc(job.log.join("\n"))}</pre></details>
    </div>
    <div id="job-trace-box"></div>
    <div id="job-files"></div>
    <div id="job-results"></div>`;
  const log = $("#job-log");
  log.scrollTop = log.scrollHeight;
  $$("#jobs-list [data-job]").forEach((tr) => tr.classList.toggle("bg-indigo-50", tr.dataset.job === job.id));
  updateProgress(job);
  if (!running) { guard(() => loadFiles(job)); guard(() => loadResults(job)); }
}

function summaryValue(v) {
  if (Array.isArray(v)) return v.map((x) => `<div class="w-full text-xs">${esc(x)}</div>`).join("");
  if (v && typeof v === "object") return Object.entries(v).map(([k, n]) => badge(`${k} ${n}`, k)).join(" ");
  return esc(v);
}

async function loadFiles(job) {
  const { files } = await api(`/api/jobs/${encodeURIComponent(job.id)}/files`);
  const shown = files.filter((f) => !f.path.startsWith("papers/") || files.length < 60);
  $("#job-files").innerHTML = `<div class="card"><h3 class="card-title">Files</h3>${shown.length ? `<ul class="grid gap-1 text-sm md:grid-cols-2">${shown.map((f) => `
      <li class="flex items-center justify-between gap-2 rounded px-2 py-1 hover:bg-slate-50">
        <a class="truncate text-indigo-700 hover:underline" target="_blank" href="${fileUrl(job.id, f.path)}">${esc(f.path)}</a>
        <span class="flex items-center gap-2 text-xs text-slate-400">${(f.bytes / 1024).toFixed(1)} KB
          <a class="text-slate-600 hover:underline" href="${fileUrl(job.id, f.path, true)}">download</a></span></li>`).join("")}</ul>` : `<p class="text-sm text-slate-500">No files.</p>`}</div>`;
}

async function loadResults(job) {
  const id = encodeURIComponent(job.id);
  if (job.kind === "recheck") return renderRows((await api(`/api/jobs/${id}/findings`)).rows, "findings");
  if (job.kind === "extract") return renderRows((await api(`/api/jobs/${id}/review`)).rows, "review");
  if (job.kind === "analyze") return renderAnalysis(job, await api(`/api/jobs/${id}/analysis`));
  if (job.kind === "learn") {
    const skill = await api("/api/skills/learned-rules").catch(() => null);
    $("#job-results").innerHTML = skill ? `<div class="card"><h3 class="card-title">learned-rules (now part of the next runs' prompts)</h3>
      <pre class="max-h-[32rem] overflow-auto rounded-lg bg-slate-900 p-4 text-xs whitespace-pre-wrap text-slate-100">${esc(skill.text)}</pre></div>` : "";
  }
}

// one table component for recheck findings and extract review rows
const sample = (device) => `<span title="${esc(device)}">${esc(String(device ?? "").replace(/^.*_(Sample\s*\d+)$/i, "$1"))}</span>`;
const where = (r) => `${r.page ? `p${esc(r.page)} ` : ""}${r.source ? `[${esc(r.source)}] ` : ""}${esc(r.evidence)}`;
// The SI link repeats on every row of a paper, so it is shown once: one entry per distinct link, beside the table,
// with the rows it could settle. Clicking an entry shows only those rows.
// a file picker that sends the chosen PDF to /api/si for this paper
function siUploadControl(key, label) {
  return `<label class="btn-ghost cursor-pointer" style="display:inline-block;padding:.15rem .5rem;font-size:.75rem">${esc(label)}
    <input type="file" accept="application/pdf,.pdf" data-siupload="${esc(key)}" style="display:none"></label>`;
}

async function uploadSi(key, files) {
  if (!files.length) return;
  const form = new FormData();
  form.append("key", key);
  [...files].forEach((f) => form.append("files", f));
  const res = await api("/api/si", { method: "POST", body: form });
  const onExtract = (location.hash.slice(1) || "").startsWith("extract");
  toast(`Saved ${res.files.map((f) => `${f.name} (${f.pages} pages)`).join(", ")}` + (onExtract ? ": it is read with the paper when you extract."
    : `${res.paired ? "" : " - no paper with this DOI key was found"}. Run again to use it.`), res.paired || onExtract);
  state.pairs = null;
  if (onExtract) {
    loadAgentServer();
    await loadSiPlan();
  }
  if ((location.hash.slice(1) || "overview").startsWith("recheck")) await loadRecheck();
}

function siGroups(rows) {
  const groups = new Map();
  for (const r of rows) {
    if (!r.si_link) continue;
    const g = groups.get(r.si_link) || { link: r.si_link, saveAs: r.si_save_as, rows: 0, by: {} };
    g.rows += 1;
    const k = r.status || r.decision || "row";
    g.by[k] = (g.by[k] || 0) + 1;
    groups.set(r.si_link, g);
  }
  return [...groups.values()].sort((x, y) => y.rows - x.rows);
}

function siPanel(rows) {
  const groups = siGroups(rows);
  if (!groups.length) return "";
  return `<aside style="flex:0 0 17rem" class="space-y-2 rounded-lg bg-sky-50 p-3 text-xs text-sky-900">
    <div class="font-semibold">Supporting information to download</div>
    <p>It was not loaded, so these rows cannot be settled. Download each file and save it under the name shown in
      <code>original paper/SI/</code>, then run again.</p>
    ${groups.map((g) => `<div class="space-y-1 rounded-md bg-white p-2">
      <a style="display:inline-block;padding:.25rem .6rem;border-radius:.375rem;background:#0369a1;color:#fff;font-weight:600;text-decoration:none"
         href="${esc(g.link)}" target="_blank" rel="noopener noreferrer">Open the SI page ↗</a>
      <div class="break-all"><a style="color:#0369a1;text-decoration:underline" href="${esc(g.link)}" target="_blank" rel="noopener noreferrer">${esc(g.link)}</a></div>
      <div>Save as: <code>${esc(g.saveAs)}</code></div>
      <div>${siUploadControl(g.saveAs.replace(/_SI\d*\.pdf$/i, ""), "Upload the file here")}</div>
      <div class="text-slate-600">${g.rows} rows: ${Object.entries(g.by).map(([k, n]) => `${esc(k)} ${n}`).join(", ")}</div>
      <button class="btn-ghost" data-sifilter="${esc(g.link)}">${state.siFilter === g.link ? "show all rows" : "show these rows"}</button></div>`).join("")}</aside>`;
}

const ROW_VIEWS = {
  findings: { key: "status", order: FINDING_ORDER, title: "Findings (one row per checked cell)",
    head: ["Status", "Cell", "Field", "Recorded → paper", "Detail · evidence"],
    cells: (r) => [badge(r.status), `${sample(r.device)}<div class="text-slate-400">${esc(r.sheet)} · row ${esc(r.excel_row)}</div>`,
      `${esc(r.column)}<div class="text-slate-400">${esc(r.keys)}</div>`,
      `<span class="font-medium">${esc(r.recorded)} ${esc(r.recorded_unit)}</span> → <span class="font-medium">${esc(r.paper_value)} ${esc(r.paper_unit)}</span>`,
      `${esc(r.detail)}<div class="text-slate-400">${where(r)}</div>`] },
  review: { key: "decision", order: ["revise", "verify_more", "accept", ""], title: "Extracted rows (review before moving into Raw Data)",
    head: ["Decision", "Row", "Values", "Evidence", "Reason"],
    cells: (r) => [`${badge(r.decision || "none")}<div class="text-slate-400">${r.p ? `p=${esc(Number(r.p).toFixed(2))}` : ""}</div>`,
      `${esc(r.device)}<div class="text-slate-400">${esc(r.sheet)}</div>`, esc(r.values),
      `${where(r)}<div class="text-slate-400">quote on page: ${esc(r.quote_ok || "not checked")}</div>`, esc(r.reason)] },
};

function renderRows(rows, view) {
  const v = ROW_VIEWS[view];
  state.rows = rows;
  state.view = view;
  state.filter = "";
  state.siFilter = "";
  const counts = rows.reduce((c, r) => ((c[r[v.key]] = (c[r[v.key]] || 0) + 1), c), {});
  $("#job-results").innerHTML = `<div class="card space-y-3">
    <div class="flex flex-wrap items-center gap-2"><h3 class="card-title mr-2 mb-0">${esc(v.title)}</h3>
      ${v.order.filter((s) => counts[s]).map((s) => `<button data-filter="${esc(s)}">${badge(`${s || "none"} ${counts[s]}`, s)}</button>`).join("")}
      <button data-filter="" class="btn-ghost">all ${rows.length}</button>
      <input id="rows-search" class="input ml-auto w-56" placeholder="Search rows"></div>

    <div style="display:flex;gap:1.5rem;flex-wrap:wrap;align-items:flex-start">
      <div id="rows-table" style="flex:1 1 36rem;min-width:0" class="max-h-[36rem] overflow-auto rounded-lg border border-slate-200"></div>
      <div id="rows-si" style="flex:0 0 17rem"></div></div></div>`;
  drawRows();
}

function drawRows() {
  const v = ROW_VIEWS[state.view];
  const q = ($("#rows-search")?.value || "").toLowerCase();
  const rows = state.rows.filter((r) => (!state.filter || r[v.key] === state.filter) && (!state.siFilter || r.si_link === state.siFilter)
    && (!q || JSON.stringify(r).toLowerCase().includes(q)));
  $("#rows-si").innerHTML = siPanel(state.rows);
  $("#rows-table").innerHTML = `<table class="w-full"><thead><tr>${v.head.map((h) => `<th class="th">${h}</th>`).join("")}</tr></thead><tbody>
    ${rows.slice(0, 500).map((r) => `<tr>${v.cells(r).map((c) => `<td class="td text-xs">${c}</td>`).join("")}</tr>`).join("")}</tbody></table>
    ${rows.length > 500 ? `<p class="p-3 text-xs text-slate-500">Showing 500 of ${rows.length}. Download the CSV for all of them.</p>` : ""}
    ${!rows.length ? `<p class="p-4 text-sm text-slate-500">No rows.</p>` : ""}`;
}

function renderAnalysis(job, data) {
  const r = data.result;
  if (!r) { $("#job-results").innerHTML = `<div class="card text-sm text-slate-500">No result was written.</div>`; return; }
  $("#job-results").innerHTML = `
    <div class="card space-y-4"><h3 class="card-title">Acceptance criteria (PLAN.md)</h3>
      ${r.acs.map((ac) => `<div class="rounded-lg border border-slate-200 p-4">
        <div class="flex flex-wrap items-center justify-between gap-2"><div class="font-medium">${esc(ac.id)} · ${esc(ac.question)}</div>
          <div class="flex items-center gap-2">${badge(ac.verdict.choice)}<span class="text-xs text-slate-500">p=${Number(ac.verdict.p).toFixed(2)} · ${esc(ac.verdict.by)} · ${esc(ac.rounds)} round(s)</span></div></div>
        <div class="mt-1 text-xs text-slate-500">Criterion: ${esc(ac.criterion)}</div>
        ${ac.claim ? `<p class="mt-2 text-sm">${esc(ac.claim.claim)}</p>
          ${Object.keys(ac.claim.numbers || {}).length ? `<div class="mt-2 flex flex-wrap gap-1">${Object.entries(ac.claim.numbers).map(([k, v]) => badge(`${k}: ${typeof v === "object" ? JSON.stringify(v) : v}`, "role")).join("")}</div>` : ""}
          ${(ac.claim.caveats || []).length ? `<ul class="mt-2 list-disc pl-5 text-xs text-slate-600">${ac.claim.caveats.map((c) => `<li>${esc(c)}</li>`).join("")}</ul>` : ""}` : ""}
        <p class="mt-2 text-xs text-slate-600"><b>Decision:</b> ${esc(ac.verdict.reason)}</p>
        <a class="mt-2 inline-block text-xs text-indigo-700 hover:underline" target="_blank" href="${fileUrl(job.id, ac.evidence_file)}">Evidence: ${esc(ac.evidence_file)}</a>
      </div>`).join("") || `<p class="text-sm text-slate-500">No acceptance criteria.</p>`}
      ${(r.errors || []).length ? `<div class="rounded-lg bg-rose-50 p-3 text-xs text-rose-800">${r.errors.map(esc).join("<br>")}</div>` : ""}
    </div>
    <div class="card"><h3 class="card-title">Key insights (INSIGHTS.md)</h3><div class="md">${md(data.insights || "")}</div></div>`;
}

async function showTrace() {
  const res = await api(`/api/jobs/${encodeURIComponent(state.job.id)}/trace`);
  $("#job-trace-box").innerHTML = `<div class="card"><h3 class="card-title">Trace digest</h3><div class="md">${md(res.markdown)}</div></div>`;
}

// ---------------------------------------------------------------- wiring

const LOADERS = {
  overview: () => guard(loadOverview), dashboard: () => guard(loadDashboard), recheck: () => guard(loadRecheck), extract: () => guard(loadExtract),
  analyze: () => guard(loadAnalyze), columns: () => guard(loadColumns), skills: () => guard(loadSkills), jobs: (id) => guard(() => loadJobs(id)),
};

function wire() {
  window.addEventListener("hashchange", route);
  document.addEventListener("click", (e) => {
    const rel = e.target.closest("[data-colrel]");
    if (rel && state.columns) { state.colFilter.relevance = rel.dataset.colrel; renderColumns(); }
  });
  document.addEventListener("change", (e) => {
    const up = e.target.closest?.("[data-siupload]");
    if (up) guard(async () => { await uploadSi(up.dataset.siupload, up.files); up.value = ""; });
  });
  $("#columns-table").addEventListener("change", (e) => { state.colFilter.table = e.target.value; renderColumns(); });
  $("#columns-search").addEventListener("input", (e) => { state.colFilter.q = e.target.value; renderColumns(); });
  document.addEventListener("click", (e) => {
    const job = e.target.closest("[data-job]");
    if (job) { location.hash = `jobs/${job.dataset.job}`; return; }
    const skill = e.target.closest("[data-skill]");
    if (skill) { guard(() => openSkill(skill.dataset.skill)); return; }
    const filter = e.target.closest("[data-filter]");
    if (filter) { state.filter = filter.dataset.filter; drawRows(); return; }
    const dex = e.target.closest("[data-sidel-ex]");
    if (dex) {
      guard(async () => {
        await api(`/api/si/${encodeURIComponent(dex.dataset.sidelEx)}`, { method: "DELETE" });
        toast(`Removed ${dex.dataset.sidelEx}`);
        await loadSiPlan();
      });
      return;
    }
    const dp = e.target.closest("[data-dashpaper]");
    if (dp) { $("#dash-cpaper").value = dp.dataset.dashpaper; guard(drawDashCells); $("#dash-cells").scrollIntoView({ behavior: "smooth", block: "nearest" }); return; }
    const del = e.target.closest("[data-sidelete]");
    if (del) {
      guard(async () => {
        await api(`/api/si/${encodeURIComponent(del.dataset.sidelete)}`, { method: "DELETE" });
        toast(`Removed ${del.dataset.sidelete}`);
        state.pairs = null;
        await loadRecheck();
      });
      return;
    }
    const si = e.target.closest("[data-sifilter]");
    if (si) { state.siFilter = state.siFilter === si.dataset.sifilter ? "" : si.dataset.sifilter; drawRows(); return; }
    const id = e.target.closest("button")?.id;
    const actions = {
      "btn-map": async () => { const r = await api("/api/data/map", { method: "POST" }); toast(`Data map rebuilt (${r.chars.toLocaleString()} characters)`); },
      "btn-show-map": async () => { const r = await api("/api/data/map", { method: "POST" }); $("#map-text").textContent = r.text; $("#map-text").classList.remove("hidden"); },
      "dash-refresh": loadDashboard,
      "dash-all": () => { dashPaperRows().forEach((p) => state.dashSel.add(p.key)); renderDashPapers(); },
      "dash-none": () => { state.dashSel.clear(); renderDashPapers(); },
      "dash-run": () => runJob("evaluate", { dois: [...state.dashSel], model: val("#dash-model"), mapper_model: val("#dash-mapper"),
        workers: num("#dash-workers"), budget: num("#dash-budget"), audit: $("#dash-audit").checked }, $("#dash-run")),
      "recheck-all": () => { recheckRows().forEach((p) => state.selected.add(p.key)); renderRecheck(); },
      "recheck-none": () => { state.selected.clear(); renderRecheck(); },
      "recheck-run": () => runJob("recheck", { dois: [...state.selected], model: val("#recheck-model"), judge_model: val("#recheck-judge"),
        workers: num("#recheck-workers"), budget: num("#recheck-budget") }, $("#recheck-run")),
      "extract-upload-btn": uploadPdfs,
      "si-agent-run": findSiLink,
      "si-chrome-run": downloadWithChrome,
      "extract-run": () => runJob("extract", { pdf: state.pdfChoice, model: val("#extract-model"), mapper_model: val("#extract-mapper"),
        decision_model: val("#extract-decision"), budget: num("#extract-budget") }, $("#extract-run")),
      "analyze-run": () => runJob("analyze", { goal: val("#analyze-goal"), questions: num("#analyze-questions"), rounds: num("#analyze-rounds"),
        model: val("#analyze-model"), analyst_model: val("#analyze-analyst"), decision_model: val("#analyze-decision"),
        budget: num("#analyze-budget") }, $("#analyze-run")),
      "skill-new": newSkill, "skill-save": saveSkill, "skill-override": saveSkill, "skill-delete": deleteSkill,
      "job-cancel": async () => { await api(`/api/jobs/${encodeURIComponent(state.job.id)}/cancel`, { method: "POST" }); toast("Cancel requested: the job stops before its next model call"); $("#job-cancel").disabled = true; },
      "job-trace": showTrace,
      "job-learn": () => runJob("learn", { jobs: [state.job.id] }, $("#job-learn")),
    };
    if (actions[id]) guard(actions[id]);
  });
  document.addEventListener("change", (e) => {
    const id = e.target.id || "";
    if (e.target.dataset.dashdoi) {
      e.target.checked ? state.dashSel.add(e.target.dataset.dashdoi) : state.dashSel.delete(e.target.dataset.dashdoi);
      updateDashButton();
      return;
    }
    if (id === "dash-a" || id === "dash-b") { guard(drawDashboard); return; }
    if (["dash-cstatus", "dash-csheet", "dash-cpaper", "dash-ccore"].includes(id)) { guard(drawDashCells); return; }
    if (e.target.dataset.doi) {
      e.target.checked ? state.selected.add(e.target.dataset.doi) : state.selected.delete(e.target.dataset.doi);
      updateRecheckButton();
    } else if (e.target.name === "pdf") {
      guard(() => selectPdf(e.target.value));
    } else if (e.target.id === "extract-only-new") renderExtract();
    else if (e.target.id === "prompt-role") guard(loadPrompt);
  });
  document.addEventListener("input", (e) => {
    if (e.target.id === "recheck-search") renderRecheck();
    else if (e.target.id === "rows-search") drawRows();
    else if (e.target.id === "dash-search") renderDashPapers();
  });
}

wire();
guard(async () => { await loadStatus(); route(); });
