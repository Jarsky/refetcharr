// Refetcharr web UI: polls /api/status, renders the tabs, and sends actions to the same API the TUI uses.
const GROUPS = { remux: ["1080p remuxes", "var(--violet)"], big4k: ["4K over the cap", "var(--amber)"],
  remux4k: ["4K remuxes", "var(--pink)"], big1080: ["1080p over the cap", "var(--blue)"], tiny: ["Tiny x265", "var(--mint)"],
  lowq: ["Low quality", "var(--red)"], upgrade: ["Put right", "var(--cyan)"] };
const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const size = (b) => { const g = (b || 0) / 1e9; return g >= 1000 ? `${(g / 1000).toFixed(2)} TB` : `${g.toFixed(1)} GB`; };
let pageVersion = null;   // the server's version this page was loaded with
// 1.9.0: films (Radarr) and TV seasons and episodes (Sonarr). Pages with a Films / TV switch remember it; it shows once Sonarr is connected.
const KIND_KEY = "refetcharr-kind-";
const isTv = (x) => x?.kind === "season" || x?.kind === "episode";
function kindOf(page) {
  if (status && !status.tv) return "films";
  try { return localStorage.getItem(KIND_KEY + page) || "films"; } catch { return "films"; }
}
const ofKind = (page) => (x) => (kindOf(page) === "tv") === isTv(x);
const linkBase = (k) => (status?.[k] || "").replace(/\/$/, "");
const itemLink = (x) => (!x?.slug ? "" : isTv(x) ? `${linkBase("sonarr_link")}/series/${encodeURIComponent(x.slug)}` : `${linkBase("radarr_link")}/movie/${encodeURIComponent(x.slug)}`);
const epCount = (x) => (x?.kind === "season" && x.episodes ? ` <span class="muted small">${x.episodes} ep${x.episodes === 1 ? "" : "s"}</span>` : "");
function paintKinds() {
  const on = !!status?.tv;
  document.querySelectorAll("[data-kind-for]").forEach((g) => {
    g.hidden = !on; const bar = g.closest(".kindbar"); if (bar) bar.hidden = !on;
    g.querySelectorAll("[data-kind]").forEach((b) => b.setAttribute("aria-pressed", b.dataset.kind === kindOf(g.dataset.kindFor)));
  });
}
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
  const issues = (a) => status.setup_apps?.[a] || [];
  set("b-radarr", issues("radarr").length, issues("radarr").join("; "));
  set("b-sonarr", issues("sonarr").length, issues("sonarr").join("; "));
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
  replacement: "settings", quality: "settings", queue: "settings", radarr: "settings", sonarr: "settings", timings: "settings", security: "settings" };
const TAB_TITLE = { dashboard: "Dashboard", library: "Flagged", suggestions: "Suggestions", replaced: "Replaced", failed: "Not replaced", events: "Events",
  replacement: "Replacement", quality: "Quality", queue: "Queue & schedule", radarr: "Radarr", sonarr: "Sonarr", timings: "Timings", security: "Security" };
const currentTab = () => { const t = (location.hash || "#dashboard").slice(1); return t === "settings" ? "radarr" : TAB_GROUP[t] ? t : "dashboard"; };
function showTab() {
  const tab = currentTab();
  document.querySelectorAll(".tab").forEach((s) => (s.hidden = s.id !== `tab-${tab}`));
  document.querySelectorAll(".nav-group").forEach((g) => g.classList.toggle("open", g.dataset.group === TAB_GROUP[tab]));
  document.querySelectorAll(".nav a[data-tab]").forEach((a) => a.classList.toggle("on", a.dataset.tab === tab));
  $("page-title").textContent = TAB_TITLE[tab];
  closeMenu();
  if (tab === "library") loadFilms();
  if (["queue", "radarr", "sonarr", "timings", "security"].includes(tab)) loadSettings();
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
  const tvDl = s.active.filter(isTv).length;
  $("s-active-label").textContent = ["downloading", s.tv && tvDl ? `${s.active.length - tvDl} films, ${tvDl} TV` : "", warned].filter(Boolean).join(" · ");
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
  const fixAt = /Sonarr/.test(problem) ? "sonarr" : /API key|reach Radarr|Radarr/.test(problem) ? "radarr" : "";
  $("error").innerHTML = problem ? `<span>Problem: ${esc(problem)}${fixAt ? ` · <a href="#${fixAt}">Open Settings → ${fixAt === "sonarr" ? "Sonarr" : "Radarr"}</a>` : ""}</span>
    <button type="button" class="x" id="error-x" aria-label="Dismiss">✕</button>` : "";

  // 1.9.1: films and TV in their own bands, each with its downloads and its activity (app-wide entries in both, dimmed)
  const bands = !!s.tv;
  document.querySelectorAll("[data-band]").forEach((h) => (h.hidden = !bands));
  $("tv-band").hidden = !bands;
  $("tab-dashboard").classList.toggle("banded", bands);
  const films = bands ? s.active.filter((a) => !isTv(a)) : s.active, tvs = s.active.filter(isTv);
  renderDownloads("active", films, bands ? "No films downloading." : "Nothing downloading.");
  const feed = (box, want) => {
    const rows = s.activity.filter((x) => !bands || !x.item || x.item === want).slice(0, 40);
    $(box).innerHTML = rows.map((x) => `<li class="${bands && !x.item ? "general" : ""}"><time>${esc(x.time.slice(5, 16))}</time><span class="${esc(x.kind)}">${esc(x.text)}</span></li>`).join("")
      || `<li class="empty">Nothing yet.</li>`;
  };
  feed("activity", "film");
  if (bands) { renderDownloads("active-tv", tvs, "No TV downloading."); feed("activity-tv", "tv"); }
  const stalledOf = (xs) => xs.filter((a) => a.stalled).length;
  const countText = (xs) => (xs.length ? `· ${xs.length - stalledOf(xs)} downloading${stalledOf(xs) ? `, ${stalledOf(xs)} stalled` : ""}` : "");
  $("active-tv-count").textContent = countText(tvs);

  const sum = s.scan.summary || {};
  $("scan-at").textContent = s.scan.at ? `· ${s.scan.at.replace("T", " ")}` : "· not scanned yet";
  const tiles = (sm, unit, groups) => Object.entries(GROUPS).filter(([k]) => sm?.[k]).map(([k, [label, col]]) =>
    `<div class="group" style="--c:${col}"><b>${label}</b><span>${sm[k].films} ${unit} · ${(sm[k].gb / 1000).toFixed(1)} TB</span>
     <span>${k === "tiny" ? `need bigger files${groups?.tiny ? " · going after them" : " · switched off"}` : `about ${(sm[k].saving_gb / 1000).toFixed(1)} TB to win`}</span></div>`).join("");
  const filmTiles = tiles(sum, "films", s.groups), tvTiles = s.tv && sum.tv ? tiles(sum.tv, "seasons", s.tv_groups) : "";
  $("scan-summary").innerHTML = (filmTiles && tvTiles ? `<h3 class="groups-h">Films</h3>` : "") + filmTiles
    + (tvTiles ? `<h3 class="groups-h">TV${s.tv_auto ? "" : ' <span class="muted small">· picked by hand only (Auto-queue TV is off)</span>'}</h3>${tvTiles}` : "")
    + (s.tv && !s.tv_scanned ? `<p class="muted small groups-h">TV shows here after the next rescan.</p>` : "")
    || `<p class="empty">Run a scan to see the library's oversized films${s.tv ? " and seasons" : ""}.</p>`;

  $("active-count").textContent = bands ? countText(films) + (s.active.length ? ` · ${s.active.length - s.stalled} of ${s.limits.max_active} at once, films and TV` : "")
    : s.active.length ? `· ${s.active.length - s.stalled} of ${s.limits.max_active} at once${s.stalled ? `, ${s.stalled} stalled` : ""}` : "";
  paintKinds();
  renderFailed(); loadReplaced(); if (currentTab() === "suggestions") loadSuggestions(true);
  const tab = currentTab();
  if (tab === "failed" || tab === "replaced" || tab === "library") markSeen(tab); else badges();
}

function renderDownloads(box, list, empty) {
  $(box).innerHTML = list.length ? list.sort((a, b) => (b.stalled - a.stalled) || (b.finish_warn - a.finish_warn) || (b.progress ?? -1) - (a.progress ?? -1)).map((a) => {
    const ready = ["importPending", "importBlocked"].includes(a.state), pct = a.progress ?? 0;
    const importing = a.status === "importing" || a.state === "importing";
    const mins = a.finishing_minutes ?? 0, waited = mins >= 120 ? `${Math.round(mins / 60)} h` : `${mins} min`;
    const app = isTv(a) ? "Sonarr" : "Radarr";
    const label = a.stalled ? `Stalled: no progress for ${Math.round(a.stalled_hours ?? 0)} h, at ${pct}%` : a.finishing ? `Downloaded ${waited} ago, waiting for qBittorrent to complete it` : importing ? "Importing" : ready ? "Ready to import" : a.progress === null ? `Waiting for ${app}` : `${pct}%`;
    const open = expanded.has(`${box}:${a.id}`);
    return `<div class="dl row-x ${open ? "open" : ""} ${a.stalled || a.finish_warn ? "stalled" : ""}" data-id="${a.id}"><span class="t">${esc(a.title)} <span class="yr">${a.year || ""}</span>${a.stalled ? ` <span class="tag warnbg">Not using a slot</span>` : ""}</span>
      <span class="sz">${size(a.old_size)} → ${size(a.new_size)}</span>
      <div class="bar ${ready || importing ? "ready" : ""}"><i style="width:${ready || importing ? 100 : pct}%"></i></div>
      <div class="meta"><span>${label}</span><span>${esc(a.message || a.timeleft || "")}</span></div></div>`
      + (open ? `<div class="dl-detail" data-for="${a.id}">${detailCache.get(+a.id) || LOADING}</div>` : "");
  }).join("") : `<p class="empty">${empty}</p>`;
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
    <td>${esc(x.when || "")}</td><td>${esc(x.old_quality || "")}</td><td>${x.before?.rating != null ? rp(x.before) : qb(x.before_badge, "The original file wasn't rated when it was replaced, and it's gone now")}<span class="arrow">→</span>${x.after?.rating != null ? rp(x.after) : qb(x.after_badge)}</td>
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
  try {
    films = await api("GET", "/api/films");
    films.forEach((f) => { f.badge_rank = RANK[f.badge] || 0; f.rating_sort = f.rating ?? (f.badge_rank - 10); });
    renderFilms();
  } catch (e) { toast(e.message, "err"); }
}
const resKey = (r) => (r >= 2000 ? "2160" : r >= 1000 ? "1080" : r >= 700 ? "720" : "sd");
const LIB_SEL = "#lib-rows input:checked, #lib-grid input:checked";
const libPicked = () => [...document.querySelectorAll(LIB_SEL)];
function shownFilms() {
  const q = $("lib-q").value.trim().toLowerCase(), untried = $("lib-untried").checked;
  const [k, dir] = libSort;
  const showIgnored = $("lib-ignored").checked;
  const fr = $("lib-f-rating").value, fres = $("lib-f-res").value, fhdr = $("lib-f-hdr").value, fat = $("lib-f-atmos").value;
  const kind = ofKind("lib");
  return films.filter((f) => kind(f) && libGroups.has(f.group) && (!q || f.title.toLowerCase().includes(q)) && (!untried || !f.refetch) && (showIgnored || !f.ignored)
      && (!fr || f.badge === fr) && (!fres || resKey(f.resolution) === fres)
      && (!fhdr || (f.dynamic_range && f.dynamic_range !== "SDR" ? "1" : "0") === fhdr) && (!fat || (f.atmos ? "1" : "0") === fat))
    .sort((a, b) => (a[k] > b[k] ? 1 : a[k] < b[k] ? -1 : 0) * dir);
}
const savingCell = (f) => (f.group === "tiny" ? `<span class="grew" title="Needs a bigger file">+${(-f.saving_gb).toFixed(1)} GB</span>` : f.saving_gb > 0 ? `saves ${f.saving_gb.toFixed(1)} GB` : "");
function filmCover(f) {
  const [label, col] = GROUPS[f.group] || [f.group, "var(--line)"], canPick = !f.refetch;
  return `<div class="cover ${f.ignored ? "ignored" : ""}" data-id="${f.id}">
    ${canPick ? `<label class="cover-sel" title="Select"><input type="checkbox" value="${f.id}" data-ignored="${f.ignored ? 1 : ""}" aria-label="Select ${esc(f.title)}"></label>` : ""}
    <button type="button" class="cover-img" data-open-film="${f.id}" aria-label="Details of ${esc(f.title)}">${poster(f)}</button>
    <div class="cover-meta"><b class="cover-title">${esc(f.title)}</b> <span class="yr">${f.year || ""}</span>${epCount(f)}
      <div class="cover-line">${rp(f)}<span>${f.size_gb.toFixed(1)} GB</span><span>${resLabel(f.resolution)}</span></div>
      <div class="cover-line"><span class="tag" style="--c:${col}">${label}</span>${savingCell(f)}${f.refetch ? `<span class="tag">${esc(f.refetch)}</span>` : ""}</div></div>
  </div>`;
}
function renderFilms() {
  const v = shownFilms(), covers = viewOf("lib") === "covers";
  $("lib-table").hidden = covers; $("lib-grid").hidden = !covers;
  $("lib-grid").innerHTML = covers ? v.slice(0, libShown).map(filmCover).join("") || `<p class="empty">No films match.</p>` : "";
  $("lib-rows").innerHTML = covers ? "" : v.slice(0, libShown).map((f) => {
    const [label, col] = GROUPS[f.group] || [f.group, "var(--line)"], canPick = !f.refetch;
    const link = itemLink(f);
    return xrow("lib-rows", f.id, `<td class="pick">${canPick ? `<input type="checkbox" value="${f.id}" data-ignored="${f.ignored ? 1 : ""}" aria-label="Select ${esc(f.title)}">` : ""}</td>
      <td><a href="${esc(link)}" target="_blank" rel="noopener">${esc(f.title)}</a><span class="yr">${f.year || ""}</span>${epCount(f)}${f.release_group ? `<span class="rg">${esc(f.release_group)}</span>` : ""}${f.mixed ? ' <span class="tag" title="Its episodes are in more than one quality: the one most of its size is in is shown">mixed</span>' : ""}</td>
      <td><span class="tag" style="--c:${col}">${label}</span></td><td>${esc(f.quality)} · ${esc(f.video_codec)}${f.dynamic_range && f.dynamic_range !== "SDR" ? " · " + esc(f.dynamic_range) : ""}${f.audio ? ` · ${esc(audioLabel(f.audio))} ${chans(f.channels)}${f.atmos ? " Atmos" : ""}` : ""}</td>
      <td>${rp(f)}</td>
      <td class="r">${f.size_gb.toFixed(1)} GB</td><td class="r">${f.mb_per_min}</td>
      <td class="r">${f.group === "tiny" ? `<span class="grew" title="Needs a bigger file">+${(-f.saving_gb).toFixed(1)} GB</span>` : f.saving_gb > 0 ? f.saving_gb.toFixed(1) + " GB" : "–"}</td>
      <td>${f.refetch ? `<span class="tag">${esc(f.refetch)}</span>` : ""}${f.ignored ? `<span class="tag" title="${esc(f.ignored)}">Ignored</span>` : ""}</td>`, f.ignored ? "ignored" : "");
  }).join("") || `<tr><td colspan="9" class="empty">No ${kindOf("lib") === "tv" ? "seasons" : "films"} match.${kindOf("lib") === "tv" && status?.tv && !status.tv_scanned ? " TV is listed after the next rescan." : ""}</td></tr>`;
  const save = v.filter((f) => f.group !== "tiny").reduce((t, f) => t + Math.max(f.saving_gb, 0), 0);
  const tvNow = kindOf("lib") === "tv";
  $("lib-th-title").textContent = tvNow ? "Season" : "Film";
  $("lib-count").textContent = `${v.length.toLocaleString()} ${tvNow ? "seasons" : "films"} · about ${(save / 1000).toFixed(1)} TB to win`;
  $("lib-more").hidden = v.length <= libShown;
  document.querySelectorAll("#tab-library th[data-sort]").forEach((th) => { th.classList.toggle("sorted", th.dataset.sort === libSort[0]); th.classList.toggle("asc", th.dataset.sort === libSort[0] && libSort[1] > 0); });
  updateLibQueue();
}
function updateLibQueue() {
  const picked = libPicked(), n = picked.length, allIgnored = n && picked.every((i) => i.dataset.ignored);
  $("lib-queue").disabled = !n; $("lib-queue").textContent = n ? `Replace ${n} selected` : "Replace selected";
  $("lib-ignore").disabled = !n; $("lib-ignore").textContent = n ? `${allIgnored ? "Stop ignoring" : "Ignore"} ${n} selected` : "Ignore selected";
  $("lib-ignore").dataset.mode = allIgnored ? "off" : "on";
  $("lib-suggest").disabled = !n; $("lib-suggest").textContent = n ? `Find replacements for ${n}` : "Find replacements";
  renderLibEst();
}

// ── 1.7.0: profiles and what a selection would save ───────────────────────
let profiles = null, liveRules = null, liveTvRules = null, estWith = "current";
async function loadProfiles(force) {
  if (profiles && !force) return;
  try {
    const [p, s] = await Promise.all([api("GET", "/api/profiles"), api("GET", "/api/settings")]);
    profiles = p; liveRules = s.values.rules; liveTvRules = s.values.tv.rules;
  } catch { /* the estimate waits for the next try */ }
}
const profileLabel = (id) => (id === "custom" ? "Custom" : profiles?.profiles.find((p) => p.id === id)?.label || id);
// a profile's rules on top of the saved ones, for films or for TV (its targets differ)
const rulesFor = (id, tv = false) => {
  const live = tv ? liveTvRules : liveRules, list = tv ? profiles.tv_profiles : profiles.profiles;
  return id === "current" ? live : { ...live, ...(list.find((p) => p.id === id)?.rules || {}) };
};
const pickOf = (sg) => sg.options.find((o) => o.guid === sg.chosen) || sg.options[0];
function estAfterGb(f, r) {   // what a replacement would likely weigh: the profile's target size, within its rules
  const k = f.resolution >= 2000 ? 2160 : 1080, mins = f.runtime_min || 0;
  const target = r[`preferred_${k}`] * mins / 1000;
  if (f.group === "tiny") return Math.max(f.size_gb * 1.1, (r.prefer === "small" ? r[`min_${k}`] : r[`preferred_${k}`]) * mins / 1000);
  if (f.group === "lowq") return Math.max(target, r[`min_${k}`] * mins / 1000);
  return Math.min(target, f.size_gb * r.max_ratio);
}
const gb = (x) => size(x * 1e9);
// a selection's summary as tiles (1.8.0): films, size now, about after, the net, freed and added apart, the ratings
function estTiles(o) {
  const net = o.now - o.after;
  const t = (label, value, cls = "", sub = "") => `<div class="tile ${cls}"><span class="tile-v">${value}</span><span class="tile-l">${label}</span>${sub ? `<span class="tile-s">${sub}</span>` : ""}</div>`;
  const avg = (xs) => (xs.length ? (xs.reduce((a, b) => a + b, 0) / xs.length).toFixed(1) : null);
  const rb = avg(o.ratingsBefore || []), ra = avg(o.ratingsAfter || []);
  return `<div class="tiles">
    ${t(o.what, o.n.toLocaleString())}
    ${t("Size now", gb(o.now))}
    ${t(o.real ? "Size after" : "Size after, about", gb(o.after))}
    ${t(net >= 0 ? "Saves" : "Adds", gb(Math.abs(net)), net >= 0 ? "save" : "grew")}
    ${o.freed > 0.05 && o.added > 0.05 ? t("Freed", gb(o.freed), "save", "by the films getting smaller") + t("Added", gb(o.added), "grew", "by the films getting bigger") : ""}
    ${rb && ra ? t("Average rating", `${rb} → ${ra}`, +ra >= +rb ? "save" : "grew", "out of 5") : ""}
  </div>`;
}
function renderLibEst() {
  const box = $("lib-est"), ids = new Set(libPicked().map((i) => +i.value));
  if (!ids.size) { box.hidden = true; return; }
  if (!liveRules) { loadProfiles().then(() => { if (liveRules) renderLibEst(); }); loadSuggestions(true); return; }
  const sel = films.filter((f) => ids.has(f.id));
  let now = 0, after = 0, freed = 0, added = 0, real = 0;
  const ratingsBefore = [], ratingsAfter = [];
  for (const f of sel) {
    const sg = estWith === "current" ? sugg.find((s) => s.id === f.id && s.options.length) : null, pick = sg && pickOf(sg);
    const a = pick ? pick.size / 1e9 : estAfterGb(f, rulesFor(estWith, isTv(f)));
    if (pick) { real++; if (f.rating != null && pick.rating != null) { ratingsBefore.push(f.rating); ratingsAfter.push(pick.rating); } }
    now += f.size_gb; after += a;
    if (a < f.size_gb) freed += f.size_gb - a; else added += a - f.size_gb;
  }
  const basis = real === sel.length ? "Sizes after are the releases found." : real ? `${real} from releases found under Suggestions, ${sel.length - real} estimated from the target size.` : "Estimated from the target size: find replacements for real release sizes.";
  const cur = kindOf("lib") === "tv" ? profiles.current_tv : profiles.current_films;
  const opts = [["current", `Your settings (${profileLabel(cur)})`], ...profiles.profiles.filter((p) => p.id !== cur).map((p) => [p.id, p.label])];
  const unit = kindOf("lib") === "tv" ? ["Season selected", "Seasons selected"] : ["Film selected", "Films selected"];
  box.innerHTML = estTiles({ what: sel.length === 1 ? unit[0] : unit[1], n: sel.length, now, after, freed, added, real: real === sel.length, ratingsBefore, ratingsAfter })
    + `<div class="est-foot"><span class="muted small">${basis}</span>
      <label class="est-with">Estimate with <select id="est-with">${opts.map(([v, l]) => `<option value="${v}" ${v === estWith ? "selected" : ""}>${esc(l)}</option>`).join("")}</select></label></div>`;
  box.hidden = false;
}
$("lib-est").addEventListener("change", (e) => { if (e.target.id === "est-with") { estWith = e.target.value; renderLibEst(); } });
async function loadProfileCards() {
  await loadProfiles(true);
  if (!profiles) return;
  const tv = kindOf("rules") === "tv", cur = tv ? profiles.current_tv : profiles.current_films;
  $("profiles").innerHTML = profiles.profiles.map((p) => `<button type="button" class="profile" role="radio" aria-checked="${p.id === cur}" data-profile="${p.id}">
      <b>${esc(p.label)}</b><span>${esc(p.blurb)}</span></button>`).join("")
    + (cur === "custom" ? `<p class="muted small profiles-note">Custom: your ${tv ? "TV " : ""}settings don't match a profile. Pick one to start from it.</p>` : "")
    + (tv ? `<p class="muted small profiles-note">For TV a profile's target sizes are TV's own (Balanced: 35 MB/min at 1080p, 90 at 4K).</p>` : "");
}
$("profiles").addEventListener("click", (e) => {
  const b = e.target.closest("[data-profile]");
  if (!b || b.getAttribute("aria-checked") === "true") return;
  if (!tracked.get("rules").btn.disabled && !confirm("Picking a profile saves it now and drops the unsaved changes on this page. Carry on?")) return;
  act(async () => { const r = await api("POST", "/api/profile", { id: b.dataset.profile, kind: kindOf("rules") }); estWith = "current"; await loadRules(); return r; });
});
$("lib-rows").addEventListener("change", updateLibQueue);
$("lib-grid").addEventListener("change", updateLibQueue);
$("lib-grid").addEventListener("click", (e) => { const b = e.target.closest("[data-open-film]"); if (b) openFilm(+b.dataset.openFilm); });
$("lib-all").addEventListener("change", (e) => { document.querySelectorAll("#lib-rows input, #lib-grid input").forEach((i) => (i.checked = e.target.checked)); updateLibQueue(); });
$("lib-q").addEventListener("input", () => { libShown = 200; renderFilms(); });
$("lib-untried").addEventListener("change", renderFilms);
$("lib-ignored").addEventListener("change", () => { libShown = 200; renderFilms(); });
for (const id of ["lib-f-rating", "lib-f-res", "lib-f-hdr", "lib-f-atmos"]) $(id).addEventListener("change", () => { libShown = 200; renderFilms(); });
$("lib-ignore").addEventListener("click", () => act(async () => {
  const ids = libPicked().map((i) => +i.value);
  const r = await api("POST", "/api/ignore", { ids, ignore: $("lib-ignore").dataset.mode !== "off" }); await loadFilms(); return r;
}));
$("lib-suggest").addEventListener("click", () => act(() => api("POST", "/api/suggest", { ids: libPicked().map((i) => +i.value) })));
$("lib-more").addEventListener("click", () => { libShown += 200; renderFilms(); });
document.querySelectorAll("#tab-library th[data-sort]").forEach((th) => th.addEventListener("click", () => {
  const k = th.dataset.sort; libSort = [k, libSort[0] === k ? -libSort[1] : (k === "title" || k === "group" || k === "quality" ? 1 : -1)]; renderFilms();
}));
$("lib-queue").addEventListener("click", () => act(async () => {
  const ids = libPicked().map((i) => +i.value);
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
  const sc = $("sconn");
  sc.sonarr_url.value = conn.sonarr_url || ""; sc.sonarr_url.placeholder = settings.env.sonarr_url;
  $("sconn-url-note").textContent = conn.sonarr_url ? "" : `Blank uses ${settings.env.sonarr_url} (from the container's .env)`;
  sc.sonarr_link.value = conn.sonarr_link || "";
  $("sconn-link-note").textContent = conn.sonarr_link ? "" : `Blank uses the address above (${conn.sonarr_url || settings.env.sonarr_url})`;
  sc.sonarr_api_key.value = "";
  sc.sonarr_api_key.placeholder = settings.secrets_set.sonarr_api_key ? "Saved: leave blank to keep it" : "Paste Sonarr's API key";
  $("sconn-key-note").textContent = settings.secrets_set.sonarr_api_key && !conn.sonarr_api_key && settings.env.sonarr_api_key ? "Currently from the container's .env" : "";
  sc.tv_enabled.checked = settings.tv.enabled;
  if (currentTab() === "radarr" || currentTab() === "sonarr") loadSetup(false, currentTab());
  const f = $("settings"), q = settings.queue;
  f.auto.checked = q.auto; f.max_active.value = q.max_active; f.max_per_day.value = q.max_per_day;
  f.w_enabled.checked = q.window.enabled; f.w_start.value = q.window.start; f.w_end.value = q.window.end;
  $("set-days").innerHTML = DAYS.map((d, i) => `<button type="button" class="chip" data-d="${i}" aria-pressed="${q.window.days.includes(i)}">${d}</button>`).join("");
  f.every_days.value = settings.scan.every_days;
  fillTiming(settings.timing);
  const sec = $("security");
  sec.username.value = settings.auth.username; sec.password.value = sec.confirm.value = sec.current.value = "";
  $("apikey").textContent = "••••••••••••••••••••••••••••••••"; $("key-show").textContent = "Show";
  for (const id of ["conn", "sconn", "settings", "timing", "security"]) clean(id);
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
  try { $("apikey").textContent = (await api("POST", "/api/auth/key")).api_key; $("key-show").textContent = "Hide"; say("key-msg", "New key made. Fix the webhooks under Settings → Radarr and Settings → Sonarr so they use it."); }
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
// Sonarr's connection (1.9.0): the same as Radarr's, plus whether TV is looked after at all
const sconnBody = () => ({ app: "sonarr", sonarr_url: $("sconn").sonarr_url.value.trim(), sonarr_api_key: $("sconn").sonarr_api_key.value.trim() });
const sconnResult = (ok, msg) => say("sconn-result", msg, ok === null ? "info" : ok ? "ok" : "err");
$("sconn-test").addEventListener("click", async () => {
  sconnResult(null, "Testing…");
  try { const r = await api("POST", "/api/connections/test", sconnBody()); sconnResult(r.ok, r.message); } catch (e) { sconnResult(false, e.message); }
});
$("sconn").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = $("sconn"), { app: _, ...conn } = sconnBody();
  try {
    let msg = "Saved.";
    if (f.tv_enabled.checked) {
      const t = await api("POST", "/api/connections/test", sconnBody());
      if (!t.ok) return sconnResult(false, t.message + " (not saved)");
      msg = `Saved. ${t.message}. TV shows on the pages after the next rescan.`;
    }
    await api("PATCH", "/api/settings", { connections: { ...conn, sonarr_link: f.sonarr_link.value.trim() }, tv: { enabled: f.tv_enabled.checked } });
    sconnResult(true, msg); await loadSettings(); refresh();
  } catch (err) { sconnResult(false, err.message); }
});
const SETUP_BOX = { radarr: ["setup", "setup-fix-all"], sonarr: ["ssetup", "ssetup-fix-all"] };
let setupChecks = { radarr: [], sonarr: [] };
async function loadSetup(force, app = "radarr") {
  const [list, fixAll] = SETUP_BOX[app];
  try { setupChecks[app] = (await api("GET", `/api/setup?app=${app}`)).checks; } catch (e) { $(list).innerHTML = `<li class="empty">${esc(e.message)}</li>`; return; }
  $(list).innerHTML = setupChecks[app].map((c) => {
    const cls = c.ok ? "ok" : c.level, icon = c.ok ? "✓" : c.level === "warn" ? "!" : "✕";
    return `<li><span class="icon ${cls}" aria-label="${c.ok ? "OK" : "Needs attention"}">${icon}</span><b>${esc(c.label)}</b>
      ${c.fix ? `<button type="button" class="btn" data-fix="${c.id}">Fix</button>` : "<span></span>"}
      <span class="detail">${esc(c.detail)}</span>${c.fix ? `<span class="fixdesc">Fix: ${esc(c.fix)}</span>` : ""}</li>`;
  }).join("");
  $(fixAll).hidden = setupChecks[app].filter((c) => c.fix).length < 2;
  if (force) refresh();
}
async function applyFix(ids, app) {
  try { const r = await api("POST", "/api/setup/fix", { ids, app }); toast(r.done.length ? r.done.join(". ") + "." : "Nothing needed changing."); await loadSetup(true, app); }
  catch (e) { toast(e.message, "err"); }
}
for (const app of ["radarr", "sonarr"]) {
  const [list, fixAll] = SETUP_BOX[app], p = app === "sonarr" ? "s" : "";
  $(list).addEventListener("click", (e) => { const b = e.target.closest("[data-fix]"); if (b) { b.disabled = true; applyFix([b.dataset.fix], app); } });
  $(fixAll).addEventListener("click", () => applyFix(setupChecks[app].filter((c) => c.fix).map((c) => c.id), app));
  $(`${p}setup-refresh`).addEventListener("click", () => loadSetup(true, app));
}
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
  if (!ids.length) { $("ig-films").innerHTML = `<p class="muted small">None. Pick films or seasons under Flagged and press Ignore selected.</p>`; return; }
  if (!films.length) { try { films = await api("GET", "/api/films"); } catch { /* names fall back to ids */ } }
  const name = (id) => { const f = films.find((x) => x.id === id); return f ? `${esc(f.title)} <span class="yr">${f.year || ""}</span>` : id >= 1e9 ? `TV item ${id}` : `Radarr film ${id}`; };
  $("ig-films").innerHTML = `<ul class="iglist">${ids.map((id) => `<li><span>${name(id)}</span><button type="button" class="btn ghost" data-unignore="${id}">Stop ignoring</button></li>`).join("")}</ul>`;
}
$("ig-films").addEventListener("click", (e) => { const b = e.target.closest("[data-unignore]"); if (b) act(async () => { const r = await api("POST", "/api/ignore", { ids: [+b.dataset.unignore], ignore: false }); loadRules(); return r; }); });

function fillRules(r, groups, ig, tv) {
  const f = $("rules"), isTvPage = kindOf("rules") === "tv";
  $("tv-card").hidden = !isTvPage;
  $("groups-note").textContent = isTvPage ? "Which kinds of season Refetcharr looks for replacements for (by their sizes and ratings under TV's settings)."
    : "Which kinds of film Refetcharr looks for replacements for.";
  $("rules-kind-note").textContent = isTvPage ? "TV's own rules: everything on this page but the leave-alone lists applies to TV only." : "The rules for films. TV has its own: switch to TV.";
  if (tv) { f.tv_auto.checked = tv.auto; f.tv_fallback.checked = tv.episode_fallback; }
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
    const tv = kindOf("rules") === "tv";
    fillRules(tv ? d.values.tv.rules : d.values.rules, tv ? d.values.tv.groups : d.values.queue.groups, d.values.ignore, d.values.tv); clean("rules");
  } catch (e) { toast(e.message, "err"); }
  loadProfileCards();
}
$("rules-reset").addEventListener("click", () => { const tv = kindOf("rules") === "tv"; fillRules(tv ? ruleDefaults.tv.rules : ruleDefaults.rules, tv ? ruleDefaults.tv.groups : ruleDefaults.queue.groups, null, tv ? ruleDefaults.tv : null); say("rules-saved", "Defaults filled in (the leave-alone lists are kept). Nothing changes until you save.", "info"); recheck("rules"); });
function rulesPatch() {
  const f = $("rules"), r = {};
  for (const [name, [, , write]] of Object.entries(RANGES)) r[name] = write ? write(+f[name].value) : +f[name].value;
  for (const k of BOOLS) r[k] = f[k].checked;
  document.querySelectorAll("#sizes .qs").forEach((box) => box.querySelectorAll("input[type=range]").forEach((i) => { r[`${i.dataset.k}_${box.dataset.res}`] = +i.value; }));
  for (const kind of ["video", "audio"]) { r[`${kind}_codecs`] = codecLists[kind]; r[`${kind}_strict`] = strict[kind] && codecLists[kind].length > 0; }
  const groups = Object.fromEntries(GROUP_KEYS.map((k) => [k, f[`g_${k}`]?.checked ?? false]));
  const ignore = { file_groups: [...igLists.file_groups], release_groups: [...igLists.release_groups] };
  if (kindOf("rules") === "tv") return { tv: { rules: r, groups, auto: f.tv_auto.checked, episode_fallback: f.tv_fallback.checked }, ignore };
  return { rules: r, queue: { groups }, ignore };
}
$("rules").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    await api("PATCH", "/api/settings", rulesPatch());
    say("rules-saved", `Saved${kindOf("rules") === "tv" ? " for TV" : ""}. New searches use these now; the Flagged list follows at the next rescan.`); clean("rules");
    refresh(); loadProfileCards();
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

// ── 1.8.0: ratings out of 5, the score card, covers ───────────────────────
const r1 = (v) => (v == null ? "?" : (+v).toFixed(1));
const tierOf = (v) => (v == null ? "" : v >= 3.5 ? "HIGH" : v >= 2 ? "MEDIUM" : "LOW");
// a rating pill: the overall rating out of 5, coloured by its tier (older records without one show the tier word)
function rp(x, missing = "Rated at the next rescan, or search again") {
  if (!x || x.rating == null) return qb(x?.badge || x?.video_tier, missing);
  const t = x.badge || tierOf(x.rating);
  return `<span class="qb rp ${t}" title="${esc(`${t}: ${r1(x.rating)} of 5 · picture ${r1(x.video_rating)}, audio ${r1(x.audio_rating)}`)}">${r1(x.rating)}</span>`;
}
// the score card: resolution, the overall rating and its tier, then picture and audio with what HIGH needs
function ratingCard(x, label = "") {
  if (!x || x.rating == null) return "";
  const bar = (name, v, line) => `<div class="rbar"><span class="rbar-name">${name}</span>
    <span class="rbar-track"><i class="${tierOf(v)}" style="width:${Math.max(0, Math.min(5, v || 0)) * 20}%"></i></span><b class="${tierOf(v)}">${r1(v)}</b>
    <small class="rbar-line">${line}</small></div>`;
  const vb = x.video_bounds || [], ab = x.audio_bounds || [];
  const pic = x.picture_mbps ? `${x.picture_mbps} Mbps picture${x.estimated ? " (from size)" : ""} · HIGH from ${vb[1] ?? "?"} Mbps` : "picture bitrate unknown";
  const aud = [audioLabel(x.audio), chans(x.channels)].filter(Boolean).join(" ") || "audio";
  const snd = x.audio_rating == null ? "not named in the title, so not rated" : x.lossless ? `${aud} · lossless (always 5.0)` : x.audio_kbps ? `${aud} · ${x.audio_kbps} kbps · HIGH from ${ab[1] ?? "?"} kbps` : "audio bitrate unknown";
  return `<div class="rcard">${label ? `<div class="rcard-label">${label}</div>` : ""}
    <div class="rcard-top"><span class="rres">${resLabel(x.resolution) || "?"}</span><span class="rsep"></span>
      <span class="rnum ${x.badge}">${r1(x.rating)}</span><span class="rof">/ 5<span class="rtier ${x.badge}">${x.badge}</span></span></div>
    ${bar("Picture", x.video_rating, pic)}${bar("Audio", x.audio_rating, snd)}</div>`;
}
function miniCard(x) {
  if (!x || x.rating == null) return `<div class="rmini">${qb(x?.badge || x?.video_tier, "Not rated")}</div>`;
  const bar = (name, v, why) => `<span class="rmini-name">${name}</span><span class="rbar-track"><i class="${tierOf(v)}" style="width:${Math.max(0, Math.min(5, v || 0)) * 20}%"></i></span>`
    + `<b class="${tierOf(v)}" title="${esc(why)}">${v == null ? "–" : r1(v)}</b>`;
  return `<div class="rmini" title="${esc(`${x.badge}: ${r1(x.rating)} of 5`)}">
    <div class="rmini-top"><span class="rnum ${x.badge}">${r1(x.rating)}</span><span class="rof">/ 5<span class="rtier ${x.badge}">${x.badge}</span></span></div>
    <div class="rmini-bars">${bar("Picture", x.video_rating, x.picture_mbps ? `${x.picture_mbps} Mbps picture` : "picture")}${bar("Audio", x.audio_rating, x.audio_rating == null ? "Not named in the title, so not rated" : "audio")}</div></div>`;
}
const poster = (f) => `<img loading="lazy" decoding="async" src="/api/poster/${f.id}" alt=""><span class="cover-fallback">${esc(f.title)}</span>`;
document.addEventListener("error", (e) => { if (e.target.matches?.(".cover-img img")) e.target.parentNode.classList.add("noimg"); }, true);
const VIEW_KEY = "refetcharr-view-";
const viewOf = (page) => { try { return localStorage.getItem(VIEW_KEY + page) || "list"; } catch { return "list"; } };
function setView(page, v) {
  try { localStorage.setItem(VIEW_KEY + page, v); } catch { /* private window */ }
  document.querySelectorAll(`[data-view-for="${page}"] [data-view]`).forEach((b) => b.setAttribute("aria-pressed", b.dataset.view === v));
  document.querySelectorAll(`[data-covers-only="${page}"]`).forEach((el) => (el.hidden = v !== "covers"));
  page === "lib" ? renderFilms() : renderSuggestions();
}
document.addEventListener("click", (e) => { const b = e.target.closest("[data-view-for] [data-view]"); if (b) setView(b.closest("[data-view-for]").dataset.viewFor, b.dataset.view); });
for (const page of ["lib", "sg"]) {
  document.querySelectorAll(`[data-view-for="${page}"] [data-view]`).forEach((b) => b.setAttribute("aria-pressed", b.dataset.view === viewOf(page)));
  document.querySelectorAll(`[data-covers-only="${page}"]`).forEach((el) => (el.hidden = viewOf(page) !== "covers"));
}
// a film's details in a dialog (the cover views have no row to open underneath)
let dlgSugg = null;
function openDialog(html) { $("film-dlg-body").innerHTML = html; if (!$("film-dlg").open) $("film-dlg").showModal(); }
async function openFilm(id) {
  dlgSugg = null; openDialog(LOADING);
  try { openDialog(renderDetail(await api("GET", `/api/films/${id}`))); } catch (e) { openDialog(`<div class="dd"><p class="problem">${esc(e.message)}</p></div>`); }
}
function openSugg(id) { const sg = sugg.find((s) => s.id === id); if (sg) { dlgSugg = id; openDialog(suggDetail(sg)); } }
$("film-dlg").addEventListener("click", (e) => {
  if (e.target === $("film-dlg") || e.target.closest("[data-close]")) { $("film-dlg").close(); return; }
  const c = e.target.closest("[data-choose]");
  if (c && !e.target.closest("a, button")) { choosePick(+c.dataset.choose, c.dataset.guid); return; }
  const p = e.target.closest("[data-pick]");
  if (p) { p.disabled = true; act(async () => { const r = await api("POST", "/api/pick", { id: +p.dataset.pick, guid: p.dataset.guid }); $("film-dlg").close(); setTimeout(() => loadSuggestions(), 4000); return r; }); }
});
$("film-dlg").addEventListener("keydown", (e) => {
  const c = e.target.closest("[data-choose]");
  if (c && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); choosePick(+c.dataset.choose, c.dataset.guid); }
});
$("film-dlg").addEventListener("close", () => { dlgSugg = null; });
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
for (const box of ["lib-rows", "fail-rows", "done-rows", "active", "active-tv"]) {
  $(box).addEventListener("click", (e) => {
    if (e.target.closest("input, a, button, label, .dd")) return;
    const el = e.target.closest(".row-x"); if (el) toggleDetail(box, el);
  });
}

const DETAIL_ROWS = [
  ["Rating", (x) => (x.rating != null ? `${rp(x)} <span class="muted small">of 5 · picture ${r1(x.video_rating)} · audio ${r1(x.audio_rating)}</span>`
    : x.badge || x.video_tier ? `${qb(x.badge)} <span class="muted small">${x.video_tier ? `picture ${x.video_tier.toLowerCase()}` : ""}${x.audio_tier ? ` · audio ${x.audio_tier.toLowerCase()}` : ""}</span>` : "")],
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
// score cards over the comparison: records from before 1.8 have no ratings, so the file on disk is rated live instead
function cardsFor(cols, d, done) {
  const cards = cols.map(([h, x], i) => {
    const live = (i === 0 && !done) || h === "Now on disk";
    const src = x?.rating != null ? x : live && d.now?.rating != null ? d.now : null;
    return src ? ratingCard(src, h.replace(/<[^>]*>/g, "").replace(/\(from its title\)/, "").trim()) : "";
  }).filter(Boolean);
  return cards.length ? `<div class="rcards ${cards.length > 1 ? "vs" : ""}">${cards.join('<span class="rarrow" aria-hidden="true">→</span>')}</div>` : "";
}
function renderDetail(d) {
  const st = d.state || {}, dec = st.decision || {}, legacy = st.status && !st.decision;
  const link = itemLink(d);
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
      ${link ? `<a href="${esc(link)}" target="_blank" rel="noopener">Open in ${isTv(d) ? "Sonarr" : "Radarr"} ↗</a>` : ""}</div>
    ${dec.flagged || d.flagged_now ? `<p class="dd-flag"><b>Flagged</b>${esc(dec.flagged || d.flagged_now)}</p>` : ""}
    ${st.problem ? `<p class="problem">${esc(st.problem)}</p>` : ""}
    ${fallbackList(d)}
    ${cardsFor(cols, d, done)}
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

// a season replaced episode by episode: where each episode is
function fallbackList(d) {
  const eps = d.fallback || [];
  if (!eps.length) return "";
  const n = (st) => eps.filter((x) => x.status === st).length, going = eps.filter((x) => PENDING.includes(x.status)).length;
  const left = eps.length - n("replaced") - going - n("waiting");
  return `<details class="note"><summary><b>Episode by episode:</b> ${n("replaced")} of ${eps.length} replaced${going ? `, ${going} in progress` : ""}${n("waiting") ? `, ${n("waiting")} still to search` : ""}${left ? `, ${left} not replaced` : ""}</summary>
    <ul class="rej">${eps.map((x) => `<li><b>${esc(x.title || x.id)}</b>: ${esc(x.status)}</li>`).join("")}</ul></details>`;
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
  const item = $("ev-item").value;
  const v = events.filter((x) => evKinds.has(x.kind) && (!item || x.item === item) && (!q || x.text.toLowerCase().includes(q)));
  $("ev-rows").innerHTML = v.slice(0, 1000).map((x) => `<tr><td>${esc(x.time)}</td><td><span class="kind ${esc(x.kind)}">${esc(x.kind)}</span></td><td>${esc(x.text)}</td></tr>`).join("")
    || `<tr><td colspan="3" class="empty">Nothing matches.</td></tr>`;
  $("ev-count").textContent = `${v.length.toLocaleString()} of ${events.length.toLocaleString()}`;
}
$("ev-q").addEventListener("input", renderEvents);
$("ev-item").addEventListener("change", renderEvents);
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
const PREFER = [["high", "Smallest HIGH, else the best MEDIUM"], ["small", "Smallest that qualifies"], ["size", "Nearest the Preferred size"]];
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
  const [lo, hi] = qv.video[qRes], f = qv.codec_factor, label = Q_RES.find(([k]) => k === qRes)[1], tv = kindOf("quality") === "tv";
  const secs = tv ? 2700 : 7200;   // a 45-minute episode, or a 2-hour film, with DD+ 5.1
  const gb = (pic, codec) => ((pic * (f[codec] || 1) + 0.64) * secs / 8 / 1000).toFixed(1);
  $("q-example").innerHTML = `A ${tv ? "45-minute" : "2-hour"} ${label} ${tv ? "episode" : "film"} with DD+ 5.1 rates <b>MEDIUM</b> from about <b>${gb(lo, "x265")} GB</b> in x265 (${gb(lo, "x264")} GB in x264)
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
  try {
    const d = await api("GET", "/api/settings"), tv = kindOf("quality") === "tv";
    qDefaults = tv ? { quality: d.defaults.tv.quality, rules: d.defaults.tv.rules } : d.defaults;
    $("quality-kind-note").textContent = tv ? "TV's own thresholds: TV encodes are leaner than films', so HIGH starts lower." : "The thresholds for films. TV has its own: switch to TV.";
    if (tv) fillQuality(d.values.tv.quality, d.values.tv.rules.min_picture, d.values.tv.rules.prefer); else fillQuality(d.values.quality, d.values.rules.min_picture, d.values.rules.prefer);
    clean("quality");
  }
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
    const tv = kindOf("quality") === "tv", body = { quality: qv, rules: { min_picture: qMin, prefer: qPrefer } };
    await api("PATCH", "/api/settings", tv ? { tv: body } : body);
    say("quality-saved", `Saved${tv ? " for TV" : ""}. Searches use it now, and the ratings follow straight away.`); clean("quality");
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
const fileLine = (x) => `${rp(x)} ${size(x.size)}${x.resolution ? ` · ${resLabel(x.resolution)}` : ""}`;
const short = (x) => [x.video_codec, x.hdr || "SDR", [audioLabel(x.audio), chans(x.channels)].filter(Boolean).join(" ") + (x.atmos ? " Atmos" : "")]
  .filter(Boolean).map(esc).join(" · ");
const saveText = (n) => (n >= 0 ? `saves ${size(n)}` : `<span class="grew">+${size(-n)}</span>`);
function facts(x) {
  return [`<span class="fact"><b>${size(x.size)}</b> ${x.mb_per_min ? `${x.mb_per_min} MB/min` : ""}</span>`,
    x.picture_mbps ? `<span class="fact">${x.picture_mbps} Mbps picture</span>` : "",
    `<span class="fact">${esc([resLabel(x.resolution), x.video_codec].filter(Boolean).join(" "))}</span>`,
    `<span class="fact ${x.hdr ? "hdr" : ""}">${esc(x.hdr || "SDR")}</span>`,
    `<span class="fact">${esc([audioLabel(x.audio), chans(x.channels)].filter(Boolean).join(" ") || "audio ?")}${x.atmos ? " Atmos" : ""}${x.lossless ? " · lossless" : ""}</span>`,
    x.seeders != null ? `<span class="fact">${x.seeders} seeders</span>` : ""].filter(Boolean).join("");
}
function optionCard(sg, o, i) {
  const on = o.guid === pickOf(sg)?.guid;
  return `<div class="opt ${i ? "" : "best"} ${on ? "chosen" : ""}" role="radio" tabindex="0" aria-checked="${on}" data-choose="${sg.id}" data-guid="${esc(o.guid)}"
      title="${on ? "Your pick: Grab takes this one" : "Click to pick this release"}">
    <div class="opt-main">
      <div class="opt-title"><span class="radio" aria-hidden="true"></span>${i ? "" : '<span class="pill-best">Best pick</span>'}${on && i ? '<span class="pill-pick">Your pick</span>' : ""}<span class="name">${esc(o.title)}</span></div>
      <div class="opt-meta">${esc([o.indexer, o.release_group, o.score ? `format score ${o.score}` : ""].filter(Boolean).join(" · "))}</div>
      <div class="facts">${facts(o)}</div>
    </div>
    <div class="opt-side">${miniCard(o)}<span class="opt-save">${saveText(o.saving)}</span></div>
  </div>`;
}
function choosePick(id, guid) {
  const sg = sugg.find((s) => s.id === id);
  if (!sg || pickOf(sg)?.guid === guid) return;
  const was = sg.chosen; sg.chosen = guid; renderSuggestions();
  api("POST", "/api/suggestions/choose", { id, guid }).catch((e) => { sg.chosen = was; renderSuggestions(); toast(e.message, "err"); });
}
function suggDetail(sg) {
  const rej = sg.rejected || [], pick = sg.options.length ? pickOf(sg) : null;
  const cards = [ratingCard(sg.before, "Now on disk"), pick ? ratingCard(pick, pick === sg.options[0] ? "Best pick" : "Your pick") : ""].filter(Boolean);
  return `<div class="dd">
    <div class="dd-head"><b>${esc(sg.title)} <span class="yr">${sg.year || ""}</span></b><span class="muted small">searched ${esc(sg.at)} · ${sg.releases} ${sg.kind === "season" ? "season packs" : "releases"}, ${sg.options.length} qualify</span></div>
    ${sg.flagged ? `<p class="dd-flag"><b>${sg.mode === "upgrade" ? "Putting right" : "Flagged"}</b>${esc(sg.flagged)}</p>` : ""}
    ${cards.length ? `<div class="rcards ${cards.length > 1 ? "vs" : ""}">${cards.join('<span class="rarrow" aria-hidden="true">→</span>')}</div>`
      : ""}
    <div class="opt now"><div class="opt-main"><div class="opt-title"><span class="pill-now">Now on disk</span><span class="name">${sg.kind === "season" && sg.episodes ? `${sg.episodes} episodes, such as ` : ""}${esc(sg.before.name || sg.before.quality || "")}</span></div>
      <div class="facts">${facts(sg.before)}</div></div><div class="opt-side">${miniCard(sg.before)}<span class="opt-save muted">${size(sg.before.size)}</span></div></div>
    ${sg.options.length ? `<div class="opts" role="radiogroup" aria-label="Releases for ${esc(sg.title)}">${sg.options.map((o, i) => optionCard(sg, o, i)).join("")}</div>
      <div class="opt-grab"><span class="muted small">Click a release to pick it: the pick is remembered for Grab picks.</span>
        <button type="button" class="btn primary sm" data-pick="${sg.id}" data-guid="${esc(pick.guid)}">Grab ${pick === sg.options[0] ? "the best pick" : "your pick"} · ${size(pick.size)}</button></div>`
      : `<p class="problem">Nothing qualified of ${sg.releases} releases. The reasons are below.</p>`}
    <div class="dd-cols">
      ${sg.why?.length ? `<div><h4>Why the best pick</h4><ul>${sg.why.map((w) => `<li>${esc(w)}</li>`).join("")}</ul></div>` : ""}
      ${rej.length ? `<div><details ${sg.options.length ? "" : "open"}><summary class="muted small">${rej.length} turned down</summary><ul class="rej">${rej.map((n) => { const m = /^(.*?): (.*)$/.exec(n); return `<li>${m ? `<b>${esc(m[1])}</b>: ${esc(m[2])}` : esc(n)}</li>`; }).join("")}</ul></details></div>` : ""}
    </div></div>`;
}
// what the list shows (1.8.0): find a title, filter, sort
const SG_SORT = {
  at: (a, b) => (a.at < b.at ? 1 : a.at > b.at ? -1 : 0),
  saving: (a, b) => (pickSave(b) ?? -Infinity) - (pickSave(a) ?? -Infinity),
  grow: (a, b) => (pickSave(a) ?? Infinity) - (pickSave(b) ?? Infinity),
  now: (a, b) => (b.before.size || 0) - (a.before.size || 0),
  pick: (a, b) => (pickSize(b) ?? -1) - (pickSize(a) ?? -1),
  rating: (a, b) => (pickRating(b) ?? -1) - (pickRating(a) ?? -1),
  gain: (a, b) => (ratingGain(b) ?? -9) - (ratingGain(a) ?? -9),
  title: (a, b) => a.title.localeCompare(b.title),
};
const pickSave = (s) => (s.options.length ? pickOf(s).saving : null);
const pickSize = (s) => (s.options.length ? pickOf(s).size : null);
const pickRating = (s) => (s.options.length ? pickOf(s).rating ?? null : null);
const ratingGain = (s) => (pickRating(s) != null && s.before.rating != null ? pickRating(s) - s.before.rating : null);
function shownSugg() {
  const q = $("sg-q").value.trim().toLowerCase(), show = $("sg-f-show").value, fr = $("sg-f-rating").value, fres = $("sg-f-res").value;
  const kind = ofKind("sg");
  return sugg.filter((s) => {
    const p = s.options.length ? pickOf(s) : null;
    if (!kind(s)) return false;
    if (q && !s.title.toLowerCase().includes(q)) return false;
    if (show === "options" && !p) return false;
    if (show === "none" && p) return false;
    if (show === "mine" && !(p && p !== s.options[0])) return false;
    if (show === "shrink" && !(p && p.saving >= 0)) return false;
    if (show === "grow" && !(p && p.saving < 0)) return false;
    if (fr && !(p && (p.badge || p.video_tier) === fr)) return false;
    if (fres && !(p && resKey(p.resolution) === fres)) return false;
    return true;
  }).sort(SG_SORT[$("sg-sort").value] || SG_SORT.at);
}
const SG_SEL = "#sg-rows input:checked, #sg-grid input:checked";
const pickedSg = () => [...document.querySelectorAll(SG_SEL)].map((i) => +i.value);
function suggCover(sg, keep) {
  const p = sg.options.length ? pickOf(sg) : null;
  return `<div class="cover" data-id="${sg.id}">
    <label class="cover-sel" title="Select"><input type="checkbox" value="${sg.id}" ${keep.has(String(sg.id)) ? "checked" : ""} aria-label="Select ${esc(sg.title)}"></label>
    <button type="button" class="cover-img" data-open-sugg="${sg.id}" aria-label="Releases for ${esc(sg.title)}">${poster(sg)}</button>
    <div class="cover-meta"><b class="cover-title">${esc(sg.title)}</b> <span class="yr">${sg.year || ""}</span>
      <div class="cover-line">${rp(sg.before)}<span>${size(sg.before.size)}</span>${p ? `<span class="arrow">→</span>${rp(p)}<span>${size(p.size)}</span>` : ""}</div>
      <div class="cover-line">${p ? `${saveText(p.saving)}${p !== sg.options[0] ? '<span class="tag" style="--c: var(--violet)">your pick</span>' : ""}` : '<span class="muted">nothing qualified</span>'}</div></div>
  </div>`;
}
function renderSuggestions() {
  const keep = new Set(pickedSg().map(String)), v = shownSugg(), covers = viewOf("sg") === "covers";
  $("sg-table").hidden = covers; $("sg-grid").hidden = !covers;
  $("sg-grid").innerHTML = covers ? v.map((sg) => suggCover(sg, keep)).join("") || `<p class="empty">No suggestions match.</p>` : "";
  $("sg-rows").innerHTML = covers ? "" : v.map((sg) => {
    const best = sg.options.length ? pickOf(sg) : null, open = expanded.has(`sg-rows:${sg.id}`);
    const mine = best && best !== sg.options[0] ? ' <span class="tag" style="--c: var(--violet)">your pick</span>' : "";
    return `<tr class="row-x ${open ? "open" : ""}" data-id="${sg.id}"><td class="pick"><input type="checkbox" value="${sg.id}" ${keep.has(String(sg.id)) ? "checked" : ""} aria-label="Select ${esc(sg.title)}"></td>
      <td>${esc(sg.title)} <span class="yr">${sg.year || ""}</span>${epCount(sg)}${sg.mode === "upgrade" ? ' <span class="tag" style="--c: var(--cyan)">put right</span>' : ""}</td>
      <td class="nowrap">${fileLine(sg.before)}<div class="muted small">${short(sg.before)}</div></td>
      <td>${best ? `<span class="nowrap">${fileLine(best)}${mine}</span><div class="muted small">${short(best)}</div>` : '<span class="muted">nothing qualified</span>'}</td>
      <td class="r nowrap">${best ? saveText(best.saving) : "–"}</td>
      <td class="r">${sg.options.length}</td><td class="muted small nowrap">${esc(sg.at)}</td></tr>`
      + (open ? `<tr class="detail" data-for="${sg.id}"><td colspan="7">${suggDetail(sg)}</td></tr>` : "");
  }).join("") || `<tr><td colspan="7" class="empty">${sugg.length ? "No suggestions match." : "No suggestions yet. Pick films or seasons under Flagged (or Replaced) and press Find replacements."}</td></tr>`;
  const ofThis = sugg.filter(ofKind("sg")).length;
  $("sg-count").textContent = sugg.length ? `${v.length.toLocaleString()} of ${ofThis.toLocaleString()} ${kindOf("sg") === "tv" ? "seasons" : "films"}` : "";
  $("sg-busy").textContent = status?.suggesting ? `${status.suggesting} still to search…` : "";
  if (dlgSugg != null && $("film-dlg").open) { const sg = sugg.find((s) => s.id === dlgSugg); if (sg) $("film-dlg-body").innerHTML = suggDetail(sg); }
  updateSgSel();
}
function updateSgSel() {
  const picked = pickedSg(), n = picked.length;
  const sel = sugg.filter((s) => picked.includes(s.id) && s.options.length);
  $("sg-again").disabled = $("sg-clear").disabled = !n;
  $("sg-grab").disabled = !sel.length; $("sg-grab").textContent = sel.length ? `Grab ${sel.length} pick${sel.length === 1 ? "" : "s"}` : "Grab picks";
  let now = 0, after = 0, freed = 0, added = 0;
  const ratingsBefore = [], ratingsAfter = [];
  for (const s of sel) {
    const p = pickOf(s), b = (s.before.size || 0) / 1e9, a = p.size / 1e9;
    now += b; after += a;
    if (a < b) freed += b - a; else added += a - b;
    if (s.before.rating != null && p.rating != null) { ratingsBefore.push(s.before.rating); ratingsAfter.push(p.rating); }
  }
  $("sg-est").hidden = !sel.length;
  if (sel.length) {
    $("sg-est").innerHTML = estTiles({ what: sel.length === 1 ? "Film with a pick" : "Films with a pick", n: sel.length, now, after, freed, added, real: true, ratingsBefore, ratingsAfter })
      + (n > sel.length ? `<div class="est-foot"><span class="muted small">${n - sel.length} selected with nothing to grab aren't counted.</span></div>` : "");
  }
}
for (const box of ["sg-rows", "sg-grid"]) $(box).addEventListener("change", updateSgSel);
$("sg-all").addEventListener("change", (e) => { document.querySelectorAll("#sg-rows input, #sg-grid input").forEach((i) => (i.checked = e.target.checked)); updateSgSel(); });
$("sg-all-c").addEventListener("change", (e) => { document.querySelectorAll("#sg-grid input").forEach((i) => (i.checked = e.target.checked)); updateSgSel(); });
$("lib-all-c").addEventListener("change", (e) => { document.querySelectorAll("#lib-grid input").forEach((i) => (i.checked = e.target.checked)); updateLibQueue(); });
$("sg-q").addEventListener("input", renderSuggestions);
for (const id of ["sg-f-show", "sg-f-rating", "sg-f-res", "sg-sort"]) $(id).addEventListener("change", renderSuggestions);
$("sg-grid").addEventListener("click", (e) => { const b = e.target.closest("[data-open-sugg]"); if (b) openSugg(+b.dataset.openSugg); });
$("sg-rows").addEventListener("keydown", (e) => {
  const c = e.target.closest("[data-choose]");
  if (c && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); choosePick(+c.dataset.choose, c.dataset.guid); }
});
$("sg-rows").addEventListener("click", (e) => {
  const c = e.target.closest("[data-choose]");
  if (c && !e.target.closest("a, button")) { choosePick(+c.dataset.choose, c.dataset.guid); return; }
  const p = e.target.closest("[data-pick]");
  if (p) { p.disabled = true; act(async () => { const r = await api("POST", "/api/pick", { id: +p.dataset.pick, guid: p.dataset.guid }); setTimeout(() => loadSuggestions(), 4000); return r; }); return; }
  if (e.target.closest("input, a, button, label, .dd")) return;
  const tr = e.target.closest("tr.row-x"); if (!tr) return;
  const key = `sg-rows:${tr.dataset.id}`; expanded.has(key) ? expanded.delete(key) : expanded.add(key); renderSuggestions();
});
$("sg-again").addEventListener("click", () => act(() => api("POST", "/api/suggest", { ids: pickedSg() })));
$("sg-grab").addEventListener("click", () => {
  $("sg-grab").disabled = true;
  act(async () => { const r = await api("POST", "/api/pick/best", { ids: pickedSg() }); setTimeout(() => loadSuggestions(), 4000); return r; });
});
$("sg-clear").addEventListener("click", () => act(async () => { const r = await api("POST", "/api/suggestions/dismiss", { ids: pickedSg() }); await loadSuggestions(); return r; }));

document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-kind-for] [data-kind]"); if (!b) return;
  const page = b.closest("[data-kind-for]").dataset.kindFor;
  if (kindOf(page) === b.dataset.kind) return;
  if ((page === "rules" || page === "quality") && !tracked.get(page).btn.disabled && !confirm("Switching drops the unsaved changes on this page. Carry on?")) return;
  try { localStorage.setItem(KIND_KEY + page, b.dataset.kind); } catch { /* private window */ }
  paintKinds();
  ({ lib: () => { libShown = 200; renderFilms(); }, sg: renderSuggestions, rules: loadRules, quality: loadQuality })[page]();
});
track("rules", () => JSON.stringify(rulesPatch()));
track("quality", () => JSON.stringify([qv, qMin, qPrefer]));
$("q-prefer").addEventListener("click", (e) => {
  const b = e.target.closest("[data-p]"); if (!b) return; qPrefer = b.dataset.p;
  $("q-prefer").querySelectorAll("[data-p]").forEach((x) => { x.setAttribute("aria-pressed", x === b); x.setAttribute("aria-checked", x === b); });
});
track("settings", () => JSON.stringify(settingsPatch()));
track("timing", () => JSON.stringify(TIMINGS.map((k) => $("timing")[k].value)));
track("conn", () => JSON.stringify(["radarr_url", "radarr_api_key", "radarr_link", "refetcharr_url"].map((k) => $("conn")[k].value.trim())));
track("sconn", () => JSON.stringify([...["sonarr_url", "sonarr_api_key", "sonarr_link"].map((k) => $("sconn")[k].value.trim()), $("sconn").tv_enabled.checked]));
track("security", () => JSON.stringify(["username", "password", "confirm"].map((k) => $("security")[k].value)));
showTab(); refresh(); setInterval(refresh, 5000);
