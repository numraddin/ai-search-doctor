"use strict";
const $ = (s) => document.querySelector(s);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const money = (n) => (n < 0.01 ? "$" + n.toFixed(4) : "$" + n.toFixed(2));
let MAX_LINKS = 3;
// Agent activity and cost tabs are for the report author: open the site with ?details=1 to see them.
const DETAILS = new URLSearchParams(location.search).get("details") === "1";
if (DETAILS) document.body.classList.add("details");
let current = null, pollTimer = null, retestOf = null, history = [], running = false;

async function api(path, opts) {
  const r = await fetch(path, opts);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || "Request failed");
  return data;
}

/* ---------- link box ---------- */
function rows() { return [...document.querySelectorAll(".linkrow")]; }
function addRow(value = "") {
  if (rows().length >= MAX_LINKS) return;
  const first = rows().length === 0;
  const div = document.createElement("div");
  div.className = "linkrow";
  div.innerHTML = `<input type="text" id="${first ? "firstlink" : ""}" placeholder="${first ? "https://yourwebsite.com" : "https://yourwebsite.com/another-page"}" value="${esc(value)}" aria-label="Website link" spellcheck="false">` +
    (first ? "" : `<button type="button" class="rm" aria-label="Remove link" title="Remove">&times;</button>`);
  $("#links").appendChild(div);
  syncLinks();
  if (!value && !first) div.querySelector("input").focus();
}
function syncLinks() {
  const n = rows().length;
  $("#addlink").hidden = n >= MAX_LINKS;
  $("#linkcount").textContent = n >= MAX_LINKS ? `${MAX_LINKS} of ${MAX_LINKS} links added` : `${n} of ${MAX_LINKS} links · add up to ${MAX_LINKS - n} more`;
}
function setLinks(urls) {
  $("#links").innerHTML = "";
  (urls.length ? urls : [""]).slice(0, MAX_LINKS).forEach((u) => addRow(u));
}
// one box may hold several pasted links separated by spaces, commas or new lines
const getLinks = () => [...new Set(rows().flatMap((r) => r.querySelector("input").value.split(/[\s,;]+/)).map((x) => x.trim()).filter(Boolean))];

async function init() {
  const cfg = await api("/api/config");
  MAX_LINKS = cfg.max_pages || MAX_LINKS;
  window._cfg = cfg;
  const st = $("#cfg");
  st.textContent = cfg.ai_enabled ? "AI Live" : "AI key missing: rule-based checks only";
  st.classList.toggle("off", !cfg.ai_enabled);
  $("#addlink").onclick = () => addRow();
  $("#links").onclick = (e) => { const b = e.target.closest(".rm"); if (b) { b.parentElement.remove(); syncLinks(); } };
  setLinks([]);
  $("#firstlink").focus();
  $("#form").onsubmit = (e) => {
    e.preventDefault();
    if (running) return;
    retestOf = null;
    const links = getLinks();
    if (links.length > MAX_LINKS) { $("#formerr").textContent = `You can add up to ${MAX_LINKS} links.`; $("#formerr").hidden = false; return; }
    if (links.length > 1) setLinks(links);  // show the split links as separate rows
    start(links);
  };
  $("#savereport").onclick = () => window.print();
  $("#tabs").onclick = (e) => { const b = e.target.closest("button"); if (b) showTab(b.dataset.tab); };
  refreshHistory();
  loadTestimonials();
  refreshQuota();
  restoreFromHash();
}

// Refreshing the page brings back the report (or the audit still running) named in the address.
async function restoreFromHash() {
  const m = location.hash.match(/^#r=([a-f0-9]+)$/);
  if (!m) return;
  try {
    const job = await api("/api/audit/" + m[1]);
    if (job.status === "running") { busy(true); $("#progress").hidden = false; poll(m[1]); }
    else if (job.status === "done" && job.result) show(job.result);
    else window.history.replaceState(null, "", location.pathname);
  } catch { window.history.replaceState(null, "", location.pathname); }
}

async function loadTestimonials() {
  try {
    const { entries } = await api("/static/testimonials.json");
    if (!entries || !entries.length) return;
    $("#tgrid").innerHTML = entries.slice(0, 3).map((t) => {
      // ratings may be halves (4.5): round to the nearest half star
      const rating = Math.max(0, Math.min(5, Math.round((Number(t.rating) || 0) * 2) / 2));
      const full = Math.floor(rating), half = rating % 1 ? 1 : 0, empty = 5 - full - half;
      const stars = `${"★".repeat(full)}${half ? '<span class="hstar">★</span>' : ""}<span>${"★".repeat(empty)}</span>`;
      const initials = (t.name || "?").split(/\s+/).map((w) => w[0]).slice(0, 2).join("").toUpperCase();
      return `<figure class="tcard${t.placeholder ? " draft" : ""}">
        ${t.placeholder ? '<span class="draftbadge">Draft placeholder</span>' : ""}
        <div class="stars" aria-label="${rating} out of 5 stars">${stars}</div>
        <blockquote>“${esc(t.quote)}”</blockquote>
        <figcaption><span class="avatar">${esc(initials)}</span><span><b>${esc(t.name)}</b><br><span class="muted small">${esc(t.role)}</span></span></figcaption></figure>`;
    }).join("");
    $("#testimonials").hidden = false;
  } catch { /* testimonials are optional */ }
}
async function refreshQuota() {
  try {
    const q = await api("/api/quota");
    $("#quota").innerHTML = q.limited ? ` <b>${q.remaining} of ${q.limit}</b> free analyses left today.` : "";
  } catch { /* optional */ }
}
async function refreshHistory() { try { history = await api("/api/audits"); } catch { history = []; } }

function showTab(t) {
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("on", b.dataset.tab === t));
  document.querySelectorAll(".tab").forEach((d) => (d.hidden = d.id !== "tab-" + t));
}

function busy(on) {
  running = on;
  $("#go").disabled = on;
  $(".spin").hidden = !on;
  $(".ctatxt").textContent = on ? "Analyzing…" : "Analyze Website";
}

async function start(urls) {
  $("#formerr").hidden = true;
  $("#results").hidden = true;
  $("#unreadable").hidden = true;
  $("#printreport").innerHTML = "";
  if (!urls.length) { $("#formerr").textContent = "Please enter a website link."; $("#formerr").hidden = false; return; }
  busy(true);
  try {
    const { id } = await api("/api/audit", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ urls }) });
    $("#results").hidden = true;
    $("#progress").hidden = false;
    $("#progress").scrollIntoView({ behavior: "smooth", block: "start" });
    refreshQuota();
    window.history.replaceState(null, "", "#r=" + id);
    poll(id);
  } catch (e) {
    refreshQuota();
    $("#formerr").textContent = e.message;
    $("#formerr").hidden = false;
    busy(false);
  }
}

function poll(id) {
  clearTimeout(pollTimer);
  api("/api/audit/" + id).then((job) => {
    renderProgress(job);
    if (job.status === "running") { pollTimer = setTimeout(() => poll(id), 700); return; }
    busy(false);
    $("#progress").hidden = true;
    if (job.status === "error") { $("#formerr").textContent = job.error; $("#formerr").hidden = false; window.scrollTo({ top: 0, behavior: "smooth" }); return; }
    show(job.result);
  }).catch((e) => { busy(false); $("#formerr").textContent = e.message; $("#formerr").hidden = false; });
}

function renderProgress(job) {
  const label = { pending: "Waiting", running: "Working", done: "Done", failed: "Could not finish", skipped: "Skipped" };
  const mark = { done: "&#10003;", failed: "!", skipped: "–" };
  $("#agentlist").innerHTML = Object.values(job.agents || {}).map((a, i) => `
    <div class="agent"><span class="node ${esc(a.status)}">${mark[a.status] || i + 1}</span>
    <div style="flex:1"><b>${esc(a.name.replace(/^\d\.\s*/, ""))}</b> <span class="muted small">· ${label[a.status] || ""}${a.seconds != null ? " · " + a.seconds + "s" : ""}</span>
    <div class="sub">${esc(a.summary || a.role)}</div>${a.status === "running" ? '<div class="scan"></div>' : ""}</div></div>`).join("");
}

/* ---------- results ---------- */
const FACTORS = {
  readable: { tab: "Can AI read it?", intro: "Whether crawlers and AI systems can reach and understand each page: access rules, titles, headings and readable text.",
    checks: ["Page loads and is not blocked by robots.txt", "Text is in the page itself, not hidden behind JavaScript", "Clear page title and meta description", "One main heading and sensible heading order", "Sitemap present"] },
  answer_quality: { tab: "Answer quality", intro: "Whether your pages actually answer what customers would ask an AI about your business.",
    checks: ["Likely customer questions are answered with real text", "Six key facts are clearly stated (what you do, who for, offerings, pricing, contact, location)", "No unclear offering or missing pricing", "Enough text on each page"] },
  credibility: { tab: "Credibility", intro: "Signs that a real, trustworthy business is behind the site, and that its facts agree with each other.",
    checks: ["Contact details (email, phone, address)", "About page and privacy policy links", "Links to official profiles and customer reviews", "Named author on articles", "Secure connection (HTTPS, HSTS)", "Domain age and email protection (SPF, DMARC), from public sources", "Facts and prices that do not contradict each other", "Claims backed by evidence", "No text trying to manipulate AI systems"] },
  speed_mobile: { tab: "Speed & mobile", intro: "We load each page's real files and measure their weight, number of requests and outside services. This is a strong proxy for mobile speed, not Google Core Web Vitals or a test on a real phone.",
    checks: ["Total page weight (HTML, scripts, styles, images, fonts)", "JavaScript size and blocking scripts", "Large images and lazy loading", "Number of requests and outside services", "Server response time and compression", "Mobile setup (viewport tag, zoom allowed)"] },
  freshness: { tab: "Freshness", intro: "Whether the pages show they are current. AI systems favor recent information and cannot judge undated facts.",
    checks: ["Visible published or updated date", "How old the newest date is", "Out-of-date copyright year"] },
  structured_data: { tab: "Structured data", intro: "Machine-readable facts (schema.org) that help AI state your details correctly.",
    checks: ["Structured data exists and is valid", "Business or organization type present", "Values match what visitors can see on the page"] },
};
const REJECT_CRED = new Set(["price_contradiction", "inconsistent_facts", "missing_author_or_source", "unsupported_claim"]);

function showUnreadable(r) {
  const refused = r.findings.some((f) => f.issue_type === "access_refused");
  const status = r.pages.find((p) => !p.ok && p.status);
  const title = refused ? "This website refuses AI checking" : "This website can't be reached";
  const why = refused
    ? `Its firewall blocks automated readers${status ? " (HTTP " + esc(status.status) + ")" : ""}, so it can't be analyzed. If it's your site, allow <b>AISearchDoctorBot</b> in your firewall and try again. AI crawlers may be blocked too.`
    : "None of the links opened as a public page. Check that the link works in your browser, then try again.";
  const items = refused ? [] : r.pages.filter((p) => !p.ok).map((p) => `<li>${esc(p.url)}<span class="muted"> · ${esc(p.error || "could not be read")}</span></li>`)
    .concat(r.blocked.map((b) => `<li>${esc(b.url)}<span class="muted"> · blocked by the site's robots.txt</span></li>`));
  $("#printreport").innerHTML = "";
  $("#unreadable").innerHTML = `<div class="uicon">!</div><div><h3>${title}</h3><p>${why}</p>${items.length ? `<ul>${items.join("")}</ul>` : ""}</div>`;
  $("#unreadable").hidden = false;
  $("#reportbody").hidden = true;
  $("#results").hidden = false;
  $("#results").scrollIntoView({ behavior: "smooth" });
}

function show(r) {
  current = r;
  if (r.score && r.score.not_scored) { showUnreadable(r); return; }
  $("#unreadable").hidden = true;
  $("#reportbody").hidden = false;
  window.history.replaceState(null, "", "#r=" + r.id);
  r.findings.forEach((f) => (f.dimensionLabel = r.score.dimensions[f.dimension].label));
  $("#results").hidden = false;
  const first = r.pages.find((p) => p.ok && p.title) || r.pages[0] || {};
  $("#rtitle").textContent = first.title || first.rel || r.domain;
  $("#rlinks").innerHTML = (r.urls || [r.url]).map((u) => `<span class="plink">${esc(u)}</span>`).join("");
  const n = r.pages.filter((p) => p.ok).length;
  $("#rmeta").textContent = `${r.domain} · ${new Date(r.timestamp).toLocaleString()} · ${n} page${n === 1 ? "" : "s"} analyzed`;

  const dims = Object.keys(FACTORS);
  const tabs = [`<button data-tab="summary" class="on">Overview</button>`]
    .concat(dims.map((d) => {
      const sc = r.score.dimensions[d].score;
      const cls = sc == null ? "none" : verdict(sc, d)[1];
      return `<button data-tab="${d}"><i class="dotv ${cls}"></i>${esc(FACTORS[d].tab)}</button>`;
    }));
  if (DETAILS) tabs.push(`<button data-tab="agents">AI agents</button>`, `<button data-tab="costs">Usage &amp; cost</button>`);
  tabs.push(`<button data-tab="compare" class="sec">Compare audits</button>`);
  $("#tabs").innerHTML = tabs.join("");
  $("#panels").innerHTML = ['summary', ...dims, "agents", "costs", "compare"].map((id) => `<div id="tab-${id}" class="tab" ${id === "summary" ? "" : "hidden"}></div>`).join("");

  renderSummary(r);
  buildPrint(r);
  dims.forEach((d) => renderFactor(r, d));
  if (DETAILS) { renderAgents(r); renderCosts(r); }
  $("#panels").onclick = (e) => { const a = e.target.closest("[data-go]"); if (a) { showTab(a.dataset.go); window.scrollTo({ top: $("#tabs").getBoundingClientRect().top + window.scrollY - 80, behavior: "smooth" }); } };
  showTab("summary");
  $("#results").scrollIntoView({ behavior: "smooth" });
  refreshHistory().then(() => { if (retestOf) { openCompare(retestOf, r.id); retestOf = null; } else renderCompareForm(r); });
}

function buildPrint(r) {
  const sc = r.score, c = r.counts, total = c.high + c.medium + c.low;
  const first = r.pages.find((p) => p.ok && p.title) || r.pages[0] || {};
  const pill = (v, key) => (v == null ? '<span class="pill none">Not measured</span>' : (([w, k]) => `<span class="pill ${k === "good" ? "good" : k === "mid" ? "mid" : "poor"}">${w}</span>`)(verdict(v, key)));
  const prio = { high: "High priority", medium: "Medium priority", low: "Low priority" };
  const top = r.findings.slice(0, 5);
  const brk = [c.high ? c.high + " high" : "", c.medium ? c.medium + " medium" : "", c.low ? c.low + " low" : ""].filter(Boolean).join(" · ");
  const sections = Object.keys(FACTORS).map((d) => {
    const dim = sc.dimensions[d], list = r.findings.filter((f) => f.dimension === d);
    return `<h2>${esc(FACTORS[d].tab)} ${dim.score == null ? "" : "· " + dim.score + "/100"} ${pill(dim.score, d)}</h2>
      ${list.length ? `<ul>${list.map((f) => { const n = top.indexOf(f) + 1; return `<li><b>${esc(f.title)}</b> <span class="pg">(${esc(prio[f.severity])} · ${esc(pageLabel(f.page))}${f.status === "potential" ? " · please double-check" : ""})</span>${n ? ` <span class="pg">See suggestion ${n} above.</span>` : `<br>${esc(f.recommendation)}`}</li>`; }).join("")}</ul>` : '<div class="ok">No issues found.</div>'}`;
  }).join("");
  $("#printreport").innerHTML = `
    <div class="brandline">AI Search Doctor · AI referral report</div>
    <h1>${esc(first.title || first.rel || r.domain)}</h1>
    ${(r.urls || [r.url]).map((u) => `<span class="plink">${esc(u)}</span>`).join("")}
    <div class="meta">${esc(r.domain)} · ${esc(new Date(r.timestamp).toLocaleString())} · ${r.pages.filter((p) => p.ok).length} page(s) analyzed</div>
    <div class="top3"><div class="bigscore">${sc.overall == null ? "–" : sc.overall}<small> /100</small></div>
      <div><b>${total === 0 ? "No issues found" : total + " issue" + (total === 1 ? "" : "s") + " found"}</b>${brk ? `<div class="meta">${brk}</div>` : ""}
      ${(sc.caps || []).map((x) => `<div class="cap">${esc(x)}</div>`).join("")}</div></div>
    <h2>The six checks</h2>
    <table>${Object.entries(sc.dimensions).map(([k, d]) => `<tr><td>${esc(FACTORS[k].tab)}</td><td>${pill(d.score, k)}</td><td class="n"><b>${d.score ?? "–"}</b></td></tr>`).join("")}</table>
    ${top.length ? `<h2>Suggestions: do these first</h2><ol>${top.map((f) => `<li><b>${esc(f.title)}</b> <span class="pg">(${esc(prio[f.severity])} · ${esc(pageLabel(f.page))})</span><br>${esc(f.recommendation)}${f.fix ? `<div class="wording">${esc(f.fix.suggestion)}</div>` : ""}</li>`).join("")}</ol>` : ""}
    ${sections}
    <div class="note">The AI referral score is an internal checklist score. It is not a prediction or a search ranking. Off-site factors such as backlinks and brand mentions are not measured.</div>`;
}

const verdict = (s, key) => {
  if (s == null) return ["Not measured", "none"];
  let v = s >= 85 ? ["Good", "good"] : s >= 60 ? ["Needs work", "mid"] : ["Poor", "poor"];
  // an area with a medium or high issue is never labelled plain "Good"
  if (v[1] === "good" && key && current && current.findings.some((f) => f.dimension === key && (f.severity === "medium" || f.severity === "high"))) v = ["Needs work", "mid"];
  return v;
};
const barClass = (s, key) => ({ good: "ok", mid: "mid", poor: "lo", none: "lo" }[verdict(s, key)[1]]);
const pageLabel = (p) => (p === "/" ? "Home page" : p);

function gauge(score) {
  if (score == null) return `<div class="gwrap"><div class="num" style="position:static;font-size:30px">Not scored</div></div>`;
  const C = 2 * Math.PI * 78;
  const [c1, c2] = score >= 85 ? ["#10b981", "#4ade80"] : score >= 60 ? ["#f59e0b", "#fcd34d"] : ["#ef4444", "#fb923c"];
  return `<div class="gwrap"><svg viewBox="0 0 190 190"><defs><linearGradient id="gg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="${c1}"/><stop offset="1" stop-color="${c2}"/></linearGradient></defs>
    <circle class="track" cx="95" cy="95" r="78" fill="none" stroke-width="13"/>
    <circle class="arc" id="arc" cx="95" cy="95" r="78" fill="none" stroke-width="13" stroke-dasharray="${C}" stroke-dashoffset="${C}" data-target="${C * (1 - score / 100)}"/></svg>
    <div class="num">${score}</div><div class="of">out of 100</div></div>`;
}

function suggestions(r) {
  const top = r.findings.slice(0, 5);
  if (!top.length) return `<div class="sugg"><h3>Suggestions</h3><div class="allgood">No verified issues were found on the pages you entered. Add more pages to check further.</div></div>`;
  const prio = { high: "High priority", medium: "Medium priority", low: "Low priority" };
  return `<div class="sugg"><h3>Suggestions</h3>
    ${top.map((f, i) => { const dm = r.score.dimensions[f.dimension]; const [vw, vc] = dm.score == null ? ["Not measured", "none"] : verdict(dm.score, f.dimension);
      return `<div class="sg ${esc(f.severity)}"><div class="num">${i + 1}</div><div class="body">
      <h4>${esc(f.title)}</h4><div class="todo">${esc(f.recommendation)}</div>
      ${f.fix ? `<details class="wording"><summary>Show suggested wording</summary><div class="box">${esc(f.fix.suggestion)}</div></details>` : ""}
      <div class="foot">${prio[f.severity] || ""} · ${esc(pageLabel(f.page))}${f.status === "potential" ? " · please double-check" : ""} · <a class="tablink" data-go="${esc(f.dimension)}">${esc(FACTORS[f.dimension].tab)} &rarr;</a></div></div>
      <div class="sgside"><div class="small muted">${esc(FACTORS[f.dimension].tab)}</div><span class="chipv ${vc}">${vw}</span></div></div>`; }).join("")}
    ${r.findings.length > top.length ? `<div class="small muted">${r.findings.length - top.length} more in the check tabs above.</div>` : ""}</div>`;
}

function renderSummary(r) {
  const s = r.score, c = r.counts;
  const dims = Object.entries(s.dimensions).map(([k, d]) => {
    const tab = FACTORS[k].tab;
    if (d.score == null) return `<a class="dim link" data-go="${k}"><div class="row"><span>${esc(tab)}</span><span class="chipv none">Not measured</span></div><div class="bar"></div></a>`;
    const [word, cls] = verdict(d.score, k);
    return `<a class="dim link" data-go="${k}"><div class="row"><span>${esc(tab)}</span><span><span class="chipv ${cls}">${word}</span> <b>${d.score}</b></span></div>
    <div class="bar"><i class="${barClass(d.score, k)}" style="width:${d.score}%"></i></div></a>`;
  }).join("");
  const pages = r.pages.map((p) => `<tr><td><b>${esc(p.title || p.rel)}</b><span class="plink">${esc(p.url)}</span></td><td>${p.ok ? "Read" : "Failed: " + esc(p.error)}</td><td class="num">${p.ok ? p.word_count : "-"}</td></tr>`).join("")
    + r.blocked.map((b) => `<tr><td><b>${esc(b.rel)}</b><span class="plink">${esc(b.url)}</span></td><td>Blocked by robots.txt (not read)</td><td class="num">-</td></tr>`).join("");
  const warn = r.warnings.map((w) => `<div class="note">${esc(w)}</div>`).join("");
  const total = c.high + c.medium + c.low;
  const breakdown = [c.high ? `<b class="hi">${c.high} high priority</b>` : "", c.medium ? `<b class="md">${c.medium} medium priority</b>` : "", c.low ? `<b>${c.low} low priority</b>` : ""].filter(Boolean).join(" · ");
  const counts = `<div class="issuetotal">${total === 0 ? "No issues found" : total + " issue" + (total === 1 ? "" : "s") + " found"}</div>${breakdown ? `<div class="small counts">${breakdown}</div>` : ""}`;
  $("#tab-summary").innerHTML = `${warn}
    <div class="grid g2">
      <div class="card gauge">${gauge(s.overall)}<div class="small muted" style="margin-top:6px"><b>AI referral score</b></div>
        ${counts}
        ${(s.caps || []).map((c) => `<div class="capnote">${esc(c)}</div>`).join("")}${s.not_scored ? `<div class="capnote">${esc(s.reason)}</div>` : ""}${s.partial ? `<div class="capnote">Partial score: some areas could not be measured.</div>` : ""}</div>
      <div class="card"><h3>The six checks <span class="small muted">· click one for details</span></h3>${dims}</div>
    </div>
    ${suggestions(r)}
    <details class="card"><summary>Pages checked (${r.pages.length + r.blocked.length})</summary><table><tr><th>Page</th><th>Status</th><th class="num">Words</th></tr>${pages}</table></details>
    <details class="card"><summary>About this score</summary><p class="small muted">${esc(s.disclaimer)}</p><p class="small muted">${esc(s.methodology)}</p></details>`;
  const arc = $("#arc");
  if (arc) requestAnimationFrame(() => requestAnimationFrame(() => arc.setAttribute("stroke-dashoffset", arc.dataset.target)));
}

function findingHTML(f) {
  const ev = f.evidence.map((e) => `<div class="ev ${e.kind === "quote" ? "q" : ""}">${e.kind === "quote" ? "“" + esc(e.text) + "”" : esc(e.text)}${e.verified ? ' <span class="tag confirmed">quote verified</span>' : ""}</div>`).join("");
  return `<div class="finding ${esc(f.severity)}">
    <div class="meta"><span class="tag ${esc(f.severity)}">${esc(f.severity)}</span>${f.status === "potential" ? '<span class="tag potential">please double-check</span>' : ""}<span>${esc(pageLabel(f.page))}</span></div>
    <h4>${esc(f.title)}</h4>
    <div class="todo">${esc(f.recommendation)}</div>
    <details class="more"><summary>Evidence, why it matters${f.fix ? " and suggested wording" : ""}</summary>
      <div class="lbl">Evidence</div>${ev}${f.verification_note ? `<div class="small muted">${esc(f.verification_note)}</div>` : ""}
      <div class="lbl">Why it matters</div><div>${esc(f.why)}</div>
      ${f.fix ? `<div class="fixlbl">Suggested wording · ${esc(f.fix.where)}</div><div class="fix">${esc(f.fix.suggestion)}</div>` : ""}
      <div class="small muted" style="margin-top:8px">${esc(f.source === "code" ? "Rule-based check" : "AI finding, quotes verified")} · confidence ${esc(f.confidence)}</div></details>
  </div>`;
}

const yes = '<span class="tag confirmed">yes</span>', no = '<span class="tag rejected">no</span>';
const first1 = (arr) => (arr && arr.length ? esc(arr[0]) : "");

function domainCard(r) {
  const di = r.domain_info || {};
  if (di.skipped) return `<div class="card"><h3>Domain credibility</h3><div class="muted">${esc(di.reason || "Not checked.")}</div></div>`;
  const nc = '<span class="tag low">not checked</span>';
  const age = di.age_days != null ? `${di.age_days >= 730 ? Math.floor(di.age_days / 365) + " years" : Math.max(1, Math.floor(di.age_days / 30)) + " months"} (registered ${esc(di.registered)})` : "";
  const rows = [
    ["Domain age", di.age_days == null ? nc : (di.age_days >= 730 ? yes : no), di.age_days == null ? "Registry did not publish a date" : age],
    ["Email protection (SPF)", di.spf == null ? nc : (di.spf ? yes : no), ""],
    ["Email protection (DMARC)", di.dmarc == null ? nc : (di.dmarc ? yes : no), di.dmarc_policy ? "policy: " + esc(di.dmarc_policy) : ""],
    ["Popularity rank (context only, not scored)", !di.tranco_checked ? nc : (di.tranco_rank ? '<span class="tag confirmed">top 1M</span>' : '<span class="tag low">not in top 1M</span>'), di.tranco_rank ? "rank " + di.tranco_rank.toLocaleString() + " (Tranco list)" : ""],
  ];
  return `<div class="card"><h3>Domain credibility <span class="small muted">· ${esc(di.domain || "")}</span></h3><table><tr><th>Signal</th><th>Result</th><th>Detail</th></tr>
    ${rows.map((x) => `<tr><td>${x[0]}</td><td>${x[1]}</td><td class="small muted">${x[2]}</td></tr>`).join("")}</table>
    <div class="small muted" style="margin-top:8px">Sources: public registry (RDAP), Tranco domain ranking, and public DNS. No public source proves these signals decide whether an AI cites a site. They are trust proxies that match Google's guidance and industry studies, so treat them as supporting evidence, not as a guarantee.</div></div>`;
}

function lighthouseCard(r, ok) {
  const ps = r.pagespeed || {};
  if (!ps.enabled) return DETAILS ? `<div class="note">Optional: set PAGESPEED_API_KEY in .env to add Google's real mobile speed test (Lighthouse) to this score.</div>` : "";
  const rows = ok.map((p) => ({ p, x: (ps.pages || {})[p.rel] }));
  const sec = (ms) => (ms == null ? "–" : (ms / 1000).toFixed(1) + " s");
  return `<div class="card"><h3>Google PageSpeed, mobile (Lighthouse)</h3>${rows.some((r2) => r2.x && r2.x.ok) ? "" : '<div class="muted">The Google test did not return a result' + ((ps.skipped_local || 0) ? " (local test pages are skipped)." : ".") + "</div>"}
    <table><tr><th>Page</th><th>Score</th><th>Largest content</th><th>Blocking time</th><th>Layout shift</th><th>Biggest savings</th></tr>
    ${rows.map(({ p, x }) => !x ? "" : x.ok ? `<tr><td>${esc(p.rel)}</td><td><b>${x.score ?? "–"}</b></td><td>${sec(x.lcp_ms)}</td><td>${x.tbt_ms != null ? Math.round(x.tbt_ms) + " ms" : "–"}</td><td>${x.cls != null ? x.cls.toFixed(2) : "–"}</td><td class="small muted">${(x.opportunities || []).map((o) => esc(o.title) + " (" + o.saves_s + "s)").join("; ")}</td></tr>` : `<tr><td>${esc(p.rel)}</td><td colspan="5" class="small muted">${esc(x.error)}</td></tr>`).join("")}</table>
    <div class="small muted" style="margin-top:8px">Lab test run by Google PageSpeed Insights on a simulated mobile phone. It makes up 70% of the Speed & mobile score.</div></div>`;
}

function measured(r, d) {
  const ok = r.pages.filter((p) => p.ok);
  const head = (cols) => `<tr>${cols.map((c, i) => `<th${i ? "" : ""}>${c}</th>`).join("")}</tr>`;
  const nm = (p) => `<b>${esc(p.title || p.rel)}</b><span class="plink">${esc(p.rel)}</span>`;
  if (!ok.length) return "";
  if (d === "readable") return `<div class="card"><h3>What we found on each page</h3><table>${head(["Page", "Title", "Meta description", "Main headings (H1)", "Words"])}
    ${ok.map((p) => `<tr><td>${nm(p)}</td><td>${p.title ? yes : no}</td><td>${p.has_meta_description ? yes : no}</td><td>${p.h1_count}</td><td>${p.word_count}</td></tr>`).join("")}</table>
    <div class="small muted" style="margin-top:8px">Sitemap: ${r.sitemap_found ? "found" : "not found"} · robots.txt: ${r.robots_found ? "found" : "not found (everything allowed)"}${r.blocked.length ? " · " + r.blocked.length + " page(s) blocked by robots.txt" : ""}</div></div>`;
  if (d === "answer_quality") {
    const qs = r.questions || [], facts = r.key_facts || [];
    if (!qs.length && !facts.length) return `<div class="note">The AI content analysis did not run, so questions and key facts could not be checked.</div>`;
    return `<div class="card"><h3>Questions a customer might ask an AI about you</h3>
      ${qs.map((q) => `<div class="qa"><span class="tag ${q.answered ? "confirmed" : "rejected"}">${q.answered ? "answered" : "not answered"}</span> ${esc(q.question)}${q.answered && q.answer_quote ? `<div class="ev q">“${esc(q.answer_quote)}”</div>` : ""}</div>`).join("")}</div>
      <div class="card"><h3>Key facts about your business</h3><table>${head(["Fact", "Clearly stated?", "What the page says"])}
      ${facts.map((f) => `<tr><td>${esc(f.fact.replace(/_/g, " "))}</td><td>${f.found ? yes : no}</td><td>${f.found ? esc(f.value || f.quote) : ""}</td></tr>`).join("")}</table></div>`;
  }
  if (d === "credibility") {
    const any = (k) => ok.some((p) => (Array.isArray(p[k]) ? p[k].length : p[k]));
    const has = (w) => ok.some((p) => (p.trust_links || []).includes(w));
    const row = (label, v, extra = "") => `<tr><td>${label}</td><td>${v ? yes : no}</td><td class="small muted">${extra}</td></tr>`;
    const e = ok.flatMap((p) => p.emails || []), ph = ok.flatMap((p) => p.phones || []), ad = ok.flatMap((p) => p.addresses || []);
    return `<div class="card"><h3>Trust signals found on the pages you entered</h3><table>${head(["Signal", "Found?", "Detail"])}
      ${row("Email address", e.length || ok.some((p) => p.contact_links), esc(e[0] || ""))}${row("Phone number", ph.length, esc(ph[0] || ""))}${row("Street address", ad.length, esc(ad[0] || ""))}
      ${row("About page or link", has("about") || ok.some((p) => /about/i.test(p.rel + " " + p.title)))}${row("Privacy policy or terms link", has("privacy") || has("terms"))}
      ${row("Secure connection (HTTPS)", (r.root || "").startsWith("https://") || /127\.0\.0\.1|localhost/.test(r.root || ""), /127\.0\.0\.1|localhost/.test(r.root || "") ? "local test site" : "")}
      ${row("Links to official profiles", ok.some((p) => (p.social_links || []).length), esc([...new Set(ok.flatMap((p) => p.social_links || []))].join(", ")))}
      ${row("Customer reviews or testimonials", ok.some((p) => p.has_reviews))}
      ${ok.some((p) => p.article_like) ? row("Named author on articles", ok.filter((p) => p.article_like).every((p) => p.has_author)) : ""}</table>
      <div class="small muted" style="margin-top:8px">Only the pages you entered are checked, so a missing item may exist elsewhere on your site.</div></div>${domainCard(r)}`;
  }
  if (d === "speed_mobile") {
    const kb = (n) => (n == null ? "–" : n < 1e6 ? Math.round(n / 1000) + " KB" : (n / 1e6).toFixed(1) + " MB");
    return `<div class="card"><h3>Measured on each page</h3><table>${head(["Page", "Total weight", "Scripts", "Images", "Files", "Outside hosts", "Server response", "Mobile tag"])}
    ${ok.map((p) => { const f = p.perf || {}, w = p.weight || {}; return `<tr><td>${nm(p)}</td><td><b>${w.measured ? kb(w.total) : kb(f.bytes)}</b></td><td>${w.measured ? kb(w.js) : "–"}</td><td>${w.measured ? kb(w.img) : "–"}</td><td>${w.measured ? w.files : "–"}</td><td>${w.measured ? w.third_party_hosts : "–"}</td><td>${f.ttfb != null ? f.ttfb.toFixed(2) + " s" : "–"}</td><td>${p.viewport ? yes : no}</td></tr>`; }).join("")}</table>
    <div class="small muted" style="margin-top:8px">Weight is the download size of the page and the files it loads (up to 60 files per page). Lighthouse treats about 1.6 MB as a good ceiling for mobile.</div></div>${lighthouseCard(r, ok)}`;
  }
  if (d === "freshness") return `<div class="card"><h3>Dates found on each page</h3><table>${head(["Page", "Newest date found", "Where it was found"])}
    ${ok.map((p) => { const ds = (p.dates || []).slice().sort((a, b) => b.date.localeCompare(a.date)); return `<tr><td>${nm(p)}</td><td>${ds.length ? esc(ds[0].date) : '<span class="tag rejected">none</span>'}</td><td class="small muted">${ds.length ? esc(ds[0].src) : (p.copyright_year ? "only a © " + p.copyright_year + " footer" : "")}</td></tr>`; }).join("")}</table></div>`;
  if (d === "structured_data") return `<div class="card"><h3>Structured data on each page</h3><table>${head(["Page", "Types found"])}
    ${ok.map((p) => `<tr><td>${nm(p)}</td><td>${(p.jsonld_types || []).length ? esc([...new Set(p.jsonld_types)].join(", ")) : '<span class="tag rejected">none</span>'}${p.jsonld_errors ? " · " + p.jsonld_errors + " invalid block(s)" : ""}</td></tr>`).join("")}</table></div>`;
  return "";
}

function renderFactor(r, d) {
  const F = FACTORS[d], dim = r.score.dimensions[d];
  const list = r.findings.filter((f) => f.dimension === d);
  const rej = (r.rejected || []).filter((x) => (d === "credibility") === REJECT_CRED.has(x.issue_type) && ["answer_quality", "credibility"].includes(d));
  let head;
  if (dim.score == null) head = `<span class="chipv none">Not measured</span>`;
  else { const [w, c] = verdict(dim.score, d); head = `<span class="chipv ${c}">${w}</span> <b class="big">${dim.score}</b><span class="muted"> / 100</span>`; }
  $("#tab-" + d).innerHTML = `
    <div class="card"><div class="fhead"><div><h3 style="margin:0">${esc(dim.label)}</h3><div class="muted">${esc(F.intro)}</div></div><div class="fscore">${head}</div></div>
      ${dim.score != null ? `<div class="bar" style="margin-top:12px"><i class="${barClass(dim.score, d)}" style="width:${dim.score}%"></i></div>` : ""}
      ${dim.note ? `<div class="small muted" style="margin-top:6px">${esc(dim.note)}</div>` : ""}
      <details class="checks"><summary>What this check looks at</summary><ul>${F.checks.map((c) => `<li>${esc(c)}</li>`).join("")}</ul></details></div>
    <div class="sugg"><h3>Suggestions</h3>
    ${list.length ? list.map(findingHTML).join("") : '<div class="allgood">No issues found for this check on the pages you entered.</div>'}</div>
    ${measured(r, d) ? `<details class="mdet"><summary>See what we measured</summary>${measured(r, d)}</details>` : ""}
    ${rej.length ? `<details class="card"><summary>${rej.length} AI claim(s) rejected by the Evidence Verifier</summary>
      <p class="small muted">The AI proposed these, but they could not be supported by your pages, so they are NOT in your report.</p>
      ${rej.map((x) => `<div class="finding"><span class="tag rejected">rejected</span> <b>${esc(x.title)}</b><div class="small muted">Reason: ${esc(x.reason)}</div>${x.claimed_quotes.filter(Boolean).map((q) => `<div class="ev q">“${esc(q)}”</div>`).join("")}</div>`).join("")}</details>` : ""}`;
}

function renderAgents(r) {
  $("#tab-agents").innerHTML = `<div class="card"><table><tr><th>Agent</th><th>Status</th><th>What it did</th><th class="num">Time</th></tr>
    ${r.agents.map((a) => `<tr><td><b>${esc(a.name)}</b><div class="small muted">${esc(a.role)}</div></td><td><span class="tag ${a.status === "done" ? "confirmed" : a.status === "failed" ? "rejected" : "low"}">${esc(a.status)}</span></td>
    <td>${esc(a.summary)}${(a.notes || []).length ? `<div class="small muted">${a.notes.filter((n) => n !== "started").map(esc).join("<br>")}</div>` : ""}</td><td class="num">${a.seconds ?? "-"}s</td></tr>`).join("")}</table></div>
    ${r.warnings.length ? r.warnings.map((w) => `<div class="note">Incomplete check: ${esc(w)}</div>`).join("") : ""}`;
}

function renderCosts(r) {
  const u = r.usage, t = u.total, names = { content: "Content & Answer-Readiness Auditor", fixwriter: "Fix Writer" };
  const rows = Object.entries(u.by_agent).map(([a, v]) => `<tr><td>${esc(names[a] || a)}</td><td class="num">${v.calls}</td><td class="num">${v.input}</td><td class="num">${v.cache_read}</td><td class="num">${v.output}</td><td class="num">${money(v.cost_usd)}</td></tr>`).join("");
  $("#tab-costs").innerHTML = `<div class="card"><h3>Measured usage for this audit</h3>
    ${t.calls ? `<table><tr><th>Agent</th><th class="num">Calls</th><th class="num">Input tokens</th><th class="num">Cached input</th><th class="num">Output tokens</th><th class="num">Cost</th></tr>${rows}
    <tr><td><b>Total</b></td><td class="num"><b>${t.calls}</b></td><td class="num"><b>${t.input}</b></td><td class="num"><b>${t.cache_read}</b></td><td class="num"><b>${t.output}</b></td><td class="num"><b>${money(t.cost_usd)}</b></td></tr></table>
    <p class="small muted">Total tokens: ${t.total_tokens}. Agents 1, 2, 4 and 5 are rule-based code and use no tokens.</p>` : '<p class="muted">No model calls were made in this audit.</p>'}</div>
    <div class="card"><h3>Projected cost</h3><table><tr><th>Number of audits</th><th class="num">Estimated cost</th></tr>
    ${r.projections.map((p) => `<tr><td>${p.audits.toLocaleString()}</td><td class="num">${money(p.cost_usd)}</td></tr>`).join("")}</table>
    <p class="small muted">${esc(r.cost_assumptions)}</p></div>`;
}

/* ---------- compare ---------- */
function renderCompareForm(r) {
  const others = history.filter((a) => a.id !== r.id && a.domain === (r.site || r.domain) && a.score != null).sort((a, b) => (b.url === r.url) - (a.url === r.url));
  const short = (u) => { const t = u.replace(/^https?:\/\//, "").replace(/\/$/, ""); return t.length > 38 ? t.slice(0, 37) + "…" : t; };
  const when = (iso) => new Date(iso).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit", second: "2-digit" });
  $("#tab-compare").innerHTML = others.length ? `<div class="card"><h3>Compare with an earlier audit</h3>
    <p class="muted">Pick the earlier ("before") audit. It is compared with the report you have open ("after"). To test a fix: change your site, run the audit again on the same links, then compare here.</p>
    <div class="cmprow"><select id="cmpold" title="Earlier audit">${others.map((a) => `<option value="${esc(a.id)}">${esc(short(a.url))} · ${esc(when(a.timestamp))} · ${a.pages || 1} page${(a.pages || 1) === 1 ? "" : "s"} · score ${a.score ?? "n/a"}</option>`).join("")}</select>
    <button class="ghost" id="cmpgo">Compare</button></div></div><div id="cmpout"></div>`
    : '<div class="card muted">No earlier audit of this website to compare with yet. Fix something on your site, then analyze the same links again.</div>';
  const go = $("#cmpgo");
  if (go) go.onclick = () => openCompare($("#cmpold").value, r.id);
}

async function openCompare(oldId, newId) {
  if (!$("#cmpout")) renderCompareForm(current);
  let c;
  try { c = await api(`/api/compare?old=${encodeURIComponent(oldId)}&new=${encodeURIComponent(newId)}`); }
  catch (e) { $("#cmpout").innerHTML = `<div class="note">${esc(e.message)}</div>`; showTab("compare"); return; }
  const arrow = c.score_delta > 0 ? "up" : c.score_delta < 0 ? "down" : "";
  const list = (items, label, cls) => items.length ? `<div class="card"><h3>${label} (${items.length})</h3>${items.map((f) => `<div class="finding"><span class="tag ${cls}">${label.split(" ")[0].toLowerCase()}</span> <span class="tag ${esc(f.severity)}">${esc(f.severity)}</span> <b>${esc(f.title)}</b> <span class="small muted">${esc(pageLabel(f.page))}</span>
      ${(f.evidence || []).slice(0, 2).map((e) => `<div class="ev ${e.kind === "quote" ? "q" : ""}">${esc(e.text)}</div>`).join("")}${f.now ? `<div class="small"><b>After:</b> ${esc(f.now)}</div>` : ""}</div>`).join("")}</div>` : `<div class="card muted">${label}: none</div>`;
  $("#cmpout").innerHTML = `
    <div class="card"><h3>Before and after</h3>
      <div class="grid g3"><div class="stat"><b>${c.old.score ?? "–"}</b>Before (${c.old.issues} issues)</div><div class="stat"><b>${c.new.score ?? "–"}</b>After (${c.new.issues} issues)</div>
      <div class="stat"><b class="delta ${arrow}">${c.score_delta == null ? "–" : (c.score_delta > 0 ? "+" : "") + c.score_delta}</b>Score change</div></div>
      <table style="margin-top:12px"><tr><th>Area</th><th class="num">Before</th><th class="num">After</th><th class="num">Change</th></tr>
      ${Object.values(c.dimensions).map((d) => `<tr><td>${esc(d.label)}</td><td class="num">${d.before ?? "–"}</td><td class="num">${d.after}</td><td class="num delta ${d.delta > 0 ? "up" : d.delta < 0 ? "down" : ""}">${d.delta == null ? "–" : (d.delta > 0 ? "+" : "") + d.delta}</td></tr>`).join("")}</table>
      <div class="note">${esc(c.caveat)}</div></div>
    ${list(c.resolved, "Resolved issues", "confirmed")}${list(c.remaining, "Remaining issues", "medium")}${list(c.new_issues, "New issues", "high")}`;
  showTab("compare");
  $("#tab-compare").scrollIntoView({ behavior: "smooth" });
}

init();
