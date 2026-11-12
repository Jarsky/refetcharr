"""Settings, in data/settings.json, edited from the TUI and web UI. Unknown keys are ignored and missing ones fall back
to the defaults, so old files keep working. The Radarr connection can also come from the environment (.env): a value
saved in Settings wins. Secrets (API key, passwords) are never sent back to the UI, only whether they're set."""

import copy
import json
import os
import threading

from . import quality

DATA = os.environ.get("REFETCHARR_DATA", "/data/refetcharr")
ENV_RADARR_URL = os.environ.get("RADARR_URL", "http://radarr:7878").rstrip("/")
ENV_RADARR_API_KEY = os.environ.get("RADARR_API_KEY", "")
FFPROBE = os.environ.get("FFPROBE", "ffprobe")
PORT = int(os.environ.get("PORT", "8787"))
API = os.environ.get("REFETCHARR_API", f"http://127.0.0.1:{PORT}")  # where the TUI finds the server

SECRETS = ("radarr_api_key",)

DEFAULTS = {
    "connections": {
        "radarr_url": "",  # blank: RADARR_URL from .env, else http://radarr:7878
        "radarr_api_key": "",  # blank: RADARR_API_KEY from .env
        "radarr_link": "",  # Radarr's address in your browser, for the film links (blank: the address above)
        "refetcharr_url": "http://refetcharr:8787",  # how Radarr reaches Refetcharr, for its import webhook
    },
    "queue": {
        "auto": False,  # keep the queue topped up by itself (within the limits and window below)
        "paused": False,  # stop starting new searches (downloads already running carry on and are imported)
        "max_active": 5,  # downloads running at once; refilled as each finishes
        "max_per_day": 20,  # films grabbed per day, counting manual ones too (0 = no limit)
        # which kinds of win to go after; "tiny" swaps a too-small x265 encode for a bigger one (it costs space)
        "groups": {"remux": True, "big4k": True, "remux4k": True, "big1080": False, "tiny": False, "lowq": False},
        "window": {"enabled": False, "days": [0, 1, 2, 3, 4, 5, 6], "start": "00:00", "end": "23:59"},  # Mon = 0
    },
    "rules": {
        "min_seeders": 3,  # private trackers rarely have more; a download that stalls gets a warning and frees its slot
        "max_ratio": 0.7,  # a replacement is at most this share of the current file
        "cap_1080": 125, "cap_2160": 210,  # over this a film counts as oversized (Radarr's maximums)
        "preferred_1080": 60, "preferred_2160": 150,  # what a good replacement usually comes in at
        "min_1080": 35, "min_2160": 90,  # a replacement must be at least this to look good
        "tiny_1080": 20, "tiny_2160": 50,  # x265 below this is flagged as too small
        "min_runtime": 20,  # minutes: anything shorter is left alone (extras, shorts)
        # most preferred first, ids from rules.VIDEO_CODECS / AUDIO_CODECS or any word in a release title. Empty: no preference
        "video_codecs": ["x265", "x264", "av1"],
        "audio_codecs": ["atmos", "truehd", "dtsx", "dtshd", "dts", "eac3", "ac3"],
        "video_strict": False, "audio_strict": False,  # reject releases that name nothing on the list, rather than ranking them last
        "prefer_higher_res": True,  # a higher resolution beats everything else when choosing between releases
        "allow_lower_res": False,  # accept a replacement below the film's current resolution
        "allow_usenet": False,  # accept usenet releases (Radarr needs a usenet client)
        "reject_negative_score": True,  # refuse releases Radarr's custom formats score below zero
        "keep_english": True,  # a film with English audio must keep it, checked before the search and on the file
        "untagged_is_english": True,  # audio with no language tag ("und") passes that check, unless another track names a language
        "reject_dv_no_hdr10": True,  # the TVs can't play Dolby Vision without an HDR10 base
        "keep_hdr": True,  # an HDR film's replacement must be HDR too (its title says HDR/DV; the file is checked)
        "keep_surround": True,  # a 5.1 or 7.1 film's replacement mustn't be stereo
        "keep_atmos": True,  # an Atmos film's replacement must have Atmos (DD+ Atmos counts)
        "keep_lossless": False,  # a film with lossless audio (TrueHD, DTS-HD MA, FLAC) must keep it
        "min_picture": "medium",  # the least picture quality for a replacement (Settings → Quality): off, low, medium, high
        # which qualifying release to pick: "high" the smallest rated HIGH (else the best MEDIUM), "small" the smallest
        # that qualifies at all (Max savings), or "size" the size nearest Preferred
        "prefer": "high",
        "audio_before_size": False,  # among HIGH releases, your audio codec order beats size (Max quality)
        "stall_warn_hours": 2,  # warn when a download has made no progress for this long (0 = never)
    },
    "ignore": {
        "file_groups": [],  # leave a film alone when its current file is from one of these release groups (e.g. nikt0)
        "release_groups": [],  # never download a release from one of these groups
        "films": [],  # Radarr movie ids to leave alone
    },
    "timing": {
        "missing_minutes": 15,  # a download must be gone from Radarr's queue this long before it counts as gone
        "import_wait_minutes": 30,  # let Radarr finish an import we asked for before asking again
        "no_video_hours": 2,  # a finished download may have no video file this long (scene releases still unpacking)
        "finish_warn_minutes": 60,  # a fully downloaded film waiting on qBittorrent this long is called out
        "library_minutes": 10,  # how long the library read from Radarr is reused
        "setup_minutes": 60,  # how often Radarr's setup is checked (it badges Settings)
    },
    "scan": {"every_days": 7},  # rescan the library this often (0 = only by hand)
    "quality": copy.deepcopy(quality.DEFAULTS),  # the LOW / MEDIUM / HIGH thresholds (refetcharr.quality)
    # the one sign-in (refetcharr.auth): never sent to the browser except the username
    "auth": {"username": "", "password_hash": "", "secret": "", "api_key": "", "epoch": 0},
}

# Replacement profiles (1.7.0): one click sets these rules; any other value of them shows as Custom. Balanced is the
# defaults. Sizes are MB/min, as in Settings → Replacement; the caps, floors and everything else stay as you set them.
PROFILES = {
    "savings": {
        "label": "Max savings",
        "blurb": "The smallest file that still looks good: MEDIUM picture is enough, Atmos isn't kept, smaller targets.",
        "rules": {"prefer": "small", "min_picture": "medium", "audio_before_size": False, "keep_atmos": False,
                  "keep_lossless": False, "preferred_1080": 45, "preferred_2160": 110, "max_ratio": 0.6},
    },
    "balanced": {
        "label": "Balanced",
        "blurb": "HIGH picture at the smallest size (else the best MEDIUM), keeping Atmos. Your audio order only decides "
                 "between releases of about the same size.",
        "rules": {"prefer": "high", "min_picture": "medium", "audio_before_size": False, "keep_atmos": True,
                  "keep_lossless": False, "preferred_1080": 60, "preferred_2160": 150, "max_ratio": 0.7},
    },
    "quality": {
        "label": "Max quality",
        "blurb": "HIGH picture only, keeping Atmos and lossless audio, with your audio order ahead of size and roomier targets.",
        "rules": {"prefer": "high", "min_picture": "high", "audio_before_size": True, "keep_atmos": True,
                  "keep_lossless": True, "preferred_1080": 80, "preferred_2160": 180, "max_ratio": 0.85},
    },
}


def profile_of(r: dict) -> str:
    """The profile whose rules all match `r`, else "custom"."""
    return next((k for k, p in PROFILES.items() if all(r.get(x) == v for x, v in p["rules"].items())), "custom")


_lock = threading.Lock()


def _path() -> str:
    return os.path.join(DATA, "settings.json")


def _merge(base: dict, over: dict) -> dict:
    """`over` laid on top of `base`, key by key; keys `base` doesn't have are dropped, so a typo can't add settings."""
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if k not in out:
            continue
        out[k] = _merge(out[k], v) if isinstance(out[k], dict) and isinstance(v, dict) else v
    return out


def radarr_url() -> str:
    return (load()["connections"]["radarr_url"] or ENV_RADARR_URL).rstrip("/")


def radarr_key() -> str:
    return load()["connections"]["radarr_api_key"] or ENV_RADARR_API_KEY


def public(values: dict) -> dict:
    """Settings as the UI may see them: secrets blanked, with whether each is set (in Settings or .env)."""
    out = copy.deepcopy(values)
    c = out["connections"]
    out["secrets_set"] = {"radarr_api_key": bool(c["radarr_api_key"] or ENV_RADARR_API_KEY)}
    out["env"] = {"radarr_url": ENV_RADARR_URL, "radarr_api_key": bool(ENV_RADARR_API_KEY)}
    for k in SECRETS:
        c[k] = ""
    out["auth"] = {"username": out["auth"]["username"]}  # the hash, the cookie secret and the API key stay on the server
    return out


def load() -> dict:
    try:
        with open(_path()) as f:
            return _merge(DEFAULTS, json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        return copy.deepcopy(DEFAULTS)


def save(patch: dict) -> dict:
    """Apply a partial update and return the whole settings. A blank secret keeps the saved one (the UI never has
    it); null clears it."""
    patch = copy.deepcopy(patch)
    conn = patch.get("connections") or {}
    for k in SECRETS:
        if k in conn and conn[k] == "":
            del conn[k]
        elif k in conn and conn[k] is None:
            conn[k] = ""
    if isinstance(conn.get("radarr_url"), str):
        conn["radarr_url"] = conn["radarr_url"].strip().rstrip("/")
    with _lock:
        cur = _merge(load(), patch)
        os.makedirs(DATA, exist_ok=True)
        tmp = _path() + ".tmp"
        with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:  # it holds secrets
            json.dump(cur, f, indent=1)
        os.replace(tmp, _path())
        return cur
