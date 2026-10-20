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
  missing_in_record: "bg-violet-100 text-violet-800", not_found: "bg-slate-100 text-slate-700", review: "bg-amber-100 text-amber-800",
  not_checked: "bg-slate-200 text-slate-700", project: "bg-indigo-100 text-indigo-800", generated: "bg-teal-100 text-teal-800",
  role: "bg-slate-100 text-slate-600", other: "bg-slate-100 text-slate-700",
};
const badge = (text, kind = text) => `<span class="badge ${BADGE[kind] || BADGE.other}">${esc(text)}</span>`;
const FINDING_ORDER = ["mismatch", "unit_mismatch", "missing_in_record", "not_found", "review", "not_checked", "match"];
const TABS = ["overview", "recheck", "extract", "analyze", "skills", "jobs"];

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
  $("#recheck-papers").innerHTML = `<table class="w-full"><thead><tr><th class="th w-8"></th><th class="th">DOI key</th><th class="th">PDF</th><th class="th">Devices</th><th class="th">Flags</th></tr></thead><tbody>
    ${rows.map((p) => `<tr class="hover:bg-slate-50">
      <td class="td"><input type="checkbox" data-doi="${esc(p.key)}" ${state.selected.has(p.key) ? "checked" : ""}></td>
      <td class="td font-mono text-xs">${esc(p.key)}</td><td class="td text-xs">${esc(p.pdf)}</td>
      <td class="td tabular-nums">${p.workbooks}</td>
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
}

function renderExtract() {
  const onlyNew = $("#extract-only-new").checked;
  const rows = state.pdfs.filter((p) => !onlyNew || !p.has_workbooks);
  $("#extract-pdfs").innerHTML = rows.length ? `<table class="w-full"><tbody>${rows.map((p) => `<tr class="hover:bg-slate-50">
      <td class="td w-8"><input type="radio" name="pdf" value="${esc(p.id)}" ${state.pdfChoice === p.id ? "checked" : ""}></td>
      <td class="td text-sm">${esc(p.name)}</td>
      <td class="td text-right">${p.uploaded ? badge("uploaded", "project") : ""} ${p.has_workbooks ? badge("has workbooks", "done") : ""}</td>
    </tr>`).join("")}</tbody></table>` : `<p class="p-4 text-sm text-slate-500">No PDFs here.</p>`;
  $("#extract-run").disabled = !state.pdfChoice;
}

async function uploadPdfs() {
  const files = $("#extract-upload").files;
  if (!files.length) return toast("Choose a PDF first", false);
  const form = new FormData();
  [...files].forEach((f) => form.append("files", f));
  const res = await api("/api/uploads", { method: "POST", body: form });
  toast(`Uploaded ${res.files.length} file(s)`);
  state.pdfChoice = res.files[0].id;
  $("#extract-only-new").checked = true;
  await loadExtract();
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
      const log = $("#job-log");
      log.textContent = fresh.log.join("\n");
      log.scrollTop = log.scrollHeight;
    }), 2000);
  }
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
      <details ${running ? "open" : ""}><summary class="cursor-pointer text-sm font-medium">Log</summary>
        <pre id="job-log" class="mt-2 max-h-72 overflow-auto rounded-lg bg-slate-900 p-3 text-xs whitespace-pre-wrap text-slate-100">${esc(job.log.join("\n"))}</pre></details>
    </div>
    <div id="job-trace-box"></div>
    <div id="job-files"></div>
    <div id="job-results"></div>`;
  const log = $("#job-log");
  log.scrollTop = log.scrollHeight;
  $$("#jobs-list [data-job]").forEach((tr) => tr.classList.toggle("bg-indigo-50", tr.dataset.job === job.id));
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
  const counts = rows.reduce((c, r) => ((c[r[v.key]] = (c[r[v.key]] || 0) + 1), c), {});
  $("#job-results").innerHTML = `<div class="card space-y-3">
    <div class="flex flex-wrap items-center gap-2"><h3 class="card-title mr-2 mb-0">${esc(v.title)}</h3>
      ${v.order.filter((s) => counts[s]).map((s) => `<button data-filter="${esc(s)}">${badge(`${s || "none"} ${counts[s]}`, s)}</button>`).join("")}
      <button data-filter="" class="btn-ghost">all ${rows.length}</button>
      <input id="rows-search" class="input ml-auto w-56" placeholder="Search rows"></div>
    <div id="rows-table" class="max-h-[36rem] overflow-auto rounded-lg border border-slate-200"></div></div>`;
  drawRows();
}

function drawRows() {
  const v = ROW_VIEWS[state.view];
  const q = ($("#rows-search")?.value || "").toLowerCase();
  const rows = state.rows.filter((r) => (!state.filter || r[v.key] === state.filter) && (!q || JSON.stringify(r).toLowerCase().includes(q)));
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
  overview: () => guard(loadOverview), recheck: () => guard(loadRecheck), extract: () => guard(loadExtract),
  analyze: () => guard(loadAnalyze), skills: () => guard(loadSkills), jobs: (id) => guard(() => loadJobs(id)),
};

function wire() {
  window.addEventListener("hashchange", route);
  document.addEventListener("click", (e) => {
    const job = e.target.closest("[data-job]");
    if (job) { location.hash = `jobs/${job.dataset.job}`; return; }
    const skill = e.target.closest("[data-skill]");
    if (skill) { guard(() => openSkill(skill.dataset.skill)); return; }
    const filter = e.target.closest("[data-filter]");
    if (filter) { state.filter = filter.dataset.filter; drawRows(); return; }
    const id = e.target.closest("button")?.id;
    const actions = {
      "btn-map": async () => { const r = await api("/api/data/map", { method: "POST" }); toast(`Data map rebuilt (${r.chars.toLocaleString()} characters)`); },
      "btn-show-map": async () => { const r = await api("/api/data/map", { method: "POST" }); $("#map-text").textContent = r.text; $("#map-text").classList.remove("hidden"); },
      "recheck-all": () => { recheckRows().forEach((p) => state.selected.add(p.key)); renderRecheck(); },
      "recheck-none": () => { state.selected.clear(); renderRecheck(); },
      "recheck-run": () => runJob("recheck", { dois: [...state.selected], model: val("#recheck-model"), judge_model: val("#recheck-judge"),
        workers: num("#recheck-workers"), budget: num("#recheck-budget") }, $("#recheck-run")),
      "extract-upload-btn": uploadPdfs,
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
    if (e.target.dataset.doi) {
      e.target.checked ? state.selected.add(e.target.dataset.doi) : state.selected.delete(e.target.dataset.doi);
      updateRecheckButton();
    } else if (e.target.name === "pdf") {
      state.pdfChoice = e.target.value;
      $("#extract-run").disabled = false;
    } else if (e.target.id === "extract-only-new") renderExtract();
    else if (e.target.id === "prompt-role") guard(loadPrompt);
  });
  document.addEventListener("input", (e) => {
    if (e.target.id === "recheck-search") renderRecheck();
    else if (e.target.id === "rows-search") drawRows();
  });
}

wire();
guard(async () => { await loadStatus(); route(); });
