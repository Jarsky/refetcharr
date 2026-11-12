// Refetcharr web UI: polls /api/status, renders the tabs, and sends actions to the same API the TUI uses.
const GROUPS = { remux: ["1080p remuxes", "var(--violet)"], big4k: ["4K over the cap", "var(--amber)"],
  remux4k: ["4K remuxes", "var(--pink)"], big1080: ["1080p over the cap", "var(--blue)"], tiny: ["Tiny x265", "var(--mint)"],
  lowq: ["Low quality", "var(--red)"], upgrade: ["Put right", "var(--cyan)"] };
const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const size = (b) => { const g = (b || 0) / 1e9; return g >= 1000 ? `${(g / 1000).toFixed(2)} TB` : `${g.toFixed(1)} GB`; };
let pageVersion = null;   // the server's version this page was loaded with
let status = null, films = [], libSort = ["saving_gb", -1], libShown = 200, settings = null;

// What this browser has already seen, so the tabs can badge what's new (like Radarr's and Sonarr's counts).
const SEEN_KEY = "refetcharr-seen";
let seen = (() => { try { return JSON.parse(localStorage.getItem(SEEN_KEY)); } catch { return null; } })();
const saveSeen = () => { try { localStorage.setItem(SEEN_KEY, JSON.stringify(seen)); } catch { /* private window */ } };
const failKey = (x) => `${x.id}:${x.status}`;
function markSeen(tab) {
  if (!status || !seen) return;
  if (tab === "failed") seen.failed = status.failed.map(failKey);
  if (tab === "replaced") seen.replaced = status.replaced.map((x) => String(x.id));
  if (tab === "library") seen.scan = status.scan.at || "";
  saveSeen(); badges();
}
function badges() {
  if (!status) return;
  if (!seen) { seen = { failed: status.failed.map(failKey), replaced: status.replaced.map((x) => String(x.id)), scan: status.scan.at || "" }; saveSeen(); }
  const set = (id, n, title) => { const b = $(id); b.hidden = !n; b.textContent = b.classList.contains("dot") ? "" : n; if (title) b.title = title; };
  const newFail = status.failed.filter((x) => !seen.failed.includes(failKey(x))).length;
  const newDone = status.replaced.filter((x) => !seen.replaced.includes(String(x.id))).length;
  set("b-dashboard", status.active.length, `${status.active.length - status.stalled} downloading${status.stalled ? `, ${status.stalled} stalled` : ""}`);
  $("b-dashboard").classList.toggle("warnbg", !!(status.stalled || status.stuck));
  set("b-failed", newFail, `${newFail} new since you last looked`);
  set("b-replaced", newDone, `${newDone} new since you last looked`);
  set("b-library", status.scan.at && status.scan.at !== seen.scan ? 1 : 0);
  set("b-radarr", status.setup_issues.length, status.setup_issues.join("; "));
  set("b-suggestions", status.suggestions || 0, `${status.suggestions} film(s) with replacements to pick from`);
  document.title = (newFail ? `(${newFail}) ` : "") + "Refetcharr";
  rollUp();
}
// A section shows what its sub-items have, for when it's collapsed: their total, or a dot, in the most urgent colour.
function rollUp() {
  document.querySelectorAll(".nav-group").forEach((g) => {
    const top = g.querySelector(".nav-top .badge"), kids = [...g.querySelectorAll(".nav-sub .badge")].filter((b) => !b.hidden);
    if (!top || !g.querySelector(".nav-sub")) return;
    const n = kids.reduce((t, b) => t + (parseInt(b.textContent, 10) || 0), 0);
    const urgent = kids.find((b) => !/\b(info|ok|dot)\b/.test(b.className)) || kids.find((b) => b.classList.contains("info")) || kids[0];
    top.hidden = !kids.length;
    top.className = "badge" + (urgent?.classList.contains("info") ? " info" : urgent?.classList.contains("ok") ? " ok" : "") + (n ? "" : " dot");
    top.textContent = n || "";
    top.title = kids.map((b) => b.title).filter(Boolean).join("; ");
  });
}

async function api(method, path, body) {
  const r = await fetch(path, { method, headers: { "Content-Type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body) });
  if (r.status === 401) { location.replace("/login"); throw new Error("Signed out"); }   // the session ended: sign in again
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof data.detail === "string" ? data.detail : data.detail?.[0]?.msg || `Request failed (${r.status})`);
  return data;
}
function toast(msg, kind = "info") {
  const t = $("toast"); t.textContent = msg; t.className = `toast ${kind}`; t.hidden = false;
  clearTimeout(toast.t); toast.t = setTimeout(() => (t.hidden = true), kind === "err" ? 8000 : 4500);
}
async function act(fn) { try { const r = await fn(); if (r?.message) toast(r.message, "ok"); await refresh(); } catch (e) { toast(e.message, "err"); } }
// One look for every result message: ok (mint), err (pink), info (cyan). Successes fade after a while.
function say(id, text, kind = "ok") {
  const el = $(id); clearTimeout(el._t);
  el.className = `msg ${kind}`; el.textContent = text;
  if (kind === "ok") el._t = setTimeout(() => (el.textContent = ""), 6000);
}
// A form's save button stays dull until something differs from what's saved (and again once it's saved).
const tracked = new Map();
function track(id, snap) {
  const form = $(id), btn = form.querySelector("button.primary:not([type=button])");
  const t = { snap, base: null, btn, check: () => setTimeout(() => { if (t.base !== null) btn.disabled = snap() === t.base; }) };
  tracked.set(id, t); btn.disabled = true;
  for (const ev of ["input", "change", "click", "drop", "dragend", "keyup"]) form.addEventListener(ev, t.check);
}
function clean(id) { const t = tracked.get(id); t.base = t.snap(); t.btn.disabled = true; }
const recheck = (id) => tracked.get(id).check();

// ── tabs ──────────────────────────────────────────────────────────────────
const TAB_GROUP = { dashboard: "dashboard", library: "library", suggestions: "library", replaced: "activity", failed: "activity", events: "activity",
  replacement: "settings", quality: "settings", queue: "settings", radarr: "settings", timings: "settings", security: "settings" };
const TAB_TITLE = { dashboard: "Dashboard", library: "Flagged films", suggestions: "Suggestions", replaced: "Replaced", failed: "Not replaced", events: "Events",
  replacement: "Replacement", quality: "Quality", queue: "Queue & schedule", radarr: "Radarr", timings: "Timings", security: "Security" };
const currentTab = () => { const t = (location.hash || "#dashboard").slice(1); return t === "settings" ? "radarr" : TAB_GROUP[t] ? t : "dashboard"; };
function showTab() {
  const tab = currentTab();
  document.querySelectorAll(".tab").forEach((s) => (s.hidden = s.id !== `tab-${tab}`));
  document.querySelectorAll(".nav-group").forEach((g) => g.classList.toggle("open", g.dataset.group === TAB_GROUP[tab]));
  document.querySelectorAll(".nav a[data-tab]").forEach((a) => a.classList.toggle("on", a.dataset.tab === tab));
  $("page-title").textContent = TAB_TITLE[tab];
  closeMenu();
  if (tab === "library") loadFilms();
  if (["queue", "radarr", "timings", "security"].includes(tab)) loadSettings();
  if (tab === "replacement") loadRules();
  if (tab === "quality") loadQuality();
  if (tab === "events") loadEvents();
  if (tab === "replaced") loadReplaced(true);
  if (tab === "suggestions") loadSuggestions();
  markSeen(tab);
}
window.addEventListener("hashchange", showTab);
// the menu slides in on small screens
function closeMenu() { $("side").classList.remove("open"); $("scrim").hidden = true; $("menu").setAttribute("aria-expanded", "false"); }
$("menu").addEventListener("click", () => {
  const open = !$("side").classList.contains("open");
  $("side").classList.toggle("open", open); $("scrim").hidden = !open; $("menu").setAttribute("aria-expanded", open);
});
$("scrim").addEventListener("click", closeMenu);
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeMenu(); });

// ── dashboard ─────────────────────────────────────────────────────────────
function shortError(e) {
  if (!e) return "";
  const m = /"message":\s*"([^"]+)"/.exec(e) || /"description":\s*"[^:"]*?:\s*([^"\\]+)/.exec(e);
  return (m ? m[1] : e.trim().split("\n").pop()).slice(0, 220);
}
$("error").addEventListener("click", (e) => {
  if (e.target.id !== "error-x") return;
  try { sessionStorage.setItem("refetcharr-dismissed", shortError(status?.error)); } catch { /* private window */ }
  $("error").hidden = true;
});
async function refresh() {
  try { status = await api("GET", "/api/status"); } catch (e) { $("error").hidden = false; $("error").textContent = "Can't reach the Refetcharr server: " + e.message; return; }
  const s = status, c = s.counts;
  if (pageVersion && s.version !== pageVersion) { location.reload(); return; }   // updated: a page left open would keep running the old code
  pageVersion = s.version;
  $("version").textContent = `v${s.version}`;
  $("s-freed").textContent = size(s.freed);
  $("s-freed-label").textContent = s.grown?.films ? `freed · ${s.grown.films} tiny file${s.grown.films === 1 ? "" : "s"} grew ${size(s.grown.bytes)}` : "freed";
  $("s-replaced").textContent = c.replaced || 0;
  $("s-active").textContent = `${s.active.length - s.stalled}/${s.limits.max_active}`;
  const warned = [s.stalled ? `${s.stalled} stalled` : "", s.stuck ? `${s.stuck} waiting on qBittorrent` : ""].filter(Boolean).join(", ");
  $("s-active-label").textContent = warned ? `downloading · ${warned}` : "downloading";
  $("stat-active").classList.toggle("warned", !!warned);
  $("s-today").textContent = `${s.today}/${s.limits.max_per_day || "∞"}`;
  $("s-failed").textContent = s.failed.length;
  const mode = $("mode");
  mode.className = "pill " + (s.auto ? "auto" : "manual");
  mode.textContent = s.auto ? (s.in_window ? "Auto-queue on" : "Auto-queue (outside schedule)") : "Manual queue";
  mode.title = s.auto ? "Keeping downloads topped up by itself. Click for manual: only what you queue" : "Only what you queue. Click to keep downloads topped up by itself";
  const waiting = s.waiting.ids.length + s.waiting.count;
  $("busy").textContent = (s.busy ? s.busy + "…" : "Idle") + (waiting ? ` · ${waiting} waiting to search${s.paused ? " (paused)" : ""}` : "")
    + (s.suggesting ? ` · ${s.suggesting} to find replacements for` : "");
  const pause = $("pause");
  pause.textContent = s.paused ? "Paused" : "Pause";
  pause.classList.toggle("paused", s.paused);
  pause.title = s.paused ? "Paused: no new searches start. Click to resume" : "Stop starting new searches (downloads already running finish and import)";
  pause.setAttribute("aria-pressed", s.paused);
  const problem = shortError(s.error);
  let dismissed = ""; try { dismissed = sessionStorage.getItem("refetcharr-dismissed") || ""; } catch { /* private window */ }
  $("error").hidden = !problem || problem === dismissed;
  $("error").innerHTML = problem ? `<span>Problem: ${esc(problem)}${/API key|reach Radarr/.test(problem) ? ' · <a href="#radarr">Open Settings → Radarr</a>' : ""}</span>
    <button type="button" class="x" id="error-x" aria-label="Dismiss">✕</button>` : "";

  $("active").innerHTML = s.active.length ? s.active.sort((a, b) => (b.stalled - a.stalled) || (b.finish_warn - a.finish_warn) || (b.progress ?? -1) - (a.progress ?? -1)).map((a) => {
    const ready = ["importPending", "importBlocked"].includes(a.state), pct = a.progress ?? 0;
    const importing = a.status === "importing" || a.state === "importing";
    const mins = a.finishing_minutes ?? 0, waited = mins >= 120 ? `${Math.round(mins / 60)} h` : `${mins} min`;
    const label = a.stalled ? `Stalled: no progress for ${Math.round(a.stalled_hours ?? 0)} h, at ${pct}%` : a.finishing ? `Downloaded ${waited} ago, waiting for qBittorrent to complete it` : importing ? "Importing" : ready ? "Ready to import" : a.progress === null ? "Waiting for Radarr" : `${pct}%`;
    const open = expanded.has(`active:${a.id}`);
    return `<div class="dl row-x ${open ? "open" : ""} ${a.stalled || a.finish_warn ? "stalled" : ""}" data-id="${a.id}"><span class="t">${esc(a.title)} <span class="yr">${a.year || ""}</span>${a.stalled ? ` <span class="tag warnbg">Not using a slot</span>` : ""}</span>
      <span class="sz">${size(a.old_size)} → ${size(a.new_size)}</span>
      <div class="bar ${ready || importing ? "ready" : ""}"><i style="width:${ready || importing ? 100 : pct}%"></i></div>
      <div class="meta"><span>${label}</span><span>${esc(a.message || a.timeleft || "")}</span></div></div>`
      + (open ? `<div class="dl-detail" data-for="${a.id}">${detailCache.get(+a.id) || LOADING}</div>` : "");
  }).join("") : `<p class="empty">Nothing downloading.</p>`;

  $("activity").innerHTML = s.activity.map((x) => `<li><time>${esc(x.time.slice(5, 16))}</time><span class="${esc(x.kind)}">${esc(x.text)}</span></li>`).join("")
    || `<li class="empty">Nothing yet.</li>`;

  const sum = s.scan.summary || {};
  $("scan-at").textContent = s.scan.at ? `· ${s.scan.at.replace("T", " ")}` : "· not scanned yet";
  $("scan-summary").innerHTML = Object.entries(GROUPS).filter(([k]) => sum[k]).map(([k, [label, col]]) =>
    `<div class="group" style="--c:${col}"><b>${label}</b><span>${sum[k].films} films · ${(sum[k].gb / 1000).toFixed(1)} TB</span>
     <span>${k === "tiny" ? `need bigger files${s.groups?.tiny ? " · going after them" : " · switched off"}` : `about ${(sum[k].saving_gb / 1000).toFixed(1)} TB to win`}</span></div>`).join("")
    || `<p class="empty">Run a scan to see the library's oversized films.</p>`;

  $("active-count").textContent = s.active.length ? `· ${s.active.length - s.stalled} of ${s.limits.max_active} at once${s.stalled ? `, ${s.stalled} stalled` : ""}` : "";
  renderFailed(); loadReplaced(); if (currentTab() === "suggestions") loadSuggestions(true);
  const tab = currentTab();
  if (tab === "failed" || tab === "replaced" || tab === "library") markSeen(tab); else badges();
}

function renderFailed() {
  const rows = status.failed;
  const keep = new Set([...document.querySelectorAll("#fail-rows input:checked")].map((i) => i.value));
  $("fail-rows").innerHTML = rows.map((x) => xrow("fail-rows", x.id, `<td class="pick"><input type="checkbox" value="${x.id}" ${keep.has(String(x.id)) ? "checked" : ""} aria-label="Select ${esc(x.title)}"></td>
    <td>${esc(x.title)} <span class="yr">${x.year || ""}</span></td><td>${esc(x.status)}</td><td class="why">${esc(x.problem)}</td><td class="r">${size(x.old_size)}</td>`)).join("")
    || `<tr><td colspan="5" class="empty">Every film tried so far was replaced or is still in progress.</td></tr>`;
  $("retry-all").disabled = !rows.length;
  updateRetrySel();
}
function updateRetrySel() { $("retry-sel").disabled = !document.querySelectorAll("#fail-rows input:checked").length; }
$("fail-rows").addEventListener("change", updateRetrySel);
$("fail-all").addEventListener("change", (e) => { document.querySelectorAll("#fail-rows input").forEach((i) => (i.checked = e.target.checked)); updateRetrySel(); });
$("retry-sel").addEventListener("click", () => act(async () => {
  const ids = [...document.querySelectorAll("#fail-rows input:checked")].map((i) => +i.value);
  const r = await api("POST", "/api/retry", { ids }); return { message: `${r.retried.length} film(s) will be tried again.` };
}));
$("retry-all").addEventListener("click", () => act(async () => { const r = await api("POST", "/api/retry", {}); return { message: `${r.retried.length} film(s) will be tried again.` }; }));

let replacedRows = null, replacedAt = 0, doneShow = "all";
async function loadReplaced(force) {
  if (!force && (currentTab() !== "replaced" || Date.now() - replacedAt < 15000)) return;
  replacedAt = Date.now();
  try { replacedRows = await api("GET", "/api/replaced"); renderReplaced(); } catch (e) { toast(e.message, "err"); }
}
const ISSUE_TAG = (t) => `<span class="tag" style="--c: var(--red)">${esc(t)}</span>`;
function renderReplaced() {
  if (!replacedRows) return;
  const q = $("done-q").value.trim().toLowerCase(), keep = new Set([...document.querySelectorAll("#done-rows input:checked")].map((i) => i.value));
  const withIssues = replacedRows.filter((x) => x.issues.length);
  $("done-n-all").textContent = replacedRows.length; $("done-n-issues").textContent = withIssues.length;
  const rows = (doneShow === "issues" ? withIssues : replacedRows).filter((x) => !q || x.title.toLowerCase().includes(q)).slice(0, 400);
  $("done-rows").innerHTML = rows.map((x) => xrow("done-rows", x.id, `<td class="pick"><input type="checkbox" value="${x.id}" ${keep.has(String(x.id)) ? "checked" : ""} aria-label="Select ${esc(x.title)}"></td>
    <td>${esc(x.title)} <span class="yr">${x.year || ""}</span> ${x.issues.map(ISSUE_TAG).join(" ")}${x.again ? ' <span class="tag" style="--c: var(--cyan)">replaced again</span>' : ""}${x.problem ? `<div class="why">${esc(x.problem)}</div>` : ""}</td>
    <td>${esc(x.when || "")}</td><td>${esc(x.old_quality || "")}</td><td>${qb(x.before_badge, "Before: not recorded (replaced before 1.5)")}<span class="arrow">→</span>${qb(x.after_badge)}</td>
    <td class="r">${size(x.old_size)}</td><td class="r">${size(x.old_size - x.freed)}</td>
    <td class="r">${x.freed < 0 ? `<span class="grew" title="A tiny file swapped for a bigger one">+${size(-x.freed)}</span>` : size(x.freed)}</td>`)).join("")
    || `<tr><td colspan="8" class="empty">${doneShow === "issues" ? "Nothing needs attention." : "Nothing replaced yet."}</td></tr>`;
  updateDoneSel();
}
function updateDoneSel() {
  const n = document.querySelectorAll("#done-rows input:checked").length;
  $("done-again").disabled = $("done-suggest").disabled = !n;
  $("done-again").textContent = n ? `Replace ${n} again` : "Replace again";
  $("done-suggest").textContent = n ? `Find replacements for ${n}` : "Find replacements";
}
$("done-rows").addEventListener("change", updateDoneSel);
$("done-all").addEventListener("change", (e) => { document.querySelectorAll("#done-rows input").forEach((i) => (i.checked = e.target.checked)); updateDoneSel(); });
$("done-q").addEventListener("input", renderReplaced);
document.querySelector("#tab-replaced .chips").addEventListener("click", (e) => {
  const b = e.target.closest("[data-show]"); if (!b) return; doneShow = b.dataset.show;
  document.querySelectorAll("#tab-replaced [data-show]").forEach((x) => x.setAttribute("aria-pressed", x === b)); renderReplaced();
});
const pickedDone = () => [...document.querySelectorAll("#done-rows input:checked")].map((i) => +i.value);
$("done-again").addEventListener("click", () => act(async () => { const r = await api("POST", "/api/requeue", { ids: pickedDone() }); await loadReplaced(true); return r; }));
$("done-suggest").addEventListener("click", () => act(() => api("POST", "/api/suggest", { ids: pickedDone() })));

// ── oversized films ───────────────────────────────────────────────────────
const libGroups = new Set(["remux", "big4k", "remux4k", "big1080", "lowq"]);
$("lib-groups").innerHTML = Object.entries(GROUPS).map(([k, [label, col]]) =>
  `<button type="button" class="chip" data-g="${k}" style="--c:${col}" aria-pressed="${libGroups.has(k)}">${label}</button>`).join("");
$("lib-groups").addEventListener("click", (e) => {
  const b = e.target.closest(".chip"); if (!b) return;
  libGroups.has(b.dataset.g) ? libGroups.delete(b.dataset.g) : libGroups.add(b.dataset.g);
  b.setAttribute("aria-pressed", libGroups.has(b.dataset.g)); libShown = 200; renderFilms();
});
const RANK = { HIGH: 3, MEDIUM: 2, LOW: 1 };
async function loadFilms() {
  try { films = await api("GET", "/api/films"); films.forEach((f) => (f.badge_rank = RANK[f.badge] || 0)); renderFilms(); } catch (e) { toast(e.message, "err"); }
}
function shownFilms() {
  const q = $("lib-q").value.trim().toLowerCase(), untried = $("lib-untried").checked;
  const [k, dir] = libSort;
  const showIgnored = $("lib-ignored").checked;
  return films.filter((f) => libGroups.has(f.group) && (!q || f.title.toLowerCase().includes(q)) && (!untried || !f.refetch) && (showIgnored || !f.ignored))
    .sort((a, b) => (a[k] > b[k] ? 1 : a[k] < b[k] ? -1 : 0) * dir);
}
function renderFilms() {
  const v = shownFilms();
  $("lib-rows").innerHTML = v.slice(0, libShown).map((f) => {
    const [label, col] = GROUPS[f.group] || [f.group, "var(--line)"], canPick = !f.refetch;
    const link = `${(status?.radarr_link || "").replace(/\/$/, "")}/movie/${encodeURIComponent(f.slug)}`;
    return xrow("lib-rows", f.id, `<td class="pick">${canPick ? `<input type="checkbox" value="${f.id}" data-ignored="${f.ignored ? 1 : ""}" aria-label="Select ${esc(f.title)}">` : ""}</td>
      <td><a href="${esc(link)}" target="_blank" rel="noopener">${esc(f.title)}</a><span class="yr">${f.year || ""}</span>${f.release_group ? `<span class="rg">${esc(f.release_group)}</span>` : ""}</td>
      <td><span class="tag" style="--c:${col}">${label}</span></td><td>${esc(f.quality)} · ${esc(f.video_codec)}${f.dynamic_range && f.dynamic_range !== "SDR" ? " · " + esc(f.dynamic_range) : ""}${f.audio ? ` · ${esc(audioLabel(f.audio))} ${chans(f.channels)}${f.atmos ? " Atmos" : ""}` : ""}</td>
      <td>${qb(f.badge, "Rated at the next rescan", f.video_tier ? `Picture ${f.video_tier.toLowerCase()} (${f.picture_mbps} Mbps in x265 terms), audio ${(f.audio_tier || "unknown").toLowerCase()}` : "")}</td>
      <td class="r">${f.size_gb.toFixed(1)} GB</td><td class="r">${f.mb_per_min}</td>
      <td class="r">${f.group === "tiny" ? `<span class="grew" title="Needs a bigger file">+${(-f.saving_gb).toFixed(1)} GB</span>` : f.saving_gb > 0 ? f.saving_gb.toFixed(1) + " GB" : "–"}</td>
      <td>${f.refetch ? `<span class="tag">${esc(f.refetch)}</span>` : ""}${f.ignored ? `<span class="tag" title="${esc(f.ignored)}">Ignored</span>` : ""}</td>`, f.ignored ? "ignored" : "");
  }).join("") || `<tr><td colspan="9" class="empty">No films match.</td></tr>`;
  const save = v.filter((f) => f.group !== "tiny").reduce((t, f) => t + Math.max(f.saving_gb, 0), 0);
  $("lib-count").textContent = `${v.length.toLocaleString()} films · about ${(save / 1000).toFixed(1)} TB to win`;
  $("lib-more").hidden = v.length <= libShown;
  document.querySelectorAll("#tab-library th[data-sort]").forEach((th) => { th.classList.toggle("sorted", th.dataset.sort === libSort[0]); th.classList.toggle("asc", th.dataset.sort === libSort[0] && libSort[1] > 0); });
  updateLibQueue();
}
function updateLibQueue() {
  const picked = [...document.querySelectorAll("#lib-rows input:checked")], n = picked.length, allIgnored = n && picked.every((i) => i.dataset.ignored);
  $("lib-queue").disabled = !n; $("lib-queue").textContent = n ? `Replace ${n} selected` : "Replace selected";
  $("lib-ignore").disabled = !n; $("lib-ignore").textContent = n ? `${allIgnored ? "Stop ignoring" : "Ignore"} ${n} selected` : "Ignore selected";
  $("lib-ignore").dataset.mode = allIgnored ? "off" : "on";
  $("lib-suggest").disabled = !n; $("lib-suggest").textContent = n ? `Find replacements for ${n}` : "Find replacements";
}
$("lib-rows").addEventListener("change", updateLibQueue);
$("lib-all").addEventListener("change", (e) => { document.querySelectorAll("#lib-rows input").forEach((i) => (i.checked = e.target.checked)); updateLibQueue(); });
$("lib-q").addEventListener("input", () => { libShown = 200; renderFilms(); });
$("lib-untried").addEventListener("change", renderFilms);
$("lib-ignored").addEventListener("change", () => { libShown = 200; renderFilms(); });
$("lib-ignore").addEventListener("click", () => act(async () => {
  const ids = [...document.querySelectorAll("#lib-rows input:checked")].map((i) => +i.value);
  const r = await api("POST", "/api/ignore", { ids, ignore: $("lib-ignore").dataset.mode !== "off" }); await loadFilms(); return r;
}));
$("lib-suggest").addEventListener("click", () => act(() => api("POST", "/api/suggest", { ids: [...document.querySelectorAll("#lib-rows input:checked")].map((i) => +i.value) })));
$("lib-more").addEventListener("click", () => { libShown += 200; renderFilms(); });
document.querySelectorAll("#tab-library th[data-sort]").forEach((th) => th.addEventListener("click", () => {
  const k = th.dataset.sort; libSort = [k, libSort[0] === k ? -libSort[1] : (k === "title" || k === "group" || k === "quality" ? 1 : -1)]; renderFilms();
}));
$("lib-queue").addEventListener("click", () => act(async () => {
  const ids = [...document.querySelectorAll("#lib-rows input:checked")].map((i) => +i.value);
  const r = await api("POST", "/api/queue", { ids }); await loadFilms(); return r;
}));

// ── actions ───────────────────────────────────────────────────────────────
$("queue-form").addEventListener("submit", (e) => { e.preventDefault(); act(() => api("POST", "/api/queue", { count: +$("queue-n").value })); });
$("queue-clear").addEventListener("click", () => act(() => api("POST", "/api/queue/clear")));
$("pause").addEventListener("click", () => act(() => api("POST", status?.paused ? "/api/resume" : "/api/pause")));
$("mode").addEventListener("click", () => act(async () => {
  const on = !status?.auto;
  await api("PATCH", "/api/settings", { queue: { auto: on } });
  return { message: on ? "Auto-queue on: downloads are kept topped up within your limits." : "Manual queue: only what you queue is searched for." };
}));
$("scan").addEventListener("click", () => { $("scan-confirm").returnValue = "cancel"; $("scan-confirm").showModal(); });
$("scan-confirm").addEventListener("close", () => { if ($("scan-confirm").returnValue === "scan") act(() => api("POST", "/api/scan")); });

// ── settings ──────────────────────────────────────────────────────────────
async function loadSettings() {
  settings = (await api("GET", "/api/settings")).values;
  const c = $("conn"), conn = settings.connections;
  c.radarr_url.value = conn.radarr_url; c.radarr_url.placeholder = settings.env.radarr_url;
  $("conn-url-note").textContent = conn.radarr_url ? "" : `Blank uses ${settings.env.radarr_url} (from the container's .env)`;
  c.radarr_link.value = conn.radarr_link || "";
  c.refetcharr_url.value = conn.refetcharr_url || "";
  $("conn-link-note").textContent = conn.radarr_link ? "" : `Blank uses the address above (${conn.radarr_url || settings.env.radarr_url})`;
  c.radarr_api_key.value = "";
  c.radarr_api_key.placeholder = settings.secrets_set.radarr_api_key ? "Saved: leave blank to keep it" : "Paste Radarr's API key";
  $("conn-key-note").textContent = settings.secrets_set.radarr_api_key && !settings.connections.radarr_api_key && settings.env.radarr_api_key ? "Currently from the container's .env" : "";
  loadSetup();
  const f = $("settings"), q = settings.queue;
  f.auto.checked = q.auto; f.max_active.value = q.max_active; f.max_per_day.value = q.max_per_day;
  f.w_enabled.checked = q.window.enabled; f.w_start.value = q.window.start; f.w_end.value = q.window.end;
  $("set-days").innerHTML = DAYS.map((d, i) => `<button type="button" class="chip" data-d="${i}" aria-pressed="${q.window.days.includes(i)}">${d}</button>`).join("");
  f.every_days.value = settings.scan.every_days;
  fillTiming(settings.timing);
  const sec = $("security");
  sec.username.value = settings.auth.username; sec.password.value = sec.confirm.value = sec.current.value = "";
  $("apikey").textContent = "••••••••••••••••••••••••••••••••"; $("key-show").textContent = "Show";
  for (const id of ["conn", "settings", "timing", "security"]) clean(id);
}
$("security").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  if (f.password.value !== f.confirm.value) { say("sec-saved", "The new passwords don't match.", "err"); return; }
  try {
    await api("POST", "/api/auth/change", { current: f.current.value, username: f.username.value, password: f.password.value });
    f.password.value = f.confirm.value = f.current.value = "";
    say("sec-saved", "Saved. Other browsers are signed out."); clean("security");
  } catch (err) { say("sec-saved", err.message, "err"); }
});
$("key-show").addEventListener("click", async () => {
  if ($("key-show").textContent === "Hide") { $("apikey").textContent = "••••••••••••••••••••••••••••••••"; $("key-show").textContent = "Show"; return; }
  try { $("apikey").textContent = (await api("GET", "/api/auth/key")).api_key; $("key-show").textContent = "Hide"; } catch (err) { say("key-msg", err.message, "err"); }
});
$("key-new").addEventListener("click", async () => {
  if (!confirm("Make a new API key? Scripts using the old one stop working (the terminal UI picks up the new one by itself).")) return;
  try { $("apikey").textContent = (await api("POST", "/api/auth/key")).api_key; $("key-show").textContent = "Hide"; say("key-msg", "New key made. Fix the webhook under Settings → Radarr so Radarr uses it."); }
  catch (err) { say("key-msg", err.message, "err"); }
});
$("logout").addEventListener("click", async () => { await fetch("/api/auth/logout", { method: "POST" }); location.replace("/login"); });
const TIMINGS = ["missing_minutes", "import_wait_minutes", "no_video_hours", "finish_warn_minutes", "library_minutes", "setup_minutes"];
function fillTiming(t) { const f = $("timing"); for (const k of TIMINGS) f[k].value = t[k]; }
$("timing").addEventListener("submit", async (e) => {
  e.preventDefault();
  const timing = Object.fromEntries(TIMINGS.map((k) => [k, Number(e.target[k].value)]));
  try { await api("PATCH", "/api/settings", { timing }); say("timing-saved", "Saved."); clean("timing"); }
  catch (err) { say("timing-saved", err.message, "err"); }
});
$("timing-reset").addEventListener("click", async () => {
  const d = (await api("GET", "/api/settings")).defaults; fillTiming(d.timing); say("timing-saved", "Defaults filled in. Nothing changes until you save.", "info"); recheck("timing");
});
// ── Radarr connection and setup ──────────────────────────────────────────
const connBody = () => ({ radarr_url: $("conn").radarr_url.value.trim(), radarr_api_key: $("conn").radarr_api_key.value.trim() });
const connResult = (ok, msg) => say("conn-result", msg, ok === null ? "info" : ok ? "ok" : "err");
$("conn-test").addEventListener("click", async () => {
  connResult(null, "Testing…");
  try { const r = await api("POST", "/api/connections/test", connBody()); connResult(r.ok, r.message); } catch (e) { connResult(false, e.message); }
});
$("conn").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    const t = await api("POST", "/api/connections/test", connBody());
    if (!t.ok) return connResult(false, t.message + " (not saved)");
    await api("PATCH", "/api/settings", { connections: { ...connBody(), radarr_link: $("conn").radarr_link.value.trim(),
      refetcharr_url: $("conn").refetcharr_url.value.trim() || "http://refetcharr:8787" } });
    connResult(true, "Saved. " + t.message); await loadSettings(); refresh();
  } catch (err) { connResult(false, err.message); }
});
let setupChecks = [];
async function loadSetup(force) {
  try { setupChecks = (await api("GET", "/api/setup")).checks; } catch (e) { $("setup").innerHTML = `<li class="empty">${esc(e.message)}</li>`; return; }
  $("setup").innerHTML = setupChecks.map((c) => {
    const cls = c.ok ? "ok" : c.level, icon = c.ok ? "✓" : c.level === "warn" ? "!" : "✕";
    return `<li><span class="icon ${cls}" aria-label="${c.ok ? "OK" : "Needs attention"}">${icon}</span><b>${esc(c.label)}</b>
      ${c.fix ? `<button type="button" class="btn" data-fix="${c.id}">Fix</button>` : "<span></span>"}
      <span class="detail">${esc(c.detail)}</span>${c.fix ? `<span class="fixdesc">Fix: ${esc(c.fix)}</span>` : ""}</li>`;
  }).join("");
  $("setup-fix-all").hidden = setupChecks.filter((c) => c.fix).length < 2;
  if (force) refresh();
}
async function applyFix(ids) {
  try { const r = await api("POST", "/api/setup/fix", { ids }); toast(r.done.length ? r.done.join(". ") + "." : "Nothing needed changing."); await loadSetup(true); }
  catch (e) { toast(e.message, "err"); }
}
$("setup").addEventListener("click", (e) => { const b = e.target.closest("[data-fix]"); if (b) { b.disabled = true; applyFix([b.dataset.fix]); } });
$("setup-fix-all").addEventListener("click", () => applyFix(setupChecks.filter((c) => c.fix).map((c) => c.id)));
$("setup-refresh").addEventListener("click", () => loadSetup(true));
$("set-days").addEventListener("click", (e) => { const b = e.target.closest(".chip"); if (b) b.setAttribute("aria-pressed", b.getAttribute("aria-pressed") !== "true"); });
function settingsPatch() {
  const f = $("settings"), num = (n) => Number(f[n].value);
  return {
    queue: { auto: f.auto.checked, max_active: num("max_active"), max_per_day: num("max_per_day"),
      window: { enabled: f.w_enabled.checked, start: f.w_start.value || "00:00", end: f.w_end.value || "23:59",
        days: [...document.querySelectorAll("#set-days .chip")].filter((b) => b.getAttribute("aria-pressed") === "true").map((b) => +b.dataset.d) } },
    scan: { every_days: num("every_days") },
  };
}
$("settings").addEventListener("submit", async (e) => {
  e.preventDefault();
  try { await api("PATCH", "/api/settings", settingsPatch()); say("saved", "Saved."); clean("settings"); refresh(); }
  catch (err) { say("saved", err.message, "err"); }
});

// ── replacement rules ─────────────────────────────────────────────────────
const SIZE_MARKS = [["tiny", "Too small"], ["min", "Minimum"], ["preferred", "Preferred"], ["cap", "Maximum"]];
const SIZE_TOP = { 1080: 300, 2160: 500 };   // where the sliders end, unless a saved value is higher
const RES_LABEL = { 1080: "1080p", 2160: "4K" };
const gbh = (mbm) => `${(mbm * 60 / 1000).toFixed(1)} GB/h`;
const BOOLS = ["keep_hdr", "keep_surround", "keep_atmos", "keep_lossless", "keep_english", "untagged_is_english", "reject_dv_no_hdr10", "reject_negative_score", "prefer_higher_res", "allow_lower_res", "allow_usenet"];
const RANGES = {
  max_ratio: [(v) => `${v}%`, (r) => Math.round(r.max_ratio * 100), (v) => v / 100],
  min_runtime: [(v) => (v ? `${v} min` : "no limit")],
  min_seeders: [(v) => `${v} seeder${v === 1 ? "" : "s"}`],
  stall_warn_hours: [(v) => (v ? `${v} hour${v === 1 ? "" : "s"}` : "never")],
};
let catalog = { video: [], audio: [] }, ruleDefaults = null;
const codecLists = { video: [], audio: [] }, strict = { video: false, audio: false };

function sizeSlider(res, r) {
  const top = Math.max(SIZE_TOP[res], Math.ceil(r[`cap_${res}`] * 1.25 / 10) * 10);
  return `<div class="qs" data-res="${res}"><b>${RES_LABEL[res]}</b>
    <div class="qs-track">${SIZE_MARKS.map(([k]) => `<input type="range" class="m-${k}" data-k="${k}" min="0" max="${top}" value="${r[`${k}_${res}`]}" aria-label="${RES_LABEL[res]} ${k}">`).join("")}</div>
    <div class="qs-vals">${SIZE_MARKS.map(([k, label]) => `<label class="m-${k}"><span>${label}</span>
      <input type="number" data-n="${k}" min="0" max="${top}" value="${r[`${k}_${res}`]}"><small data-g="${k}"></small></label>`).join("")}</div></div>`;
}
function paintSlider(box) {
  const inputs = [...box.querySelectorAll("input[type=range]")], top = +inputs[0].max;
  const at = (i) => `${(100 * +inputs[i].value) / top}%`;
  box.querySelector(".qs-track").style.setProperty("--a", at(0));
  box.querySelector(".qs-track").style.setProperty("--b", at(1));
  box.querySelector(".qs-track").style.setProperty("--c", at(3));
  for (const i of inputs) {
    box.querySelector(`[data-n=${i.dataset.k}]`).value = i.value;
    box.querySelector(`[data-g=${i.dataset.k}]`).textContent = gbh(+i.value);
  }
}
function setMark(box, k, v) {   // a marker can't pass its neighbours
  const inputs = [...box.querySelectorAll("input[type=range]")], i = inputs.findIndex((x) => x.dataset.k === k);
  const lo = i > 0 ? +inputs[i - 1].value : 0, hi = i < inputs.length - 1 ? +inputs[i + 1].value : +inputs[i].max;
  inputs[i].value = Math.min(Math.max(v, lo), hi);
  inputs.forEach((x) => x.classList.toggle("last", x === inputs[i]));
  paintSlider(box);
}
$("sizes").addEventListener("input", (e) => {
  const box = e.target.closest(".qs"), k = e.target.dataset.k || e.target.dataset.n;
  if (box && k && e.target.value !== "") setMark(box, k, +e.target.value);
});

function renderList(kind) {
  const list = codecLists[kind], known = catalog[kind], label = (id) => known.find((c) => c.id === id.toLowerCase())?.label ?? id;
  const rest = known.filter((c) => !list.some((x) => x.toLowerCase() === c.id));
  $(`list-${kind}`).innerHTML = `
    <p class="muted">${list.length ? "Top of the list is tried first. Drag to reorder." : "No preference: every codec counts the same. Add some below, most preferred first."}</p>
    <ol class="rank" data-kind="${kind}">${list.map((id, i) => `<li draggable="true" data-i="${i}"><span class="grip" aria-hidden="true">⠿</span><b>${i + 1}</b><span class="nm">${esc(label(id))}</span>
      <button type="button" class="ic" data-up="${i}" ${i ? "" : "disabled"} aria-label="Move ${esc(label(id))} up">↑</button>
      <button type="button" class="ic" data-down="${i}" ${i < list.length - 1 ? "" : "disabled"} aria-label="Move ${esc(label(id))} down">↓</button>
      <button type="button" class="ic" data-del="${i}" aria-label="Remove ${esc(label(id))}">✕</button></li>`).join("")}</ol>
    <div class="addrow"><div class="chips">${rest.map((c) => `<button type="button" class="chip" data-add="${esc(c.id)}">+ ${esc(c.label)}</button>`).join("")}</div>
      <span class="inline"><input type="text" class="other" maxlength="40" placeholder="Something else in the title…" aria-label="Add another ${kind} codec"><button type="button" class="btn" data-other>Add</button></span></div>
    <label class="switch"><input type="checkbox" data-strict ${strict[kind] ? "checked" : ""} ${list.length ? "" : "disabled"}><span>Only accept these <small>otherwise anything else just ranks last${kind === "audio" ? ". Many release titles don't name their audio at all, and those would be refused" : ""}</small></span></label>`;
}
for (const kind of ["video", "audio"]) {
  const box = $(`list-${kind}`);
  const move = (from, to) => { const l = codecLists[kind]; l.splice(to, 0, l.splice(from, 1)[0]); renderList(kind); };
  const addOne = (v) => { if (v && !codecLists[kind].some((x) => x.toLowerCase() === v.toLowerCase())) codecLists[kind].push(v); renderList(kind); };
  box.addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b) return;
    if (b.dataset.up) move(+b.dataset.up, +b.dataset.up - 1);
    else if (b.dataset.down) move(+b.dataset.down, +b.dataset.down + 1);
    else if (b.dataset.del) { codecLists[kind].splice(+b.dataset.del, 1); renderList(kind); }
    else if (b.dataset.add) addOne(b.dataset.add);
    else if ("other" in b.dataset) addOne(box.querySelector(".other").value.trim());
  });
  box.addEventListener("keydown", (e) => { if (e.key === "Enter" && e.target.matches(".other")) { e.preventDefault(); addOne(e.target.value.trim()); } });
  box.addEventListener("change", (e) => { if (e.target.matches("[data-strict]")) strict[kind] = e.target.checked; });
  let from = null;
  box.addEventListener("dragstart", (e) => { const li = e.target.closest("li"); if (li) { from = +li.dataset.i; e.dataTransfer.effectAllowed = "move"; li.classList.add("dragging"); } });
  box.addEventListener("dragover", (e) => { if (from !== null && e.target.closest("li")) e.preventDefault(); });
  box.addEventListener("drop", (e) => { const li = e.target.closest("li"); if (li && from !== null) { e.preventDefault(); move(from, +li.dataset.i); from = null; } });
  box.addEventListener("dragend", () => { from = null; renderList(kind); });
}
function paintRange(name) { const f = $("rules"); f[`${name}_o`].value = RANGES[name][0](+f[name].value); }
$("rules").addEventListener("input", (e) => { if (RANGES[e.target.name]) paintRange(e.target.name); });

const GROUP_KEYS = ["remux", "big4k", "remux4k", "big1080", "tiny", "lowq"];
const GROUP_NOTE = { tiny: "x265 files under Too small, swapped for a bigger release (up to Maximum). These use more space, not less, and come after the savings",
  lowq: "files whose picture rates LOW for their resolution (Settings → Quality), swapped for a better release of any size up to Maximum" };
const igLists = { file_groups: [], release_groups: [] };
function renderTags(k) {
  $(`ig-${k}`).innerHTML = `<div class="chips">${igLists[k].map((g, i) => `<span class="chip on">${esc(g)}<button type="button" class="x" data-del="${i}" aria-label="Remove ${esc(g)}">✕</button></span>`).join("")
    || `<span class="muted small">None.</span>`}</div>
    <span class="inline"><input type="text" class="other" maxlength="60" placeholder="Release group, e.g. nikt0" aria-label="Add a release group"><button type="button" class="btn" data-add>Add</button></span>`;
}
for (const k of Object.keys(igLists)) {
  const box = $(`ig-${k}`);
  const add = () => { const v = box.querySelector(".other").value.trim(); if (v && !igLists[k].some((x) => x.toLowerCase() === v.toLowerCase())) igLists[k].push(v); renderTags(k); box.querySelector(".other").focus(); };
  box.addEventListener("click", (e) => { const b = e.target.closest("button"); if (!b) return; if (b.dataset.del) { igLists[k].splice(+b.dataset.del, 1); renderTags(k); } else if ("add" in b.dataset) add(); });
  box.addEventListener("keydown", (e) => { if (e.key === "Enter" && e.target.matches(".other")) { e.preventDefault(); add(); } });
}
async function renderIgnoredFilms(ids) {
  if (!ids.length) { $("ig-films").innerHTML = `<p class="muted small">None. Pick films in Oversized films and press Ignore selected.</p>`; return; }
  if (!films.length) { try { films = await api("GET", "/api/films"); } catch { /* names fall back to ids */ } }
  const name = (id) => { const f = films.find((x) => x.id === id); return f ? `${esc(f.title)} <span class="yr">${f.year || ""}</span>` : `Radarr film ${id}`; };
  $("ig-films").innerHTML = `<ul class="iglist">${ids.map((id) => `<li><span>${name(id)}</span><button type="button" class="btn ghost" data-unignore="${id}">Stop ignoring</button></li>`).join("")}</ul>`;
}
$("ig-films").addEventListener("click", (e) => { const b = e.target.closest("[data-unignore]"); if (b) act(async () => { const r = await api("POST", "/api/ignore", { ids: [+b.dataset.unignore], ignore: false }); loadRules(); return r; }); });

function fillRules(r, groups, ig) {
  const f = $("rules");
  $("set-groups").innerHTML = GROUP_KEYS.map((k) =>
    `<label class="switch"><input type="checkbox" name="g_${k}" ${groups[k] ? "checked" : ""}><span>${GROUPS[k][0]}${GROUP_NOTE[k] ? ` <small>${GROUP_NOTE[k]}</small>` : ""}</span></label>`).join("");
  if (ig) { for (const k of Object.keys(igLists)) { igLists[k] = [...ig[k]]; renderTags(k); } renderIgnoredFilms(ig.films); }
  $("sizes").innerHTML = sizeSlider(1080, r) + sizeSlider(2160, r);
  document.querySelectorAll("#sizes .qs").forEach(paintSlider);
  for (const [name, [, read]] of Object.entries(RANGES)) { f[name].value = read ? read(r) : r[name]; paintRange(name); }
  for (const k of BOOLS) f[k].checked = r[k];
  for (const kind of ["video", "audio"]) { codecLists[kind] = [...r[`${kind}_codecs`]]; strict[kind] = r[`${kind}_strict`]; renderList(kind); }
}
async function loadRules() {
  try {
    const d = await api("GET", "/api/settings");
    catalog = d.catalog; ruleDefaults = d.defaults;
    fillRules(d.values.rules, d.values.queue.groups, d.values.ignore); clean("rules");
  } catch (e) { toast(e.message, "err"); }
}
$("rules-reset").addEventListener("click", () => { fillRules(ruleDefaults.rules, ruleDefaults.queue.groups); say("rules-saved", "Defaults filled in (the leave-alone lists are kept). Nothing changes until you save.", "info"); recheck("rules"); });
function rulesPatch() {
  const f = $("rules"), r = {};
  for (const [name, [, , write]] of Object.entries(RANGES)) r[name] = write ? write(+f[name].value) : +f[name].value;
  for (const k of BOOLS) r[k] = f[k].checked;
  document.querySelectorAll("#sizes .qs").forEach((box) => box.querySelectorAll("input[type=range]").forEach((i) => { r[`${i.dataset.k}_${box.dataset.res}`] = +i.value; }));
  for (const kind of ["video", "audio"]) { r[`${kind}_codecs`] = codecLists[kind]; r[`${kind}_strict`] = strict[kind] && codecLists[kind].length > 0; }
  const groups = Object.fromEntries(GROUP_KEYS.map((k) => [k, f[`g_${k}`]?.checked ?? false]));
  return { rules: r, queue: { groups }, ignore: { file_groups: [...igLists.file_groups], release_groups: [...igLists.release_groups] } };
}
$("rules").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    await api("PATCH", "/api/settings", rulesPatch());
    say("rules-saved", "Saved. New searches use these now; the Oversized list follows at the next rescan."); clean("rules");
    refresh();
  } catch (err) { say("rules-saved", err.message, "err"); }
});

// ── 1.5.0: ratings, a film's details (click a row), events, the Quality page ──
const LOADING = `<div class="dd"><p class="note">Loading…</p></div>`;
const expanded = new Set(), detailCache = new Map();
const AUDIO_NAMES = { truehd: "TrueHD", dtshd: "DTS-HD", flac: "FLAC", lpcm: "PCM", dts: "DTS", eac3: "DD+", ac3: "DD", aac: "AAC", opus: "Opus", mp3: "MP3" };
const audioLabel = (a) => AUDIO_NAMES[String(a || "").toLowerCase()] || a || "";
const chans = (c) => (c ? (+c).toFixed(1) : "");
function qb(badge, missing = "", title = "") {
  return badge ? `<span class="qb ${badge}" title="${esc(title || badge)}">${badge}</span>` : `<span class="qb none" title="${esc(missing || "Not rated")}">–</span>`;
}
// a table row that opens its film's details underneath when clicked
function xrow(box, id, cells, cls = "") {
  const open = expanded.has(`${box}:${id}`), cols = $(box)?.dataset.cols || 6;
  return `<tr class="row-x ${cls} ${open ? "open" : ""}" data-id="${id}">${cells}</tr>`
    + (open ? `<tr class="detail" data-for="${id}"><td colspan="${cols}">${detailCache.get(+id) || LOADING}</td></tr>` : "");
}
async function loadDetail(id) {
  try { detailCache.set(id, renderDetail(await api("GET", `/api/films/${id}`))); }
  catch (e) { detailCache.set(id, `<div class="dd"><p class="problem">${esc(e.message)}</p></div>`); }
  document.querySelectorAll(`tr.detail[data-for="${id}"] td, .dl-detail[data-for="${id}"]`).forEach((el) => (el.innerHTML = detailCache.get(id)));
}
function toggleDetail(box, el) {
  const id = +el.dataset.id, key = `${box}:${id}`, next = el.nextElementSibling;
  if (expanded.has(key)) {
    expanded.delete(key); el.classList.remove("open");
    if (next && (next.classList.contains("detail") || next.classList.contains("dl-detail"))) next.remove();
    return;
  }
  expanded.add(key); el.classList.add("open");
  el.insertAdjacentHTML("afterend", el.tagName === "TR"
    ? `<tr class="detail" data-for="${id}"><td colspan="${$(box).dataset.cols || 6}">${LOADING}</td></tr>` : `<div class="dl-detail" data-for="${id}">${LOADING}</div>`);
  loadDetail(id);   // always fresh when opened; re-renders reuse it while open
}
for (const box of ["lib-rows", "fail-rows", "done-rows", "active"]) {
  $(box).addEventListener("click", (e) => {
    if (e.target.closest("input, a, button, label, .dd")) return;
    const el = e.target.closest(".row-x"); if (el) toggleDetail(box, el);
  });
}

const DETAIL_ROWS = [
  ["Rating", (x) => (x.badge || x.video_tier ? `${qb(x.badge)} <span class="muted small">${x.video_tier ? `picture ${x.video_tier.toLowerCase()}` : ""}${x.audio_tier ? ` · audio ${x.audio_tier.toLowerCase()}` : ""}</span>` : "")],
  ["Quality", (x) => esc(x.quality || "")],
  ["Size", (x) => (x.size ? `${size(x.size)}${x.mb_per_min ? ` · ${x.mb_per_min} MB/min` : ""}` : "")],
  ["Video", (x) => esc([x.video_codec, x.bit_depth ? `${x.bit_depth}-bit` : ""].filter(Boolean).join(" · "))],
  ["Picture", (x) => (x.picture_mbps ? `${x.picture_mbps} Mbps in x265 terms` : "")],
  ["HDR", (x) => (x.resolution || x.hdr !== undefined ? esc(x.hdr || "SDR") : ""), (b, a) => b.hdr && a.resolution && !a.hdr],
  ["Audio", (x) => esc([audioLabel(x.audio), chans(x.channels)].filter(Boolean).join(" ") + (x.audio_kbps && !x.lossless ? ` · ${x.audio_kbps} kbps` : "")),
    (b, a) => b.channels >= 5 && a.channels > 0 && a.channels < 5],
  ["Atmos", (x) => (x.resolution ? (x.atmos ? "yes" : "no") : ""), (b, a) => b.atmos && a.resolution && !a.atmos],
  ["Lossless audio", (x) => (x.resolution ? (x.lossless ? "yes" : "no") : ""), (b, a) => b.lossless && a.resolution && !a.lossless],
  ["Release", (x) => ((x.title || x.name) ? `<span class="name">${esc(x.title || x.name)}</span>` : "") + (x.release_group ? ` <span class="rg">${esc(x.release_group)}</span>` : "")],
  ["Seeders · score", (x) => (x.seeders != null ? `${x.seeders} seeders · format score ${x.score ?? 0}${x.indexer ? ` · ${esc(x.indexer)}` : ""}` : "")],
];
function renderDetail(d) {
  const st = d.state || {}, dec = st.decision || {}, legacy = st.status && !st.decision;
  const link = d.slug ? `${(status?.radarr_link || "").replace(/\/$/, "")}/movie/${encodeURIComponent(d.slug)}` : "";
  const done = st.status === "replaced";
  // until a film is replaced, the file on disk is the original
  const before = dec.before || (legacy && done ? { quality: st.old_quality, size: st.old_size } : d.now || { quality: st.old_quality, size: st.old_size });
  const chosen = dec.after || (st.new_title ? { title: st.new_title, size: st.new_size } : null);
  const now = done ? (st.after_file || d.now) : null;
  const cols = [[st.status ? "Before" : "Current file", before]];
  if (chosen) cols.push(["Chosen release" + (dec.after ? " <small>(from its title)</small>" : ""), chosen]);
  if (now) cols.push(["Now on disk", now]);
  const rows = DETAIL_ROWS.map(([label, fmt, lost]) => {
    const cells = cols.map(([, x], i) => {
      const v = fmt(x || {}) || (i ? "" : "");
      const isLost = i > 0 && lost && x && lost(before, x);
      return `<td class="${isLost ? "lost" : ""} ${x?.estimated ? "est" : ""}">${v || '<span class="muted">–</span>'}</td>`;
    });
    return cells.some((c) => !c.includes('class="muted">–')) ? `<tr><td>${label}</td>${cells.join("")}</tr>` : "";
  }).join("");
  const need = Object.entries(d.needs || {}).filter(([, v]) => v).map(([k]) => ({ hdr: "HDR", surround: "surround sound", atmos: "Atmos", lossless: "lossless audio" }[k]));
  const rej = dec.rejected || [], split = (n) => { const m = /^(.*?): (.*)$/.exec(n); return m ? `<b>${esc(m[1])}</b>: ${esc(m[2])}` : esc(n); };
  return `<div class="dd">
    <div class="dd-head"><b>${esc(d.title || "")} <span class="yr">${d.year || ""}</span></b>
      ${st.status ? `<span class="tag">${esc(st.status)}</span>` : `<span class="tag">not tried yet</span>`}
      ${st.grabbed ? `<span class="muted small">${st.status === "no release" ? "searched" : "grabbed"} ${esc(st.grabbed)}</span>` : ""}
      ${link ? `<a href="${esc(link)}" target="_blank" rel="noopener">Open in Radarr ↗</a>` : ""}</div>
    ${dec.flagged || d.flagged_now ? `<p class="dd-flag"><b>Flagged</b>${esc(dec.flagged || d.flagged_now)}</p>` : ""}
    ${st.problem ? `<p class="problem">${esc(st.problem)}</p>` : ""}
    <div class="cmpbox"><table class="cmp"><thead><tr><th></th>${cols.map(([h]) => `<th>${h}</th>`).join("")}</tr></thead><tbody>${rows}</tbody></table></div>
    ${need.length ? `<p class="note">A replacement must keep: <b>${need.join(", ")}</b> (checked on the title, then on the downloaded file).</p>` : ""}
    ${dec.why || rej.length ? `<div class="dd-cols">
      ${dec.why ? `<div><h4>Why this release</h4><ul>${dec.why.map((w) => `<li>${esc(w)}</li>`).join("")}</ul>
        ${dec.runners_up?.length ? `<h4 style="margin-top:10px">Next best</h4><ul class="rej">${dec.runners_up.map((r) => `<li><b>${esc(r.title)}</b> · ${size(r.size)} · ${r.seeders ?? "?"} seeders</li>`).join("")}</ul>` : ""}</div>` : ""}
      ${rej.length ? `<div><h4>Turned down (${rej.length}${dec.releases ? ` of ${dec.releases}` : ""})</h4><ul class="rej">${rej.slice(0, 8).map((n) => `<li>${split(n)}</li>`).join("")}</ul>
        ${rej.length > 8 ? `<details><summary class="muted small">${rej.length - 8} more</summary><ul class="rej">${rej.slice(8).map((n) => `<li>${split(n)}</li>`).join("")}</ul></details>` : ""}</div>` : ""}
    </div>` : ""}
    <div class="dd-actions">${PENDING.includes(st.status) ? "" : `<button type="button" class="btn" data-suggest="${d.id}">Find replacements</button>`}
      ${done ? `<button type="button" class="btn" data-requeue="${d.id}">Replace again</button>` : ""}</div>
    ${legacy ? `<p class="note">${done ? "Replaced" : "Tried"} before Refetcharr 1.5, which didn't record ${done ? "the original file's details or " : ""}why each release was chosen or turned down.${done ? "" : " Retry it to see the reasons."}</p>` : ""}
  </div>`;
}

// ── events ────────────────────────────────────────────────────────────────
let events = [];
const evKinds = new Set();
async function loadEvents() {
  try { events = (await api("GET", "/api/activity?n=2000")).events; } catch (e) { toast(e.message, "err"); return; }
  const kinds = [...new Set(events.map((x) => x.kind))].sort();
  if (!evKinds.size) kinds.forEach((k) => evKinds.add(k));
  $("ev-kinds").innerHTML = kinds.map((k) => `<button type="button" class="chip" data-k="${esc(k)}" aria-pressed="${evKinds.has(k)}">${esc(k)}</button>`).join("");
  renderEvents();
}
function renderEvents() {
  const q = $("ev-q").value.trim().toLowerCase();
  const v = events.filter((x) => evKinds.has(x.kind) && (!q || x.text.toLowerCase().includes(q)));
  $("ev-rows").innerHTML = v.slice(0, 1000).map((x) => `<tr><td>${esc(x.time)}</td><td><span class="kind ${esc(x.kind)}">${esc(x.kind)}</span></td><td>${esc(x.text)}</td></tr>`).join("")
    || `<tr><td colspan="3" class="empty">Nothing matches.</td></tr>`;
  $("ev-count").textContent = `${v.length.toLocaleString()} of ${events.length.toLocaleString()}`;
}
$("ev-q").addEventListener("input", renderEvents);
$("ev-kinds").addEventListener("click", (e) => {
  const b = e.target.closest(".chip"); if (!b) return;
  evKinds.has(b.dataset.k) ? evKinds.delete(b.dataset.k) : evKinds.add(b.dataset.k);
  b.setAttribute("aria-pressed", evKinds.has(b.dataset.k)); renderEvents();
});

// ── the Quality page ──────────────────────────────────────────────────────
const Q_RES = [["sd", "SD", 6, 0.1, "DVD and other standard definition"], ["720", "720p", 12, 0.25, "720p HD"],
  ["1080", "1080p", 24, 0.5, "1080p Full HD"], ["2160", "4K", 60, 1, "4K Ultra HD"]];
const Q_CODECS = [["x265", "x265 / HEVC"], ["av1", "AV1"], ["vp9", "VP9"], ["x264", "x264 / AVC"], ["vc1", "VC-1"], ["mpeg2", "MPEG-2"], ["xvid", "XviD / DivX"]];
const MIN_PICTURE = [["off", "Off"], ["low", "Any"], ["medium", "MEDIUM or better"], ["high", "HIGH only"]];
let qv = null, qRes = "1080", qMin = "medium", qPrefer = "high", qDefaults = null;
const PREFER = [["high", "Smallest HIGH, else the best MEDIUM"], ["size", "Nearest the Preferred size"]];
function tierSlider(id, lo, hi, max, step, unit) {
  return `<div class="tt" id="${id}-tt"><input type="range" min="0" max="${max}" step="${step}" value="${lo}" data-i="0" aria-label="Top of LOW">
    <input type="range" min="0" max="${max}" step="${step}" value="${hi}" data-i="1" aria-label="Top of MEDIUM"></div>
    <div class="tt-labels"><span>LOW</span><span>MEDIUM</span><span>HIGH</span></div>
    <div class="tt-vals"><label>LOW below<input type="number" min="0" step="${step}" value="${lo}" data-n="0"> <small>${unit}</small></label>
      <label>HIGH from<input type="number" min="0" step="${step}" value="${hi}" data-n="1"> <small>${unit}</small></label></div>`;
}
function paintTier(box) {
  const [a, b] = box.querySelectorAll(".tt input"), max = +a.max;
  box.querySelector(".tt").style.setProperty("--a", `${(100 * a.value) / max}%`);
  box.querySelector(".tt").style.setProperty("--b", `${(100 * b.value) / max}%`);
  box.querySelector("[data-n='0']").value = a.value; box.querySelector("[data-n='1']").value = b.value;
}
function wireTier(box, onChange) {
  box.addEventListener("input", (e) => {
    const i = e.target.dataset.i ?? e.target.dataset.n; if (i === undefined || e.target.value === "") return;
    const [a, b] = box.querySelectorAll(".tt input"), v = +e.target.value;
    if (+i === 0) a.value = Math.min(v, +b.value - +a.step); else b.value = Math.max(v, +a.value + +a.step);
    a.classList.toggle("last", +i === 0); b.classList.toggle("last", +i === 1);
    paintTier(box); onChange(+a.value, +b.value);
  });
}
function renderQRes() {
  $("q-res").innerHTML = Q_RES.map(([k, label]) => `<button type="button" role="tab" data-r="${k}" aria-selected="${k === qRes}">${label}</button>`).join("");
  const [, , max, step, note] = Q_RES.find(([k]) => k === qRes), [lo, hi] = qv.video[qRes];
  $("q-res-note").textContent = `${note}: picture bitrate in Mbps, x265 terms.`;
  $("q-video").innerHTML = tierSlider("qv", lo, hi, Math.max(max, Math.ceil(hi * 1.25)), step, "Mbps");
  paintTier($("q-video")); paintExample();
}
function paintExample() {
  const [lo, hi] = qv.video[qRes], f = qv.codec_factor, label = Q_RES.find(([k]) => k === qRes)[1];
  const gb = (pic, codec) => ((pic * (f[codec] || 1) + 0.64) * 7200 / 8 / 1000).toFixed(1);   // 2 hours, with DD+ 5.1
  $("q-example").innerHTML = `A 2-hour ${label} film with DD+ 5.1 rates <b>MEDIUM</b> from about <b>${gb(lo, "x265")} GB</b> in x265 (${gb(lo, "x264")} GB in x264)
    and <b>HIGH</b> from about <b>${gb(hi, "x265")} GB</b> (${gb(hi, "x264")} GB in x264).`;
}
function fillQuality(q, minPicture, prefer) {
  qv = JSON.parse(JSON.stringify(q)); qMin = minPicture; qPrefer = prefer || "high";
  $("q-prefer").innerHTML = PREFER.map(([k, label]) => `<button type="button" class="chip" role="radio" data-p="${k}" aria-checked="${k === qPrefer}" aria-pressed="${k === qPrefer}">${label}</button>`).join("");
  renderQRes();
  $("q-audio").innerHTML = tierSlider("qa", qv.audio[0], qv.audio[1], 2000, 16, "kbps"); paintTier($("q-audio"));
  $("q-min").innerHTML = MIN_PICTURE.map(([k, label]) => `<button type="button" class="chip" role="radio" data-m="${k}" aria-checked="${k === qMin}" aria-pressed="${k === qMin}">${label}</button>`).join("");
  $("q-codecs").innerHTML = Q_CODECS.map(([k, label]) => `<label>${label}<input type="number" min="0.2" max="5" step="0.05" data-c="${k}" value="${qv.codec_factor[k]}"></label>`).join("");
  const f = $("quality"); f.video_weight.value = qv.video_weight; paintWeight();
}
function paintWeight() { const w = +$("quality").video_weight.value; $("quality").video_weight_o.value = `${w}% picture, ${100 - w}% audio`; }
async function loadQuality() {
  try { const d = await api("GET", "/api/settings"); qDefaults = d.defaults; fillQuality(d.values.quality, d.values.rules.min_picture, d.values.rules.prefer); clean("quality"); }
  catch (e) { toast(e.message, "err"); }
}
$("q-res").addEventListener("click", (e) => { const b = e.target.closest("[data-r]"); if (b) { qRes = b.dataset.r; renderQRes(); } });
wireTier($("q-video"), (lo, hi) => { qv.video[qRes] = [lo, hi]; paintExample(); });
wireTier($("q-audio"), (lo, hi) => { qv.audio = [lo, hi]; });
$("q-min").addEventListener("click", (e) => {
  const b = e.target.closest("[data-m]"); if (!b) return; qMin = b.dataset.m;
  $("q-min").querySelectorAll("[data-m]").forEach((x) => { x.setAttribute("aria-pressed", x === b); x.setAttribute("aria-checked", x === b); });
});
$("q-codecs").addEventListener("input", (e) => { if (e.target.dataset.c && e.target.value !== "") { qv.codec_factor[e.target.dataset.c] = +e.target.value; paintExample(); } });
$("quality").addEventListener("input", (e) => { if (e.target.name === "video_weight") { qv.video_weight = +e.target.value; paintWeight(); } });
$("quality-reset").addEventListener("click", () => { fillQuality(qDefaults.quality, qDefaults.rules.min_picture, qDefaults.rules.prefer); say("quality-saved", "Defaults filled in. Nothing changes until you save.", "info"); recheck("quality"); });
$("quality").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    await api("PATCH", "/api/settings", { quality: qv, rules: { min_picture: qMin, prefer: qPrefer } });
    say("quality-saved", "Saved. Searches use it now, and the ratings follow straight away."); clean("quality");
  } catch (err) { say("quality-saved", err.message, "err"); }
});

// ── 1.6.0: suggestions (find replacements, pick one), actions in a film's details ──
const PENDING = ["grabbed", "downloading", "importing"];
document.addEventListener("click", (e) => {
  const s = e.target.closest("[data-suggest]"), r = e.target.closest("[data-requeue]");
  if (s) { s.disabled = true; act(() => api("POST", "/api/suggest", { ids: [+s.dataset.suggest] })); }
  if (r) { r.disabled = true; act(async () => { const x = await api("POST", "/api/requeue", { ids: [+r.dataset.requeue] }); loadReplaced(true); return x; }); }
});
let sugg = [], suggAt = 0;
async function loadSuggestions(quiet) {
  if (quiet && Date.now() - suggAt < 10000) return;
  suggAt = Date.now();
  try { sugg = await api("GET", "/api/suggestions"); renderSuggestions(); } catch (e) { if (!quiet) toast(e.message, "err"); }
}
const resLabel = (r) => (r >= 2000 ? "4K" : r ? `${r}p` : "");
const fileLine = (x) => `${qb(x.badge || x.video_tier)} ${size(x.size)}${x.resolution ? ` · ${resLabel(x.resolution)}` : ""}`;
const short = (x) => [x.video_codec, x.hdr || "SDR", [audioLabel(x.audio), chans(x.channels)].filter(Boolean).join(" ") + (x.atmos ? " Atmos" : "")]
  .filter(Boolean).map(esc).join(" · ");
const saveText = (n) => (n >= 0 ? `saves ${size(n)}` : `<span class="grew">+${size(-n)}</span>`);
function facts(x) {
  return [qb(x.video_tier, "", `Picture ${x.picture_mbps} Mbps in x265 terms`),
    `<span class="fact"><b>${size(x.size)}</b> ${x.mb_per_min ? `${x.mb_per_min} MB/min` : ""}</span>`,
    x.picture_mbps ? `<span class="fact">${x.picture_mbps} Mbps picture</span>` : "",
    `<span class="fact">${esc([resLabel(x.resolution), x.video_codec].filter(Boolean).join(" "))}</span>`,
    `<span class="fact ${x.hdr ? "hdr" : ""}">${esc(x.hdr || "SDR")}</span>`,
    `<span class="fact">${esc([audioLabel(x.audio), chans(x.channels)].filter(Boolean).join(" ") || "audio ?")}${x.atmos ? " Atmos" : ""}${x.lossless ? " · lossless" : ""}</span>`,
    x.seeders != null ? `<span class="fact">${x.seeders} seeders</span>` : ""].filter(Boolean).join("");
}
function optionCard(sg, o, i) {
  return `<div class="opt ${i ? "" : "best"}">
    <div class="opt-main">
      <div class="opt-title">${i ? "" : '<span class="pill-best">Best pick</span>'}<span class="name">${esc(o.title)}</span></div>
      <div class="opt-meta">${esc([o.indexer, o.release_group, o.score ? `format score ${o.score}` : ""].filter(Boolean).join(" · "))}</div>
      <div class="facts">${facts(o)}</div>
    </div>
    <div class="opt-side"><span class="opt-save">${saveText(o.saving)}</span>
      <button type="button" class="btn sm" data-pick="${sg.id}" data-guid="${esc(o.guid)}">Grab</button></div>
  </div>`;
}
function suggDetail(sg) {
  const rej = sg.rejected || [];
  return `<div class="dd">
    <div class="dd-head"><b>${esc(sg.title)} <span class="yr">${sg.year || ""}</span></b><span class="muted small">searched ${esc(sg.at)} · ${sg.releases} releases, ${sg.options.length} qualify</span></div>
    ${sg.flagged ? `<p class="dd-flag"><b>${sg.mode === "upgrade" ? "Putting right" : "Flagged"}</b>${esc(sg.flagged)}</p>` : ""}
    <div class="opt now"><div class="opt-main"><div class="opt-title"><span class="pill-now">Now on disk</span><span class="name">${esc(sg.before.name || sg.before.quality || "")}</span></div>
      <div class="facts">${facts(sg.before)}</div></div></div>
    ${sg.options.length ? `<div class="opts">${sg.options.map((o, i) => optionCard(sg, o, i)).join("")}</div>`
      : `<p class="problem">Nothing qualified of ${sg.releases} releases. The reasons are below.</p>`}
    <div class="dd-cols">
      ${sg.why?.length ? `<div><h4>Why the best pick</h4><ul>${sg.why.map((w) => `<li>${esc(w)}</li>`).join("")}</ul></div>` : ""}
      ${rej.length ? `<div><details ${sg.options.length ? "" : "open"}><summary class="muted small">${rej.length} turned down</summary><ul class="rej">${rej.map((n) => { const m = /^(.*?): (.*)$/.exec(n); return `<li>${m ? `<b>${esc(m[1])}</b>: ${esc(m[2])}` : esc(n)}</li>`; }).join("")}</ul></details></div>` : ""}
    </div></div>`;
}
function renderSuggestions() {
  const keep = new Set([...document.querySelectorAll("#sg-rows input:checked")].map((i) => i.value));
  $("sg-rows").innerHTML = sugg.map((sg) => {
    const best = sg.options[0], open = expanded.has(`sg-rows:${sg.id}`);
    return `<tr class="row-x ${open ? "open" : ""}" data-id="${sg.id}"><td class="pick"><input type="checkbox" value="${sg.id}" ${keep.has(String(sg.id)) ? "checked" : ""} aria-label="Select ${esc(sg.title)}"></td>
      <td>${esc(sg.title)} <span class="yr">${sg.year || ""}</span>${sg.mode === "upgrade" ? ' <span class="tag" style="--c: var(--cyan)">put right</span>' : ""}</td>
      <td class="nowrap">${fileLine(sg.before)}<div class="muted small">${short(sg.before)}</div></td>
      <td>${best ? `<span class="nowrap">${fileLine(best)}</span><div class="muted small">${short(best)}</div>` : '<span class="muted">nothing qualified</span>'}</td>
      <td class="r nowrap">${best ? saveText(best.saving) : "–"}</td>
      <td class="r">${sg.options.length}</td><td class="muted small nowrap">${esc(sg.at)}</td></tr>`
      + (open ? `<tr class="detail" data-for="${sg.id}"><td colspan="7">${suggDetail(sg)}</td></tr>` : "");
  }).join("") || `<tr><td colspan="7" class="empty">No suggestions yet. Pick films in Flagged films (or Replaced) and press Find replacements.</td></tr>`;
  $("sg-busy").textContent = status?.suggesting ? `${status.suggesting} still to search…` : "";
  updateSgSel();
}
function updateSgSel() {
  const picked = [...document.querySelectorAll("#sg-rows input:checked")].map((i) => +i.value), n = picked.length;
  const grabbable = sugg.filter((s) => picked.includes(s.id) && s.options.length).length;
  $("sg-again").disabled = $("sg-clear").disabled = !n;
  $("sg-grab").disabled = !grabbable; $("sg-grab").textContent = grabbable ? `Grab ${grabbable} best pick${grabbable === 1 ? "" : "s"}` : "Grab best picks";
}
$("sg-rows").addEventListener("change", updateSgSel);
$("sg-all").addEventListener("change", (e) => { document.querySelectorAll("#sg-rows input").forEach((i) => (i.checked = e.target.checked)); updateSgSel(); });
$("sg-rows").addEventListener("click", (e) => {
  const p = e.target.closest("[data-pick]");
  if (p) { p.disabled = true; act(async () => { const r = await api("POST", "/api/pick", { id: +p.dataset.pick, guid: p.dataset.guid }); setTimeout(() => loadSuggestions(), 4000); return r; }); return; }
  if (e.target.closest("input, a, button, label, .dd")) return;
  const tr = e.target.closest("tr.row-x"); if (!tr) return;
  const key = `sg-rows:${tr.dataset.id}`; expanded.has(key) ? expanded.delete(key) : expanded.add(key); renderSuggestions();
});
const pickedSg = () => [...document.querySelectorAll("#sg-rows input:checked")].map((i) => +i.value);
$("sg-again").addEventListener("click", () => act(() => api("POST", "/api/suggest", { ids: pickedSg() })));
$("sg-grab").addEventListener("click", () => {
  $("sg-grab").disabled = true;
  act(async () => { const r = await api("POST", "/api/pick/best", { ids: pickedSg() }); setTimeout(() => loadSuggestions(), 4000); return r; });
});
$("sg-clear").addEventListener("click", () => act(async () => { const r = await api("POST", "/api/suggestions/dismiss", { ids: pickedSg() }); await loadSuggestions(); return r; }));

track("rules", () => JSON.stringify(rulesPatch()));
track("quality", () => JSON.stringify([qv, qMin, qPrefer]));
$("q-prefer").addEventListener("click", (e) => {
  const b = e.target.closest("[data-p]"); if (!b) return; qPrefer = b.dataset.p;
  $("q-prefer").querySelectorAll("[data-p]").forEach((x) => { x.setAttribute("aria-pressed", x === b); x.setAttribute("aria-checked", x === b); });
});
track("settings", () => JSON.stringify(settingsPatch()));
track("timing", () => JSON.stringify(TIMINGS.map((k) => $("timing")[k].value)));
track("conn", () => JSON.stringify(["radarr_url", "radarr_api_key", "radarr_link", "refetcharr_url"].map((k) => $("conn")[k].value.trim())));
track("security", () => JSON.stringify(["username", "password", "confirm"].map((k) => $("security")[k].value)));
showTab(); refresh(); setInterval(refresh, 5000);
