"""The quality model, the keep rules (HDR, surround, Atmos, lossless, picture floor), decisions and the webhook."""

import pytest

from refetcharr import config, quality, rules, store

R = {**config.DEFAULTS["rules"], "video_codecs": [], "audio_codecs": [], "keep_atmos": False}


@pytest.fixture(autouse=True)
def data(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA", str(tmp_path))
    store._log.clear()
    monkeypatch.setattr(store, "_loaded", False)
    config.save({"rules": {"keep_atmos": False, "video_codecs": [], "audio_codecs": []}})  # what these tests were written for
    return tmp_path


def film(gb=40.0, mins=120, quality_name="Remux-2160p", res=2160, codec="HEVC", hdr="HDR10", audio="TrueHD Atmos", ch=7.1, kbps=0):
    return {"id": 7, "title": "Film", "year": 2000, "runtime": mins, "titleSlug": "film",
            "movieFile": {"size": int(gb * 1e9), "path": "/data/movies/Film/Film.mkv", "quality": {"quality": {"name": quality_name, "resolution": res}},
                          "mediaInfo": {"runTime": f"{mins // 60}:{mins % 60:02d}:00", "videoCodec": codec, "audioLanguages": "eng",
                                        "videoDynamicRange": "HDR" if hdr else "", "videoDynamicRangeType": hdr, "videoBitrate": 0,
                                        "audioStreams": [{"codec": audio, "channels": ch, "bitrate": kbps * 1000, "language": "eng"}]}}}


def release(title, gb=14.0, res=2160, seeders=50):
    return {"guid": title, "indexerId": 1, "protocol": "torrent", "size": int(gb * 1e9), "seeders": seeders, "customFormatScore": 0,
            "title": title, "languages": [{"name": "English"}], "quality": {"quality": {"name": f"WEBDL-{res}p", "resolution": res}},
            "rejections": []}


def test_picture_bitrate_takes_off_the_audio_and_counts_in_x265_terms():
    # 40 GB over 2 h = 44.4 Mbps; TrueHD 7.1 is estimated at 4.5 Mbps -> about 40 Mbps of x265 picture: HIGH for 4K
    d = quality.describe_file(film()["movieFile"], 120)
    assert d["hdr"] == "HDR10" and d["atmos"] and d["lossless"] and d["channels"] == 7.1
    assert 39 < d["picture_mbps"] < 41 and d["video_tier"] == "HIGH" and d["badge"] == "HIGH"
    # the same size in x264 needs 1.5x the bitrate for the same picture
    d264 = quality.describe_file(film(codec="AVC")["movieFile"], 120)
    assert d264["picture_mbps"] == pytest.approx(d["picture_mbps"] / 1.5, rel=0.01)
    # a known audio bitrate is used as is
    assert quality.describe_file(film(audio="EAC3", ch=5.1, kbps=640)["movieFile"], 120)["audio_kbps"] == 640


def test_releases_are_read_from_their_titles():
    d = quality.describe_release(release("Film 2000 2160p WEB-DL DDP5 1 Atmos DV HDR H 265-GRP"), 120)
    assert d["video_codec"] == "x265" and d["audio"] == "eac3" and d["channels"] == 5.1 and d["atmos"] and "HDR" in d["hdr"]
    assert quality.describe_release(release("Film 2000 2160p WEB-DL SDR DDP5 1 H 265-GRP"), 120)["hdr"] == ""
    assert quality.describe_release(release("Film 1080p WEB-DL AAC2.0 H 264-GRP", res=1080), 120)["channels"] == 2.0
    assert quality.title_channels("Film 2000 2160p x265 10bit-GRP") == 0  # "x265 10bit" isn't a channel layout
    assert quality.describe_release(release("Film 2000 2160p WEB-DL DDPA 5 1 H 265"), 120)["atmos"]  # DDPA = DD+ Atmos


def test_hdr_must_be_kept():
    m = film()
    assert any("would lose HDR" in w for w in rules.judge(m, release("Film 2000 2160p WEB-DL DD 5 1 H 265-PiRaTeS"), R))
    assert any("would lose HDR" in w for w in rules.judge(m, release("Film 2000 2160p WEB-DL SDR DDP5 1 H265-X"), R))
    assert not any("HDR" in w for w in rules.judge(m, release("Film 2000 2160p WEB-DL DDP5 1 HDR H 265-X"), R))
    assert not any("HDR" in w for w in rules.judge(film(hdr=""), release("Film 2000 2160p WEB-DL DD 5 1 H 265-X"), R))  # SDR film
    assert not any("HDR" in w for w in rules.judge(m, release("Film 2000 2160p WEB-DL DD 5 1 H 265-X"), {**R, "keep_hdr": False}))


def test_surround_atmos_and_lossless():
    m = film()
    assert any("stereo audio" in w for w in rules.judge(m, release("Film 2000 2160p WEB-DL DDP 2 0 HDR H 265-X"), R))
    assert not any("stereo" in w for w in rules.judge(m, release("Film 2000 2160p WEB-DL HDR H 265-X"), R))  # unknown: the file check decides
    lossy = release("Film 2000 2160p WEB-DL DDP5 1 HDR H 265-X")
    assert not any("Atmos" in w or "lossless" in w for w in rules.judge(m, lossy, R))  # off by default
    strict = {**R, "keep_atmos": True, "keep_lossless": True}
    why = " ".join(rules.judge(m, lossy, strict))
    assert "would lose Atmos" in why and "would lose lossless audio" in why


def test_picture_floor_counts_the_audio_and_the_codec():
    m = film(quality_name="Remux-1080p", res=1080, hdr="", codec="AVC")
    # 4.2 GB in 2 h with DD+ 5.1: about 4 Mbps of x265 picture, just MEDIUM
    assert not any("picture" in w for w in rules.judge(m, release("Film 1080p WEB-DL DDP5 1 H 265-X", gb=4.2, res=1080), R))
    # the same size in x264 is LOW for 1080p
    assert any("picture LOW for 1080p" in w for w in rules.judge(m, release("Film 1080p WEB-DL DDP5 1 H 264-X", gb=4.2, res=1080), R))
    # and with a TrueHD track taking 3.5 Mbps of it
    assert any("picture LOW" in w for w in rules.judge(m, release("Film 1080p BluRay TrueHD 5 1 x265-X", gb=4.2, res=1080), R))
    assert not any("picture" in w for w in rules.judge(m, release("Film 1080p WEB-DL DDP5 1 H 264-X", gb=4.2, res=1080), {**R, "min_picture": "off"}))


class Probe:
    def __init__(self, streams):
        self.streams = streams

    def __call__(self, path, ffprobe="ffprobe"):
        return {"streams": self.streams}


def test_the_downloaded_file_is_checked_for_what_must_be_kept(monkeypatch):
    sdr = [{"codec_type": "video", "color_transfer": "bt709"}, {"codec_type": "audio", "channels": 6, "codec_name": "eac3", "tags": {"language": "eng"}}]
    monkeypatch.setattr(rules, "probe", Probe(sdr))
    need = rules.needs(quality.describe_file(film()["movieFile"], 120), R)
    assert need == {"hdr": True, "surround": True, "atmos": False, "lossless": False}
    assert "no HDR in the file" in rules.file_problem("/x", True, need=need)
    stereo = [{"codec_type": "video", "color_transfer": "smpte2084"}, {"codec_type": "audio", "channels": 2, "codec_name": "aac", "tags": {"language": "eng"}}]
    monkeypatch.setattr(rules, "probe", Probe(stereo))
    assert "stereo audio" in rules.file_problem("/x", True, need=need)
    good = [{"codec_type": "video", "color_transfer": "smpte2084"},
            {"codec_type": "audio", "channels": 8, "codec_name": "truehd", "profile": "Dolby TrueHD + Dolby Atmos", "tags": {"language": "eng"}}]
    monkeypatch.setattr(rules, "probe", Probe(good))
    assert rules.file_problem("/x", True, need={"hdr": True, "surround": True, "atmos": True, "lossless": True}) is None
    assert rules.file_problem("/x", True) is None  # entries from before 1.5 have no needs


def test_a_grab_records_the_decision(monkeypatch):
    from refetcharr import engine as eng
    m = film(gb=60)
    good = release("Film 2000 2160p WEB-DL DDP5 1 Atmos HDR H 265-GOOD", gb=17, seeders=40)  # 142 MB/min: nearest the preferred 150
    other = release("Film 2000 2160p WEB-DL DDP5 1 HDR H 265-OK", gb=13, seeders=10)
    sdr = release("Film 2000 2160p WEB-DL DD 5 1 H 265-PiRaTeS", gb=12)
    monkeypatch.setattr(eng.radarr, "releases", lambda mid: [sdr, other, good])
    grabbed = []
    monkeypatch.setattr(eng.radarr, "grab", lambda rel, mid: grabbed.append(rel["title"]))
    e = eng.Engine()
    e.try_film(m, config.load())
    entry = store.load()["7"]
    d = entry["decision"]
    assert grabbed == [good["title"]] and entry["needs"]["hdr"]
    assert "4K remux" in d["flagged"] and d["before"]["hdr"] == "HDR10" and d["after"]["atmos"]
    assert d["why"][0] == "2 of 3 releases qualified" and any("would lose HDR" in x for x in d["rejected"])
    assert d["runners_up"][0]["title"] == other["title"]


def test_the_webhook_rereads_the_film_and_flags_it(monkeypatch):
    from fastapi.testclient import TestClient

    from refetcharr import auth, engine as eng, web
    m = film(quality_name="Remux-1080p", res=1080, hdr="")
    monkeypatch.setattr(eng.radarr, "movie", lambda mid: m)
    web.engine._save_json("scan.json", {"at": "2026-11-12T00:00:00", "summary": {}, "films": []})
    key = auth.ensure_keys()["api_key"]
    client = TestClient(web.app)
    assert client.post("/api/webhook/radarr", json={"eventType": "Download", "movie": {"id": 7}}).status_code == 401
    r = client.post(f"/api/webhook/radarr?apikey={key}", json={"eventType": "Download", "movie": {"id": 7}, "isUpgrade": False})
    assert r.status_code == 200 and ("film", 7, "imported by Radarr") in web.engine.jobs
    web.engine.jobs.clear()
    web.engine.refresh_film(7, "imported by Radarr")
    rows = web.engine._load_json("scan.json", {})["films"]
    assert [x["group"] for x in rows] == ["remux"] and rows[0]["badge"]
    assert any("now flagged as 1080p remuxes" in x["text"] for x in store.recent())
    detail = client.get("/api/films/7", headers={"X-Api-Key": key}).json()
    assert detail["now"]["resolution"] == 1080 and "remux" in detail["flagged_now"]


def test_events_come_from_the_whole_log_on_disk(monkeypatch):
    for i in range(350):
        store.log("scan", f"event {i}")
    assert len(store.recent(1000)) == 300  # the feed keeps the last 300
    events = store.history(2000)
    assert len(events) == 350 and events[0]["text"] == "event 349"


# ── 1.6.0: target HIGH at the smallest size, low quality, replace again, suggestions ──
def test_the_smallest_high_release_wins_else_the_best_medium():
    m = film(gb=40, quality_name="Remux-1080p", res=1080, hdr="", codec="AVC", audio="TrueHD", ch=5.1)
    big = release("Film 1080p BluRay DDP5 1 x265-BIG", gb=9, res=1080)     # 9.3 Mbps picture: HIGH
    small = release("Film 1080p BluRay DDP5 1 x265-SMALL", gb=6, res=1080)  # 6.0: HIGH, smaller
    medium = release("Film 1080p BluRay DDP5 1 x265-MED", gb=4.5, res=1080)  # 4.3: MEDIUM
    assert rules.best(m, [big, small, medium], R)[0] is small
    better_medium = release("Film 1080p BluRay DDP5 1 x265-MED2", gb=5.4, res=1080)  # 5.3: MEDIUM, better picture
    assert rules.best(m, [medium, better_medium], R)[0] is better_medium   # none HIGH: the best MEDIUM
    assert rules.best(m, [big, small, medium], {**R, "prefer": "size"})[0] is big  # old way: nearest 60 MB/min
    why = rules.explain(m, small, [small, big, medium], 3, R)
    assert any("smallest release rated HIGH" in w for w in why)


def test_low_quality_films_are_flagged_and_want_a_better_file_of_any_size():
    m = film(gb=2.0, quality_name="WEBDL-1080p", res=1080, hdr="", codec="AVC", audio="AAC", ch=2.0, kbps=128)  # 2.1 Mbps x264
    assert rules.group(m, R) == "lowq"
    assert any("no better than the current file" in w for w in rules.judge(m, release("Film 1080p WEB H264-X", gb=2.2, res=1080), R))
    assert not rules.judge(m, release("Film 1080p WEB DDP5 1 x265-X", gb=5.0, res=1080), R)  # bigger, MEDIUM: fine


def test_replace_again_wins_back_hdr(monkeypatch):
    from refetcharr import engine as eng
    sdr_now = film(gb=12, quality_name="WEBDL-2160p", res=2160, hdr="", codec="x265", audio="EAC3", ch=5.1, kbps=640)
    store.update(7, title="Film", year=2000, old_size=int(60e9), old_quality="Bluray-2160p", new_title="Film 2160p WEB", new_size=int(12e9),
                 status="replaced", grabbed="2026-11-01 10:00", saved=int(48e9))
    monkeypatch.setattr(eng.radarr, "movies", lambda: [sdr_now])
    e = eng.Engine()
    rows = e.replaced()
    assert rows[0]["issues"] == ["lost HDR"]
    assert "1 film(s) queued again" in e.requeue([7])
    assert "7" not in store.load() and e.upgrades[7]["need"] == {"hdr": True} and 7 in e.manual_ids
    hdr = release("Film 2000 2160p WEB-DL DDP5 1 HDR H 265-OK", gb=14)
    sdr = release("Film 2000 2160p WEB-DL DDP5 1 H 265-NO", gb=13)
    grabbed = []
    monkeypatch.setattr(eng.radarr, "releases", lambda mid: [sdr, hdr])
    monkeypatch.setattr(eng.radarr, "grab", lambda rel, mid: grabbed.append(rel["title"]))
    s = config.load(); s["queue"]["paused"] = False
    e.refill(s)
    entry = store.load()["7"]
    assert grabbed == [hdr["title"]] and entry["group"] == "upgrade" and entry["needs"]["hdr"]
    assert entry["previous"]["saved"] == int(48e9)
    assert store.freed({**entry, "saved": int(-2e9)}) == int(46e9)  # the first saving still counts


def test_suggestions_list_the_options_and_a_pick_grabs_that_one(monkeypatch):
    from refetcharr import engine as eng
    m = film(gb=60)
    a = release("Film 2000 2160p WEB-DL DDP5 1 Atmos HDR H 265-A", gb=15)
    b = release("Film 2000 2160p WEB-DL DDP5 1 HDR H 265-B", gb=18)
    monkeypatch.setattr(eng.radarr, "movie", lambda mid: m)
    monkeypatch.setattr(eng.radarr, "releases", lambda mid: [b, a])
    grabbed = []
    monkeypatch.setattr(eng.radarr, "grab", lambda rel, mid: grabbed.append(rel["title"]))
    e = eng.Engine()
    assert "Finding replacements for 1" in e.suggest([7])
    e.tick = None  # not needed: run the job directly
    e.suggest_one(7)
    sg = e.suggestions()[0]
    assert [o["title"] for o in sg["options"]] == [a["title"], b["title"]] and sg["options"][0]["video_tier"] == "HIGH"
    e.pick(7, b["guid"])
    assert e.jobs[-1] == ("pick", 7, b["guid"])
    e.pick_now(7, b["guid"])
    entry = store.load()["7"]
    assert grabbed == [b["title"]] and entry["status"] == "grabbed" and entry["decision"]["why"][0] == "picked by hand from the suggestions"
    assert e.suggestions() == []
    with pytest.raises(ValueError):
        e.pick(7, "nope")


def test_grab_best_picks_starts_the_first_option_of_each(monkeypatch):
    from refetcharr import engine as eng
    e = eng.Engine()
    e._save_json("suggestions.json", {
        "7": {"id": 7, "title": "A", "at": "x", "mode": "", "options": [{"guid": "a1"}, {"guid": "a2"}]},
        "8": {"id": 8, "title": "B", "at": "x", "mode": "", "options": []}})
    msg = e.pick_best([7, 8, 9])
    assert list(e.jobs) == [("pick", 7, "a1")] and "for 1 film" in msg and "Skipped 2" in msg


def test_a_refused_pick_leaves_the_film_as_it_was(monkeypatch):
    from refetcharr import engine as eng
    m = film(gb=60)
    a = release("Film 2000 2160p WEB-DL DDP5 1 HDR H 265-A", gb=15)
    store.update(7, title="Film", year=2000, old_size=int(60e9), old_quality="Remux-2160p", status="no release", grabbed="2026-11-01 10:00")
    monkeypatch.setattr(eng.radarr, "movie", lambda mid: m)
    monkeypatch.setattr(eng.radarr, "releases", lambda mid: [a])

    def refuse(rel, mid):
        raise eng.radarr.RadarrError('{"message": "Getting release from indexer failed"}')
    monkeypatch.setattr(eng.radarr, "grab", refuse)
    e = eng.Engine()
    e.suggest_one(7)
    e.pick_now(7, a["guid"])
    assert store.load()["7"]["status"] == "no release"           # untouched
    assert e.suggestions() and "qBittorrent already has this torrent" in store.recent(1)[0]["text"]


def test_the_defaults_carry_the_tuned_preferences():
    d = config.DEFAULTS["rules"]
    assert d["video_codecs"][0] == "x265" and d["audio_codecs"][0] == "atmos" and d["keep_atmos"] and d["min_seeders"] == 3
    assert d["prefer"] == "high" and config.DEFAULTS["quality"]["video"]["1080"] == [3.5, 6.0]


def test_a_download_waiting_in_the_queue_is_not_stalled():
    from refetcharr import engine as eng
    store.update(7, title="Film", year=2000, old_size=int(40e9), status="downloading", grabbed="2026-11-01 10:00",
                 left_best=int(5e9), progress_at="2026-11-01T10:00:00")
    e = eng.Engine()
    e._track_progress("7", store.load()["7"], {"status": "queued", "sizeleft": int(5e9)}, config.load()["rules"])
    entry = store.load()["7"]
    assert not entry.get("stalled") and entry["progress_at"] > "2026-11-01T10:00:00"


def test_audio_preference_only_decides_between_high_releases_of_about_the_same_size():
    """1.6.3: Robin Hood's 29 GB DTS-HD MA release beat a 20 GB DD+ one of the same HIGH picture."""
    m = film(gb=60)
    r = {**R, "audio_codecs": ["dtshd", "eac3"]}
    lossless = release("Film 2000 2160p WEB-DL DTS-HD MA 5 1 HDR H 265-A", gb=29)
    plus = release("Film 2000 2160p WEB-DL DDP 5 1 HDR H 265-B", gb=20)
    assert rules.best(m, [lossless, plus], r)[0] is plus  # 45% bigger: size wins
    near = release("Film 2000 2160p WEB-DL DTS-HD MA 5 1 HDR H 265-C", gb=21)
    assert rules.best(m, [plus, near], r)[0] is near  # within 10%: your audio preference decides
    assert rules.best(m, [plus, lossless], {**r, "audio_before_size": True})[0] is lossless  # Max quality: audio first
    for rel in (lossless, plus, near):
        assert quality.describe_release(rel, 120, None)["video_tier"] == "HIGH"


def test_suggestions_list_a_release_once_when_two_indexers_have_it(monkeypatch):
    from refetcharr import engine as eng
    m = film(gb=60)
    a = release("Film 2000 2160p WEB-DL DDP5 1 HDR H 265-A", gb=15)
    twin = {**a, "guid": "other-indexer", "indexerId": 2}
    b = release("Film 2000 2160p WEB-DL DDP5 1 HDR H 265-B", gb=18)
    monkeypatch.setattr(eng.radarr, "movie", lambda mid: m)
    monkeypatch.setattr(eng.radarr, "releases", lambda mid: [a, twin, b])
    e = eng.Engine()
    e.suggest_one(7)
    assert [o["title"] for o in e.suggestions()[0]["options"]] == [a["title"], b["title"]]


def test_profiles_set_their_rules_and_anything_else_is_custom():
    from fastapi.testclient import TestClient
    from refetcharr import auth, web
    c = TestClient(web.app, headers={"X-Api-Key": auth.ensure_keys()["api_key"]})
    assert c.get("/api/profiles").json()["current"] == "custom"  # these tests save their own rules
    r = c.post("/api/profile", json={"id": "savings"})
    assert r.status_code == 200 and "Max savings" in r.json()["message"]
    saved = config.load()["rules"]
    assert saved["prefer"] == "small" and saved["preferred_2160"] == 110 and not saved["keep_atmos"]
    assert c.get("/api/profiles").json()["current"] == "savings"
    config.save({"rules": {"preferred_2160": 120}})
    assert c.get("/api/profiles").json()["current"] == "custom"
    assert config.profile_of(config.DEFAULTS["rules"]) == "balanced"  # the defaults are Balanced
    assert c.post("/api/profile", json={"id": "nope"}).status_code == 400


def test_max_savings_takes_the_smallest_release_that_qualifies():
    m = film(gb=60)
    high = release("Film 2000 2160p WEB-DL DDP5 1 HDR H 265-HI", gb=20)
    medium = release("Film 2000 2160p WEB-DL DDP5 1 HDR H 265-MED", gb=13)
    r = {**R, "prefer": "small"}
    assert quality.describe_release(medium, 120, None)["video_tier"] == "MEDIUM"
    assert rules.best(m, [high, medium], r)[0] is medium
    assert rules.best(m, [high, medium], R)[0] is high  # Balanced: HIGH first
    assert any("smallest release that qualifies" in w for w in rules.explain(m, medium, [medium, high], 2, r))


def test_a_picked_option_is_remembered_grabbed_in_bulk_and_kept_by_a_new_search(monkeypatch):
    from refetcharr import engine as eng
    m = film(gb=60)
    a = release("Film 2000 2160p WEB-DL DDP5 1 Atmos HDR H 265-A", gb=15)
    b = release("Film 2000 2160p WEB-DL DDP5 1 HDR H 265-B", gb=18)
    monkeypatch.setattr(eng.radarr, "movie", lambda mid: m)
    monkeypatch.setattr(eng.radarr, "releases", lambda mid: [b, a])
    e = eng.Engine()
    e.suggest_one(7)
    assert e.suggestions()[0]["chosen"] == a["guid"]  # the best pick until you pick another
    assert e.choose(7, b["guid"]) == "Picked." and e.suggestions()[0]["chosen"] == b["guid"]
    with pytest.raises(ValueError):
        e.choose(7, "nope")
    e.suggest_one(7)  # search again: the same release is still offered, so your pick stays
    assert e.suggestions()[0]["chosen"] == b["guid"]
    assert "picked release for 1 film" in e.pick_best([7]) and list(e.jobs)[-1] == ("pick", 7, b["guid"])


def test_ratings_out_of_5_land_on_the_tier_marks():
    b = [3.5, 6.0]  # 1080p picture thresholds
    assert quality.rate(3.49, b) == 1.9 and quality.rate(3.5, b) == 2.0  # never shown as its mark while below it
    assert quality.rate(5.99, b) == 3.4 and quality.rate(6.0, b) == 3.5
    assert quality.rate(6.0 * quality.HIGH_TOP, b) == 5.0 and quality.rate(50, b) == 5.0
    assert quality.rate(0, b) is None and quality.tier_of(1.9) == "LOW" and quality.tier_of(2.0) == "MEDIUM" and quality.tier_of(3.5) == "HIGH"
    assert quality.overall(4.0, 2.0, 70) == 3.4 and quality.overall(4.0, None, 70) == 4.0
    d = quality.describe_release(release("Film 2000 2160p BluRay TrueHD 7 1 Atmos HDR x265-X", gb=30), 120, None)
    assert d["audio_rating"] == 5.0 and d["lossless"]  # lossless audio is always 5.0
    lossy = quality.describe_release(release("Film 2000 2160p WEB-DL DDP 5 1 HDR H 265-X", gb=30), 120, None)
    assert lossy["audio_rating"] <= quality.LOSSY_TOP and lossy["badge"] == quality.tier_of(lossy["rating"])
    assert d["video_bounds"] == [9.0, 15.0] and d["audio_bounds"] == [256, 640]


def test_the_overall_rating_picks_so_weak_audio_counts_against_a_release():
    m = film(gb=40, quality_name="Remux-1080p", res=1080, codec="AVC", hdr="", audio="AC3", ch=2.0)
    stereo = release("Film 2000 1080p WEB-DL AAC 2 0 H 265-A", gb=5.99, res=1080)
    surround = release("Film 2000 1080p WEB-DL DDP 5 1 H 265-B", gb=6.16, res=1080)
    a, b = (quality.describe_release(x, 120, None) for x in (stereo, surround))
    assert a["video_tier"] == "HIGH" and a["badge"] != "HIGH"  # a HIGH picture, but stereo AAC pulls it under 3.5
    assert b["badge"] == "HIGH" and b["size"] > a["size"]
    assert rules.best(m, [stereo, surround], R)[0] is surround
    assert any("rated" in w and "of 5" in w for w in rules.explain(m, surround, [surround, stereo], 2, R))


def test_low_quality_and_better_than_now_use_the_overall_rating():
    m = film(gb=3.0, quality_name="WEBDL-1080p", res=1080, codec="x265", hdr="", audio="AC3", ch=5.1, kbps=448)
    cur = rules.current(m, None)  # 2.9 Mbps picture (1.6) and DD 5.1 at 448 kbps (2.7): 1.9 overall
    assert cur["video_tier"] == "LOW" and cur["badge"] == "LOW" and cur["rating"] < 2.0
    assert rules.group(m, {**R, "tiny_1080": 1}, None) == "lowq"
    unnamed = quality.describe_release(release("Film 2000 1080p WEB H264-X", gb=2.2, res=1080), 120, None)
    assert unnamed["audio_rating"] is None and unnamed["rating"] == unnamed["video_rating"]  # a guessed track earns nothing
    before = {"rating": 3.0, "video_tier": "MEDIUM"}
    assert rules.keeps(before, {"rating": 2.9, "video_tier": "HIGH"}, R, upgrade=True)  # a higher picture tier isn't enough
    assert not rules.keeps(before, {"rating": 3.2, "video_tier": "MEDIUM"}, R, upgrade=True)


def test_posters_come_from_radarr_once_and_are_kept(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from refetcharr import auth, radarr, web
    c = TestClient(web.app, headers={"X-Api-Key": auth.ensure_keys()["api_key"]})
    calls = []
    monkeypatch.setattr(radarr, "poster", lambda mid: calls.append(mid) or b"\xff\xd8poster")
    r = c.get("/api/poster/7")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and r.content == b"\xff\xd8poster"
    assert (tmp_path / "posters" / "7.jpg").exists()

    def gone(mid):
        raise radarr.RadarrError("down")
    monkeypatch.setattr(radarr, "poster", gone)
    assert c.get("/api/poster/7").status_code == 200 and calls == [7]  # kept: Radarr isn't asked again
    assert c.get("/api/poster/8").status_code == 404
    assert TestClient(web.app).get("/api/poster/7").status_code == 401  # signed in only


def test_suggestions_searched_before_ratings_are_rated_from_what_they_kept(monkeypatch):
    from refetcharr import engine as eng
    m = film(gb=60)
    monkeypatch.setattr(eng.radarr, "movie", lambda mid: m)
    monkeypatch.setattr(eng.radarr, "releases", lambda mid: [release("Film 2000 2160p WEB-DL DDP5 1 HDR H 265-A", gb=15),
                                                             release("Film 2000 2160p WEB-DL HDR H 265-B", gb=14)])
    e = eng.Engine()
    e.suggest_one(7)
    fresh = e.suggestions()[0]
    old = e._load_json("suggestions.json", {})
    rating_keys = ("rating", "video_rating", "audio_rating", "badge", "video_tier", "audio_tier", "video_bounds", "audio_bounds")
    for x in [old["7"]["before"], *old["7"]["options"]]:  # as 1.7 kept them
        for k in rating_keys:
            x.pop(k, None)
    e._save_json("suggestions.json", old)
    again = e.suggestions()[0]
    for was, now in zip([fresh["before"], *fresh["options"]], [again["before"], *again["options"]], strict=True):
        assert {k: now[k] for k in rating_keys} == {k: was[k] for k in rating_keys}
    assert again["options"][1]["audio_rating"] is None  # audio the title doesn't name stays unrated
