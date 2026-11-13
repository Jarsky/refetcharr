"""Radarr and Sonarr behind one set of calls (1.9.0), so the engine treats a TV season like a film.

A season is shaped like one of Radarr's movies: its "movieFile" stands for all its episode files (their total size and
runtime, the quality most of its bytes are in, the picture bitrate averaged over its runtime), so the rules, the ratings,
the ranking and every page work on it unchanged. It's replaced by a season pack. When no pack qualifies, each episode
becomes an item of its own (the fallback), replaced by an episode release. Which app an id belongs to is in its number
(refetcharr.ids).

Only seasons that are complete and finished airing are in the library: a pack for a season still airing, or one with
episodes missing, would be judged against a part of the season.
"""

import collections
import re
import threading

from . import config, ids, radarr, sonarr

VIDEO = re.compile(r"\.(mkv|mp4|m4v|avi|ts)$", re.I)
_MULTI = re.compile(r"\bS\d{1,2}\s*-\s*S?\d{1,2}\b", re.I)  # "S01-S05", "S01-05": several seasons in one pack


def tv_on() -> bool:
    return bool(config.sonarr_key()) and bool(config.load()["tv"]["enabled"])


def films_on() -> bool:
    """Radarr is read when it's set up, or when Sonarr isn't either (then its missing key is the error shown)."""
    return bool(config.radarr_key()) or not tv_on()


def app(i) -> str:
    return "sonarr" if ids.is_tv(i) else "radarr"


# ── shaping Sonarr's files like Radarr's movies ─────────────────────────────
def file_minutes(f: dict, fallback: float = 0) -> float:
    """An episode's runtime: Sonarr gives "mm:ss" under an hour and "h:mm:ss" over it."""
    rt = ((f or {}).get("mediaInfo") or {}).get("runTime") or ""
    parts = rt.split(":")
    try:
        nums = [float(x) for x in parts]
    except ValueError:
        return fallback
    if len(nums) == 3:
        return nums[0] * 60 + nums[1] + nums[2] / 60
    if len(nums) == 2:
        return nums[0] + nums[1] / 60
    return fallback


def hms(mins: float) -> str:
    s = round(mins * 60)
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}"


def _langs(f: dict) -> set:
    return {x for x in re.split(r"[/,; ]+", ((f.get("mediaInfo") or {}).get("audioLanguages") or "").lower()) if x}


def season_item(ser: dict, number: int, files: list) -> dict | None:
    """One season as Radarr would show a film (None when it has no files)."""
    files = [f for f in files if f.get("seasonNumber") == number and f.get("size")]
    if not files:
        return None
    per = (ser.get("runtime") or 0)
    mins = [file_minutes(f, per) for f in files]
    total = sum(mins)
    by_q: collections.Counter = collections.Counter()
    by_g: collections.Counter = collections.Counter()
    for f in files:
        by_q[f["quality"]["quality"]["name"]] += f["size"]
        if f.get("releaseGroup"):
            by_g[f["releaseGroup"]] += f["size"]
    qname = by_q.most_common(1)[0][0]
    main = max((f for f in files if f["quality"]["quality"]["name"] == qname), key=lambda f: f["size"])
    mi = dict(main.get("mediaInfo") or {})
    rates = [((f.get("mediaInfo") or {}).get("videoBitrate") or 0, m) for f, m in zip(files, mins, strict=True)]
    mi["videoBitrate"] = round(sum(b * m for b, m in rates) / total) if total and all(b for b, _ in rates) else 0
    mi["runTime"] = hms(total)
    common = set.intersection(*[_langs(f) for f in files])  # English in every episode, not just one
    mi["audioLanguages"] = "/".join(sorted(common)) if common else mi.get("audioLanguages") or ""
    added = sorted(f.get("dateAdded") or "" for f in files)
    return {"id": ids.season(ser["id"], number), "kind": "season", "series_id": ser["id"], "season": number,
            "title": f"{ser['title']} · Season {number}", "series_title": ser["title"], "year": ser.get("year"),
            "runtime": round(total), "titleSlug": ser.get("titleSlug"), "qualityProfileId": ser.get("qualityProfileId"),
            "episodes": len(files), "mixed": len(by_q) > 1,
            "movieFile": {"kind": "season", "size": sum(f["size"] for f in files), "path": main.get("path") or "",
                          "quality": main["quality"], "mediaInfo": mi, "releaseGroup": by_g.most_common(1)[0][0] if by_g else "",
                          "sceneName": main.get("sceneName") or "", "relativePath": main.get("relativePath") or "",
                          "added_first": added[0], "added_last": added[-1], "files": len(files)}}


def complete(ser: dict, number: int) -> bool:
    """Every aired episode has a file and nothing more is coming: only then is a season pack a like-for-like swap."""
    st = next((s.get("statistics") or {} for s in ser.get("seasons") or [] if s.get("seasonNumber") == number), {})
    return bool(st.get("episodeFileCount")) and st["episodeFileCount"] >= (st.get("episodeCount") or 0) and not st.get("nextAiring")


def episode_item(ser: dict, f: dict, eps: list, hint: str = "") -> dict:
    """One episode file as a film-like item, for the fallback. `eps`: the episodes the file holds (a double episode holds
    two); `hint`: the season's group, so the episode is judged the way its season was (smaller, bigger or better)."""
    eps = sorted(eps, key=lambda e: e["episodeNumber"])
    first, number = eps[0], f.get("seasonNumber", eps[0].get("seasonNumber", 0))
    label = f"S{number:02d}E{first['episodeNumber']:02d}" + (f"-E{eps[-1]['episodeNumber']:02d}" if len(eps) > 1 else "")
    mins = file_minutes(f, ser.get("runtime") or 0)
    mi = dict(f.get("mediaInfo") or {})
    mi["runTime"] = hms(mins)
    return {"id": ids.episode(first["id"]), "kind": "episode", "series_id": ser["id"], "season": number,
            "episode_ids": [e["id"] for e in eps], "episode_numbers": [e["episodeNumber"] for e in eps],
            "episode_file_id": f["id"], "parent": ids.season(ser["id"], number),
            "title": f"{ser['title']} · {label}", "series_title": ser["title"], "year": ser.get("year"), "runtime": round(mins),
            "titleSlug": ser.get("titleSlug"), "qualityProfileId": ser.get("qualityProfileId"), "group_hint": hint,
            "movieFile": {**f, "kind": "episode", "mediaInfo": mi}}


# ── the TV library: series read once, episode files again only for series that changed ──
class _Tv:
    def __init__(self):
        self.series: dict = {}
        self.files: dict = {}
        self.stamp: dict = {}
        self.lock = threading.Lock()

    def reset(self) -> None:
        with self.lock:
            self.series, self.files, self.stamp = {}, {}, {}

    def refresh(self) -> None:
        """One call for the series, then the episode files of each series whose size or file count changed since."""
        listing = sonarr.series_list()
        with self.lock:
            seen = set()
            for ser in listing:
                sid = ser["id"]
                seen.add(sid)
                st = ser.get("statistics") or {}
                stamp = (st.get("sizeOnDisk"), st.get("episodeFileCount"))
                self.series[sid] = ser
                if not st.get("episodeFileCount"):
                    self.files[sid], self.stamp[sid] = [], stamp
                elif self.stamp.get(sid) != stamp or sid not in self.files:
                    self.files[sid], self.stamp[sid] = sonarr.episode_files(sid), stamp
            for sid in set(self.series) - seen:
                self.series.pop(sid, None), self.files.pop(sid, None), self.stamp.pop(sid, None)

    def put(self, ser: dict, files: list) -> None:
        with self.lock:
            st = ser.get("statistics") or {}
            self.series[ser["id"]], self.files[ser["id"]] = ser, files
            self.stamp[ser["id"]] = (st.get("sizeOnDisk"), st.get("episodeFileCount"))

    def seasons(self, sid: int) -> list:
        ser, files = self.series.get(sid), self.files.get(sid) or []
        if not ser:
            return []
        out = []
        for n in sorted({f.get("seasonNumber") for f in files if (f.get("seasonNumber") or 0) > 0}):
            it = season_item(ser, n, files) if complete(ser, n) else None
            if it:
                out.append(it)
        return out

    def items(self) -> list:
        with self.lock:
            return [it for sid in sorted(self.series) for it in self.seasons(sid)]


TV = _Tv()
_hints: dict = {}  # episode id -> its season's group, for episodes read again after the fallback started
_singles: dict = {}  # season id -> the single-episode releases its last search turned up (the fallback's free look ahead)


def library() -> list:
    """Every film and every complete TV season."""
    films = radarr.movies() if films_on() else []
    if not tv_on():
        return films
    TV.refresh()
    return films + TV.items()


def series_items(sid: int) -> list:
    """One series read afresh (Sonarr's webhook): its seasons as items."""
    ser, files = sonarr.series(sid), sonarr.episode_files(sid)
    TV.put(ser, files)
    with TV.lock:
        return TV.seasons(sid)


def item(i) -> dict:
    """One item read afresh: a film from Radarr, a season or an episode from Sonarr. A season with no files left (or an
    episode whose file is gone) comes back with movieFile None, like a film Radarr has no file for."""
    i = int(i)
    k = ids.kind(i)
    if k == "film":
        return radarr.movie(i)
    if k == "season":
        sid, n = ids.split_season(i)
        ser, files = sonarr.series(sid), sonarr.episode_files(sid)
        TV.put(ser, files)
        return season_item(ser, n, files) or {"id": i, "kind": "season", "series_id": sid, "season": n,
                                                "title": f"{ser['title']} · Season {n}", "year": ser.get("year"),
                                                "titleSlug": ser.get("titleSlug"), "movieFile": None}
    ep = sonarr.episode(ids.episode_of(i))
    ser = sonarr.series(ep["seriesId"])
    if not ep.get("episodeFileId"):
        return {"id": i, "kind": "episode", "series_id": ser["id"], "season": ep.get("seasonNumber"), "episode_ids": [ep["id"]],
                "title": f"{ser['title']} · S{ep.get('seasonNumber', 0):02d}E{ep.get('episodeNumber', 0):02d}", "year": ser.get("year"),
                "titleSlug": ser.get("titleSlug"), "movieFile": None}
    f = sonarr.episode_file(ep["episodeFileId"])
    eps = [e for e in sonarr.episodes(ser["id"]) if e.get("episodeFileId") == f["id"]] or [ep]
    return episode_item(ser, f, eps, _hints.get(i, ""))


def episode_items(season: dict, hint: str = "") -> list:
    """The fallback: each episode file of a season as its own item."""
    sid, n = season["series_id"], season["season"]
    ser = TV.series.get(sid) or sonarr.series(sid)
    files = [f for f in sonarr.episode_files(sid) if f.get("seasonNumber") == n]
    by_file = collections.defaultdict(list)
    for e in sonarr.episodes(sid):
        if e.get("episodeFileId"):
            by_file[e["episodeFileId"]].append(e)
    out = [episode_item(ser, f, by_file[f["id"]], hint) for f in files if by_file.get(f["id"])]
    for it in out:
        _hints[it["id"]] = hint
    return sorted(out, key=lambda x: x["title"])


def releases(i) -> list:
    """Interactive search. A season gets season packs only (one season each); an episode gets single-episode releases."""
    i = int(i)
    k = ids.kind(i)
    if k == "film":
        return radarr.releases(i)
    if k == "season":
        sid, n = ids.split_season(i)
        found = sonarr.season_releases(sid, n)
        _singles[i] = [r for r in found if not r.get("fullSeason") and r.get("seasonNumber") == n and r.get("episodeNumbers")]
        return [r for r in found if r.get("fullSeason") and r.get("seasonNumber") == n
                and len(r.get("seasonNumbers") or [n]) <= 1 and not _MULTI.search(r.get("title") or "")]
    return [r for r in sonarr.episode_releases(ids.episode_of(i)) if not r.get("fullSeason")]


def singles(i) -> list:
    """The single-episode releases the season's last search turned up (empty when it hasn't been searched)."""
    return _singles.pop(int(i), [])


def grab(rel: dict, i) -> None:
    if ids.is_tv(i):
        sonarr.grab(rel)
    else:
        radarr.grab(rel, int(i))


class Live(dict):
    """The download queues by item id (as a string), each with its records: a season pack is one record per episode, all
    with the same downloadId. `failed`: the apps whose queue couldn't be read this time (their items are left as they
    are, rather than counted as gone); `errors`: why."""
    def __init__(self):
        super().__init__()
        self.failed: set = set()
        self.errors: list = []

    def get_for(self, mid, e: dict | None = None) -> dict | None:
        """The record of our download: the one named like the release we grabbed, else the first."""
        recs = self.get(str(mid)) or []
        want = re.sub(r"[^a-z0-9]", "", ((e or {}).get("new_title") or "").lower())
        return next((x for x in recs if want and re.sub(r"[^a-z0-9]", "", (x.get("title") or "").lower()) == want), None) or (recs[0] if recs else None)


def live_queue() -> Live:
    out = Live()
    if films_on():
        try:
            for x in radarr.queue():
                out.setdefault(str(x.get("movieId")), []).append(x)
        except radarr.RadarrError as e:
            out.failed.add("radarr")
            out.errors.append(str(e))
    if tv_on():
        try:
            for x in sonarr.queue():
                if x.get("seriesId") is None:
                    continue
                if x.get("seasonNumber") is not None:
                    out.setdefault(str(ids.season(x["seriesId"], x["seasonNumber"])), []).append(x)
                if x.get("episodeId"):
                    out.setdefault(str(ids.episode(x["episodeId"])), []).append(x)
        except radarr.RadarrError as e:
            out.failed.add("sonarr")
            out.errors.append(str(e))
    return out


def import_items(download_id: str, i, m: dict | None = None) -> list:
    """The video files of a finished download to import: a film's biggest, a season pack's one per episode, an
    episode's biggest. `m`: the item, when it's known (saves a lookup for an episode's series)."""
    i = int(i)
    k = ids.kind(i)
    if k == "film":
        vids = [x for x in radarr.manual_import_items(download_id, i) if x.get("movie") and VIDEO.search(x.get("path") or "")]
        return [max(vids, key=lambda x: x.get("size") or 0)] if vids else []
    if k == "season":
        sid, n = ids.split_season(i)
        items = sonarr.manual_import_items(download_id, sid, n)
    else:
        sid = (m or {}).get("series_id") or sonarr.episode(ids.episode_of(i))["seriesId"]
        items = sonarr.manual_import_items(download_id, sid)
    vids = [x for x in items if x.get("episodes") and VIDEO.search(x.get("path") or "")]
    if k == "episode":
        want = set((m or {}).get("episode_ids") or [ids.episode_of(i)])
        vids = [x for x in vids if want & {e["id"] for e in x["episodes"]}]
        return [max(vids, key=lambda x: x.get("size") or 0)] if vids else []
    best: dict = {}  # one file per episode set (a pack can carry a sample or a second copy)
    for x in vids:
        key = tuple(sorted(e["id"] for e in x["episodes"]))
        if key not in best or (x.get("size") or 0) > (best[key].get("size") or 0):
            best[key] = x
    return sorted(best.values(), key=lambda x: x.get("path") or "")


def manual_import(items: list, i, download_id: str, m: dict | None = None) -> None:
    i = int(i)
    if not ids.is_tv(i):
        radarr.manual_import(items[0], i, download_id)
        return
    sid = (m or {}).get("series_id") or (ids.split_season(i)[0] if ids.kind(i) == "season" else sonarr.episode(ids.episode_of(i))["seriesId"])
    sonarr.manual_import(items, sid, download_id)


def poster(i) -> tuple[bytes, str]:
    """The poster and the name it's cached under: a film's own, or its series' for a season or an episode."""
    i = int(i)
    k = ids.kind(i)
    if k == "film":
        return radarr.poster(i), str(i)
    sid = ids.split_season(i)[0] if k == "season" else sonarr.episode(ids.episode_of(i))["seriesId"]
    return sonarr.poster(sid), f"tv-{sid}"


def poster_name(i) -> str | None:
    """The cache name without asking Sonarr (None for an episode: its series needs a lookup)."""
    k = ids.kind(i)
    return str(int(i)) if k == "film" else f"tv-{ids.split_season(i)[0]}" if k == "season" else None
