"""The quality rules: how a film is grouped, what a replacement must be, and the checks on a downloaded file.

Sizes are MB per minute of runtime, so long films get proportionally more room. CAP mirrors Radarr's quality
definitions (set 2026-11-03); the rest are Refetcharr's own. All of them can be changed in Settings.
"""

import functools
import json
import re
import subprocess

from . import quality

GROUPS = {
    "remux": "1080p remuxes",
    "big4k": "4K over the cap",
    "remux4k": "4K remuxes",
    "big1080": "1080p over the cap",
    "tiny": "Tiny x265",  # these need a bigger file, not a smaller one (off unless switched on)
    "lowq": "Low quality",  # the picture rates LOW for its resolution: these need a better file, of any size
}
SHRINKABLE = ("remux", "big4k", "remux4k", "big1080")
GROWABLE = ("tiny",)
UPGRADE = ("lowq",)  # judged on getting better, not on size (and films queued again by hand: mode="upgrade")
PREFER = ("high", "size")  # rank by: the smallest HIGH release (else the best MEDIUM), or the size nearest Preferred
GROW_MIN = 1.1  # a replacement for a tiny file must be at least this much bigger to be worth the download

DV = re.compile(r"\b(dv|dovi|dolby[ ._-]?vision)\b", re.I)
HDR = re.compile(r"\bhdr(10)?(\+|plus)?\b", re.I)
ENGLISH = ("eng", "en", "english")
# Radarr's objections that a smaller file of the same quality always gets: the whole point is to accept these
IGNORABLE = re.compile(r"upgrade|cutoff|existing file|not an improvement", re.I)

# What a release title can say about its codecs (id: label, pattern). Titles come in every spelling: "H 265", "x265", "HEVC",
# "DD 5 1", "DDP5.1", "E-AC-3". Anything else a person adds to their list is matched as a word in the title.
VIDEO_CODECS = {
    "av1": ("AV1", r"av1"),
    "x265": ("x265 / HEVC", r"[xh][ ._-]?265|hevc"),
    "x264": ("x264 / AVC", r"[xh][ ._-]?264|avc"),
    "vp9": ("VP9", r"vp9"),
    "vc1": ("VC-1", r"vc[ ._-]?1"),
    "mpeg2": ("MPEG-2", r"mpeg[ ._-]?2"),
    "xvid": ("XviD / DivX", r"xvid|divx"),
}
AUDIO_CODECS = {
    "atmos": ("Dolby Atmos", r"atmos"),
    "truehd": ("TrueHD", r"true[ ._-]?hd"),
    "dtsx": ("DTS:X", r"dts[ ._:-]?x(?!26[45])"),  # not the x of "DTS x264"
    "dtshd": ("DTS-HD", r"dts[ ._-]?(?:hd|ma)"),
    "dts": ("DTS", r"dts(?![ ._:-]?(?:hd|ma|x(?![a-z]|26[45])))"),
    "eac3": ("DD+ / E-AC-3", r"e[ ._-]?ac[ ._-]?3|ddp|dd\+|dd[ ._-]?plus"),
    "ac3": ("DD / AC-3", r"(?<!e[ ._-])ac[ ._-]?3|dd(?=[ ._-]?\d)"),
    "flac": ("FLAC", r"flac"),
    "lpcm": ("LPCM", r"l?pcm"),
    "aac": ("AAC", r"aac"),
    "opus": ("Opus", r"opus"),
}


def catalog() -> dict:
    """The codecs the settings page offers."""
    return {k: [{"id": i, "label": v[0]} for i, v in c.items()] for k, c in (("video", VIDEO_CODECS), ("audio", AUDIO_CODECS))}


@functools.lru_cache(maxsize=256)
def _codec_re(name: str, kind: str) -> re.Pattern:
    known = (VIDEO_CODECS if kind == "video" else AUDIO_CODECS).get(name.lower())
    body = known[1] if known else r"[ ._-]?".join(re.escape(w) for w in re.split(r"[ ._-]+", name.strip()) if w)
    # digits may follow ("DDP5.1", "AAC2.0"), letters may not; nothing may lead in ("EAC3" isn't AC3)
    return re.compile(rf"(?<![a-z0-9])(?:{body})(?![a-z])", re.I)


def codec_rank(title: str, prefs: list, kind: str) -> int:
    """Where a release's codec sits in a preference list: the position of the first entry its title names, or one past
    the end when it names none. An empty list ranks everything equal, so it changes nothing."""
    return next((i for i, name in enumerate(prefs) if _codec_re(name, kind).search(title)), len(prefs))


def minutes(m: dict) -> float:
    """Runtime from the file itself, else Radarr's listed runtime."""
    rt = ((m.get("movieFile") or {}).get("mediaInfo") or {}).get("runTime") or ""
    if re.match(r"^\d+:\d+:\d+", rt):
        h, mi, s = rt.split(":")[:3]
        return int(h) * 60 + int(mi) + int(s.split(".")[0]) / 60
    return m.get("runtime") or 0


def resolution(m: dict) -> int:
    return ((m.get("movieFile") or {}).get("quality") or {}).get("quality", {}).get("resolution") or 0


def is_x265(m: dict) -> bool:
    codec = (((m.get("movieFile") or {}).get("mediaInfo") or {}).get("videoCodec") or "").lower()
    return codec in ("x265", "hevc", "h265")


def mb_per_min(m: dict) -> float:
    mins = minutes(m)
    return m["movieFile"]["size"] / 1e6 / mins if mins else 0


def group(m: dict, r: dict, q: dict | None = None, cur_file: dict | None = None) -> str | None:
    """Which problem group a film's file is in (None: it's fine). `cur_file`: current(m), when it's already known."""
    f = m.get("movieFile")
    if not f or minutes(m) < r["min_runtime"]:
        return None
    q = f["quality"]["quality"]
    res, mbm = q.get("resolution") or 0, mb_per_min(m)
    if "Remux" in q["name"]:
        return "remux4k" if res >= 2160 else "remux"
    if res >= 2160 and mbm > r["cap_2160"]:
        return "big4k"
    if res == 1080 and mbm > r["cap_1080"]:
        return "big1080"
    if is_x265(m) and ((res >= 2160 and mbm < r["tiny_2160"]) or (res == 1080 and mbm < r["tiny_1080"])):
        return "tiny"
    if res >= 700 and (cur_file or current(m, q))["video_tier"] == "LOW":
        return "lowq"
    return None


def preferred(res: int, r: dict) -> float:
    return r["preferred_2160"] if res >= 2160 else r["preferred_1080"]


def saving(m: dict, r: dict) -> float:
    """Bytes freed if the file were replaced at the preferred size for its resolution (at least 1080p)."""
    return m["movieFile"]["size"] - preferred(max(resolution(m), 1080), r) * minutes(m) * 1e6


def file_group(m: dict) -> str:
    """The release group of the film's current file (Radarr reads it from the file name)."""
    return ((m.get("movieFile") or {}).get("releaseGroup") or "").strip()


def release_group(rel: dict) -> str:
    """A release's group: Radarr's parse, else what follows the last dash of the title ("...x264-nikt0")."""
    g = (rel.get("releaseGroup") or "").strip()
    if g:
        return g
    m = re.search(r"-([A-Za-z0-9]+)\W*$", rel.get("title") or "")
    return m.group(1) if m else ""


def _named(name: str, names: list) -> bool:
    return bool(name) and name.lower() in {n.strip().lower() for n in names}


def ignored(m: dict, ig: dict) -> str:
    """Why a film is left alone ("" when it isn't): picked by hand, or its file is from a group on the list."""
    if m.get("id") in ig.get("films", []):
        return "ignored by hand"
    g = file_group(m)
    return f"file from {g}" if _named(g, ig.get("file_groups", [])) else ""


def has_english(m: dict) -> bool:
    langs = (((m.get("movieFile") or {}).get("mediaInfo") or {}).get("audioLanguages") or "").lower()
    return any(x in ENGLISH for x in re.split(r"[/,; ]+", langs))


def current(m: dict, q: dict | None = None) -> dict:
    """The film's file as quality.describe_file sees it (HDR, audio, picture bitrate, badge)."""
    return quality.describe_file(m.get("movieFile") or {}, minutes(m), q)


def judge(m: dict, rel: dict, r: dict, never: list | None = None, q: dict | None = None, cur_file: dict | None = None,
          mode: str = "", need: dict | None = None) -> list[str]:
    """Why a release can't replace the film's file (empty: it qualifies). A tiny file wants a bigger replacement (up to
    the maximum size); a low-quality file, or a film queued again to be put right (mode "upgrade"), wants a better one
    of any size up to the maximum; every other film wants a smaller one. `never`: release groups never to download from.
    `q`: the quality settings; `cur_file`: current(m), passed in when judging many releases for one film; `need`: what
    must be kept or won back (HDR, surround...) beyond what the current file has."""
    cur, cur_res, mins = m["movieFile"]["size"], resolution(m), minutes(m) or 1
    res = rel["quality"]["quality"].get("resolution") or 0
    mbm = rel["size"] / 1e6 / mins
    cur_file = cur_file or current(m, q)
    g = group(m, r, q, cur_file)
    upgrade = mode == "upgrade" or g in UPGRADE
    grow = g in GROWABLE and not upgrade
    why = []
    norm = lambda s: re.sub(r"[^a-z0-9]", "", re.sub(r"\.(mkv|mp4|m4v|avi)$", "", (s or "").lower()))
    have = {norm(m["movieFile"].get("sceneName")), norm((m["movieFile"].get("relativePath") or "").split("/")[-1])} - {""}
    if norm(rel.get("title")) in have:
        why.append("the same release as the current file")
    if never and _named(release_group(rel), never):
        why.append(f"from {release_group(rel)} (never download from it)")
    if rel.get("protocol") != "torrent" and not r["allow_usenet"]:
        why.append("usenet (not allowed in Replacement settings)")
    hard = [x if isinstance(x, str) else x.get("reason", "") for x in rel.get("rejections", [])]
    hard = [x for x in hard if not IGNORABLE.search(x)]
    if hard:
        why.append(hard[0][:80])
    if res < cur_res and not r["allow_lower_res"]:
        why.append(f"lower resolution ({rel['quality']['quality']['name']})")
    if (rel.get("seeders") or 0) < r["min_seeders"]:
        why.append(f"{rel.get('seeders') or 0} seeders")
    if upgrade:
        cap = r["cap_2160"] if res >= 2160 else r["cap_1080"]
        if mbm > cap:
            why.append(f"{rel['size'] / 1e9:.1f} GB over the maximum ({mbm:.0f} MB/min)")
    elif grow:
        cap = r["cap_2160"] if res >= 2160 else r["cap_1080"]
        if rel["size"] < cur * GROW_MIN:
            why.append(f"{rel['size'] / 1e9:.1f} GB not bigger than the current {cur / 1e9:.1f} GB")
        elif mbm > cap:
            why.append(f"{rel['size'] / 1e9:.1f} GB over the maximum ({mbm:.0f} MB/min)")
    elif rel["size"] > cur * r["max_ratio"]:
        why.append(f"{rel['size'] / 1e9:.1f} GB not small enough")
    floor = r["min_2160"] if res >= 2160 else r["min_1080"]
    if mbm < floor:
        why.append(f"{rel['size'] / 1e9:.1f} GB too small to look good ({mbm:.0f} MB/min)")
    if (rel.get("customFormatScore") or 0) < 0 and r["reject_negative_score"]:
        why.append(f"format score {rel.get('customFormatScore')}")
    if r["keep_english"] and has_english(m) and not any((x.get("name") or "").lower() == "english" for x in rel.get("languages") or []):
        why.append("no English audio listed")
    if r["reject_dv_no_hdr10"] and DV.search(rel["title"]) and not HDR.search(rel["title"]):
        why.append("Dolby Vision without HDR10")
    for kind, label in (("video", "video"), ("audio", "audio")):
        prefs = r[f"{kind}_codecs"]
        if r[f"{kind}_strict"] and prefs and codec_rank(rel["title"], prefs, kind) == len(prefs):
            why.append(f"{label} codec isn't on your list")
    why += keeps(cur_file, quality.describe_release(rel, mins, q), r, upgrade, need)
    return why


TIER_RANK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "": -1}


def keeps(before: dict, after: dict, r: dict, upgrade: bool = False, need: dict | None = None) -> list[str]:
    """What a release would lose that the settings say to keep: HDR, surround sound, Atmos, lossless audio, and a
    picture good enough for its resolution (in x265 terms, with the audio's share taken off the size). An upgrade must
    also be better than the current file: a higher picture rating, or HDR or surround sound won back."""
    need = need or {}
    why = []
    if need.get("hdr") and not before.get("hdr") and not after.get("hdr"):
        why.append("not HDR (the original was, and that's what's being put right)")
    if need.get("surround") and before.get("channels", 0) < 5 and 0 < after.get("channels", 0) < 5:
        why.append(f"stereo audio ({after['channels']:g}; the original had surround)")
    if upgrade:
        won = (need.get("hdr") and after.get("hdr") and not before.get("hdr")) or \
              (need.get("surround") and after.get("channels", 0) >= 5 and before.get("channels", 0) < 5)
        if not won and TIER_RANK[after.get("video_tier", "")] <= TIER_RANK[before.get("video_tier", "")]:
            why.append(f"no better than the current file (picture {after.get('video_tier') or '?'} against "
                       f"{before.get('video_tier') or '?'}: {after.get('picture_mbps', 0):g} against {before.get('picture_mbps', 0):g} Mbps)")
    if r.get("keep_hdr", True) and before.get("hdr") and not after.get("hdr"):
        why.append(f"would lose HDR (the film is {before['hdr']}; the release doesn't say HDR or DV)")
    if r.get("keep_surround", True) and before.get("channels", 0) >= 5 and 0 < after.get("channels", 0) < 5:
        why.append(f"stereo audio ({after['channels']:g}; the film has {before['channels']:g})")
    if r.get("keep_atmos") and before.get("atmos") and not after.get("atmos"):
        why.append("would lose Atmos")
    if r.get("keep_lossless") and before.get("lossless") and not after.get("lossless"):
        why.append(f"would lose lossless audio ({before.get('audio')})")
    floor = r.get("min_picture", "medium")
    if after.get("video_tier") and not quality.at_least(after["video_tier"], floor):
        why.append(f"picture {after['video_tier']} for {quality.res_label(after['resolution'])} "
                   f"(about {after['picture_mbps']:g} Mbps in x265 terms; your minimum is {floor.upper()})")
    return why


def explain(m: dict, chosen: dict, ok: list, total: int, r: dict, q: dict | None = None) -> list[str]:
    """Why `chosen` won, in the order best() ranks: resolution, codecs, Radarr's format score, size, seeders."""
    mins = minutes(m) or 1
    res = lambda x: x["quality"]["quality"].get("resolution") or 0
    out = [f"{len(ok)} of {total} releases qualified" if total else "no other releases"]
    others = [x for x in ok if x is not chosen]
    if not others:
        return out + ["it was the only one that qualified"]
    if r["prefer_higher_res"] and any(res(x) < res(chosen) for x in others):
        out.append(f"highest resolution ({chosen['quality']['quality']['name']})")
    for kind in ("video", "audio"):
        prefs = r[f"{kind}_codecs"]
        rank = codec_rank(chosen["title"], prefs, kind)
        if prefs and rank < len(prefs) and any(codec_rank(x["title"], prefs, kind) > rank for x in others):
            out.append(f"{kind} codec {prefs[rank]} is #{rank + 1} on your list")
    sc = chosen.get("customFormatScore") or 0
    if any((x.get("customFormatScore") or 0) < sc for x in others):
        out.append(f"Radarr format score {sc}")
    if r.get("prefer", "high") == "high":
        d = quality.describe_release(chosen, mins, q)
        if d["video_tier"] == "HIGH":
            out.append(f"the smallest release rated HIGH ({chosen['size'] / 1e9:.1f} GB, picture {d['picture_mbps']:g} Mbps in x265 terms)")
        else:
            out.append(f"none rated HIGH: the best picture below it ({d['video_tier'] or '?'}, {d['picture_mbps']:g} Mbps in x265 terms)")
    else:
        pref = preferred(res(chosen), r)
        out.append(f"{chosen['size'] / 1e6 / mins:.0f} MB/min against a preferred {pref:g}")
    if any((x.get("seeders") or 0) < (chosen.get("seeders") or 0) for x in others):
        out.append(f"{chosen.get('seeders')} seeders")
    return out


def best(m: dict, releases: list, r: dict, never: list | None = None, q: dict | None = None,
         keep: list | None = None, mode: str = "", need: dict | None = None) -> tuple[dict | None, list[str]]:
    """The best qualifying release, and a note per release that didn't qualify. With prefer "high" (the default): the
    highest resolution, then the best picture rating, then your preferred codecs, then the smallest HIGH release (or,
    with none HIGH, the best MEDIUM picture), then Radarr's format score and seeders. With prefer "size": resolution,
    codecs, format score, the size nearest Preferred, seeders. `keep`, a list, receives the qualifying releases in rank
    order (for explain())."""
    mins = minutes(m) or 1
    ok, notes = [], []
    cur_file = current(m, q)
    for rel in releases:
        why = judge(m, rel, r, never, q, cur_file, mode, need)
        if why:
            notes.append(f"{rel['title'][:70]}: {'; '.join(why)}")
        else:
            ok.append(rel)

    desc = {id(rel): quality.describe_release(rel, mins, q) for rel in ok}

    def rank(rel):
        res = rel["quality"]["quality"].get("resolution") or 0
        codecs = (codec_rank(rel["title"], r["video_codecs"], "video"), codec_rank(rel["title"], r["audio_codecs"], "audio"))
        if r.get("prefer", "high") == "high":
            d = desc[id(rel)]
            tier = TIER_RANK[d["video_tier"]]
            within = rel["size"] / 1e9 if tier == 2 else -d["picture_mbps"]  # HIGH: the smallest; below it: the best picture
            return (-res if r["prefer_higher_res"] else 0, -tier, *codecs, round(within, 1), -(rel.get("customFormatScore") or 0),
                    -(rel.get("seeders") or 0))
        pref = preferred(res, r)
        return (-res if r["prefer_higher_res"] else 0, *codecs, -(rel.get("customFormatScore") or 0),
                round(abs(rel["size"] / 1e6 / mins - pref) / pref, 1), -(rel.get("seeders") or 0))

    ok.sort(key=rank)
    if keep is not None:
        keep.extend(ok)
    return (ok[0] if ok else None), notes


def probe(path: str, ffprobe: str = "ffprobe") -> dict:
    out = subprocess.run([ffprobe, "-v", "error", "-show_entries",
                          "stream=codec_type,codec_name,profile,channels,color_transfer:stream_tags=language,title"
                          ":stream_side_data=dv_profile,dv_bl_signal_compatibility_id",
                          "-of", "json", path], capture_output=True, text=True, timeout=180)
    return json.loads(out.stdout or "{}")


UNTAGGED = ("und", "unk", "")
ENGLISH_TITLE = re.compile(r"\beng(lish)?\b", re.I)


HDR_TRANSFERS = ("smpte2084", "arib-std-b67")  # PQ (HDR10, DV's base) and HLG


def file_problem(path: str, need_english: bool, ffprobe: str = "ffprobe", check_dv: bool = True, untagged_ok: bool = True,
                 need: dict | None = None) -> str | None:
    """Why a downloaded file can't replace the old one (None when it's fine): English audio must survive, and Dolby
    Vision needs an HDR10 base, because the TVs can't play DV-only (profile 5 or an incompatible base layer).
    Many encodes don't tag their audio language ("und"): with `untagged_ok`, a file whose tracks are all untagged
    passes (the release was only chosen because it listed English), but one that names only other languages doesn't.
    A track whose title says English counts too: some releases tag it wrong ("chi" on "English DTS-HD Master Audio")."""
    streams = probe(path, ffprobe).get("streams") or []
    if not streams:
        return "couldn't read the file"
    tracks = [x.get("tags") or {} for x in streams if x.get("codec_type") == "audio"]
    audio = [(t.get("language") or "und").lower() for t in tracks]
    titled = any(ENGLISH_TITLE.search(t.get("title") or "") for t in tracks)
    untagged = bool(audio) and all(a in UNTAGGED for a in audio)
    if need_english and not any(a in ENGLISH for a in audio) and not titled and not (untagged_ok and untagged):
        return f"no English audio track (tracks: {', '.join(audio) or 'none'})"
    for v in (x for x in streams if check_dv and x.get("codec_type") == "video"):
        dv = next((sd for sd in v.get("side_data_list") or [] if "dv_profile" in sd), None)
        if dv and (dv.get("dv_profile") == 5 or dv.get("dv_bl_signal_compatibility_id") == 0 or v.get("color_transfer") != "smpte2084"):
            return f"Dolby Vision without an HDR10 base (profile {dv.get('dv_profile')})"
    return keeps_problem(streams, need or {})


def needs(before: dict, r: dict) -> dict:
    """What the downloaded file must still have, from the film's file at grab time (stored with the film)."""
    return {"hdr": bool(r.get("keep_hdr", True) and before.get("hdr")),
            "surround": bool(r.get("keep_surround", True) and before.get("channels", 0) >= 5),
            "atmos": bool(r.get("keep_atmos") and before.get("atmos")),
            "lossless": bool(r.get("keep_lossless") and before.get("lossless"))}


LOSSLESS_CODECS = ("truehd", "flac", "mlp")


def keeps_problem(streams: list, need: dict) -> str | None:
    """The file checks for what must be kept (see needs())."""
    video = [x for x in streams if x.get("codec_type") == "video"]
    audio = [x for x in streams if x.get("codec_type") == "audio"]
    if need.get("hdr") and video and not any(v.get("color_transfer") in HDR_TRANSFERS for v in video):
        return f"no HDR in the file (colour transfer {video[0].get('color_transfer') or 'unknown'}; the original was HDR)"
    if need.get("surround") and audio and max(int(a.get("channels") or 0) for a in audio) < 6:
        return f"stereo audio ({max(int(a.get('channels') or 0) for a in audio)} channels; the original had surround)"
    if need.get("atmos") and audio and not any("atmos" in (a.get("profile") or "").lower() for a in audio):
        return "no Atmos track (the original had Atmos)"
    if need.get("lossless") and audio and not any((a.get("codec_name") or "") in LOSSLESS_CODECS or (a.get("codec_name") or "").startswith("pcm")
                                                  or re.search(r"DTS-HD MA|DTS:X", a.get("profile") or "") for a in audio):
        return "no lossless audio track (the original had one)"
    return None
