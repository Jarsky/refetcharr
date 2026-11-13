"""How good a file or a release is: picture bitrate in x265 terms, audio, HDR, and a LOW / MEDIUM / HIGH badge.

Radarr reports the video bitrate as 0 for most MKVs, so the picture's share is worked out: the file's bitrate minus its
audio tracks (Radarr gives lossy ones; lossless ones are estimated from their format and channels). A release only has
its title and size, so its audio is estimated from the format the title names.

Codecs differ in how much bitrate they need for the same picture, so bitrates are compared in x265 terms: an x264
stream at 12 Mbps looks about like x265 at 8 (CODEC_FACTOR, editable in Settings → Quality).
"""

import re

TIERS = ("LOW", "MEDIUM", "HIGH")
MIN_PICTURE = ("off", "low", "medium", "high")  # the setting: the least picture tier a replacement may have

# how much more bitrate a codec needs than x265 for the same picture (x265 = 1)
CODEC_FACTOR = {"av1": 0.75, "x265": 1.0, "vp9": 0.9, "x264": 1.5, "vc1": 1.8, "mpeg2": 2.5, "xvid": 2.0}

# audio kbps when nothing says: by format, for 2.0 / 5.1 / 7.1 (lossless ones vary a lot; these are typical)
AUDIO_KBPS = {
    "truehd": (1800, 3500, 4500), "dtshd": (1600, 3400, 4300), "flac": (900, 2300, 3000), "lpcm": (1500, 4600, 6100),
    "dts": (768, 1509, 1509), "eac3": (224, 640, 768), "ac3": (192, 448, 640), "aac": (160, 384, 512), "opus": (128, 256, 384),
    "mp3": (192, 192, 192),
}
LOSSLESS = ("truehd", "dtshd", "flac", "lpcm")

DEFAULTS = {
    # picture, in Mbps x265-equivalent: [top of LOW, top of MEDIUM] per resolution (above that is HIGH)
    # calibrated on a 4,900-film library (2026-11): typical good encodes are 5-6 Mbps at 1080p and 15-18 at 4K
    "video": {"sd": [1.0, 2.0], "720": [2.0, 3.5], "1080": [3.5, 6.0], "2160": [9.0, 15.0]},
    "audio": [256, 640],  # kbps: [top of LOW, top of MEDIUM]; lossless is always HIGH
    "video_weight": 70,  # % of the overall badge that comes from the picture (the rest from the audio)
    "codec_factor": dict(CODEC_FACTOR),
}

_HDR = re.compile(r"\b(hdr(10)?(\+|plus)?|hlg|dv|dovi|dolby[ ._-]?vision)\b", re.I)
_SDR = re.compile(r"\bsdr\b", re.I)
_ATMOS = re.compile(r"atmos|\bddpa\b", re.I)  # "DDPA 5 1" is how some groups write DD+ Atmos
_CHANNELS = re.compile(r"(?<![\d.])([1-9])[ ._]([01])(?!\d)")  # "5.1", "DDP5 1", "AAC2.0" (not "x265 10bit")
_VIDEO = {"av1": r"av1", "x265": r"[xh][ ._-]?265|hevc", "x264": r"[xh][ ._-]?264|avc", "vp9": r"vp9", "vc1": r"vc[ ._-]?1",
          "mpeg2": r"mpeg[ ._-]?2", "xvid": r"xvid|divx"}
_AUDIO = [  # first match wins: the specific before the general
    ("truehd", r"true[ ._-]?hd"), ("dtshd", r"dts[ ._-]?(?:hd|ma)|dts[ ._:-]?x(?!26[45])"), ("flac", r"flac"), ("lpcm", r"l?pcm"),
    ("eac3", r"e[ ._-]?ac[ ._-]?3|ddpa?|dd\+|dd[ ._-]?plus"), ("ac3", r"(?<!e[ ._-])ac[ ._-]?3|\bdd(?=[ ._-]?\d)"),
    ("dts", r"dts"), ("aac", r"aac"), ("opus", r"opus"), ("mp3", r"mp3"),
]


def settings(q: dict | None) -> dict:
    """The quality settings with defaults filled in (old settings files have none)."""
    q = q or {}
    return {"video": {**DEFAULTS["video"], **(q.get("video") or {})}, "audio": q.get("audio") or DEFAULTS["audio"],
            "video_weight": q.get("video_weight", DEFAULTS["video_weight"]),
            "codec_factor": {**CODEC_FACTOR, **(q.get("codec_factor") or {})}}


def res_label(res: int) -> str:
    return {"2160": "4K", "1080": "1080p", "720": "720p", "sd": "SD"}[res_key(res)]


def res_key(res: int) -> str:
    return "2160" if res >= 2000 else "1080" if res >= 1000 else "720" if res >= 700 else "sd"


def video_codec(name: str) -> str:
    """'x265', 'HEVC', 'h264', 'AVC', 'VC-1' ... -> a CODEC_FACTOR key ('' when unknown)."""
    n = (name or "").lower()
    return next((k for k, pat in _VIDEO.items() if re.search(rf"(?<![a-z0-9])(?:{pat})(?![a-z])", n)), "")


def audio_codec(name: str) -> str:
    """A codec name or a release title -> an AUDIO_KBPS key ('' when it names none)."""
    n = (name or "").lower()
    return next((k for k, pat in _AUDIO if re.search(rf"(?<![a-z0-9])(?:{pat})(?![a-z])", n)), "")


def audio_kbps(codec: str, channels: float, bitrate: float = 0) -> float:
    if bitrate and bitrate > 0:
        return bitrate / 1000
    table = AUDIO_KBPS.get(codec)
    if not table:
        return 0
    return table[0] if channels < 5 else table[1] if channels < 7 else table[2]


def title_channels(title: str) -> float:
    """The most channels a release title names (7.1 -> 7.1), 0 when it names none."""
    found = [int(a) + int(b) / 10 for a, b in _CHANNELS.findall(title or "")]
    return max(found, default=0)


def tier(value: float, bounds: list) -> int:
    """0 LOW, 1 MEDIUM, 2 HIGH."""
    return 0 if value < bounds[0] else 1 if value < bounds[1] else 2


# Ratings out of 5.0 (1.8.0, user 2026-11-13): the thresholds above land on fixed marks, so a rating reads the same way
# for every resolution and for audio: LOW is under 2.0, MEDIUM 2.0 to 3.5, HIGH 3.5 and up. Within HIGH a rating climbs
# to 5.0 at HIGH_TOP times the HIGH threshold. Lossless audio is 5.0; lossy audio stops at LOSSY_TOP. The overall rating
# weighs picture and audio by video_weight, and it's what picks a release (rules.best) and gives the badge.
MEDIUM_MARK, HIGH_MARK, TOP = 2.0, 3.5, 5.0
HIGH_TOP = 1.6
LOSSY_TOP = 4.5


def _floor1(x: float) -> float:
    """Down to one decimal, so a value just under a threshold never shows as its mark (1.99 is 1.9, not 2.0)."""
    return int(x * 10 + 1e-9) / 10


def rate(value: float, bounds: list, top: float = TOP) -> float | None:
    """A bitrate against its [top of LOW, top of MEDIUM] thresholds, out of 5.0 (None when unknown)."""
    if not value or value <= 0:
        return None
    lo, hi = bounds
    if value < lo:
        x = MEDIUM_MARK * value / lo
    elif value < hi:
        x = MEDIUM_MARK + (HIGH_MARK - MEDIUM_MARK) * (value - lo) / (hi - lo)
    else:
        x = HIGH_MARK + (TOP - HIGH_MARK) * min(1.0, (value - hi) / (hi * (HIGH_TOP - 1)))
    return _floor1(min(x, top))


def tier_of(rating: float | None) -> str:
    if rating is None:
        return ""
    return "HIGH" if rating >= HIGH_MARK else "MEDIUM" if rating >= MEDIUM_MARK else "LOW"


def overall(video: float | None, audio: float | None, weight: float) -> float | None:
    if video is None:
        return None
    if audio is None:
        return video
    return _floor1((video * weight + audio * (100 - weight)) / 100)


def badge(video_tier: int | None, audio_tier: int | None, weight: float) -> str:
    """The overall badge from tiers alone (kept for callers without bitrates; describe_* use the ratings)."""
    if video_tier is None:
        return ""
    if audio_tier is None:
        return TIERS[video_tier]
    score = (video_tier * weight + audio_tier * (100 - weight)) / 100
    return TIERS[min(2, int(score + 0.5))]


def _picture(total_mbps: float, audio: float, codec: str, q: dict) -> float:
    """Picture Mbps in x265 terms (0 when unknown)."""
    video = max(total_mbps - audio / 1000, 0)
    return round(video / q["codec_factor"].get(codec or "x264", 1.0), 2) if video else 0


def _tiers(res: int, picture: float, audio_kbps_: float, lossless: bool, q: dict, audio_known: bool = True) -> dict:
    """Ratings out of 5.0 for the picture, the audio and both (weighted), their tiers, and the thresholds they were
    measured against (the UI shows "HIGH from ..."). Audio a release title doesn't name is unrated (its assumed size
    still comes off the picture's share), so the overall rating is the picture's: a guess earns no credit."""
    vb = q["video"][res_key(res)]
    vr = rate(picture, vb)
    ar = (TOP if lossless else rate(audio_kbps_, q["audio"], LOSSY_TOP)) if audio_known else None
    r = overall(vr, ar, q["video_weight"])
    return {"video_tier": tier_of(vr), "audio_tier": tier_of(ar), "badge": tier_of(r),
            "video_rating": vr, "audio_rating": ar, "rating": r, "video_bounds": list(vb), "audio_bounds": list(q["audio"])}


def rated(d: dict | None, q: dict | None = None) -> dict | None:
    """A record kept from before 1.8 (a suggestion, a decision's file) with its ratings out of 5 filled in from the
    bitrates it kept, so every file and release shows the same rating. Records that already have one are left alone."""
    if not d or d.get("rating") is not None or not d.get("resolution") or not d.get("picture_mbps"):
        return d
    known = bool(d.get("audio") or d.get("lossless")) or not d.get("estimated")  # a file's audio is always known
    return {**d, **_tiers(d["resolution"], d["picture_mbps"], d.get("audio_kbps") or 0, bool(d.get("lossless")), settings(q), audio_known=known)}


def minutes_of(mi: dict, fallback: float = 0) -> float:
    rt = (mi or {}).get("runTime") or ""
    if re.match(r"^\d+:\d+:\d+", rt):
        h, m, s = rt.split(":")[:3]
        return int(h) * 60 + int(m) + float(s) / 60
    if re.match(r"^\d+:\d+(\.\d+)?$", rt):  # Sonarr: "mm:ss" under an hour
        m, s = rt.split(":")
        return int(m) + float(s) / 60
    return fallback


def describe_file(f: dict, runtime_min: float, q: dict | None = None) -> dict:
    """What a file on disk is (Radarr's movieFile): resolution, codecs, HDR, audio, picture bitrate and badge."""
    q = settings(q)
    f = f or {}
    mi = f.get("mediaInfo") or {}
    qual = (f.get("quality") or {}).get("quality") or {}
    res = qual.get("resolution") or 0
    mins = minutes_of(mi, runtime_min) or runtime_min or 0
    vcodec = video_codec(mi.get("videoCodec") or "")
    streams = mi.get("audioStreams") or [{"codec": mi.get("audioCodec") or "", "channels": mi.get("audioChannels") or 0,
                                          "bitrate": mi.get("audioBitrate") or 0}]
    tracks = []
    for st in streams:
        c = audio_codec(st.get("codec") or "")
        tracks.append({"codec": c, "name": st.get("codec") or "", "channels": float(st.get("channels") or 0),
                       "kbps": audio_kbps(c, float(st.get("channels") or 0), st.get("bitrate") or 0),
                       "language": st.get("language") or ""})
    main = max(tracks, key=lambda t: (t["channels"], t["kbps"]), default={"codec": "", "name": "", "channels": 0, "kbps": 0})
    all_audio = sum(t["kbps"] for t in tracks)
    total_mbps = f.get("size", 0) * 8 / 1e6 / (mins * 60) if mins else 0
    vbr = (mi.get("videoBitrate") or 0) / 1e6
    picture = round(vbr / q["codec_factor"].get(vcodec or "x264", 1.0), 2) if vbr else _picture(total_mbps, all_audio, vcodec, q)
    dr = mi.get("videoDynamicRangeType") or mi.get("videoDynamicRange") or ""
    names = " ".join(t["name"] for t in tracks) + " " + (mi.get("audioCodec") or "")
    lossless = main["codec"] in LOSSLESS
    out = {"quality": qual.get("name") or "", "resolution": res, "size": f.get("size") or 0, "runtime_min": round(mins, 1),
           "mb_per_min": round(f.get("size", 0) / 1e6 / mins) if mins else 0, "video_codec": mi.get("videoCodec") or "",
           "codec_key": vcodec, "bit_depth": mi.get("videoBitDepth") or 0, "hdr": dr if dr and dr.upper() != "SDR" else "",
           "picture_mbps": picture, "audio": main["name"], "audio_key": main["codec"], "channels": main["channels"],
           "audio_kbps": round(main["kbps"]), "atmos": bool(_ATMOS.search(names)), "lossless": lossless,
           "audio_tracks": len(tracks), "languages": mi.get("audioLanguages") or "", "release_group": f.get("releaseGroup") or "",
           "name": f.get("sceneName") or (f.get("relativePath") or "").split("/")[-1]}
    out.update(_tiers(res, picture, main["kbps"], lossless, q))
    return out


def describe_release(rel: dict, runtime_min: float, q: dict | None = None) -> dict:
    """What a release would be, from its title and size (estimates: nothing is known for sure until it's downloaded)."""
    q = settings(q)
    title = rel.get("title") or ""
    qual = (rel.get("quality") or {}).get("quality") or {}
    res = qual.get("resolution") or 0
    vcodec = video_codec(title)
    acodec = audio_codec(title)
    ch = title_channels(title)
    kbps = audio_kbps(acodec, ch or 5.1) if acodec else 640  # nothing named: assume the usual DD+ 5.1
    extra = 640 if re.search(r"\b(dual|multi)\b", title, re.I) else 0  # a second language track
    mins = runtime_min or 0
    total_mbps = rel.get("size", 0) * 8 / 1e6 / (mins * 60) if mins else 0
    picture = _picture(total_mbps, kbps + extra, vcodec, q)
    hdr = "" if _SDR.search(title) else ", ".join(sorted({m.group(0).upper().replace("DOVI", "DV").replace(".", " ")
                                                         for m in _HDR.finditer(title)}))
    lossless = acodec in LOSSLESS
    out = {"title": title, "quality": qual.get("name") or "", "resolution": res, "size": rel.get("size") or 0,
           "mb_per_min": round(rel.get("size", 0) / 1e6 / mins) if mins else 0, "video_codec": vcodec, "codec_key": vcodec,
           "hdr": hdr, "picture_mbps": picture, "audio": acodec, "audio_key": acodec, "channels": ch,
           "audio_kbps": round(kbps), "atmos": bool(_ATMOS.search(title)), "lossless": lossless,
           "seeders": rel.get("seeders"), "score": rel.get("customFormatScore"), "indexer": rel.get("indexer") or "",
           "release_group": rel.get("releaseGroup") or "", "languages": ", ".join(x.get("name", "") for x in rel.get("languages") or []),
           "estimated": True}
    out.update(_tiers(res, picture, kbps, lossless, q, audio_known=bool(acodec)))
    return out


def at_least(badge_tier: str, minimum: str) -> bool:
    """Whether a picture tier ('LOW'...) reaches the minimum setting ('off', 'low', 'medium', 'high')."""
    if minimum in ("off", "low", "") or not badge_tier:
        return True
    return TIERS.index(badge_tier) >= TIERS.index(minimum.upper())
