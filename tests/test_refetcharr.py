import datetime as dt
import os

import pytest

from refetcharr import config, rules, store


def film(mid=1, title="Film", gb=40.0, mins=120, quality="Remux-1080p", res=1080, codec="AVC", langs="eng"):
    return {"id": mid, "title": title, "year": 2000, "runtime": mins, "titleSlug": str(mid),
            "movieFile": {"size": int(gb * 1e9), "path": f"/data/movies/{title}/{title}.mkv",
                          "quality": {"quality": {"name": quality, "resolution": res}},
                          "mediaInfo": {"runTime": f"{mins // 60}:{mins % 60:02d}:00", "videoCodec": codec, "audioLanguages": langs}}}


def release(gb=8.0, res=1080, name=None, seeders=50, score=100, langs=("English",), rejections=("Not an upgrade for existing movie file",), title=None):
    return {"guid": f"g{gb}{res}{seeders}", "indexerId": 6, "protocol": "torrent", "size": int(gb * 1e9), "seeders": seeders,
            "customFormatScore": score, "title": title or f"Film 2000 {res}p BluRay x265", "languages": [{"name": x} for x in langs],
            "quality": {"quality": {"name": name or f"Bluray-{res}p", "resolution": res}}, "rejections": list(rejections)}


R = {**config.DEFAULTS["rules"], "min_seeders": 10, "video_codecs": [], "audio_codecs": [], "keep_atmos": False}  # the 1.5 defaults these tests were written for


def keyed(web):
    """A test client that sends the API key, as the TUI and CLI do."""
    from fastapi.testclient import TestClient
    from refetcharr import auth
    return TestClient(web.app, headers={"X-Api-Key": auth.ensure_keys()["api_key"]})


@pytest.fixture(autouse=True)
def data(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA", str(tmp_path))
    store._log.clear()
    monkeypatch.setattr(store, "_loaded", False)
    return tmp_path


def test_activity_history_survives_a_restart(monkeypatch):
    store.log("done", "before the restart")
    store._log.clear()
    monkeypatch.setattr(store, "_loaded", False)  # a fresh server process
    store.log("start", "started")  # the first thing a new server logs
    assert [x["text"] for x in store.recent()] == ["started", "before the restart"]


# ── rules ─────────────────────────────────────────────────────────────────
def test_groups_follow_the_size_rules():
    assert rules.group(film(quality="Remux-1080p"), R) == "remux"
    assert rules.group(film(quality="Remux-2160p", res=2160), R) == "remux4k"
    assert rules.group(film(gb=40, quality="Bluray-2160p", res=2160, codec="x265"), R) == "big4k"  # 333 MB/min > 210
    assert rules.group(film(gb=18, quality="Bluray-2160p", res=2160, codec="x265"), R) is None  # 150 MB/min: fine
    assert rules.group(film(gb=1.5, quality="WEBDL-1080p", codec="x265"), R) == "tiny"  # 12.5 MB/min x265


def test_a_replacement_must_be_smaller_big_enough_and_not_lower_resolution():
    m = film(gb=40)
    assert rules.judge(m, release(gb=8), R) == []
    assert "not small enough" in " ".join(rules.judge(m, release(gb=30), R))
    assert "too small to look good" in " ".join(rules.judge(m, release(gb=2.4), R))  # 20 MB/min < 35
    assert "lower resolution" in " ".join(rules.judge(film(gb=60, quality="Bluray-2160p", res=2160), release(gb=8, res=1080), R))
    assert "seeders" in " ".join(rules.judge(m, release(seeders=3), R))
    assert "no English audio" in " ".join(rules.judge(m, release(langs=("French",)), R))
    assert rules.judge(film(langs="fre"), release(langs=("French",)), R) == []  # no English to keep
    assert "Dolby Vision without HDR10" in " ".join(rules.judge(m, release(title="Film 2000 2160p WEB-DL DV H265", res=2160, gb=20), R))
    assert rules.judge(m, release(title="Film 2000 2160p WEB-DL DV HDR H265", res=2160, gb=20), R) == []
    assert "format score" in " ".join(rules.judge(m, release(score=-9900), R))
    assert "Blocklisted" in " ".join(rules.judge(m, release(rejections=("Blocklisted",)), R))


def test_best_prefers_4k_then_x265_then_a_sensible_size():
    m = film(gb=40)
    picks = [release(gb=5, seeders=500, score=100), release(gb=8, seeders=20, score=100), release(gb=8, score=0, seeders=999),
             release(gb=20, res=2160, seeders=15, title="Film 2000 2160p WEB-DL HDR x265")]
    assert rules.best(m, picks, R)[0]["quality"]["quality"]["resolution"] == 2160
    picks = picks[:3]
    assert rules.best(m, picks, R)[0]["size"] == int(8e9)  # 8 GB is nearer 60 MB/min than 5 GB, despite fewer seeders


def test_file_check_needs_english_and_dv_with_hdr10(monkeypatch):
    def streams(*s):
        monkeypatch.setattr(rules, "probe", lambda p, f="ffprobe": {"streams": list(s)})
    streams({"codec_type": "video", "color_transfer": "smpte2084", "side_data_list": [{"dv_profile": 8, "dv_bl_signal_compatibility_id": 1}]},
            {"codec_type": "audio", "tags": {"language": "eng"}})
    assert rules.file_problem("x", True) is None
    streams({"codec_type": "video", "color_transfer": "smpte2084", "side_data_list": [{"dv_profile": 5, "dv_bl_signal_compatibility_id": 0}]},
            {"codec_type": "audio", "tags": {"language": "eng"}})
    assert "Dolby Vision" in rules.file_problem("x", True)
    streams({"codec_type": "video", "color_transfer": "bt709"}, {"codec_type": "audio", "tags": {"language": "ita"}})
    assert "no English audio" in rules.file_problem("x", True)
    assert rules.file_problem("x", False) is None


def test_settings_merge_partially_and_ignore_unknown_keys():
    config.save({"queue": {"groups": {"big1080": True}, "nonsense": 1}, "bogus": {}})
    s = config.load()
    assert s["queue"]["groups"] == {"remux": True, "big4k": True, "remux4k": True, "big1080": True, "tiny": False, "lowq": False}
    assert "nonsense" not in s["queue"] and "bogus" not in s and s["queue"]["max_active"] == 5


# ── engine against a fake Radarr ──────────────────────────────────────────
class FakeRadarr:
    def __init__(self, movies, releases=None):
        self.movies_ = {m["id"]: m for m in movies}
        self.releases_ = releases or {}
        self.grabbed, self.queue_, self.imported = [], [], []

    def movies(self): return list(self.movies_.values())
    def movie(self, mid): return self.movies_[mid]
    def releases(self, mid): return self.releases_.get(mid, [release()])
    def grab(self, rel, mid):
        self.grabbed.append(mid)
        self.queue_.append({"id": 100 + mid, "movieId": mid, "downloadId": f"d{mid}", "size": rel["size"], "sizeleft": rel["size"],
                            "status": "downloading", "trackedDownloadState": "downloading"})
    def queue(self): return self.queue_
    def manual_import_items(self, did, mid): return [{"path": f"/data/incoming/{mid}.mkv", "movie": {"id": mid}, "size": 8e9, "quality": {}}]
    def manual_import(self, item, mid, did):
        self.imported.append(mid)
        self.movies_[mid]["movieFile"]["size"] = int(8e9)
        self.queue_ = [q for q in self.queue_ if q["movieId"] != mid]

    def finish(self, mid):
        for q in self.queue_:
            if q["movieId"] == mid:
                q.update(status="completed", trackedDownloadState="importPending", sizeleft=0)


@pytest.fixture
def eng(monkeypatch):
    from refetcharr import engine as E
    def make(movies, releases=None):
        fake = FakeRadarr(movies, releases)
        for name in ("movies", "movie", "releases", "grab", "queue", "manual_import_items", "manual_import"):
            monkeypatch.setattr(E.radarr, name, getattr(fake, name))
        monkeypatch.setattr(E.time, "sleep", lambda s: None)
        monkeypatch.setattr(E.rules, "file_problem", lambda *a, **k: None)
        e = E.Engine()
        e.fake = fake
        return e
    return make


def test_auto_queue_respects_slots_and_the_daily_limit(eng):
    e = eng([film(i, f"F{i}", gb=40 - i) for i in range(1, 9)])
    config.save({"queue": {"auto": True, "max_active": 3, "max_per_day": 4}})
    for _ in range(10):
        e.refill(config.load())
    assert e.fake.grabbed == [1, 2, 3]  # biggest savings first, three slots
    e.fake.finish(1)
    e.import_finished()
    assert store.load()["1"]["status"] == "replaced" and e.fake.imported == [1]
    for _ in range(5):
        e.refill(config.load())
    assert e.fake.grabbed == [1, 2, 3, 4]  # a slot freed up, then the daily limit of 4 stops it
    assert store.grabbed_on(dt.date.today()) == 4


def test_queued_by_hand_ignores_the_limits_but_not_pause(eng):
    e = eng([film(i, f"F{i}", gb=40 - i) for i in range(1, 9)])
    config.save({"queue": {"max_active": 2, "max_per_day": 3, "paused": True}})
    assert "paused" in e.queue_now(count=5)
    e.refill(config.load())
    assert e.fake.grabbed == []  # Pause holds them
    config.save({"queue": {"paused": False}})
    for _ in range(10):
        e.refill(config.load())
    assert e.fake.grabbed == [1, 2, 3, 4, 5]  # past both the 2 at once and the 3 a day
    assert e.manual_count == 0
    e.queue_now(count=50)  # more than there is: it stops asking when the list runs out
    for _ in range(10):
        e.refill(config.load())
    assert e.fake.grabbed == [1, 2, 3, 4, 5, 6, 7, 8] and e.manual_count == 0
    config.save({"queue": {"auto": True}})
    e.refill(config.load())
    assert len(e.fake.grabbed) == 8  # auto-queue still keeps to the limits


def test_already_tried_chosen_films_dont_let_auto_past_its_limits(eng):
    e = eng([film(1), film(2, "B", gb=39)])
    store.update(1, title="Film", old_size=1, status="no release")
    config.save({"queue": {"auto": True, "max_per_day": 1}})
    store.update(99, title="Earlier", old_size=1, status="replaced", grabbed=dt.date.today().isoformat() + " 01:00", new_title="x")
    e.queue_now(ids=[1])  # already tried: skipped, and today's limit is used up, so nothing else starts
    e.refill(config.load())
    assert e.fake.grabbed == []


def test_pause_groups_auto_and_chosen_films(eng):
    e = eng([film(1, "Remux"), film(2, "Big4k", gb=60, quality="Bluray-2160p", res=2160, codec="x265")],
            releases={2: [release(gb=20, res=2160, title="Big4k 2000 2160p WEB-DL HDR x265")]})
    config.save({"queue": {"paused": True, "auto": True}})
    e.refill(config.load())
    assert e.fake.grabbed == []
    config.save({"queue": {"paused": False, "groups": {"remux": False}}})
    e.refill(config.load())
    assert e.fake.grabbed == [2]  # auto-queue, remuxes switched off
    config.save({"queue": {"auto": False}})
    e.queue_now(ids=[1])  # chosen by hand: tried even though its group is off
    e.refill(config.load())
    assert e.fake.grabbed == [2, 1]


def test_outside_the_schedule_auto_waits_but_manual_does_not(eng, monkeypatch):
    e = eng([film(1), film(2, "B", gb=39)])
    config.save({"queue": {"auto": True, "window": {"enabled": True, "days": [], "start": "01:00", "end": "02:00"}}})
    e.refill(config.load())
    assert e.fake.grabbed == []
    e.queue_now(count=1)
    e.refill(config.load())
    assert e.fake.grabbed == [1]


def test_no_release_is_remembered_and_retry_forgets_it(eng):
    e = eng([film(1), film(2, "B", gb=39)], releases={1: [release(seeders=1)]})
    e.queue_now(count=2)
    e.refill(config.load())
    e.refill(config.load())
    st = store.load()
    assert st["1"]["status"] == "no release" and "seeders" in st["1"]["problem"] and st["2"]["status"] == "grabbed"
    assert store.grabbed_on(dt.date.today()) == 1  # a failed search doesn't use up the daily limit
    out = e.retry(match="film")
    assert [x["id"] for x in out["retried"]] == ["1"] and "1" not in store.load() and "2" in store.load()


def test_a_failed_file_check_keeps_the_original(eng, monkeypatch):
    from refetcharr import engine as E
    e = eng([film(1)])
    e.queue_now(count=1)
    e.refill(config.load())
    e.fake.finish(1)
    monkeypatch.setattr(E.rules, "file_problem", lambda *a, **k: "no English audio track (tracks: ita)")
    e.import_finished()
    s = store.load()["1"]
    assert s["status"] == "kept original" and "English" in s["problem"] and e.fake.imported == []


def test_radarr_importing_by_itself_counts_as_replaced(eng):
    e = eng([film(1)], releases={1: [release(gb=20, res=2160, title="Film 2000 2160p WEB-DL HDR x265")]})
    e.queue_now(count=1)
    e.refill(config.load())
    e.fake.queue_ = []
    e.fake.movies_[1]["movieFile"]["size"] = int(20e9)
    e.import_finished()
    s = store.load()["1"]
    assert s["status"] == "replaced" and s["saved"] == int(20e9)


def test_a_brief_gap_in_radarrs_queue_isnt_missing_and_missing_films_recover(eng):
    e = eng([film(1)])
    e.queue_now(count=1)
    e.refill(config.load())
    saved_queue, e.fake.queue_ = e.fake.queue_, []  # Radarr's queue comes back without it
    e.import_finished()
    assert store.load()["1"]["status"] == "grabbed" and store.load()["1"]["missing_since"]
    e.fake.queue_ = saved_queue  # ...and then it's back: nothing lost
    e.import_finished()
    assert store.load()["1"]["status"] == "downloading" and not store.load()["1"]["missing_since"]
    e.fake.queue_ = []
    e.import_finished()
    store.update(1, missing_since="2000-01-01T00:00:00")  # gone for longer than MISSING_AFTER
    e.import_finished()
    assert store.load()["1"]["status"] == "missing from queue"
    e.fake.queue_ = saved_queue  # it reappears later, finished: imported as normal
    e.fake.finish(1)
    e.import_finished()
    assert store.load()["1"]["status"] == "replaced" and e.fake.imported == [1]


def test_scan_writes_reports_and_the_film_list(eng, data):
    e = eng([film(1), film(2, "Fine", gb=8, quality="Bluray-1080p", codec="x265")])
    summary = e.scan()
    assert summary["remux"]["films"] == 1 and summary["_library"]["films"] == 2
    assert [f["id"] for f in e.films()] == [1]
    assert (data / "reports" / "latest" / "movies.csv").read_text().count("\n") == 3


# ── connection settings and Radarr setup ──────────────────────────────────
def test_the_api_key_is_kept_server_side(monkeypatch):
    monkeypatch.setattr(config, "ENV_RADARR_API_KEY", "")
    config.save({"connections": {"radarr_url": " http://radarr:7878/ ", "radarr_api_key": "secret123"}})
    assert config.radarr_url() == "http://radarr:7878" and config.radarr_key() == "secret123"
    pub = config.public(config.load())
    assert pub["connections"]["radarr_api_key"] == "" and pub["secrets_set"]["radarr_api_key"] is True
    config.save({"connections": {"radarr_api_key": ""}})  # a blank field (the UI never has the key) keeps it
    assert config.radarr_key() == "secret123"
    config.save({"connections": {"radarr_api_key": None}})  # null clears it
    assert config.radarr_key() == "" and oct(os.stat(os.path.join(config.DATA, "settings.json")).st_mode)[-3:] == "600"


class FakeRadarrConfig:
    """Radarr's settings endpoints, as setup.py sees them."""
    def __init__(self):
        self.db = {
            "/system/status": {"version": "5.0"},
            "/downloadclient": [{"name": "qBitTorrent", "implementation": "QBittorrent", "enable": True}],
            "/config/downloadclient": {"id": 1, "enableCompletedDownloadHandling": True},
            "/config/mediamanagement": {"id": 1, "recycleBin": "/trash"},
            "/customformat": [{"id": 1, "name": "Prefer x265"}],
            "/qualityprofile": [{"id": 7, "name": "1080ptoUltraHD", "minFormatScore": 0, "formatItems": [{"format": 1, "name": "Prefer x265", "score": 100}]},
                                {"id": 9, "name": "Unused", "minFormatScore": -50, "formatItems": []}],
            "/qualitydefinition": [{"title": "Bluray-1080p", "minSize": 0, "preferredSize": 95, "maxSize": 100},
                                   {"title": "WEBDL-2160p", "minSize": 0, "preferredSize": None, "maxSize": None},
                                   {"title": "Remux-1080p", "minSize": 0, "preferredSize": None, "maxSize": None}],
            "/notification": [],
            "/notification/schema": [{"implementation": "Webhook", "onGrab": False, "onDownload": False, "onUpgrade": False,
                                      "fields": [{"name": "url", "value": ""}, {"name": "method", "value": 1}]}],
        }
        self.puts = []

    def call(self, method, path, body=None, **kw):
        if method == "GET":
            return self.db[path]
        self.puts.append((method, path, body))
        if path == "/customformat":
            cf = dict(body, id=8)
            self.db["/customformat"].append(cf)
            return cf
        if path.startswith("/qualityprofile/"):
            self.db["/qualityprofile"] = [body if p["id"] == body["id"] else p for p in self.db["/qualityprofile"]]
        if path == "/qualitydefinition/update":
            self.db["/qualitydefinition"] = body
        if path.startswith("/config/mediamanagement"):
            self.db["/config/mediamanagement"] = body
        if path == "/notification":
            self.db["/notification"].append(dict(body, id=3))
        return body


def test_setup_checks_and_fixes(monkeypatch):
    from refetcharr import setup
    fake = FakeRadarrConfig()
    monkeypatch.setattr(setup.radarr, "call", fake.call)
    movies = [{"id": 1, "qualityProfileId": 7, "movieFile": None}]
    bad = {c["id"]: c for c in setup.checks(movies) if not c["ok"]}
    assert set(bad) == {"recycle", "remux_cf", "profiles", "sizes", "webhook", "mediaserver"}
    assert "Unused" not in bad["profiles"]["detail"]  # only profiles that films use matter
    assert bad["recycle"]["level"] == "warn" and bad["profiles"]["level"] == "error"
    done = setup.fix(["remux_cf", "profiles", "sizes"], movies)
    assert any("Created the 'Remux'" in d for d in done) and any("1080ptoUltraHD" in d for d in done)
    p7 = next(p for p in fake.db["/qualityprofile"] if p["id"] == 7)
    assert {f["name"]: f["score"] for f in p7["formatItems"]}["Remux"] == -10000
    assert next(p for p in fake.db["/qualityprofile"] if p["id"] == 9)["minFormatScore"] == -50  # unused profile untouched
    d = {x["title"]: (x["minSize"], x["preferredSize"], x["maxSize"]) for x in fake.db["/qualitydefinition"]}
    assert d["Bluray-1080p"] == (20, 60, 125) and d["WEBDL-2160p"] == (50, 150, 210) and d["Remux-1080p"] == (0, None, None)
    assert {c["id"] for c in setup.checks(movies) if not c["ok"]} == {"recycle", "webhook", "mediaserver"}  # only the ones we left alone
    assert any("webhook 'Refetcharr'" in d for d in setup.fix(["webhook"], movies))
    hook = fake.db["/notification"][0]
    assert hook["name"] == "Refetcharr" and hook["onDownload"] and hook["onUpgrade"] and not hook["onGrab"]
    assert next(f["value"] for f in hook["fields"] if f["name"] == "url") == setup.webhook_url()
    assert "/api/webhook/radarr?apikey=" in setup.webhook_url()
    assert {c["id"] for c in setup.checks(movies) if not c["ok"]} == {"recycle", "mediaserver"}
    fake.db["/notification"].append({"name": "ElementsPlex", "implementation": "PlexServer", "onUpgrade": True, "fields": []})
    ok = {c["id"]: c for c in setup.checks(movies)}
    assert ok["mediaserver"]["ok"] and "ElementsPlex (Plex)" in ok["mediaserver"]["detail"]


# ── the API ───────────────────────────────────────────────────────────────
def test_api_end_to_end(eng, monkeypatch):
    from refetcharr import web
    e = eng([film(1), film(2, "B", gb=39)])
    monkeypatch.setattr(web, "engine", e)
    monkeypatch.setenv("REFETCHARR_WORKER", "0")
    c = keyed(web)
    assert c.post("/api/queue", json={}).status_code == 400
    assert "Queued the next 2" in c.post("/api/queue", json={"count": 2}).json()["message"]
    assert c.patch("/api/settings", json={"queue": {"max_active": 0}}).status_code == 400
    assert c.patch("/api/settings", json={"queue": {"max_active": 1}}).json()["values"]["queue"]["max_active"] == 1
    e.refill(config.load())
    e.refill(config.load())
    s = c.get("/api/status").json()  # queued by hand: both start, past the limit of 1 at once
    assert len(s["active"]) == 2 and s["waiting"]["count"] == 0 and s["limits"]["max_active"] == 1
    assert c.post("/api/pause").status_code == 200 and c.get("/api/status").json()["paused"] is True
    config.save({"connections": {"radarr_api_key": "secret123"}})
    got = c.get("/api/settings").json()["values"]
    assert got["connections"]["radarr_api_key"] == "" and got["secrets_set"]["radarr_api_key"] is True and "secret123" not in c.get("/api/settings").text
    assert c.patch("/api/settings", json={"connections": {"radarr_url": "radarr:7878"}}).status_code == 400
    monkeypatch.setattr(web.radarr, "call", lambda *a, **k: {"version": "5.0"})
    assert c.post("/api/connections/test", json={"radarr_url": "http://x:7878"}).json() == {"ok": True, "message": "Connected to Radarr 5.0"}
    assert c.get("/").status_code == 200 and "Refetcharr" in c.get("/").text


# ── 1.2.0: tunable rules ──────────────────────────────────────────────────
REAL_TITLES = {  # spellings from real grabs
    "The Jungle Book 2016 2160p DSNP WEB-DL Hybrid H265 DV HDR DDP Atmos 5 1 English --HONE": ("x265", "eac3"),
    "G I Joe Retaliation 2013 2160p ATVP WEB-DL DD 5 1 DV HDR H 265-PiRaTeS": ("x265", "ac3"),
    "Dirty Rotten Scoundrels 1988 1080p BluRay x264-nikt0": ("x264", None),
    "Alien.Resurrection.1997.1080p.DSNP.WEB-DL.DDP5.1.H.264-GPRS.mkv": ("x264", "eac3"),
    "The Longest Yard (2005) REPACK (1080p BDRip x265 10bit DTS-HD MA 5.1 - JBENT)": ("x265", "dtshd"),
    "Film 2020 1080p AV1 Opus 5.1-GRP": ("av1", "opus"),
    "Film 2020 UHD BluRay 2160p DTS-X 7.1 HEVC-GRP": ("x265", "dtsx"),
    "Film 2020 1080p WEB E-AC-3 HEVC": ("x265", "eac3"),
    "Film 2020 1080p AC3 x264": ("x264", "ac3"),
    "Film 2020 1080p DTS x264": ("x264", "dts"),
}


def test_codecs_are_read_from_real_release_titles():
    for title, (video, audio) in REAL_TITLES.items():
        found_v = [c for c in rules.VIDEO_CODECS if rules.codec_rank(title, [c], "video") == 0]
        found_a = [c for c in rules.AUDIO_CODECS if rules.codec_rank(title, [c], "audio") == 0]
        assert found_v == [video], title
        assert (audio in found_a) if audio else found_a == [], (title, found_a)
    assert rules.codec_rank("Film AAC2.0", ["aac"], "audio") == 0  # digits may follow
    assert rules.codec_rank("Film EAC3", ["ac3"], "audio") == 1  # E-AC-3 isn't AC-3
    assert rules.codec_rank("Film Maxvid x264", ["vid"], "video") == 1  # part of a word doesn't count
    assert rules.codec_rank("Film Some.Thing x264", ["some thing"], "video") == 0  # your own words, any separators
    assert rules.codec_rank("Film x264", ["(x"], "video") == 1  # a typed name is literal text, never a pattern


SIZE = {**R, "prefer": "size"}  # the codec and format-score tests below are about picking the 1.5 way; HIGH-first is in test_quality


def test_empty_codec_lists_choose_exactly_as_before():
    m = film(gb=40)
    R = SIZE
    picks = [release(gb=8, score=100, title="Film 2000 1080p BluRay x264"), release(gb=8, score=0, title="Film 2000 1080p BluRay x265 Atmos")]
    assert R["video_codecs"] == [] and R["audio_codecs"] == []
    assert "x264" in rules.best(m, picks, R)[0]["title"]  # Radarr's format score still decides


def test_codec_preferences_order_and_strictness():
    m = film(gb=40)
    R = SIZE
    x264 = release(gb=8, score=100, title="Film 2000 1080p BluRay DTS x264")
    x265 = release(gb=8, score=0, title="Film 2000 1080p BluRay DDP x265")
    av1 = release(gb=8, score=0, title="Film 2000 1080p WEB Opus AV1")
    r = {**R, "video_codecs": ["av1", "x265", "x264"]}
    assert "AV1" in rules.best(m, [x264, x265, av1], r)[0]["title"]
    assert "x265" in rules.best(m, [x264, x265], r)[0]["title"]  # beats a better format score
    assert "x264" in rules.best(m, [x265, x264], {**R, "video_codecs": ["x264", "x265"]})[0]["title"]  # order is what matters
    assert "DTS" in rules.best(m, [x265, x264], {**R, "audio_codecs": ["dts"]})[0]["title"]
    assert "x265" in rules.best(m, [x264, x265], {**r, "audio_codecs": ["dts"]})[0]["title"]  # video outranks audio
    # not on the list: ranks last, or is refused when strict
    assert rules.judge(m, x264, {**R, "video_codecs": ["x265"]}) == []
    assert "video codec isn't on your list" in " ".join(rules.judge(m, x264, {**R, "video_codecs": ["x265"], "video_strict": True}))
    assert "audio codec isn't on your list" in " ".join(rules.judge(m, x264, {**R, "audio_codecs": ["opus"], "audio_strict": True}))
    assert rules.judge(m, x264, {**R, "video_strict": True}) == []  # strict with nothing listed refuses nothing


def test_every_old_fixed_rule_can_be_switched():
    four_k = film(gb=60, quality="Bluray-2160p", res=2160)
    assert rules.judge(four_k, release(gb=8, res=1080), {**R, "allow_lower_res": True}) == []
    assert rules.judge(film(), release(langs=("French",)), {**R, "keep_english": False}) == []
    assert rules.judge(film(), release(score=-5), {**R, "reject_negative_score": False}) == []
    assert rules.judge(film(gb=60), release(title="Film 2000 2160p WEB-DL DV H265", res=2160, gb=20), {**R, "reject_dv_no_hdr10": False}) == []
    usenet = release() | {"protocol": "usenet"}
    assert "usenet" in " ".join(rules.judge(film(), usenet, R)) and rules.judge(film(), usenet, {**R, "allow_usenet": True}) == []
    m, picks = film(gb=40), [release(gb=8), release(gb=20, res=2160, title="Film 2000 2160p WEB-DL HDR x265")]
    assert rules.best(m, picks, R)[0]["quality"]["quality"]["resolution"] == 2160
    assert rules.best(m, picks, {**R, "prefer_higher_res": False})[0]["quality"]["quality"]["resolution"] == 1080
    short = film(mins=15, gb=40)
    assert rules.group(short, R) is None and rules.group(short, {**R, "min_runtime": 10}) == "remux"


def test_settings_api_checks_each_kind_of_rule(monkeypatch):
    from refetcharr import web
    c = keyed(web)
    d = c.get("/api/settings").json()
    assert [x["id"] for x in d["catalog"]["video"]][:3] == ["av1", "x265", "x264"]
    ok = c.patch("/api/settings", json={"rules": {"video_codecs": [" av1", "x265", "AV1 ", "", "My Tag"], "video_strict": True, "min_1080": 40}})
    assert ok.status_code == 200 and ok.json()["values"]["rules"]["video_codecs"] == ["av1", "x265", "My Tag"]  # tidied
    assert config.load()["rules"]["video_strict"] is True and config.load()["rules"]["min_1080"] == 40
    for bad in ({"video_codecs": "x265"}, {"video_codecs": ["x" * 41]}, {"video_strict": "yes"}, {"keep_english": 1}, {"min_seeders": True},
                {"min_seeders": -1}, {"max_ratio": 1.5}, {"stall_warn_hours": 9999}):
        assert c.patch("/api/settings", json={"rules": bad}).status_code == 400, bad
    # the markers can't cross
    r = c.patch("/api/settings", json={"rules": {"min_1080": 500}})
    assert r.status_code == 400 and "in order" in r.json()["detail"]
    assert c.patch("/api/settings", json={"rules": {"cap_2160": 10}}).status_code == 400
    assert config.load()["rules"]["min_1080"] == 40  # refused means unchanged


# ── 1.2.0: stalled downloads ──────────────────────────────────────────────
def _age(eng_mod, mid, hours):
    store.update(mid, progress_at=(dt.datetime.now() - dt.timedelta(hours=hours)).isoformat(timespec="seconds"))


def test_a_stalled_download_warns_frees_its_slot_and_recovers(eng):
    config.save({"rules": {"stall_warn_hours": 6}})  # written for the 1.5 default
    from refetcharr import engine as E
    e = eng([film(i, f"Film {i}") for i in (1, 2, 3)])
    config.save({"queue": {"auto": True, "max_active": 1}})  # slots are auto-queue's: films queued by hand skip them
    s = config.load()
    e.refill(s); e.refill(s)
    assert e.fake.grabbed == [1]  # one slot, one download
    e.fake.queue_[0].update(sizeleft=int(4e9), errorMessage="qBittorrent is reporting an error")
    e.import_finished()  # first look: the clock runs from the grab
    assert store.load()["1"].get("stalled") is False or not store.load()["1"].get("stalled")
    # moving: small jitter isn't progress, a real drop is
    _age(E, 1, 5)
    e.import_finished()
    assert not store.load()["1"].get("stalled")
    # no progress past the limit: a warning, still downloading, not failed
    _age(E, 1, 7)
    e.import_finished()
    entry = store.load()["1"]
    assert entry["stalled"] is True and entry["status"] == "downloading"
    st = e.status()
    assert st["stalled"] == 1 and st["active"][0]["stalled"] and st["active"][0]["stalled_hours"] >= 7
    assert st["active"][0]["message"] == "qBittorrent is reporting an error" and not [x for x in st["failed"] if x["id"] == "1"]
    assert any(a["kind"] == "warn" and "no progress for 7 hours" in a["text"] for a in store.recent(5))
    # ...and the next film gets the slot
    e.refill(config.load())
    assert e.fake.grabbed == [1, 2]
    # jitter from a rounded percentage doesn't count as moving
    e.fake.queue_[0].update(sizeleft=int(4e9) - 5000)
    e.import_finished()
    assert store.load()["1"]["stalled"] is True
    # bytes moving again: warning gone, it takes a slot again
    e.fake.queue_[0].update(sizeleft=int(3e9))
    e.import_finished()
    assert store.load()["1"]["stalled"] is False and e.status()["stalled"] == 0
    assert any("moving again" in a["text"] for a in store.recent(5))
    e.refill(config.load())
    assert 3 not in e.fake.grabbed  # 2 active with a limit of 1: nothing new starts


def test_a_stalled_download_that_finishes_still_imports(eng):
    from refetcharr import engine as E
    e = eng([film(1)])
    e.queue_now(ids=[1]); e.refill(config.load())
    e.fake.queue_[0].update(sizeleft=int(1e9))
    e.import_finished(); _age(E, 1, 7); e.import_finished()
    assert store.load()["1"]["stalled"] is True
    e.fake.finish(1); e.import_finished()
    assert e.fake.imported == [1] and store.load()["1"]["status"] == "replaced" and not store.load()["1"]["stalled"]


def test_stall_warnings_can_be_turned_off_and_negative_sizeleft_is_clamped(eng):
    from refetcharr import engine as E
    config.save({"rules": {"stall_warn_hours": 0}})
    e = eng([film(1)])
    e.queue_now(ids=[1]); e.refill(config.load())
    e.fake.queue_[0].update(sizeleft=-14873)  # what Radarr really sends for a torrent at the very end
    e.import_finished(); _age(E, 1, 500); e.import_finished()
    assert not store.load()["1"].get("stalled") and store.load()["1"]["left_best"] == 0
    assert e.status()["active"][0]["progress"] == 100


# ── 1.2.0: import reliability ─────────────────────────────────────────────
def test_manual_import_handles_missing_quality_field(monkeypatch):
    from refetcharr import radarr as R
    seen = {}

    def fake_call(method, path, body=None, timeout=120, url="", key=""):
        seen["method"] = method
        seen["path"] = path
        seen["body"] = body
        return {"ok": True}

    monkeypatch.setattr(R, "call", fake_call)
    R.manual_import({"path": "/data/incoming/1.mkv", "movie": {"id": 1}, "size": 8e9}, 1, "d1")

    assert seen["method"] == "POST"
    assert seen["path"] == "/command"
    assert seen["body"]["files"][0]["path"] == "/data/incoming/1.mkv"
    assert "quality" not in seen["body"]["files"][0]


def test_a_release_still_unpacking_isnt_given_up_on(eng, monkeypatch):
    """The regression: scene releases are rars at first, so "no video file" was final even after the mkv appeared."""
    from refetcharr import engine as E
    e = eng([film(1)])
    e.queue_now(ids=[1]); e.refill(config.load())
    e.fake.finish(1)
    real = e.fake.manual_import_items
    monkeypatch.setattr(E.radarr, "manual_import_items", lambda d, m: [{"path": "/data/incoming/1.rar", "movie": {"id": 1}, "size": 1, "quality": {}}])
    e.import_finished()
    assert store.load()["1"]["status"] != "nothing to import" and store.load()["1"]["no_video_since"]  # waiting, not failed
    # gives up after a long wait...
    store.update(1, no_video_since=(dt.datetime.now() - dt.timedelta(hours=3)).isoformat(timespec="seconds"))
    e.import_finished()
    assert store.load()["1"]["status"] == "nothing to import"
    # ...but a film already given up on recovers when the video appears, as Flight Risk needs
    monkeypatch.setattr(E.radarr, "manual_import_items", real)
    e.import_finished()
    assert e.fake.imported == [1] and store.load()["1"]["status"] == "replaced" and not store.load()["1"]["problem"]


def test_given_up_film_gone_from_the_queue_stays_put(eng):
    e = eng([film(1)])
    store.update(1, title="Film", old_size=int(4e10), new_size=int(8e9), status="nothing to import", problem="the download has no video file")
    e.import_finished()
    assert store.load()["1"]["status"] == "nothing to import" and not store.load()["1"].get("missing_since")


def test_an_import_is_only_asked_for_once_while_radarr_works_on_it(eng, monkeypatch):
    """The regression: Dinner for Schmucks was sent for import twice because a slow import flipped back to downloading."""
    from refetcharr import engine as E
    e = eng([film(1)])
    e.queue_now(ids=[1]); e.refill(config.load())
    e.fake.finish(1)
    calls = []
    monkeypatch.setattr(E.radarr, "manual_import", lambda item, mid, did: calls.append(mid))  # Radarr takes its time: file unchanged
    e.import_finished()
    assert calls == [1] and store.load()["1"]["status"] == "importing"
    e.import_finished()
    assert calls == [1] and store.load()["1"]["status"] == "importing"
    for x in e.fake.queue_:
        x["trackedDownloadState"] = "importing"  # Radarr is moving the file
    e.import_finished()
    assert store.load()["1"]["status"] == "importing"
    for x in e.fake.queue_:
        x["trackedDownloadState"] = "importPending"  # it never happened: ask again once enough time has passed
    store.update(1, import_asked=(dt.datetime.now() - dt.timedelta(hours=1)).isoformat(timespec="seconds"))
    e.import_finished()
    assert calls == [1, 1]


def test_one_slow_lookup_doesnt_stop_other_films_importing(eng, monkeypatch):
    from refetcharr import engine as E
    e = eng([film(1), film(2, "Other")])
    e.queue_now(ids=[1, 2]); e.refill(config.load()); e.refill(config.load())
    e.fake.finish(1); e.fake.finish(2)
    real = e.fake.manual_import_items
    def slow(did, mid):
        if mid == 1:
            raise E.radarr.RadarrError("Can't reach Radarr: timed out")
        return real(did, mid)
    monkeypatch.setattr(E.radarr, "manual_import_items", slow)
    e.import_finished()
    assert e.fake.imported == [2] and store.load()["1"]["status"] != "replaced"
    monkeypatch.setattr(E.radarr, "manual_import_items", real)
    e.import_finished()
    assert sorted(e.fake.imported) == [1, 2]


# ── 1.2.1: fully downloaded, but not completed by qBittorrent ─────────────
def test_a_download_qbittorrent_hasnt_completed_is_shown_and_never_imported_early(eng):
    """Real case: every byte received (sizeleft slightly negative), Radarr still says downloading, file still in incomplete/."""
    from refetcharr import engine as E
    e = eng([film(1), film(2, "Other")])
    config.save({"queue": {"auto": True, "max_active": 1}})
    e.refill(config.load())
    e.fake.queue_[0].update(sizeleft=-14873, status="warning", errorMessage="The download is stalled with no connections")
    e.import_finished()
    a = e.status()["active"][0]
    assert a["finishing"] and not a["finish_warn"] and e.fake.imported == []  # unverified data is never imported
    _age(E, 1, 1.5)
    e.import_finished()
    s = e.status()
    assert s["stuck"] == 1 and s["active"][0]["finish_warn"] and s["active"][0]["finishing_minutes"] >= 90 and s["stalled"] == 0
    assert any("hasn't completed it" in x["text"] for x in store.recent(5))
    assert e.fake.imported == []
    # it keeps its slot: it's waiting on the disk, and another download would only add to that
    e.refill(config.load())
    assert e.fake.grabbed == [1]
    # past the stall limit it counts as stalled like any other
    _age(E, 1, 7)
    e.import_finished()
    s = e.status()
    assert s["stalled"] == 1 and s["stuck"] == 0
    # when qBittorrent finally completes it, it imports by itself
    e.fake.finish(1)
    e.import_finished()
    assert e.fake.imported == [1] and store.load()["1"]["status"] == "replaced"


def test_pages_are_revalidated_but_api_is_not_touched():
    from refetcharr import web
    c = keyed(web)
    assert c.get("/static/app.js").headers["cache-control"] == "no-cache" and c.get("/").headers["cache-control"] == "no-cache"
    assert "cache-control" not in c.get("/api/health").headers


# ── 1.3.0: tiny files, leaving films alone, timings ───────────────────────
def tiny(mid=1, title="Tiny", gb=1.5, group="LAMA"):
    f = film(mid, title, gb=gb, quality="WEBDL-1080p", codec="x265")
    f["movieFile"]["releaseGroup"] = group
    return f


def test_a_tiny_file_wants_a_bigger_replacement_up_to_the_maximum():
    m = tiny()  # 1.5 GB for 2 hours: 12 MB/min, under Too small (20)
    assert rules.group(m, R) == "tiny"
    assert rules.judge(m, release(gb=6), R) == []  # 50 MB/min: bigger, above the minimum, under the maximum
    assert any("not bigger" in w for w in rules.judge(m, release(gb=1.4), R))
    assert any("too small to look good" in w for w in rules.judge(m, release(gb=3), R))  # bigger but still under Minimum
    assert any("over the maximum" in w for w in rules.judge(m, release(gb=20), R))
    assert not any("not small enough" in w for w in rules.judge(m, release(gb=6), R))


def test_tiny_films_are_queued_after_the_savings_and_count_as_replaced_when_bigger(eng):
    e = eng([film(1, "Remux"), tiny(2, "Tiny A", gb=1.5), tiny(3, "Tiny B", gb=1.0)], releases={2: [release(gb=6)], 3: [release(gb=6)]})
    config.save({"queue": {"auto": True, "max_active": 10}})
    for _ in range(4):
        e.refill(config.load())
    assert e.fake.grabbed == [1]  # tiny is off by default
    config.save({"queue": {"groups": {"tiny": True}}})
    for _ in range(4):
        e.refill(config.load())
    assert e.fake.grabbed == [1, 3, 2]  # the smallest first
    e.fake.manual_import = lambda item, mid, did: (e.fake.imported.append(mid), e.fake.movies_[mid]["movieFile"].update(size=int(6e9)))
    e.fake.finish(3)
    e.import_finished()
    assert store.load()["3"]["status"] == "replaced" and store.freed(store.load()["3"]) < 0
    s = e.status()
    assert s["grown"]["films"] == 1 and s["freed"] == 0  # growing doesn't count as freed


def test_films_from_a_kept_group_and_ignored_films_are_left_alone(eng):
    nikt0 = film(1, "Nikt0 film")
    nikt0["movieFile"]["releaseGroup"] = "nikt0"
    e = eng([nikt0, film(2, "B", gb=39), film(3, "C", gb=38)])
    config.save({"queue": {"auto": True, "max_active": 10}, "ignore": {"file_groups": ["NIKT0"]}})
    e.ignore([3])
    for _ in range(4):
        e.refill(config.load())
    assert e.fake.grabbed == [2]
    assert rules.ignored(nikt0, config.load()["ignore"]) == "file from nikt0"
    e.queue_now(ids=[3])  # chosen by hand: tried even though it's ignored
    e.refill(config.load())
    assert e.fake.grabbed == [2, 3]
    e.ignore([3], on=False)
    assert config.load()["ignore"]["films"] == []


def test_never_download_from_a_group():
    m = film()
    bad = release(gb=8, title="Film 2000 1080p BluRay x265-YIFY")
    assert any("never download" in w for w in rules.judge(m, bad, R, ["yify"]))
    assert rules.judge(m, dict(bad, releaseGroup="GOOD"), R, ["yify"]) == []  # Radarr's own parse wins
    assert rules.release_group({"title": "Dirty Rotten Scoundrels 1988 1080p BluRay x264-nikt0"}) == "nikt0"


def test_timings_are_settings(eng):
    from refetcharr import engine as E
    assert E.wait("missing_minutes") == 900 and E.wait("no_video_hours") == 7200
    config.save({"timing": {"missing_minutes": 1}})
    assert E.wait("missing_minutes") == 60


def test_api_checks_the_new_settings_and_ignores_films(eng, monkeypatch):
    from refetcharr import web
    e = eng([film(1), film(2, "B", gb=39)])
    monkeypatch.setattr(web, "engine", e)
    c = keyed(web)
    assert c.patch("/api/settings", json={"timing": {"missing_minutes": 0}}).status_code == 400
    assert c.patch("/api/settings", json={"ignore": {"file_groups": "nikt0"}}).status_code == 400
    assert c.patch("/api/settings", json={"connections": {"radarr_link": "radarr.local"}}).status_code == 400
    v = c.patch("/api/settings", json={"ignore": {"file_groups": ["nikt0", " NIKT0 ", ""]}, "connections": {"radarr_link": "https://radarr.x/"}}).json()["values"]
    assert v["ignore"]["file_groups"] == ["nikt0"]
    assert v["connections"]["radarr_link"] == "https://radarr.x"
    assert c.post("/api/ignore", json={"ids": [2]}).status_code == 200 and config.load()["ignore"]["films"] == [2]
    assert c.get("/api/status").json()["radarr_link"] == "https://radarr.x"
    assert c.patch("/api/settings", json={"queue": {"groups": {"tiny": True}}}).json()["values"]["queue"]["groups"]["tiny"] is True


def test_untagged_audio_counts_as_english_unless_another_language_is_named(monkeypatch):
    def probe_with(*langs):
        monkeypatch.setattr(rules, "probe", lambda p, f="ffprobe": {"streams": [{"codec_type": "video"}] + [
            {"codec_type": "audio", "tags": {"language": x} if x else {}} for x in langs]})
    probe_with("und")
    assert rules.file_problem("x.mkv", True) is None
    probe_with("und", "und", None)
    assert rules.file_problem("x.mkv", True) is None
    assert "no English" in rules.file_problem("x.mkv", True, untagged_ok=False)
    probe_with("chi")
    assert "no English" in rules.file_problem("x.mkv", True)
    probe_with("und", "chi")  # one track names a language that isn't English: the untagged one could be anything
    assert "no English" in rules.file_problem("x.mkv", True)


def test_a_track_titled_english_counts_even_when_tagged_wrong(monkeypatch):
    def probe_with(lang, title):
        monkeypatch.setattr(rules, "probe", lambda p, f="ffprobe": {"streams": [{"codec_type": "video"},
                            {"codec_type": "audio", "tags": {"language": lang, "title": title}}]})
    probe_with("chi", "English DTS-HD Master Audio 5.1 @ 3661 kbps")  # Eastern Promises (BeiTai), as ffprobe reads it
    assert rules.file_problem("x.mkv", True) is None
    probe_with("chi", "Eng 5.1")
    assert rules.file_problem("x.mkv", True) is None
    probe_with("chi", "Mandarin DTS 5.1")
    assert "no English" in rules.file_problem("x.mkv", True)
    probe_with("chi", "Bengali 2.0")  # "eng" inside another word isn't English
    assert "no English" in rules.file_problem("x.mkv", True)



def test_probe_asks_ffprobe_for_track_titles(monkeypatch):
    seen = {}
    class Out:
        stdout = "{}"
    def run(cmd, **k):
        seen["cmd"] = cmd
        return Out()
    monkeypatch.setattr(rules.subprocess, "run", run)
    rules.probe("x.mkv")
    entries = seen["cmd"][seen["cmd"].index("-show_entries") + 1]
    assert "stream_tags=language,title" in entries



def test_radarr_importing_our_release_is_known_by_name_even_when_the_size_is_off(eng):
    e = eng([film(1, "Songbird", gb=23.7)])
    store.update(1, title="Songbird", old_size=int(23.7e9), new_size=int(11.2e9), status="downloading",
                 new_title="Songbird 2020 AMZN HDR 2160p WEB-DL HEVC DDP5 1-EVO", grabbed="2026-11-10 03:14")
    e.fake.movies_[1]["movieFile"].update(size=int(10.0e9), sceneName="Songbird.2020.AMZN.HDR.2160p.WEB-DL.HEVC.DDP5.1-EVO")
    e._settle("1", store.load()["1"])
    assert store.load()["1"]["status"] == "replaced" and store.load()["1"]["saved"] == int(13.7e9)


def test_written_off_films_radarr_imported_anyway_become_replaced(eng):
    e = eng([film(1, "She Said", gb=38), film(2, "Other", gb=30)])
    for mid, t in ((1, "She.Said.2022.2160p.WEB-DL"), (2, "Other.2020.1080p.BluRay")):
        store.update(mid, title=t, old_size=int(38e9), new_size=int(25e9), status="nothing to import", new_title=t, problem="the download has no video file")
    e.fake.movies_[1]["movieFile"].update(size=int(24.1e9), sceneName="She.Said.2022.2160p.WEB-DL")
    e.reconcile()
    s = store.load()
    assert s["1"]["status"] == "replaced" and s["1"]["problem"] == "" and s["2"]["status"] == "nothing to import"


# ── 1.4.0: sign-in ────────────────────────────────────────────────────────
def test_everything_needs_a_sign_in_or_the_api_key():
    from fastapi.testclient import TestClient
    from refetcharr import web
    c = TestClient(web.app)
    assert c.get("/api/health").status_code == 200  # the container's health check
    assert c.get("/api/status").status_code == 401
    assert c.get("/api/status", headers={"X-Api-Key": "wrong"}).status_code == 401
    r = c.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert c.get("/static/app.js", follow_redirects=False).status_code == 303
    assert c.get("/login").status_code == 200 and c.get("/static/login.js").status_code == 200
    assert keyed(web).get("/api/status").status_code == 200


def test_first_login_then_sign_in_change_and_reset():
    from fastapi.testclient import TestClient
    from refetcharr import auth, web
    c = TestClient(web.app)
    assert c.get("/api/auth/state").json() == {"set": False, "signed_in": False}
    assert c.post("/api/auth/setup", json={"username": "jarsky", "password": "short"}).status_code == 400
    assert c.post("/api/auth/setup", json={"username": "jarsky", "password": "correct horse"}).status_code == 200
    assert c.get("/api/status").status_code == 200 and c.get("/api/auth/state").json()["signed_in"]  # signed in by the setup
    assert c.post("/api/auth/setup", json={"username": "x", "password": "another one"}).status_code == 409  # only once
    stored = config.load()["auth"]
    assert stored["password_hash"].startswith("pbkdf2_sha256$") and "correct horse" not in str(stored)
    got = c.get("/api/settings").json()
    assert got["values"]["auth"] == {"username": "jarsky"} and stored["api_key"] not in str(got) and stored["secret"] not in str(got)
    assert c.patch("/api/settings", json={"auth": {"username": "evil"}}).status_code == 400
    # a second browser
    b = TestClient(web.app)
    assert b.post("/api/auth/login", json={"username": "jarsky", "password": "wrong password"}).status_code == 401
    assert b.post("/api/auth/login", json={"username": "JARSKY", "password": "correct horse"}).status_code == 200
    assert b.get("/api/status").status_code == 200
    # changing the password signs every other browser out, but not this one
    assert c.post("/api/auth/change", json={"current": "nope", "username": "jarsky", "password": "battery staple"}).status_code == 401
    assert c.post("/api/auth/change", json={"current": "correct horse", "username": "jarsky", "password": "battery staple"}).status_code == 200
    assert c.get("/api/status").status_code == 200 and b.get("/api/status").status_code == 401
    # the container's reset: everyone out, the page asks for a new login
    auth.reset_login()
    assert c.get("/api/status").status_code == 401 and c.get("/api/auth/state").json()["set"] is False
    b.post("/api/auth/logout")


def test_too_many_failed_sign_ins_lock_the_address_out(monkeypatch):
    from fastapi.testclient import TestClient
    from refetcharr import auth, web
    auth.set_login("jarsky", "correct horse")
    monkeypatch.setattr(auth, "_fails", __import__("collections").defaultdict(__import__("collections").deque))
    c = TestClient(web.app)
    for _ in range(auth.MAX_FAILS):
        assert c.post("/api/auth/login", json={"username": "jarsky", "password": "guess"}).status_code == 401
    assert c.post("/api/auth/login", json={"username": "jarsky", "password": "correct horse"}).status_code == 429


def test_cli_login_commands_and_api_key(monkeypatch, capsys):
    import sys
    from refetcharr import auth, cli
    monkeypatch.setattr(sys, "argv", ["refetcharr", "login", "set", "admin", "--password-stdin"])
    monkeypatch.setattr(sys, "stdin", __import__("io").StringIO("a long password\n"))
    cli.main()
    assert auth.verify("admin", "a long password")
    monkeypatch.setattr(sys, "argv", ["refetcharr", "login", "show"]); cli.main()
    assert "Login: admin" in capsys.readouterr().out
    monkeypatch.setattr(sys, "argv", ["refetcharr", "apikey"]); cli.main()
    key = capsys.readouterr().out.strip()
    monkeypatch.setattr(sys, "argv", ["refetcharr", "apikey", "--new"]); cli.main()
    assert capsys.readouterr().out.strip() != key and auth.key_ok(config.load()["auth"]["api_key"])
    monkeypatch.setattr(sys, "argv", ["refetcharr", "login", "reset"]); cli.main()
    assert not auth.is_set()
