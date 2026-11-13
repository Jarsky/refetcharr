"""TV (1.9.0): Sonarr's seasons as items, their own settings, season packs, the episode fallback, Sonarr's setup."""

import datetime as dt

import pytest

from refetcharr import arr, config, ids, quality, rules, store


@pytest.fixture(autouse=True)
def data(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA", str(tmp_path))
    monkeypatch.setattr(config, "ENV_RADARR_API_KEY", "")
    monkeypatch.setattr(config, "ENV_SONARR_API_KEY", "")
    store._log.clear()
    monkeypatch.setattr(store, "_loaded", False)
    arr.TV.reset()
    arr._hints.clear()
    return tmp_path


SID = 5


def ep_file(fid, n, season=1, gb=6.0, mins="45:00", quality_="Bluray-2160p", res=2160, langs="eng", vbr=0, added="2020-01-01T00:00:00Z", group="OLD"):
    return {"id": fid, "seriesId": SID, "seasonNumber": season, "size": int(gb * 1e9), "releaseGroup": group, "dateAdded": added,
            "path": f"/data/tv/Show/Season {season}/Show.S{season:02d}E{n:02d}.mkv", "relativePath": f"Season {season}/Show.S{season:02d}E{n:02d}.mkv",
            "quality": {"quality": {"name": quality_, "resolution": res}},
            "mediaInfo": {"runTime": mins, "videoCodec": "x265", "audioCodec": "EAC3", "audioChannels": 5.1, "audioLanguages": langs,
                          "videoBitrate": vbr, "videoDynamicRangeType": ""}}


def show(seasons=((1, 10),), airing=()):
    return {"id": SID, "title": "Show", "year": 2015, "runtime": 45, "titleSlug": "show", "qualityProfileId": 7,
            "statistics": {"sizeOnDisk": 1, "episodeFileCount": sum(n for _, n in seasons)},
            "seasons": [{"seasonNumber": 0, "statistics": {"episodeFileCount": 0, "episodeCount": 0}}]
            + [{"seasonNumber": s, "statistics": {"episodeFileCount": n, "episodeCount": n, **({"nextAiring": "2030-01-01"} if s in airing else {})}}
               for s, n in seasons]}


def pack(gb=40.5, title="Show S01 2160p WEB-DL DDP5.1 x265-GRP", seeders=50, season=1, full=True):
    return {"guid": f"p{gb}{title}", "indexerId": 3, "protocol": "torrent", "size": int(gb * 1e9), "seeders": seeders, "customFormatScore": 100,
            "title": title, "fullSeason": full, "seasonNumber": season, "seasonNumbers": [season] if full else [],
            "languages": [{"name": "English"}], "quality": {"quality": {"name": "WEBDL-2160p", "resolution": 2160}},
            "rejections": ["Season pack does not meet the upgrade criteria. Upgradable: 0/10 (0%)"]}


class FakeSonarr:
    def __init__(self, ser, files, packs=None, ep_releases=None, singles=None):
        self.ser, self.files = ser, files
        self.eps = [{"id": 100 + f["id"], "seriesId": SID, "seasonNumber": f["seasonNumber"], "episodeNumber": int(f["path"][-6:-4]),
                     "episodeFileId": f["id"]} for f in files]
        self.packs = packs if packs is not None else [pack()]
        self.ep_releases = ep_releases or {}
        # what a season search also lists: single episodes (here, one small enough for episode 1)
        self.singles = singles if singles is not None else [dict(pack(4.05, "Show S01E01 2160p WEB-DL DDP5.1 x265-GRP", full=False), episodeNumbers=[1])]
        self.grabbed, self.queue_, self.imported = [], [], []
        self.down = False

    def series_list(self): return [self.ser]
    def series(self, sid): return self.ser
    def episode_files(self, sid): return list(self.files)
    def episodes(self, sid): return self.eps
    def episode(self, eid): return next(e for e in self.eps if e["id"] == eid)
    def episode_file(self, fid): return next(f for f in self.files if f["id"] == fid)
    def season_releases(self, sid, n): return self.packs + self.singles
    def episode_releases(self, eid): return self.ep_releases.get(eid, [])

    def grab(self, rel):
        self.grabbed.append(rel["title"])
        eps = [e for e in self.eps if e["seasonNumber"] == rel["seasonNumber"]] if rel.get("fullSeason") else \
              [e for e in self.eps if e["id"] in rel.get("_eps", [])]
        for e in eps:
            self.queue_.append({"seriesId": SID, "seasonNumber": e["seasonNumber"], "episodeId": e["id"], "downloadId": "dl-" + rel["guid"],
                                "title": rel["title"], "size": rel["size"], "sizeleft": rel["size"], "status": "downloading",
                                "trackedDownloadState": "downloading"})

    def queue(self):
        if self.down:
            from refetcharr.sonarr import SonarrError
            raise SonarrError("Can't reach Sonarr at http://sonarr:8989: timed out")
        return self.queue_

    def finish(self):
        for q in self.queue_:
            q.update(status="completed", trackedDownloadState="importPending", sizeleft=0)

    def manual_import_items(self, did, sid, n=None):
        recs = [q for q in self.queue_ if q["downloadId"] == did]
        return [{"path": f"/data/incoming/{q['title']}/E{q['episodeId']}.mkv", "episodes": [{"id": q["episodeId"]}], "size": 4e9}
                for q in recs] + [{"path": "/data/incoming/sample.mkv", "episodes": [], "size": 1e7}]

    def manual_import(self, items, sid, did):
        now = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
        for it in items:
            eid = it["episodes"][0]["id"]
            fid = next(e["episodeFileId"] for e in self.eps if e["id"] == eid)
            f = next(f for f in self.files if f["id"] == fid)
            f.update(size=int(4.05e9), dateAdded=now, releaseGroup="GRP")
            self.imported.append(eid)
        self.queue_ = [q for q in self.queue_ if q["downloadId"] != did]


@pytest.fixture
def tv(monkeypatch):
    from refetcharr import engine as E

    def make(ser=None, files=None, **kw):
        fake = FakeSonarr(ser or show(), files if files is not None else [ep_file(i, i) for i in range(1, 11)], **kw)
        for name in ("series_list", "series", "episode_files", "episodes", "episode", "episode_file", "season_releases",
                     "episode_releases", "grab", "queue", "manual_import_items", "manual_import"):
            monkeypatch.setattr(arr.sonarr, name, getattr(fake, name))
        monkeypatch.setattr(E.time, "sleep", lambda s: None)
        monkeypatch.setattr(E.rules, "file_problem", lambda *a, **k: None)
        config.save({"connections": {"sonarr_api_key": "k"}})
        e = E.Engine()
        e.fake = fake
        return e
    return make


SEASON1 = ids.season(SID, 1)


# ── ids and shaping ───────────────────────────────────────────────────────
def test_ids_keep_films_seasons_and_episodes_apart():
    assert ids.kind(812) == "film" and ids.kind(SEASON1) == "season" and ids.kind(ids.episode(79352)) == "episode"
    assert ids.split_season(ids.season(1189, 2019)) == (1189, 2019)  # daily shows number seasons by year
    assert ids.episode_of(ids.episode(79352)) == 79352 and ids.series_of(SEASON1) == SID


def test_sonarr_runtimes_in_minutes_and_seconds():
    assert arr.file_minutes(ep_file(1, 1, mins="44:30")) == 44.5
    assert arr.file_minutes(ep_file(1, 1, mins="1:02:00")) == 62
    assert rules.minutes({"movieFile": {"mediaInfo": {"runTime": "44:30"}}}) == 44.5
    assert quality.minutes_of({"runTime": "22:00"}) == 22


def test_a_season_is_its_episodes_added_up():
    files = [ep_file(i, i, vbr=12_000_000) for i in range(1, 9)] + [ep_file(9, 9, gb=3, quality_="WEBDL-1080p", res=1080, vbr=6_000_000, langs="eng/fre")]
    m = arr.season_item(show(((1, 9),)), 1, files)
    f = m["movieFile"]
    assert m["id"] == SEASON1 and m["title"] == "Show · Season 1" and m["episodes"] == 9 and m["mixed"]
    assert f["size"] == int(51e9) and rules.minutes(m) == 405 and f["quality"]["quality"]["name"] == "Bluray-2160p"
    assert f["mediaInfo"]["videoBitrate"] == round((8 * 12e6 + 6e6) / 9)  # averaged over the runtime
    assert f["mediaInfo"]["audioLanguages"] == "eng" and rules.has_english(m)
    assert arr.season_item(show(), 2, files) is None  # no files in that season


def test_low_quality_is_judged_by_the_thresholds_given():
    m = arr.season_item(show(), 1, [ep_file(i, i, gb=1.0, quality_="WEBDL-1080p", res=1080) for i in range(1, 11)])  # ~2.3 Mbps picture
    r = {**config.load()["tv"]["rules"], "tiny_1080": 1}
    assert rules.group(m, r, config.load()["tv"]["quality"]) is None  # MEDIUM for TV's 1080p (LOW under 2.0)
    assert rules.group(m, r, config.load()["quality"]) == "lowq"  # LOW by the film thresholds (under 3.5)


def test_only_complete_finished_seasons_are_in_the_library(tv):
    files = [ep_file(i, i) for i in range(1, 11)] + [ep_file(20 + i, i, season=2) for i in range(1, 4)] + [ep_file(40, 1, season=3)]
    ser = show(((1, 10), (2, 3), (3, 1)), airing=(2,))
    ser["seasons"][3]["statistics"]["episodeCount"] = 8  # season 3: one file of eight aired episodes
    e = tv(ser, files)
    assert [m["id"] for m in e.library()] == [SEASON1]


# ── settings ──────────────────────────────────────────────────────────────
def test_tv_has_its_own_rules_and_quality_and_starts_from_the_film_rules():
    config.save({"rules": {"min_seeders": 7}})
    s = config.load()
    assert s["tv"]["rules"]["min_seeders"] == 7 and s["tv"]["rules"]["cap_2160"] == 120 and s["rules"]["cap_2160"] == 210
    tvs = config.for_item(s, SEASON1)
    assert tvs["rules"]["cap_1080"] == 60 and tvs["quality"]["video"]["2160"] == [7.0, 11.0] and tvs["queue"]["groups"] == s["tv"]["groups"]
    assert config.for_item(s, 12) is s
    config.save({"tv": {"rules": {**s["tv"]["rules"], "cap_2160": 140}}})
    config.save({"rules": {"min_seeders": 9}})  # once TV's rules are saved they're its own
    assert config.load()["tv"]["rules"]["min_seeders"] == 7 and config.load()["tv"]["rules"]["cap_2160"] == 140
    assert config.profile_of(config.load()["tv"]["rules"], "tv") == "balanced"  # the maximums aren't part of a profile


def test_profiles_are_set_per_kind(monkeypatch):
    from refetcharr import web
    monkeypatch.setenv("REFETCHARR_WORKER", "0")
    from test_refetcharr import keyed
    c = keyed(web)
    assert c.get("/api/profiles?kind=tv").json()["current"] == "balanced"
    assert c.post("/api/profile", json={"id": "savings", "kind": "tv"}).status_code == 200
    s = config.load()
    assert s["tv"]["rules"]["preferred_2160"] == 70 and s["tv"]["rules"]["prefer"] == "small" and s["rules"]["prefer"] == "high"
    assert c.get("/api/profiles?kind=tv").json()["current"] == "savings" and c.get("/api/profiles").json()["current"] == "balanced"
    assert c.patch("/api/settings", json={"tv": {"rules": {"cap_1080": 5}}}).status_code == 400  # below its preferred size
    assert c.patch("/api/settings", json={"tv": {"auto": "yes"}}).status_code == 400
    assert c.patch("/api/settings", json={"tv": {"quality": {"video": {"1080": [4, 2]}}}}).status_code == 400
    assert c.patch("/api/settings", json={"tv": {"auto": True, "groups": {"big1080": True}}}).json()["values"]["tv"]["groups"]["big1080"] is True


# ── a season pack, from search to import ──────────────────────────────────
def test_tv_waits_for_auto_queue_tv_but_a_season_picked_by_hand_goes(tv):
    e = tv()
    config.save({"queue": {"auto": True}})
    e.refill(config.load())
    assert e.fake.grabbed == []  # Auto-queue TV is off
    e.queue_now(ids=[SEASON1])
    e.refill(config.load())
    assert e.fake.grabbed == ["Show S01 2160p WEB-DL DDP5.1 x265-GRP"]  # the pack, not the single episode
    st = store.load()[str(SEASON1)]
    assert st["status"] == "grabbed" and st["group"] == "big4k" and st["old_size"] == int(60e9)


def test_a_season_pack_imports_every_episode_and_counts_once_all_are_in(tv):
    e = tv()
    config.save({"queue": {"auto": True}, "tv": {"auto": True}})
    e.refill(config.load())
    assert len(e.fake.queue_) == 10
    e.import_finished()
    assert store.load()[str(SEASON1)]["status"] == "downloading"
    assert e.status()["active"][0]["progress"] == 0 and e.status()["active"][0]["kind"] == "season"
    e.fake.finish()
    e.import_finished()
    st = store.load()[str(SEASON1)]
    assert sorted(e.fake.imported) == list(range(101, 111))  # one file per episode, not the sample
    assert st["status"] == "replaced" and st["saved"] == int(60e9 - 40.5e9) and st["after_file"]["resolution"] == 2160


def test_a_season_is_not_replaced_while_some_episodes_are_still_importing(tv):
    e = tv()
    store.update(SEASON1, title="Show · Season 1", old_size=int(60e9), new_size=int(40.5e9), new_title="x", status="importing",
                 group="big4k", grabbed=dt.datetime.now().strftime("%Y-%m-%d %H:%M"))
    now = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    for f in e.fake.files[:6]:
        f.update(size=int(4e9), dateAdded=now)  # six of ten done
    e.import_finished()
    assert store.load()[str(SEASON1)]["status"] == "importing"
    for f in e.fake.files:
        f.update(size=int(4e9), dateAdded=now)
    e.import_finished()
    assert store.load()[str(SEASON1)]["status"] == "replaced"


def test_sonarr_out_of_reach_leaves_tv_downloads_alone(tv):
    e = tv()
    e.queue_now(ids=[SEASON1])
    e.refill(config.load())
    e.fake.down = True
    for _ in range(3):
        e.import_finished()
    st = store.load()[str(SEASON1)]
    assert st["status"] == "grabbed" and not st.get("missing_since") and "Sonarr" in e.error


# ── no season pack qualifies: episode by episode ──────────────────────────
def test_no_pack_qualifies_so_the_episodes_are_tried_one_by_one(tv):
    eps = {100 + i: [dict(pack(4.05, f"Show S01E{i:02d} 2160p WEB-DL DDP5.1 x265-GRP", full=False), _eps=[100 + i])] for i in range(1, 11)}
    e = tv(packs=[pack(55)], ep_releases=eps)  # the only pack isn't small enough
    e.queue_now(ids=[SEASON1])
    e.refill(config.load())
    st = store.load()[str(SEASON1)]
    assert st["status"] == "no release" and len(st["fallback"]) == 10 and "one by one" in st["problem"]
    assert list(e.manual_ids) == st["fallback"]  # asked for by hand, so its episodes are too
    e.refill(config.load())
    first = ids.episode(101)
    assert e.fake.grabbed == ["Show S01E01 2160p WEB-DL DDP5.1 x265-GRP"]
    ep = store.load()[str(first)]
    assert ep["status"] == "grabbed" and ep["group"] == "big4k" and ep["title"] == "Show · S01E01"
    e.fake.finish()
    e.import_finished()
    assert store.load()[str(first)]["status"] == "replaced" and e.fake.imported == [101]
    detail = e.detail(SEASON1)
    assert detail["kind"] == "season" and {x["status"] for x in detail["fallback"]} == {"replaced", "waiting"}


def test_no_episode_search_when_the_seasons_own_search_shows_none_would_qualify(tv):
    too_big = [dict(pack(9, f"Show S01E{i:02d} 2160p ATVP WEB-DL DDP5.1 Atmos x265-BIG", full=False), episodeNumbers=[i]) for i in range(1, 11)]
    e = tv(packs=[pack(55)], singles=too_big)
    e.queue_now(ids=[SEASON1])
    e.refill(config.load())
    st = store.load()[str(SEASON1)]
    assert st["status"] == "no release" and not st.get("fallback") and "no episode release" in st["problem"]
    assert not e.manual_ids and e.fake.grabbed == []  # not one episode search


def test_fallback_episodes_wait_for_auto_queue_tv_and_survive_a_restart(tv, monkeypatch):
    from refetcharr import engine as E
    e = tv(packs=[])
    config.save({"queue": {"auto": True}, "tv": {"auto": True}})
    e.refill(config.load())
    assert store.load()[str(SEASON1)]["fallback"] and not e.manual_ids
    again = E.Engine()  # a restart: the episodes are read from Sonarr again
    assert [m["id"] for m in again._fallback_episodes(store.load(), config.load())][:2] == [ids.episode(101), ids.episode(102)]


# ── the webhook, suggestions, setup ───────────────────────────────────────
def test_sonarrs_webhook_rereads_the_series_and_flags_its_seasons(tv, monkeypatch):
    from refetcharr import web
    from test_refetcharr import keyed
    e = tv(files=[ep_file(i, i, gb=3) for i in range(1, 11)])  # 67 MB/min: fine for 4K
    e.scan()
    assert e.films() == []
    monkeypatch.setattr(web, "engine", e)
    monkeypatch.setenv("REFETCHARR_WORKER", "0")
    c = keyed(web)
    for f in e.fake.files:
        f["size"] = int(6e9)
    r = c.post("/api/webhook/sonarr", json={"eventType": "Download", "series": {"id": SID}, "isUpgrade": True})
    assert r.json()["event"] == "Download" and e.jobs[-1] == ("series", SID, "upgraded by Sonarr")
    e.jobs.clear()
    e.refresh_series(SID, "upgraded by Sonarr")
    rows = e.films()
    assert [x["id"] for x in rows] == [SEASON1] and rows[0]["kind"] == "season" and rows[0]["group"] == "big4k"
    assert "upgraded by Sonarr" in store.recent()[0]["text"]


def test_suggestions_for_a_season_list_its_packs(tv):
    e = tv(packs=[pack(), pack(39.6, "Show S01 2160p WEB-DL DDP5.1 x265-SMALL"), pack(30, "Show S01 2160p WEB-DL DDP5.1 x265-MEDIUM")])
    e.suggest_one(SEASON1)
    sg = e.suggestions()[0]
    # the smallest HIGH first (TV's 4K HIGH is from 11 Mbps); 30 GB is only MEDIUM, so it comes last
    assert sg["kind"] == "season" and [o["title"].split("-")[-1] for o in sg["options"]] == ["SMALL", "GRP", "MEDIUM"]
    assert [o["badge"] for o in sg["options"]] == ["HIGH", "HIGH", "MEDIUM"]
    assert sg["before"]["rating"] is not None and all(o["rating"] is not None for o in sg["options"])


def test_scan_summarises_tv_apart_from_films(tv, data):
    e = tv()
    summary = e.scan()
    assert summary["tv"]["big4k"]["films"] == 1 and summary["tv"]["_library"]["films"] == 1 and "big4k" not in summary
    assert "TV: 1 seasons, 1 flagged" in store.recent()[0]["text"]
    assert "season" in (data / "reports" / "latest" / "oversized.csv").read_text()


class FakeSonarrConfig:
    """Sonarr 5's settings: sizes on each quality inside a profile, none on the definitions."""
    def __init__(self):
        q = lambda name, res, mx: {"quality": {"name": name, "resolution": res}, "allowed": True, "minSize": 8, "preferredSize": 25, "maxSize": mx}
        self.db = {
            "/system/status": {"version": "5.0.0.2941"},
            "/downloadclient": [{"name": "qBitTorrent", "implementation": "QBittorrent", "enable": True}],
            "/config/downloadclient": {"id": 1, "enableCompletedDownloadHandling": True},
            "/config/mediamanagement": {"id": 1, "recycleBin": ""},
            "/customformat": [{"id": 5, "name": "Remux"}],
            "/qualityprofile": [{"id": 7, "name": "AllQuality Upgrade", "minFormatScore": 0, "formatItems": [{"format": 5, "name": "Remux", "score": -10000}],
                                 "items": [q("WEBDL-1080p", 1080, 60), {"name": "WEB 2160p", "items": [q("WEBDL-2160p", 2160, 300)]}]}],
            "/qualitydefinition": [{"title": "WEBDL-1080p", "quality": {"name": "WEBDL-1080p"}}],
            "/notification": [{"name": "ElementsPlex", "implementation": "PlexServer", "onUpgrade": True, "fields": []}],
            "/notification/schema": [{"implementation": "Webhook", "onGrab": False, "onDownload": False, "onUpgrade": False,
                                      "onEpisodeFileDelete": False, "fields": [{"name": "url", "value": ""}, {"name": "method", "value": 1}]}],
        }
        self.puts = []

    def call(self, method, path, body=None, **kw):
        if method == "GET":
            return self.db[path]
        self.puts.append((method, path))
        if path.startswith("/qualityprofile/"):
            self.db["/qualityprofile"] = [body]
        if path == "/notification":
            self.db["/notification"].append(dict(body, id=3))
        return body


def test_sonarr_setup_checks_sizes_in_its_profiles_and_adds_its_webhook(monkeypatch):
    from refetcharr import setup
    fake = FakeSonarrConfig()
    monkeypatch.setattr(setup.sonarr, "call", fake.call)
    config.save({"connections": {"sonarr_api_key": "k"}})
    items = [{"id": SEASON1, "qualityProfileId": 7, "movieFile": None}]
    bad = {c["id"]: c for c in setup.checks(items, "sonarr") if not c["ok"]}
    assert set(bad) == {"sizes", "webhook"} and all(c["app"] == "sonarr" for c in bad.values())
    assert "WEBDL-2160p (300)" in bad["sizes"]["detail"] and "1080" not in bad["sizes"]["detail"].split(":", 1)[1].split("(")[0]
    done = setup.fix(["sizes", "webhook"], items, "sonarr")
    prof = fake.db["/qualityprofile"][0]
    assert prof["items"][1]["items"][0]["maxSize"] == 120 and prof["items"][0]["maxSize"] == 60 and prof["items"][0]["preferredSize"] == 35
    hook = fake.db["/notification"][-1]
    assert hook["onEpisodeFileDelete"] and hook["onDownload"] and not hook["onGrab"] and "/api/webhook/sonarr?apikey=" in hook["fields"][0]["value"]
    assert any("Sonarr webhook" in d for d in done)
    assert {c["id"] for c in setup.checks(items, "sonarr") if not c["ok"]} == set()
