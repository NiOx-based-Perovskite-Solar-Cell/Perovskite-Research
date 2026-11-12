// The review page of one job. Extract job: rows on the left, the paper (or its SI) on the right at the page a row cites.
// Recheck job: the same, and each finding sets what is written in the Excel workbook against what the agent read in the paper.
const JOB = window.REVIEW_JOB;
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const KINDS = {
  extract: { key: "decision", order: ["revise", "verify_more", "human_read", "accept"] },
  recheck: { key: "status", order: ["mismatch", "unit_mismatch", "missing_in_record", "review", "needs_si", "not_found", "not_checked", "paper_more_detail", "match"] },
};
const st = { kind: "extract", rows: [], filter: "", sel: null, view: null, size: null };
const tag = (r) => r[KINDS[st.kind].key];

async function getJson(url) {
  const r = await fetch(url);
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.error || `${r.status}`);
  return d;
}

function visible() {
  const q = $("#q").value.trim().toLowerCase();
  return st.rows.filter((r) => (!st.filter || tag(r) === st.filter) && (!q || JSON.stringify(r).toLowerCase().includes(q)));
}

function drawChips() {
  const order = KINDS[st.kind].order;
  const chip = (d, label, n) => `<button class="chip ${st.filter === d ? "on" : ""}" data-f="${esc(d)}">${esc(label)} ${n}</button> `;
  $("#chips").innerHTML = chip("", "all", st.rows.length)
    + order.map((d) => [d, st.rows.filter((r) => tag(r) === d).length]).filter(([, n]) => n).map(([d, n]) => chip(d, d, n)).join("");
}

const short = (t, n = 70) => (t.length > n ? t.slice(0, n - 1) + "…" : t);

function listItem(r) {
  const page = `<span style="margin-left:auto">${r.page ? "p" + esc(r.page) : "no page"}</span>`;
  if (st.kind === "recheck") {
    const rec = `${r.recorded ?? ""} ${r.recorded_unit ?? ""}`.trim(), pap = `${r.paper_value ?? ""} ${r.paper_unit ?? ""}`.trim();
    return `<div class="top"><span class="badge ${esc(tag(r))}">${esc(tag(r))}</span><span>${esc(r.sheet)} · ${esc(r.column)}</span>${page}</div>
      <div class="vals">${esc(short(r.keys || ""))}</div>
      <div class="vals mute">Person <b>${esc(rec || "–")}</b> · Agent <b>${esc(pap || "–")}</b></div>
      ${r.audit ? `<div class="vals mute">Auditor: ${esc((AUDIT[r.audit] || ["", r.audit])[1])} · ${Math.round(Number(r.audit_p) * 100)}%</div>` : ""}`;
  }
  return `<div class="top"><span class="badge ${esc(tag(r))}">${esc(tag(r) || "none")}</span>${r.p ? `p=${esc(Number(r.p).toFixed(2))}` : ""}
      <span>${esc(r.device)} · ${esc(r.sheet)}</span>${page}</div><div class="vals">${esc(r.values)}</div>`;
}

function drawList() {
  $("#list").innerHTML = visible().map((r) => `<div class="row ${st.sel === r.row_id ? "sel" : ""}" data-id="${esc(r.row_id)}">${listItem(r)}</div>`).join("")
    || `<div id="empty">No rows.</div>`;
  const cur = $(".row.sel");
  if (cur) cur.scrollIntoView({ block: "nearest" });
}

// the row's own facts above the paper: for a finding, Excel against the paper
function facts(r) {
  if (st.kind === "recheck") {
    const cell = (v, u) => `${esc(v ?? "") || "–"} ${esc(u ?? "")}`;
    return `<div id="cmp">
        <div class="side person"><div class="lab">Person entered <span>Excel</span></div><div class="val">${cell(r.recorded, r.recorded_unit)}</div></div>
        <div class="verdict"><span class="badge ${esc(tag(r))}">${esc(tag(r))}</span></div>
        <div class="side agent"><div class="lab">Agent extracted <span>paper</span></div><div class="val">${cell(r.paper_value, r.paper_unit)}</div></div></div>
      <div class="mute" style="margin-top:.3rem">${esc(r.sheet)} · ${esc(r.column)} · ${esc(r.device_ref || r.device)} · Excel row ${esc(r.excel_row)}${r.keys ? ` · ${esc(r.keys)}` : ""}</div>
      ${auditBox(r)}
      ${r.detail ? `<div class="reason"><b>Detail:</b> ${esc(r.detail)}</div>` : ""}`;
  }
  return `<div><span class="badge ${esc(tag(r))}">${esc(tag(r) || "none")}</span>
      <span class="mute">${esc(r.sheet)} · ${esc(r.device)} · source: ${esc(r.source || "–")} · quote on page: ${esc(r.quote_ok || "not checked")}</span></div>
    <div style="margin-top:.3rem;word-break:break-word"><b>Extracted:</b> ${esc(r.values)}</div>`;
}

// the auditor's second reading: who the paper supports, how sure it is, and its own quote
const AUDIT = { human_correct: ["ok", "The person is right"], both_correct: ["ok", "Both are in the paper (the record is right)"],
  agent_correct: ["bad", "The agent is right: likely a slip in the Excel"], neither: ["bad", "Neither: the paper gives another value"], unclear: ["unk", "Unclear from the paper"] };

function auditBox(r) {
  if (!r.audit) return "";
  const [cls, text] = AUDIT[r.audit] || ["unk", r.audit];
  const p = Number(r.audit_p), sure = p >= 0.7 && r.audit_quote_ok !== "False";
  return `<div id="audit" class="${sure ? cls : "unk"}">
      <div><b>Auditor:</b> ${esc(text)} · confidence <b>${isNaN(p) ? "–" : Math.round(p * 100) + "%"}</b>
        ${r.audit_quote_ok === "False" ? ` · <span class="notfound">its quote is not on the page</span>` : ""}${sure ? "" : " · not sure enough: a person decides"}</div>
      ${r.audit_note ? `<div class="reason">${esc(r.audit_note)}</div>` : ""}
      ${r.audit_evidence ? `<div class="reason">“${esc(r.audit_evidence)}” <span class="mute">p${esc(r.audit_page)}</span></div>` : ""}</div>`;
}

const HOW = { quote: ["found", "quote found on this page"], "part of the quote": ["found", "part of the quote found (the rest differs from the PDF text)"],
  value: ["weak", "quote not found: the number is marked instead (it may match elsewhere on the page)"], "": ["notfound", "quote not found on this page or the pages next to it"] };

// draws one page of a file with the boxes to highlight, and scrolls the first box into view
function drawPage(v, rects = [], how = "") {
  st.view = v;
  const img = $("#pageimg"), box = $("#page");
  box.querySelectorAll(".hl").forEach((e) => e.remove());
  $("#pagebox").hidden = false;
  const q = v.paper ? `?paper=${encodeURIComponent(v.paper)}` : "";
  const base = `/api/jobs/${encodeURIComponent(JOB)}/source/${v.source}`;
  $("#where").innerHTML = `<span class="where">${v.is_si ? "Supporting information" : "Paper"} · page ${v.page}</span> <span class="mute">${esc(v.file)}</span>
    · <a href="${base}${q}#page=${v.page}" target="_blank" rel="noopener">open the PDF in a tab ↗</a>`;
  const place = () => {
    const w = st.size.width, h = st.size.height;
    rects.forEach(([x0, y0, x1, y1]) => {
      const d = document.createElement("div");
      d.className = "hl" + (how === "value" ? " value" : "");
      Object.assign(d.style, { left: `${(x0 / w) * 100}%`, top: `${(y0 / h) * 100}%`, width: `${((x1 - x0) / w) * 100}%`, height: `${((y1 - y0) / h) * 100}%` });
      box.appendChild(d);
    });
    const first = box.querySelector(".hl");
    if (first) first.scrollIntoView({ block: "center", behavior: "smooth" });
    else $("#pagebox").scrollTop = 0;
  };
  img.onload = place;
  img.src = `${base}/page/${v.page}.png?dpi=130${v.paper ? `&paper=${encodeURIComponent(v.paper)}` : ""}`;
  if (img.complete && img.naturalWidth) place();
}

async function show(id, which) {
  const r = st.rows.find((x) => x.row_id === id);
  if (!r) return;
  // the auditor's quote first when it confirms the recorded value: that is where the person's value is in the paper
  const hasAudit = !!(r.audit_evidence && r.audit_page);
  which = which || (hasAudit && (["human_correct", "both_correct"].includes(r.audit) || !r.page) ? "audit" : "agent");
  st.sel = id;
  history.replaceState(null, "", `?row=${encodeURIComponent(id)}`);
  drawList();
  const quote = ((which === "audit" ? r.audit_evidence : r.evidence) || "").trim();
  $("#info").innerHTML = `<div id="where"><span class="mute">finding the quote…</span></div>
    <div id="hit" style="margin-top:.25rem;font-size:.8rem"></div>
    <div style="margin-top:.4rem">${facts(r)}</div>
    ${quote ? `<blockquote>${esc(quote)}</blockquote>` : ""}
    ${r.note ? `<div class="reason"><b>Note:</b> ${esc(r.note)}</div>` : ""}
    ${r.reason ? `<div class="reason"><b>Decision:</b> ${esc(r.reason)}</div>` : ""}
    <div id="pager">${hasAudit && r.page ? `<button id="q-agent" type="button" class="${which === "agent" ? "on" : ""}">agent's quote</button><button id="q-audit" type="button" class="${which === "audit" ? "on" : ""}">auditor's quote (the recorded value)</button>` : ""}
      <button id="prev" type="button">‹ page</button><button id="next" type="button">page ›</button>
      <span class="mute">← → turn pages · ↑ ↓ change row</span></div>`;
  $("#prev").onclick = () => turn(-1);
  $("#next").onclick = () => turn(1);
  if ($("#q-agent")) { $("#q-agent").onclick = () => show(id, "agent"); $("#q-audit").onclick = () => show(id, "audit"); }
  let hit;
  try {
    hit = await getJson(`/api/jobs/${encodeURIComponent(JOB)}/locate?row=${encodeURIComponent(id)}${which === "audit" ? "&which=audit" : ""}`);
  } catch (e) {
    $("#where").innerHTML = `<span class="notfound">${esc(e.message)}</span>`;
    return;
  }
  if (st.sel !== id) return;  // another row was picked meanwhile
  if (hit.source == null) {
    $("#where").innerHTML = `<span class="mute">no page cited${st.kind === "recheck" ? " (the agent did not find this value in the paper)" : ""}</span>`;
    $("#pagebox").hidden = true;
    st.view = null;
    return;
  }
  st.size = { width: hit.width, height: hit.height };
  const [cls, text] = HOW[hit.how] || HOW[""];
  const citedPage = which === "audit" ? r.audit_page : r.page;
  const moved = hit.combined_page !== parseInt(citedPage, 10) ? ` (the row cites page ${esc(citedPage)}; found on the page next to it)` : "";
  $("#hit").innerHTML = `<span class="${cls}">${esc(text)}</span>${hit.rects.length ? moved : ""}`;
  drawPage(hit, hit.rects, hit.how);
}

function turn(step) {
  if (!st.view) return;
  const page = st.view.page + step;
  if (page < 1 || page > st.view.pages) return;
  drawPage({ ...st.view, page });
}

async function init() {
  const id = encodeURIComponent(JOB);
  try {
    st.kind = (await getJson(`/api/jobs/${id}`)).kind === "recheck" ? "recheck" : "extract";
    const rows = (await getJson(`/api/jobs/${id}/${st.kind === "recheck" ? "findings" : "review"}`)).rows;
    st.rows = st.kind === "recheck" ? rows.map((r, i) => ({ ...r, row_id: `f${i}` })) : rows;
  } catch (e) {
    $("#list").innerHTML = `<div id="empty">${esc(e.message)}</div>`;
    return;
  }
  document.title = st.kind === "recheck" ? "Check the Excel against the paper" : "Check extracted rows";
  $("#title").textContent = st.kind === "recheck" ? "Check the Excel against the paper" : "Check extracted rows against the paper";
  $("#meta").textContent = `job ${JOB} · ${st.rows.length} ${st.kind === "recheck" ? "findings" : "rows"}`;
  drawChips();
  drawList();
  const want = new URLSearchParams(location.search).get("row");
  if (want) show(want);
  $("#chips").addEventListener("click", (e) => { const b = e.target.closest("[data-f]"); if (b) { st.filter = b.dataset.f; drawChips(); drawList(); } });
  $("#list").addEventListener("click", (e) => { const r = e.target.closest("[data-id]"); if (r) show(r.dataset.id); });
  $("#q").addEventListener("input", drawList);
  document.addEventListener("keydown", (e) => {
    if (e.target.tagName === "INPUT") return;
    if (e.key === "ArrowLeft" || e.key === "ArrowRight") { e.preventDefault(); turn(e.key === "ArrowLeft" ? -1 : 1); return; }
    if (!["ArrowDown", "ArrowUp"].includes(e.key)) return;
    const rows = visible(), i = rows.findIndex((r) => r.row_id === st.sel);
    const next = rows[Math.max(0, Math.min(rows.length - 1, i + (e.key === "ArrowDown" ? 1 : -1)))];
    if (next) { e.preventDefault(); show(next.row_id); }
  });
}
init();
