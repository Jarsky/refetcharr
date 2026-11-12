"""The worker: scans the library, keeps the download queue topped up within the limits, and swaps in finished
downloads after checking them. It runs in one background thread inside the server, so only one thing ever changes
the queue; the TUI and the web UI ask it to do things through the API (requests are queued and picked up within
seconds)."""

import collections
import csv
import datetime as dt
import json
import os
import re
import threading
import time
import traceback

from . import VERSION, config, quality, radarr, rules, setup, store

TICK = 15  # seconds between worker loops
IMPORT_EVERY = 120  # seconds between import checks
PROGRESS_BYTES = 1_000_000  # less than this isn't progress: Radarr works sizeleft out from a rounded percentage, so it jitters
RECONCILE_EVERY = 3600  # seconds between looks at films written off, in case Radarr imported our release by itself
SETTLED = ("kept original", "nothing to import", "missing from queue")
WAITING = ("queued", "paused", "delay")  # Radarr's queue statuses for a download waiting its turn (qBittorrent's queue)  # written off, but Radarr may still import it
# The waits are settings (Settings → Timings): a missing download (Radarr's queue can briefly come back without some when
# qBittorrent is slow to answer), an import we asked for, a finished download with no video file yet (scene releases still
# unpacking), a download qBittorrent hasn't completed, how long the library is reused, and how often Radarr's setup is checked.


def wait(name: str) -> float:
    """A timing setting in seconds."""
    v = config.load()["timing"][name]
    return v * 3600 if name.endswith("_hours") else v * 60


def flag_reason(m: dict, r: dict, q: dict | None = None) -> str:
    """Why a film is in its group, in words, with the numbers."""
    g = rules.group(m, r, q)
    if not g:
        return ""
    f = m["movieFile"]
    size, mbm = f["size"] / 1e9, rules.mb_per_min(m)
    q_name = f["quality"]["quality"]["name"]
    return {"remux": f"{q_name}: a remux ({size:.1f} GB, {mbm:.0f} MB/min) is always flagged",
            "remux4k": f"{q_name}: a 4K remux ({size:.1f} GB, {mbm:.0f} MB/min) is always flagged",
            "big4k": f"{q_name} at {mbm:.0f} MB/min ({size:.1f} GB) is over the 4K maximum of {r['cap_2160']} MB/min",
            "big1080": f"{q_name} at {mbm:.0f} MB/min ({size:.1f} GB) is over the 1080p maximum of {r['cap_1080']} MB/min",
            "tiny": f"x265 at {mbm:.0f} MB/min ({size:.1f} GB) is under the too-small mark of "
                    f"{r['tiny_2160'] if rules.resolution(m) >= 2160 else r['tiny_1080']} MB/min",
            "lowq": f"{q_name} picture rates LOW for {quality.res_label(rules.resolution(m))} "
                    f"({rules.current(m, q)['picture_mbps']:g} Mbps in x265 terms; Settings → Quality)"}[g]


def grab_problem(err: Exception) -> str:
    """Radarr's answer to a refused grab, in words. It says "Getting release from indexer failed" whatever went wrong; in
    practice that's mostly qBittorrent refusing a torrent it already has (a 409 Conflict in Radarr's log)."""
    text = str(err)
    if "Getting release from indexer failed" in text:
        return ("Radarr couldn't add the release: most often qBittorrent already has this torrent (the same release via "
                "another indexer, or an earlier download still seeding), otherwise the indexer refused the download.")
    m = re.search(r'"message":\s*"([^"]+)"', text)
    return f"Radarr couldn't grab it: {(m.group(1) if m else text.strip().splitlines()[-1])[:140]}"


def _score(d: dict) -> dict:
    """Just a file's ratings, for a list row's rating pill."""
    return {k: d.get(k) for k in ("rating", "video_rating", "audio_rating", "badge", "video_tier")}


def scan_row(m: dict, g: str | None, r: dict, q: dict | None) -> dict:
    """One film's row in the scan (the Oversized list and the CSV reports)."""
    f, mins = m["movieFile"], rules.minutes(m)
    mi = f.get("mediaInfo") or {}
    d = quality.describe_file(f, mins, q)
    return {"id": m["id"], "title": m["title"], "year": m.get("year") or 0, "group": g or "", "size_gb": round(f["size"] / 1e9, 2),
            "runtime_min": round(mins), "mb_per_min": round(rules.mb_per_min(m)), "quality": f["quality"]["quality"]["name"],
            "resolution": rules.resolution(m), "video_codec": mi.get("videoCodec") or "", "dynamic_range": d["hdr"] or "SDR",
            "audio_languages": mi.get("audioLanguages") or "", "english_audio": rules.has_english(m),
            "saving_gb": round(rules.saving(m, r) / 1e9, 1), "slug": m.get("titleSlug") or str(m.get("tmdbId", "")),
            "path": f.get("path") or "", "release_group": rules.file_group(m), "picture_mbps": d["picture_mbps"],
            "audio": d["audio"], "channels": d["channels"], "atmos": d["atmos"], "video_tier": d["video_tier"],
            "audio_tier": d["audio_tier"], "badge": d["badge"], "rating": d["rating"], "video_rating": d["video_rating"],
            "audio_rating": d["audio_rating"], "video_bounds": d["video_bounds"], "audio_bounds": d["audio_bounds"],
            "hdr": bool(d["hdr"]), "lossless": d["lossless"]}


class Engine:
    def __init__(self):
        self.jobs: collections.deque = collections.deque()
        self.manual_ids: collections.deque = collections.deque()  # films asked for by name, done first
        self.manual_count = 0  # "queue N now": this many more of the biggest wins
        self.upgrades: dict = {}  # films queued again to be put right: id -> {"need": ..., "previous": ...}
        self.busy = ""  # what the worker is doing right now
        self.error = ""  # the last thing that went wrong talking to Radarr
        self.movies: list = []
        self.movies_at = 0.0
        self.scan_summary = self._load_json("scan.json", {}).get("summary", {})
        self.scan_at = self._load_json("scan.json", {}).get("at", "")
        self._last_import = 0.0
        self._last_setup = 0.0
        self._last_reconcile = 0.0
        self.setup_issues: list = []  # setup checks that fail and matter (shown as a badge on Settings)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    # ── requests from the TUI and web UI ────────────────────────────────────
    def queue_now(self, count: int = 0, ids: list | None = None) -> str:
        """Films asked for by hand start one after another straight away: the downloads-at-once and daily limits and the
        schedule are for auto-queue, so they don't hold these back. Pause still does."""
        for i in ids or []:
            if int(i) not in self.manual_ids:
                self.manual_ids.append(int(i))
        self.manual_count += max(0, int(count))
        what = ", ".join(filter(None, [f"{len(ids)} chosen film(s)" if ids else "", f"the next {count} biggest wins" if count else ""]))
        store.log("queue", f"Queued {what}")
        held = " Refetcharr is paused: they start when you resume." if config.load()["queue"].get("paused") else ""
        return f"Queued {what}. They start one after another now, whatever the limits.{held}"

    def clear_manual(self) -> str:
        self.manual_ids.clear()
        self.manual_count = 0
        store.log("queue", "Cleared the manual queue")
        return "Cleared what was waiting to be searched (downloads already running carry on)."

    def request_scan(self) -> str:
        self.jobs.append("scan")
        return "Rescan started. It can take some time; the page updates when it finishes."

    def retry(self, ids: list | None = None, match: str = "") -> dict:
        """Forget films that weren't replaced, so they're tried again. Replaced and in-progress films are untouched."""
        state = store.load()
        pick = [mid for mid, s in state.items() if s["status"] not in store.DONE + store.PENDING
                and (not ids or int(mid) in {int(i) for i in ids}) and (not match or match.lower() in s["title"].lower())]
        gone = store.remove(pick)
        for s in gone:
            store.log("retry", f"{s['title']} ({s.get('year')}) will be tried again (was {s['status']})")
        return {"retried": [{"id": s["id"], "title": s["title"], "was": s["status"]} for s in gone]}

    # ── state for the dashboard ─────────────────────────────────────────────
    def status(self) -> dict:
        s = config.load()
        q = s["queue"]
        state = store.load()
        counts = collections.Counter(e["status"] for e in state.values())
        try:
            live = {str(x.get("movieId")): x for x in radarr.queue()}
        except radarr.RadarrError as e:
            live, self.error = {}, str(e)
        active = []
        for mid, e in state.items():
            if e["status"] in store.PENDING:
                x = live.get(mid) or {}
                # sizeleft can come back slightly negative, which isn't "more than finished"
                pct = min(100, max(0, round(100 * (1 - (x.get("sizeleft") or 0) / max(x.get("size") or 1, 1))))) if x else None
                active.append({"id": mid, "title": e["title"], "year": e.get("year"), "status": e["status"], "progress": pct,
                               "timeleft": x.get("timeleft"), "state": x.get("trackedDownloadState"), "old_size": e["old_size"],
                               "new_size": e.get("new_size"), "new_title": e.get("new_title"), "grabbed": e.get("grabbed"),
                               "stalled": bool(e.get("stalled")), "stalled_hours": self._hours_since(e.get("progress_at")) if e.get("stalled") else None,
                               "message": x.get("errorMessage") or "", **self._finishing(e, x)})
        failed = [{"id": mid, "title": e["title"], "year": e.get("year"), "status": e["status"], "problem": e.get("problem", ""),
                   "old_size": e["old_size"]} for mid, e in state.items() if e["status"] not in store.DONE + store.PENDING]
        done = [e for e in state.values() if e["status"] == "replaced"]
        grown = [e for e in done if store.freed(e) < 0]  # tiny files swapped for bigger ones
        replaced = sorted(([mid, e] for mid, e in state.items() if e["status"] == "replaced"), key=lambda x: x[1].get("grabbed", ""), reverse=True)
        by_id = {str(m["id"]): m for m in self.movies}  # the cached library (no Radarr call): rates films replaced before 1.5
        return {
            "version": VERSION,
            "radarr": config.radarr_url(), "radarr_link": s["connections"]["radarr_link"] or config.radarr_url(), "error": self.error,
            "paused": q.get("paused", False), "auto": q["auto"], "in_window": self.in_window(q),
            "limits": {"max_active": q["max_active"], "max_per_day": q["max_per_day"]}, "groups": q["groups"],
            "today": store.grabbed_on(dt.date.today()),
            "busy": self.busy,
            "setup_issues": self.setup_issues,
            "waiting": {"ids": list(self.manual_ids), "count": self.manual_count},
            "suggesting": sum(1 for j in self.jobs if isinstance(j, tuple) and j[0] == "suggest"),
            "suggestions": len(self._load_json("suggestions.json", {})),
            "counts": dict(counts),
            "freed": sum(store.freed(e) for e in done if store.freed(e) > 0),
            "grown": {"films": len(grown), "bytes": -sum(store.freed(e) for e in grown)},
            "active": active, "failed": failed, "stalled": sum(1 for a in active if a["stalled"]), "stuck": sum(1 for a in active if a["finish_warn"]),
            "replaced": [{"id": mid, "title": e["title"], "year": e.get("year"), "old_size": e["old_size"], "new_size": e.get("new_size"),
                          "old_quality": e.get("old_quality"), "freed": store.freed(e), "when": e.get("grabbed"), "problem": e.get("problem", ""),
                          "group": e.get("group") or "", "before_badge": ((e.get("decision") or {}).get("before") or {}).get("badge", ""),
                          "after_badge": self._after_badge(e, by_id.get(mid), s.get("quality"))}
                         for mid, e in replaced[:100]],
            "scan": {"at": self.scan_at, "summary": self.scan_summary},
            "activity": store.recent(40),
        }

    def films(self) -> list:
        """The latest scan's flagged films, with what Refetcharr has done with each and whether it's left alone (worked
        out now, so a change to the ignore lists shows without a rescan)."""
        rows = self._load_json("scan.json", {}).get("films", [])
        state = store.load()
        s = config.load()
        ig = s["ignore"]
        if not self.movies and any("rating" not in r for r in rows):  # rows scanned before 1.8 are rated from the library
            try:
                self.library()
            except radarr.RadarrError:
                pass  # they show their old tier until Radarr can be read
        by_id = {m["id"]: m for m in self.movies}
        for r in rows:
            m = by_id.get(r["id"])
            if "rating" not in r and m and m.get("movieFile"):  # scanned before 1.8: rate it from the library
                d = quality.describe_file(m["movieFile"], rules.minutes(m), s.get("quality"))
                r.update({k: d[k] for k in ("picture_mbps", "audio", "channels", "atmos", "video_tier", "audio_tier", "badge",
                                            "rating", "video_rating", "audio_rating", "video_bounds", "audio_bounds", "lossless")})
                r["hdr"] = bool(d["hdr"])
            e = state.get(str(r["id"]))
            r["refetch"] = e["status"] if e else ""
            r["problem"] = (e or {}).get("problem", "")
            r["ignored"] = rules.ignored({"id": r["id"], "movieFile": {"releaseGroup": r.get("release_group", "")}}, ig)
        return rows

    def ignore(self, ids: list, on: bool = True) -> str:
        """Leave films alone (or stop): the queue skips them, and they show as ignored in the Oversized list."""
        ids = [int(i) for i in ids]
        cur = config.load()["ignore"]["films"]
        new = sorted(set(cur) | set(ids)) if on else [i for i in cur if i not in ids]
        config.save({"ignore": {"films": new}})
        store.log("queue", f"{'Ignoring' if on else 'Stopped ignoring'} {len(ids)} film(s)")
        return f"{'Ignoring' if on else 'No longer ignoring'} {len(ids)} film(s)."

    # ── the worker ──────────────────────────────────────────────────────────
    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="refetcharr-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        store.log("start", f"Refetcharr {VERSION} started")
        while not self._stop.is_set():
            try:
                self.tick()
                self.error = ""
            except radarr.RadarrError as e:
                self.error = str(e)
            except Exception:  # keep the worker alive; show what broke
                self.error = traceback.format_exc(limit=3)
                store.log("error", "Worker error: " + self.error.strip().splitlines()[-1])
            finally:
                self.busy = ""
            self._stop.wait(TICK)

    def tick(self) -> None:
        s = config.load()
        while self.jobs:
            job = self.jobs.popleft()
            if job == "scan":
                self.scan()
            elif isinstance(job, tuple) and job[0] == "film":
                self.refresh_film(job[1], job[2] if len(job) > 2 else "")
            elif isinstance(job, tuple) and job[0] == "suggest":
                self.suggest_one(job[1])
                break  # one search per tick: each is a search on every indexer
            elif isinstance(job, tuple) and job[0] == "pick":
                self.pick_now(job[1], job[2])
        if s["scan"]["every_days"] and self._scan_due(s["scan"]["every_days"]):
            self.scan()
        if time.time() - self._last_setup > wait("setup_minutes"):
            self.check_setup()
        if time.time() - self._last_reconcile > RECONCILE_EVERY:
            self.reconcile()
        if time.time() - self._last_import > IMPORT_EVERY:
            self.import_finished()
            self._last_import = time.time()
        self.refill(s)

    def check_setup(self) -> list:
        self._last_setup = time.time()
        try:
            movies = self.library()
        except radarr.RadarrError:
            movies = []
        all_checks = setup.checks(movies)
        self.setup_issues = [c["label"] for c in all_checks if not c["ok"] and c["level"] == "error"]
        return all_checks

    def in_window(self, q: dict) -> bool:
        w = q.get("window") or {}
        if not w.get("enabled"):
            return True
        now = dt.datetime.now()
        hm = now.strftime("%H:%M")
        inside = w["start"] <= hm <= w["end"] if w["start"] <= w["end"] else (hm >= w["start"] or hm <= w["end"])  # overnight windows
        return now.weekday() in w.get("days", []) and inside

    def refill(self, s: dict) -> None:
        """Start one more search. Films asked for by hand go first and ignore the limits and schedule (only Pause holds
        them); auto-queue needs a free slot, room in today's limit, and the schedule."""
        q = s["queue"]
        if q.get("paused"):
            return
        state = store.load()
        if not (self.manual_ids or self.manual_count or self._auto_room(q, state)):
            return
        movies = self.library()
        by_id = {m["id"]: m for m in movies}
        mid = None
        while self.manual_ids and mid is None:
            c = self.manual_ids.popleft()
            if str(c) not in state and c in by_id:  # chosen by hand: tried even if its group is off or it's ignored
                mid = c
        if mid is None and (self.manual_count > 0 or self._auto_room(q, state)):
            mid = next(iter(self.candidates(movies, s, state)), None)
            if self.manual_count > 0:
                self.manual_count = self.manual_count - 1 if mid is not None else 0  # nothing left to queue: stop asking
        if mid is not None:
            up = self.upgrades.pop(mid, None)
            self.try_film(by_id[mid], s, "upgrade" if up else "", (up or {}).get("need"), (up or {}).get("previous"))

    def _auto_room(self, q: dict, state: dict) -> bool:
        """Whether auto-queue may start another film: it's on, inside the schedule, with a free slot and room today."""
        if not (q["auto"] and self.in_window(q)):
            return False
        # a stalled download isn't using its slot: it stays tracked, and imports if it ever finishes
        if sum(1 for e in state.values() if e["status"] in store.PENDING and not e.get("stalled")) >= q["max_active"]:
            return False
        return not (q["max_per_day"] and store.grabbed_on(dt.date.today()) >= q["max_per_day"])

    def candidates(self, movies: list, s: dict, state: dict | None = None) -> list:
        """Film ids worth trying from the groups switched on, leaving out ignored films: the biggest savings first, then
        the tiny files, the smallest first (those cost space rather than free it)."""
        state = store.load() if state is None else state
        on = {g for g, v in s["queue"]["groups"].items() if v}
        r, ig = s["rules"], s["ignore"]
        out = []
        for m in movies:
            g = rules.group(m, r, s.get("quality"))
            if str(m["id"]) in state or g not in on or rules.ignored(m, ig):
                continue
            out.append(((1, rules.mb_per_min(m)) if g in rules.GROWABLE + rules.UPGRADE else (0, -rules.saving(m, r)), m["id"]))
        return [mid for _, mid in sorted(out)]

    def _base(self, m: dict, s: dict, mode: str, need: dict | None, previous: dict | None) -> tuple[dict, dict]:
        r, q = s["rules"], s.get("quality")
        f = m["movieFile"]
        before = rules.current(m, q)
        needs = rules.needs(before, r)
        for k, v in (need or {}).items():  # what's being won back counts too
            needs[k] = needs.get(k) or v
        base = {"title": m["title"], "year": m.get("year"), "old_size": f["size"], "old_quality": f["quality"]["quality"]["name"],
                "english": rules.has_english(m), "group": "upgrade" if mode == "upgrade" else rules.group(m, r, q),
                "grabbed": time.strftime("%Y-%m-%d %H:%M"), "needs": needs}
        if previous:
            base["previous"] = previous
        return base, before

    def _flagged(self, m: dict, s: dict, mode: str, need: dict | None) -> str:
        if mode != "upgrade":
            return flag_reason(m, s["rules"], s.get("quality"))
        won = [k for k, v in (need or {}).items() if v]
        return "queued again to be put right" + (f": it should have {', '.join(won)}" if won else ": a better picture")

    def try_film(self, m: dict, s: dict, mode: str = "", need: dict | None = None, previous: dict | None = None) -> None:
        r, q = s["rules"], s.get("quality")
        f = m["movieFile"]
        label = f"{m['title']} ({m.get('year')})"
        self.busy = f"Searching for {label}"
        base, before = self._base(m, s, mode, need, previous)
        releases = radarr.releases(m["id"])
        ranked: list = []
        rel, notes = rules.best(m, releases, r, s["ignore"]["release_groups"], q, keep=ranked, mode=mode, need=base["needs"])
        decision = {"flagged": self._flagged(m, s, mode, need), "before": before, "rejected": notes[:40], "releases": len(releases),
                    "searched": time.strftime("%Y-%m-%d %H:%M")}
        refused = []
        for rel in ranked[:3]:  # a grab Radarr refuses (qBittorrent already has the torrent...) moves on to the next best
            try:
                self._grab(m, s, rel, ranked, releases, base, dict(decision, rejected=refused + decision["rejected"]))
                return
            except radarr.RadarrError as err:
                refused.append(f"{rel['title'][:70]}: {grab_problem(err)}")
        notes = refused + notes
        store.update(m["id"], **base, new_size=f["size"], status="no release", problem=(notes[0] if notes else "no releases found")[:200],
                     decision=dict(decision, rejected=notes[:40]))
        store.log("skip", f"{label}: no release qualifies ({len(notes)} found)" if not refused else f"{label}: {len(refused)} grab(s) refused by Radarr")

    def _grab(self, m: dict, s: dict, rel: dict, ranked: list, releases: list, base: dict, decision: dict, by_hand: bool = False,
              replace: bool = False) -> None:
        """Grab the release, then record it. `replace`: the film's earlier record is swapped for the new one, but only once
        the grab has worked (a refused grab leaves the film as it was)."""
        r, q = s["rules"], s.get("quality")
        f = m["movieFile"]
        label = f"{m['title']} ({m.get('year')})"
        why = rules.explain(m, rel, ranked, len(releases), r, q)
        if by_hand:
            why = ["picked by hand from the suggestions"] + why[1:]
        decision.update(after=quality.describe_release(rel, rules.minutes(m), q), why=why,
                        runners_up=[{"title": x["title"], "size": x["size"], "seeders": x.get("seeders")} for x in ranked if x is not rel][:5])
        radarr.grab(rel, m["id"])
        if replace:
            store.remove([str(m["id"])])
        store.update(m["id"], **base, new_title=rel["title"], new_size=rel["size"], status="grabbed", decision=decision, problem="")
        diff = (f["size"] - rel["size"]) / 1e9
        gain = f"saves {diff:.1f} GB" if diff >= 0 else f"a bigger file, {-diff:.1f} GB more"
        store.log("grab", f"{label}: grabbed {rel['quality']['quality']['name']} {rel['size'] / 1e9:.1f} GB "
                          f"({rel.get('seeders')} seeders), {gain}", id=m["id"])

    def import_finished(self) -> None:
        """Swap in downloads that have finished: check the file, then a manual import with override (Radarr won't
        import a smaller file of the same quality by itself)."""
        state = store.load()
        pending = {mid: e for mid, e in state.items() if e["status"] in store.PENDING + ("missing from queue", "nothing to import")}
        if not pending:
            return
        self.busy = "Checking finished downloads"
        r = config.load()["rules"]
        live = {str(x.get("movieId")): x for x in radarr.queue()}
        for mid, e in pending.items():
            x = live.get(mid)
            if x is None:
                if e["status"] not in ("missing from queue", "nothing to import"):
                    self._settle(mid, e)
                continue
            if e["status"] == "missing from queue" or e.get("missing_since"):  # it was only briefly out of the queue
                store.update(mid, status="downloading", missing_since="", problem="")
                if e["status"] == "missing from queue":
                    store.log("queue", f"{e['title']}: back in Radarr's queue, carrying on")
            if x.get("status") != "completed":
                if e["status"] != "downloading":
                    store.update(mid, status="downloading")
                self._track_progress(mid, e, x, r)
                continue
            if e.get("stalled"):
                store.update(mid, stalled=False)
            if x.get("trackedDownloadState") not in ("importPending", "importBlocked", "importFailed"):
                continue  # Radarr is moving the file right now
            asked = self._hours_since(e.get("import_asked"))
            if e["status"] == "importing" and asked is not None and asked * 3600 < wait("import_wait_minutes"):
                continue  # already asked: a second request makes the file disappear from under the first
            try:
                items = radarr.manual_import_items(x["downloadId"], int(mid))
            except radarr.RadarrError as err:  # one slow lookup shouldn't hold up every other film; next check tries again
                print(f"{e['title']}: import lookup failed: {err}", flush=True)
                continue
            vids = [i for i in items if i.get("movie") and re.search(r"\.(mkv|mp4|m4v|avi)$", i["path"], re.I)]
            if not vids:
                since = e.get("no_video_since") or dt.datetime.now().isoformat(timespec="seconds")
                if not e.get("no_video_since"):
                    store.update(mid, no_video_since=since)
                if (self._hours_since(since) or 0) * 3600 >= wait("no_video_hours") and e["status"] != "nothing to import":
                    store.update(mid, status="nothing to import", problem="the download has no video file")
                    store.log("fail", f"{e['title']}: the download has no video file; the original stays")
                continue
            big = max(vids, key=lambda i: i.get("size") or 0)
            problem = rules.file_problem(big["path"], e.get("english", True) and r["keep_english"], config.FFPROBE, r["reject_dv_no_hdr10"],
                                         r["untagged_is_english"], e.get("needs"))
            if problem:
                store.update(mid, status="kept original", problem=problem)
                store.log("fail", f"{e['title']}: not imported, {problem}. The original stays; the download is left in qBittorrent.")
                continue
            radarr.manual_import(big, int(mid), x["downloadId"])
            store.update(mid, status="importing", checked=True, problem="", no_video_since="",
                         import_asked=dt.datetime.now().isoformat(timespec="seconds"))
            store.log("import", f"{e['title']}: importing {os.path.basename(big['path'])}")
        time.sleep(10)
        for mid, e in store.load().items():
            if e["status"] == "importing":
                f = radarr.movie(int(mid)).get("movieFile") or {}
                grow = e.get("group") in rules.GROWABLE  # a tiny file is replaced by a bigger one
                better = e.get("group") in rules.UPGRADE + ("upgrade",)  # any size: it's about quality
                if f.get("size") and (self._ours(f, e) if better else f["size"] > e["old_size"] if grow else f["size"] < e["old_size"]):
                    store.update(mid, status="replaced", saved=e["old_size"] - f["size"], after_file=self._describe(f, e))
                    store.log("done", f"{e['title']}: replaced, " + (f"{(f['size'] - e['old_size']) / 1e9:.1f} GB bigger" if f["size"] > e["old_size"]
                              else f"{(e['old_size'] - f['size']) / 1e9:.1f} GB freed"), id=int(mid))

    def _hours_since(self, stamp: str | None) -> float | None:
        try:
            return round((dt.datetime.now() - dt.datetime.fromisoformat(stamp)).total_seconds() / 3600, 1)
        except (TypeError, ValueError):
            return None

    def _finishing(self, e: dict, x: dict) -> dict:
        """Every byte received but qBittorrent hasn't completed it, so Radarr can't import it yet. It keeps its slot:
        it's waiting on the disk, and more downloads would only add to that."""
        waiting = bool(x) and (x.get("sizeleft") or 0) <= 0 and x.get("status") != "completed"
        hours = self._hours_since(e.get("progress_at")) if waiting else None
        minutes = round(hours * 60) if hours is not None else None
        return {"finishing": waiting, "finishing_minutes": minutes, "finish_warn": bool(minutes is not None and minutes * 60 >= wait("finish_warn_minutes") and not e.get("stalled"))}

    def _track_progress(self, mid: str, e: dict, x: dict, r: dict) -> None:
        """Warn when a download hasn't got any further for a long time. It only warns: the download keeps its slot and
        carries on if it starts moving again. Progress means bytes left falling below the lowest seen so far."""
        left = max(x.get("sizeleft") or 0, 0)
        best = e.get("left_best")
        fields: dict = {}
        if (x.get("status") or "").lower() in WAITING:  # waiting its turn in qBittorrent's queue: not stalled
            fields = {"left_best": left, "progress_at": dt.datetime.now().isoformat(timespec="seconds")}
        elif best is None:  # first look at this download: all we know is when it was grabbed
            fields = {"left_best": left, "progress_at": (e.get("grabbed") or "").replace(" ", "T") or dt.datetime.now().isoformat(timespec="seconds")}
        elif left < best - PROGRESS_BYTES:
            fields = {"left_best": left, "progress_at": dt.datetime.now().isoformat(timespec="seconds")}
        hours = self._hours_since(fields.get("progress_at") or e.get("progress_at"))
        limit = r["stall_warn_hours"]
        stalled = bool(limit and hours is not None and hours >= limit)
        if stalled != bool(e.get("stalled")):
            fields["stalled"] = stalled
            if stalled:
                why = x.get("errorMessage") or "no progress"
                store.log("warn", f"{e['title']}: no progress for {hours:.0f} hours ({why})", id=int(mid))
            else:
                store.log("queue", f"{e['title']}: moving again")
        if left <= 0 and hours is not None and hours * 3600 >= wait("finish_warn_minutes") and not e.get("finish_warned"):
            fields["finish_warned"] = True
            store.log("warn", f"{e['title']}: fully downloaded {hours:.0f} h ago, but qBittorrent hasn't completed it, so it can't import yet", id=int(mid))
        if fields:
            store.update(mid, **fields)

    @staticmethod
    def _after_badge(e: dict, m: dict | None, q: dict | None) -> str:
        if (e.get("after_file") or {}).get("badge"):
            return e["after_file"]["badge"]
        if m and m.get("movieFile"):
            return quality.describe_file(m["movieFile"], rules.minutes(m), q)["badge"]
        return ""

    @staticmethod
    def _describe(f: dict, e: dict) -> dict:
        """The new file as Radarr reports it, for the film's details."""
        before = (e.get("decision") or {}).get("before") or {}
        return quality.describe_file(f, before.get("runtime_min") or 0, config.load().get("quality"))

    @staticmethod
    def _ours(f: dict, e: dict) -> bool:
        """Whether the film's current file is the release we grabbed: Radarr keeps its name (sceneName), and the size is
        a fallback. The size alone isn't enough: an indexer's listed size can be 10% off the file (GiB, extra files)."""
        if not f.get("size"):
            return False
        norm = lambda s: re.sub(r"[^a-z0-9]", "", re.sub(r"\.(mkv|mp4|m4v|avi)$", "", (s or "").lower()))
        want = norm(e.get("new_title"))
        names = {norm(f.get("sceneName")), norm((f.get("relativePath") or "").split("/")[-1]), norm((f.get("originalFilePath") or "").split("/")[-1])}
        return bool(want and want in names) or abs(f["size"] - e.get("new_size", 0)) < e["old_size"] * 0.02

    def _replaced_by_radarr(self, mid: str, e: dict, f: dict) -> None:
        fields = {"status": "replaced", "saved": e["old_size"] - f["size"], "problem": "", "missing_since": ""}
        r = config.load()["rules"]
        problem = rules.file_problem(f["path"], e.get("english", True) and r["keep_english"], config.FFPROBE, r["reject_dv_no_hdr10"],
                                     r["untagged_is_english"], e.get("needs"))
        if problem:
            fields["problem"] = f"imported by Radarr itself, but: {problem}"
        fields["after_file"] = self._describe(f, e)
        store.update(mid, **fields)
        gain = f"{fields['saved'] / 1e9:.1f} GB freed" if fields["saved"] >= 0 else f"{-fields['saved'] / 1e9:.1f} GB bigger"
        store.log("done", f"{e['title']}: replaced (Radarr imported it), {gain}", id=int(mid))

    def reconcile(self) -> None:
        """Films written off (refused, missing, nothing to import) whose release Radarr imported anyway count as replaced."""
        self._last_reconcile = time.time()
        for mid, e in store.load().items():
            if e["status"] in SETTLED and e.get("new_title"):
                try:
                    f = radarr.movie(int(mid)).get("movieFile") or {}
                except radarr.RadarrError:
                    continue
                if self._ours(f, e):
                    self._replaced_by_radarr(mid, e, f)

    def _settle(self, mid: str, e: dict) -> None:
        """A download that left Radarr's queue: Radarr imported it itself (a real upgrade, e.g. 1080p to 2160p), or
        it was removed. Tell them apart by the film's current file."""
        f = radarr.movie(int(mid)).get("movieFile") or {}
        if self._ours(f, e):
            self._replaced_by_radarr(mid, e, f)
            return
        if not e.get("missing_since"):
            store.update(mid, missing_since=dt.datetime.now().isoformat(timespec="seconds"))  # wait before deciding
        elif (dt.datetime.now() - dt.datetime.fromisoformat(e["missing_since"])).total_seconds() >= wait("missing_minutes"):
            store.update(mid, status="missing from queue", problem="the download left Radarr's queue without being imported")
            store.log("fail", f"{e['title']}: the download left Radarr's queue without being imported; the original stays "
                              "(if it comes back, Refetcharr carries on by itself)")

    # ── one film changed (Radarr's import webhook) ──────────────────────────
    def refresh_film(self, mid: int, why: str = "") -> None:
        """Read one film again (Radarr imported or deleted a file): update the library, its row in the Oversized list,
        and log it if it's newly flagged. Auto-queue then sees it like any other flagged film."""
        try:
            m = radarr.movie(int(mid))
        except radarr.RadarrError:
            return
        self.movies = [x for x in self.movies if x["id"] != m["id"]] + [m] if self.movies else self.movies
        s = config.load()
        r = s["rules"]
        g = rules.group(m, r, s.get("quality"))
        scan = self._load_json("scan.json", {})
        films = [x for x in scan.get("films", []) if x["id"] != m["id"]]
        was = len(films) != len(scan.get("films", []))
        if g and m.get("movieFile"):
            films.append(scan_row(m, g, r, s.get("quality")))
            films.sort(key=lambda x: -x["saving_gb"])
            if not was and str(m["id"]) not in store.load():
                on = s["queue"]["groups"].get(g)
                store.log("scan", f"{m['title']} ({m.get('year')}): {why or 'new file'}, now flagged as {rules.GROUPS[g].lower()}"
                                  + (" (auto-queue can pick it up)" if on and s["queue"]["auto"] else ""), id=m["id"])
        if films != scan.get("films", []) and scan:
            scan["films"] = films
            self._save_json("scan.json", scan)

    def detail(self, mid: int) -> dict:
        """Everything about one film for its drop-down: what Refetcharr did, the decision, and the file now."""
        state = store.load().get(str(mid)) or {}
        s = config.load()
        m = next((x for x in self.movies if x["id"] == int(mid)), None)
        if m is None:
            try:
                m = radarr.movie(int(mid))
            except radarr.RadarrError:
                m = {}
        now = quality.describe_file(m.get("movieFile") or {}, rules.minutes(m) if m else 0, s.get("quality")) if m.get("movieFile") else None
        row = next((x for x in self._load_json("scan.json", {}).get("films", []) if x["id"] == int(mid)), None)
        flagged = flag_reason(m, s["rules"], s.get("quality")) if m.get("movieFile") else ""
        return {"id": int(mid), "title": m.get("title") or state.get("title"), "year": m.get("year") or state.get("year"),
                "slug": m.get("titleSlug"), "state": state, "now": now, "flagged_now": flagged, "row": row,
                # what a replacement must keep: recorded at grab, else from the file now, unless that file is the replacement
                "needs": state.get("needs") or (rules.needs(now, s["rules"]) if now and state.get("status") != "replaced" else {})}

    # ── putting replaced films right ────────────────────────────────────────
    def issues(self, e: dict, m: dict | None, q: dict | None) -> tuple[list, dict]:
        """What's wrong with a replaced film's file now: a LOW picture, HDR or surround lost. Also returns what a
        better file should win back (for a replace-again)."""
        now = e.get("after_file") or (quality.describe_file(m["movieFile"], rules.minutes(m), q) if m and m.get("movieFile") else {})
        before = (e.get("decision") or {}).get("before") or {}
        out, need = [], {}
        if now.get("badge") == "LOW":
            out.append(f"rated LOW ({now['rating']:.1f} of 5)" if now.get("rating") is not None else "rated LOW")
        was_hdr = before.get("hdr") if before else "2160" in (e.get("old_quality") or "")  # 4K Blu-rays and remuxes are HDR
        if now and was_hdr and not now.get("hdr"):
            out.append("lost HDR"); need["hdr"] = True
        if now and before.get("channels", 0) >= 5 and 0 < now.get("channels", 0) < 5:
            out.append("lost surround"); need["surround"] = True
        return out, need

    def replaced(self) -> list:
        """Every replaced film, newest first, with its ratings and what's wrong now (Activity → Replaced)."""
        s = config.load()
        q = s.get("quality")
        by_id = {str(m["id"]): m for m in self.library()}
        rows = []
        for mid, e in store.load().items():
            if e["status"] != "replaced":
                continue
            m = by_id.get(mid)
            issues, _ = self.issues(e, m, q)
            now = quality.rated(e.get("after_file"), q) or (quality.describe_file(m["movieFile"], rules.minutes(m), q) if m and m.get("movieFile") else {})
            was = quality.rated((e.get("decision") or {}).get("before"), q) or {}
            rows.append({"before": _score(was), "after": _score(now), "id": int(mid), "title": e["title"], "year": e.get("year"), "old_size": e["old_size"], "new_size": e.get("new_size"),
                         "old_quality": e.get("old_quality"), "freed": store.freed(e), "when": e.get("grabbed"), "problem": e.get("problem", ""),
                         "group": e.get("group") or "", "before_badge": ((e.get("decision") or {}).get("before") or {}).get("badge", ""),
                         "after_badge": now.get("badge", ""), "after_tier": now.get("video_tier", ""), "issues": issues,
                         "again": bool(e.get("previous"))})
        return sorted(rows, key=lambda x: x["when"] or "", reverse=True)

    def requeue(self, ids: list) -> str:
        """Replace films again to put them right (a LOW picture, HDR or surround lost): the next search wants a better file,
        of any size up to the maximum, and what was lost back. Their record so far is kept with the new one."""
        state, s = store.load(), config.load()
        by_id = {str(m["id"]): m for m in self.library()}
        done = []
        for i in ids:
            mid = str(int(i))
            e = state.get(mid)
            if not e or e["status"] in store.PENDING:
                continue
            _, need = self.issues(e, by_id.get(mid), s.get("quality")) if e["status"] == "replaced" else ([], {})
            previous = {k: e[k] for k in ("title", "old_size", "old_quality", "new_title", "new_size", "saved", "grabbed", "status", "previous") if k in e}
            store.remove([mid])
            self.upgrades[int(mid)] = {"need": need, "previous": previous}
            if int(mid) not in self.manual_ids:
                self.manual_ids.append(int(mid))
            done.append(e["title"])
        if done:
            store.log("queue", f"Queued again to put right: {', '.join(done[:5])}{' and more' if len(done) > 5 else ''} ({len(done)})")
        held = " Refetcharr is paused: they start when you resume." if s["queue"].get("paused") else ""
        return f"{len(done)} film(s) queued again: each wants a better file than it has now.{held}"

    # ── suggestions: the qualifying releases for a film, to pick from ───────
    def suggest(self, ids: list) -> str:
        queued = [("suggest", int(i)) for i in ids if ("suggest", int(i)) not in self.jobs]
        self.jobs.extend(queued)
        return f"Finding replacements for {len(queued)} film(s), one search at a time: they show under Library → Suggestions as they finish."

    def suggest_one(self, mid: int) -> None:
        s = config.load()
        r, q = s["rules"], s.get("quality")
        try:
            m = radarr.movie(mid)
        except radarr.RadarrError:
            return
        if not m.get("movieFile"):
            return
        self.busy = f"Finding replacements for {m['title']} ({m.get('year')})"
        e = store.load().get(str(mid)) or {}
        mode, need = ("upgrade", self.issues(e, m, q)[1]) if e.get("status") == "replaced" else ("", {})
        base, before = self._base(m, s, mode, need, None)
        releases = radarr.releases(mid)
        ranked: list = []
        _, notes = rules.best(m, releases, r, s["ignore"]["release_groups"], q, keep=ranked, mode=mode, need=base["needs"])
        mins = rules.minutes(m)
        options, seen = [], set()
        for rel in ranked:
            key = re.sub(r"[^a-z0-9]", "", rel["title"].lower())  # the same release from two indexers: list it once
            if key in seen:
                continue
            seen.add(key)
            if len(options) == 12:
                break
            d = quality.describe_release(rel, mins, q)
            options.append({**d, "guid": rel["guid"], "indexerId": rel.get("indexerId"), "saving": m["movieFile"]["size"] - rel["size"]})
        sugg = self._load_json("suggestions.json", {})
        was = sugg.get(str(mid)) or {}  # your pick survives a new search when the same release is offered again
        old = next((o for o in was.get("options", []) if o["guid"] == was.get("chosen")), None)
        again = next((o for o in options if old and (o["guid"] == old["guid"] or o.get("title") == old.get("title"))), None)
        sugg[str(mid)] = {"id": mid, "title": m["title"], "year": m.get("year"), "slug": m.get("titleSlug"), "at": time.strftime("%Y-%m-%d %H:%M"),
                          "chosen": again["guid"] if again else (options[0]["guid"] if options else None),
                          "mode": mode, "flagged": self._flagged(m, s, mode, need), "before": before, "options": options,
                          "why": rules.explain(m, ranked[0], ranked, len(releases), r, q) if ranked else [],
                          "rejected": notes[:40], "releases": len(releases)}
        self._save_json("suggestions.json", sugg)
        store.log("suggest", f"{m['title']} ({m.get('year')}): {len(options)} replacement(s) to pick from, of {len(releases)} releases", id=mid)

    def suggestions(self) -> list:
        q = config.load().get("quality")
        out = []
        for sg in self._load_json("suggestions.json", {}).values():  # searched before 1.8: rated from the bitrates kept
            out.append({**sg, "before": quality.rated(sg.get("before"), q), "options": [quality.rated(o, q) for o in sg.get("options", [])]})
        return sorted(out, key=lambda x: x["at"], reverse=True)

    def dismiss(self, ids: list) -> str:
        sugg = self._load_json("suggestions.json", {})
        for i in ids:
            sugg.pop(str(int(i)), None)
        self._save_json("suggestions.json", sugg)
        return f"Cleared {len(ids)} suggestion(s)."

    def pick(self, mid: int, guid: str) -> str:
        sugg = self._load_json("suggestions.json", {}).get(str(mid))
        if not sugg or not any(o["guid"] == guid for o in sugg["options"]):
            raise ValueError("That release isn't among the film's suggestions any more: find replacements again")
        if (store.load().get(str(mid)) or {}).get("status") in store.PENDING:
            raise ValueError("The film is already downloading a replacement")
        self.jobs.append(("pick", int(mid), guid))
        return "Grabbing it: it shows under Downloads in a moment."

    def choose(self, mid: int, guid: str) -> str:
        """Remember which of a film's suggestions to grab (Grab picks, and the film's own Grab)."""
        sugg = self._load_json("suggestions.json", {})
        sg = sugg.get(str(mid))
        if not sg or not any(o["guid"] == guid for o in sg["options"]):
            raise ValueError("That release isn't among the film's suggestions any more: find replacements again")
        sg["chosen"] = guid
        self._save_json("suggestions.json", sugg)
        return "Picked."

    @staticmethod
    def chosen(sg: dict) -> dict | None:
        """The option to grab: the one picked, else the best."""
        return next((o for o in sg.get("options", []) if o["guid"] == sg.get("chosen")), None) or (sg.get("options") or [None])[0]

    def pick_best(self, ids: list) -> str:
        """Grab each film's picked option (the best one unless another was picked)."""
        sugg = self._load_json("suggestions.json", {})
        started, skipped = 0, []
        for i in ids:
            sg = sugg.get(str(int(i)))
            if not sg or not sg["options"]:
                skipped.append((sg or {}).get("title") or str(i))
                continue
            try:
                self.pick(int(i), self.chosen(sg)["guid"])
                started += 1
            except ValueError:
                skipped.append(sg["title"])
        msg = f"Grabbing the picked release for {started} film(s): they show under Downloads as each starts."
        return msg + (f" Skipped {len(skipped)} with nothing to grab or already downloading." if skipped else "")

    def pick_now(self, mid: int, guid: str) -> None:
        """Grab the release picked from the suggestions. Radarr only grabs from a recent search, so search again and
        take the same release (by its guid)."""
        s = config.load()
        sugg = self._load_json("suggestions.json", {})
        sg = sugg.get(str(mid))
        try:
            m = radarr.movie(mid)
        except radarr.RadarrError:
            return
        self.busy = f"Grabbing the picked release for {m['title']}"
        releases = radarr.releases(mid)
        rel = next((x for x in releases if x["guid"] == guid), None)
        if rel is None:
            title = next((o["title"] for o in (sg or {}).get("options", []) if o["guid"] == guid), "")
            rel = next((x for x in releases if title and x["title"] == title), None)
        if rel is None:
            store.log("fail", f"{m['title']}: the picked release is no longer listed by the indexers; find replacements again", id=mid)
            return
        e = store.load().get(str(mid)) or {}
        mode = (sg or {}).get("mode", "")
        need = self.issues(e, m, s.get("quality"))[1] if e.get("status") == "replaced" else {}
        previous = {k: e[k] for k in ("title", "old_size", "old_quality", "new_title", "new_size", "saved", "grabbed", "status", "previous") if k in e} if e else None
        base, before = self._base(m, s, mode, need, previous if e.get("status") == "replaced" else None)
        ranked = [rel]
        decision = {"flagged": (sg or {}).get("flagged") or self._flagged(m, s, mode, need), "before": before,
                    "rejected": (sg or {}).get("rejected", []), "releases": (sg or {}).get("releases", len(releases)),
                    "searched": (sg or {}).get("at") or time.strftime("%Y-%m-%d %H:%M")}
        try:
            self._grab(m, s, rel, ranked, releases, base, decision, by_hand=True, replace=bool(e))
        except radarr.RadarrError as err:
            store.log("fail", f"{m['title']}: {grab_problem(err)} The film is unchanged: pick another option or search again.", id=mid)
            return
        sugg.pop(str(mid), None)
        self._save_json("suggestions.json", sugg)

    # ── library and scans ───────────────────────────────────────────────────
    def library(self) -> list:
        if time.time() - self.movies_at > wait("library_minutes") or not self.movies:
            self.busy = self.busy or "Reading the library from Radarr"
            self.movies, self.movies_at = radarr.movies(), time.time()
        return self.movies

    def _scan_due(self, days: int) -> bool:
        try:
            return dt.datetime.now() - dt.datetime.fromisoformat(self.scan_at) > dt.timedelta(days=days)
        except ValueError:
            return True

    def scan(self) -> dict:
        """Read the whole library, group every film, and write the reports (CSV + the HTML page)."""
        self.busy = "Scanning the library"
        self.movies_at = 0
        movies = self.library()
        s = config.load()
        r, q = s["rules"], s.get("quality")
        films, rows = [], []
        for m in movies:
            f = m.get("movieFile")
            mins = rules.minutes(m)
            if not f or not mins:
                continue
            g = rules.group(m, r, q)
            row = scan_row(m, g, r, q)
            rows.append(row)
            if g:
                films.append(row)
        films.sort(key=lambda x: -x["saving_gb"])
        summary = {}
        for x in films:
            n, tb, save = summary.get(x["group"], (0, 0.0, 0.0))
            summary[x["group"]] = (n + 1, tb + x["size_gb"], save + max(x["saving_gb"], 0))
        summary = {g: {"films": n, "gb": round(tb), "saving_gb": round(sv)} for g, (n, tb, sv) in summary.items()}
        summary["_library"] = {"films": len(rows), "gb": round(sum(x["size_gb"] for x in rows))}
        self.scan_at, self.scan_summary = dt.datetime.now().isoformat(timespec="seconds"), summary
        self._save_json("scan.json", {"at": self.scan_at, "summary": summary, "films": films})
        self._write_reports(rows, films)
        store.log("scan", f"Scanned {len(rows)} films: {len(films)} flagged, about "
                          f"{sum(v['saving_gb'] for k, v in summary.items() if k in rules.SHRINKABLE) / 1000:.1f} TB to win")
        return summary

    def _write_reports(self, rows: list, films: list) -> None:
        out = os.path.join(config.DATA, "reports", dt.date.today().isoformat())
        os.makedirs(out, exist_ok=True)
        for name, data in (("movies.csv", rows), ("oversized.csv", films)):
            with open(os.path.join(out, name), "w", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, list(rows[0]) if rows else ["id"])
                w.writeheader()
                w.writerows(sorted(data, key=lambda x: -x["size_gb"]))
        latest = os.path.join(config.DATA, "reports", "latest")
        os.makedirs(latest, exist_ok=True)
        for name in ("movies.csv", "oversized.csv"):
            with open(os.path.join(out, name), "rb") as src, open(os.path.join(latest, name), "wb") as dst:
                dst.write(src.read())

    # ── helpers ─────────────────────────────────────────────────────────────
    def _load_json(self, name: str, default):
        try:
            with open(os.path.join(config.DATA, name)) as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return default

    def _save_json(self, name: str, data) -> None:
        os.makedirs(config.DATA, exist_ok=True)
        tmp = os.path.join(config.DATA, name + ".tmp")
        with open(tmp, "w") as f:
            json.dump(data, f)
        os.replace(tmp, os.path.join(config.DATA, name))
