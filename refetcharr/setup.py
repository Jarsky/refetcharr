"""Radarr and Sonarr setup checks: what Refetcharr relies on in each, whether it's in place, and how to fix it.

Refetcharr can grab without any of this (it uses "download anyway"). What these settings do is stop Radarr and Sonarr
undoing the work: without the Remux format and the size limits, either would "upgrade" a freshly replaced film or season
straight back to a remux. Fixes only run when asked (a button in the UI), and each says exactly what it changes.

The checks are the same for both apps, with one difference: Sonarr 5 keeps its size limits on each quality inside a
quality profile, where Radarr and Sonarr 4 keep them on the quality definitions.
"""

import os
import shutil

from . import auth, config, radarr, sonarr

REMUX_REGEX = r"re[-_. ]?mux"
SIZE_QUALITIES = {1080: ("HDTV-1080p", "WEBDL-1080p", "WEBRip-1080p", "Bluray-1080p"),
                  2160: ("HDTV-2160p", "WEBDL-2160p", "WEBRip-2160p", "Bluray-2160p")}

WEBHOOK_PATH = "/api/webhook/radarr"
WEBHOOK_PATHS = {"radarr": WEBHOOK_PATH, "sonarr": "/api/webhook/sonarr"}
MEDIA_SERVERS = {"PlexServer": "Plex", "MediaBrowser": "Emby / Jellyfin", "Emby": "Emby", "Jellyfin": "Jellyfin"}
# what each app's webhook is switched on for: imports, upgrades and deleted files
HOOK_EVENTS = {"radarr": ("onDownload", "onUpgrade", "onMovieFileDelete"), "sonarr": ("onDownload", "onUpgrade", "onEpisodeFileDelete")}
WORDS = {"radarr": {"name": "Radarr", "items": "films", "item": "film", "replaced": "replaced films"},
         "sonarr": {"name": "Sonarr", "items": "series", "item": "season", "replaced": "replaced seasons"}}


def _client(app: str):
    return sonarr if app == "sonarr" else radarr


def _url(app: str) -> str:
    return config.sonarr_url() if app == "sonarr" else config.radarr_url()


def webhook_url(app: str = "radarr") -> str:
    """Where the app sends its import events: Refetcharr's address as the app sees it, with the API key."""
    return f"{config.load()['connections']['refetcharr_url'].rstrip('/')}{WEBHOOK_PATHS[app]}?apikey={auth.ensure_keys()['api_key']}"


def _field(n: dict, name: str):
    return next((f.get("value") for f in n.get("fields") or [] if f.get("name") == name), None)


def _hook(hooks: list, app: str = "radarr") -> dict | None:
    return next((h for h in hooks if h.get("implementation") == "Webhook" and WEBHOOK_PATHS[app] in str(_field(h, "url") or "")), None)


def _check(cid, label, ok, detail, fix=None, level="error", app="radarr"):
    """level: error (Refetcharr's work can be undone or can't run) or warn (worth knowing)."""
    return {"id": cid, "app": app, "label": label, "ok": bool(ok), "detail": detail, "fix": fix if not ok else None, "level": level}


def _profile_sizes(profile: dict):
    """The 1080p and 4K qualities of a Sonarr 5 profile, with their sizes (items can be grouped, one level down)."""
    for it in profile.get("items") or []:
        for q in ([it] if it.get("quality") else it.get("items") or []):
            res = next((r for r, names in SIZE_QUALITIES.items() if (q.get("quality") or {}).get("name") in names), None)
            if res:
                yield res, q


def _sizes_in_profiles(defs: list) -> bool:
    """Sonarr 5: the definitions carry no sizes, the profiles do."""
    return not any("maxSize" in d for d in defs)


def checks(movies: list | None = None, app: str = "radarr") -> list:
    """`movies`: the app's items (films, or TV seasons), to know which quality profiles are in use."""
    s = config.load()
    r = s["tv"]["rules"] if app == "sonarr" else s["rules"]
    cli, w = _client(app), WORDS[app]
    name = w["name"]
    out = []
    c = lambda *a, **k: _check(*a, app=app, **k)
    try:
        st = cli.call("GET", "/system/status")
        out.append(c("connection", f"{name} connection", True, f"{name} {st.get('version')} at {_url(app)}"))
    except radarr.RadarrError as e:
        return [c("connection", f"{name} connection", False, str(e))]

    clients = cli.call("GET", "/downloadclient")
    qb = [x for x in clients if x.get("implementation") == "QBittorrent" and x.get("enable")]
    out.append(c("client", f"qBittorrent is a download client in {name}", qb,
                 f"{qb[0]['name']} (enabled)" if qb else f"Add qBittorrent in {name} → Settings → Download Clients and enable it."))
    dc = cli.call("GET", "/config/downloadclient")
    out.append(c("cdh", "Completed download handling is on", dc.get("enableCompletedDownloadHandling"),
                 "On" if dc.get("enableCompletedDownloadHandling") else f"Off: {name} won't track finished downloads.",
                 f"Turn on completed download handling in {name}"))
    mm = cli.call("GET", "/config/mediamanagement")
    out.append(c("recycle", "Replaced files are deleted (no recycle bin)", not mm.get("recycleBin"),
                 "Replaced files free their space straight away." if not mm.get("recycleBin")
                 else f"{name} moves replaced files to {mm['recycleBin']}, so no space is freed until it's emptied.",
                 f"Clear {name}'s recycle bin setting (replaced files are then deleted for good)", level="warn"))

    cfs = cli.call("GET", "/customformat")
    remux = next((x for x in cfs if x["name"].lower() == "remux"), None)
    out.append(c("remux_cf", "A 'Remux' custom format exists", remux,
                 "Matches remux release names." if remux else f"Without it {name} can't tell remuxes apart to reject them.",
                 f"Create a 'Remux' custom format (release title matches {REMUX_REGEX})"))

    profiles = cli.call("GET", "/qualityprofile")
    used = {m.get("qualityProfileId") for m in (movies if movies is not None else (radarr.movies() if app == "radarr" else []))}
    bad = []
    for p in profiles:
        if p["id"] not in used:
            continue
        score = next((f["score"] for f in p.get("formatItems", []) if remux and f.get("format") == remux["id"]), None)
        if score is None or score > -10000 or (p.get("minFormatScore") or 0) < 0:
            bad.append(p["name"])
    names = ", ".join(p["name"] for p in profiles if p["id"] in used)
    out.append(c("profiles", "Profiles in use reject remuxes", remux and not bad,
                 f"Remux scored -10000 with a minimum score of 0 in: {names}." if remux and not bad
                 else f"{name} could upgrade {w['replaced']} back to remuxes in: {', '.join(bad) or names}.",
                 f"Score 'Remux' -10000 and set the minimum format score to 0 in the profiles your {w['items']} use"))

    defs = cli.call("GET", "/qualitydefinition")
    over = []
    if _sizes_in_profiles(defs):
        for p in profiles:
            if p["id"] in used:
                for res, q in _profile_sizes(p):
                    cap = r["cap_2160"] if res == 2160 else r["cap_1080"]
                    if q.get("allowed") and (not q.get("maxSize") or q["maxSize"] > cap):
                        over.append(f"{p['name']}: {q['quality']['name']} ({q.get('maxSize') or 'no limit'})")
    else:
        for res, names_ in SIZE_QUALITIES.items():
            cap = r["cap_2160"] if res == 2160 else r["cap_1080"]
            for d in defs:
                if d["title"] in names_ and (d.get("maxSize") is None or d["maxSize"] == 0 or d["maxSize"] > cap):
                    over.append(f"{d['title']} ({d.get('maxSize') or 'no limit'})")
    out.append(c("sizes", f"{name}'s size limits match Refetcharr's", not over,
                 f"1080p up to {r['cap_1080']} and 4K up to {r['cap_2160']} MB per minute." if not over
                 else f"Larger than Refetcharr allows, so {name} could grab files Refetcharr calls oversized: " + ", ".join(over[:4]),
                 f"Set {name}'s 1080p sizes to {r['tiny_1080']}/{r['preferred_1080']}/{r['cap_1080']} and 4K to "
                 f"{r['tiny_2160']}/{r['preferred_2160']}/{r['cap_2160']} MB per minute (min/preferred/max)"))

    notes = cli.call("GET", "/notification")
    servers = [n for n in notes if n.get("implementation") in MEDIA_SERVERS and n.get("onUpgrade")]
    out.append(c("mediaserver", "A media server hears about replaced files", servers,
                 f"{name} tells " + ", ".join(f"{n['name']} ({MEDIA_SERVERS[n['implementation']]})" for n in servers)
                 + " on import and upgrade, so it picks up each swap." if servers
                 else f"Add Plex, Emby or Jellyfin in {name} → Settings → Connect with On Upgrade (and Update Library) on, "
                      "or the server keeps showing the old file until its next scan.", level="warn"))
    hook = _hook(notes, app)
    hook_ok = bool(hook and _field(hook, "url") == webhook_url(app) and hook.get("onDownload") and hook.get("onUpgrade"))
    out.append(c("webhook", f"{name} tells Refetcharr about new files", hook_ok,
                 "A webhook sends each import, so new remuxes are flagged within a minute instead of at the weekly scan."
                 if hook_ok else ("The webhook's address or API key is out of date." if hook
                                  else "Without it, new files are only looked at by the weekly scan."),
                 f"Add a 'Refetcharr' webhook in {name} → Connect (on import and upgrade) to {s['connections']['refetcharr_url']}",
                 level="warn"))

    sample = [m["movieFile"]["path"] for m in (movies or [])[:200] if m.get("movieFile")][:5]
    readable = [p for p in sample if os.path.exists(p)]
    out.append(c("media", f"Refetcharr can read the {'TV' if app == 'sonarr' else 'films'}", not sample or readable,
                 f"It sees files at the same paths as {name}." if readable or not sample
                 else f"Can't see {sample[0]}: mount the folder {name} uses as /data at the same path (MEDIA_PATH in .env).",
                 level="error"))
    out.append(c("ffprobe", "ffprobe is available", shutil.which(config.FFPROBE),
                 "Downloads are checked before they replace anything." if shutil.which(config.FFPROBE) else "Install ffmpeg in the container."))
    return out


def fix(ids: list, movies: list | None = None, app: str = "radarr") -> list:
    """Apply the fixes asked for; returns what was changed."""
    s = config.load()
    r = s["tv"]["rules"] if app == "sonarr" else s["rules"]
    cli, name = _client(app), WORDS[app]["name"]
    done = []
    if "cdh" in ids:
        dc = cli.call("GET", "/config/downloadclient")
        dc["enableCompletedDownloadHandling"] = True
        cli.call("PUT", f"/config/downloadclient/{dc['id']}", dc)
        done.append(f"Turned on completed download handling in {name}")
    if "recycle" in ids:
        mm = cli.call("GET", "/config/mediamanagement")
        mm["recycleBin"] = ""
        cli.call("PUT", f"/config/mediamanagement/{mm['id']}", mm)
        done.append(f"Cleared {name}'s recycle bin setting")
    cfs = cli.call("GET", "/customformat")
    remux = next((c for c in cfs if c["name"].lower() == "remux"), None)
    if ("remux_cf" in ids or "profiles" in ids) and not remux:
        remux = cli.call("POST", "/customformat", {"name": "Remux", "includeCustomFormatWhenRenaming": False, "specifications": [
            {"name": "Remux", "implementation": "ReleaseTitleSpecification", "negate": False, "required": True,
             "fields": [{"name": "value", "value": REMUX_REGEX}]}]})
        done.append(f"Created the 'Remux' custom format in {name}")
    used = {m.get("qualityProfileId") for m in (movies if movies is not None else (radarr.movies() if app == "radarr" else []))}
    if "profiles" in ids:
        for p in cli.call("GET", "/qualityprofile"):
            if p["id"] not in used:
                continue
            items = [f for f in p.get("formatItems", []) if f.get("format") == remux["id"]]
            if items:
                items[0]["score"] = min(items[0]["score"], -10000)
            else:
                p.setdefault("formatItems", []).append({"format": remux["id"], "name": "Remux", "score": -10000})
            p["minFormatScore"] = max(p.get("minFormatScore") or 0, 0)
            cli.call("PUT", f"/qualityprofile/{p['id']}", p)
            done.append(f"Profile '{p['name']}': Remux -10000, minimum score {p['minFormatScore']}")
    if "sizes" in ids:
        defs = cli.call("GET", "/qualitydefinition")
        want = lambda res: (r[f"tiny_{res}"], r[f"preferred_{res}"], r[f"cap_{res}"])
        if _sizes_in_profiles(defs):
            for p in cli.call("GET", "/qualityprofile"):
                if p["id"] not in used:
                    continue
                for res, q in _profile_sizes(p):
                    q["minSize"], q["preferredSize"], q["maxSize"] = want("2160" if res == 2160 else "1080")
                cli.call("PUT", f"/qualityprofile/{p['id']}", p)
                done.append(f"Profile '{p['name']}': 1080p and 4K size limits set")
        else:
            for d in defs:
                for res, names in SIZE_QUALITIES.items():
                    if d["title"] in names:
                        d["minSize"], d["preferredSize"], d["maxSize"] = want("2160" if res == 2160 else "1080")
            cli.call("PUT", "/qualitydefinition/update", defs)
            done.append(f"Set {name}'s 1080p and 4K size limits")
    if "webhook" in ids:
        hook = _hook(cli.call("GET", "/notification"), app)
        if hook is None:
            hook = next(x for x in cli.call("GET", "/notification/schema") if x.get("implementation") == "Webhook")
            hook.update(name="Refetcharr", tags=[])
        for k in list(hook):
            if k.startswith("on") and isinstance(hook[k], bool):
                hook[k] = k in HOOK_EVENTS[app]
        for f in hook.get("fields") or []:
            if f.get("name") == "url":
                f["value"] = webhook_url(app)
            elif f.get("name") == "method":
                f["value"] = 1  # POST
        # the app sends a test event on save, so Refetcharr must be reachable at that address
        cli.call("PUT", f"/notification/{hook['id']}", hook) if hook.get("id") else cli.call("POST", "/notification", hook)
        done.append(f"{name} webhook 'Refetcharr' sends imports to {s['connections']['refetcharr_url']}")
    return done
