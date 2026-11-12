"""Radarr setup checks: what Refetcharr relies on in Radarr, whether it's in place, and how to fix it.

Refetcharr can grab without any of this (it uses Radarr's "download anyway"). What these settings do is stop Radarr
undoing the work: without the Remux format and the size limits, Radarr would "upgrade" a freshly replaced film straight
back to a 30 GB remux. Fixes only run when asked (a button in the UI), and each says exactly what it changes.
"""

import os
import shutil

from . import auth, config, radarr

REMUX_REGEX = r"re[-_. ]?mux"
SIZE_QUALITIES = {1080: ("HDTV-1080p", "WEBDL-1080p", "WEBRip-1080p", "Bluray-1080p"),
                  2160: ("HDTV-2160p", "WEBDL-2160p", "WEBRip-2160p", "Bluray-2160p")}


WEBHOOK_PATH = "/api/webhook/radarr"
MEDIA_SERVERS = {"PlexServer": "Plex", "MediaBrowser": "Emby / Jellyfin", "Emby": "Emby", "Jellyfin": "Jellyfin"}


def webhook_url() -> str:
    """Where Radarr sends its import events: Refetcharr's address as Radarr sees it, with the API key."""
    return f"{config.load()['connections']['refetcharr_url'].rstrip('/')}{WEBHOOK_PATH}?apikey={auth.ensure_keys()['api_key']}"


def _field(n: dict, name: str):
    return next((f.get("value") for f in n.get("fields") or [] if f.get("name") == name), None)


def _hook(hooks: list) -> dict | None:
    return next((h for h in hooks if h.get("implementation") == "Webhook" and WEBHOOK_PATH in str(_field(h, "url") or "")), None)


def _check(cid, label, ok, detail, fix=None, level="error"):
    """level: error (Refetcharr's work can be undone or can't run) or warn (worth knowing)."""
    return {"id": cid, "label": label, "ok": bool(ok), "detail": detail, "fix": fix if not ok else None, "level": level}


def checks(movies: list | None = None) -> list:
    r = config.load()["rules"]
    out = []
    try:
        st = radarr.call("GET", "/system/status")
        out.append(_check("connection", "Radarr connection", True, f"Radarr {st.get('version')} at {config.radarr_url()}"))
    except radarr.RadarrError as e:
        return [_check("connection", "Radarr connection", False, str(e))]

    clients = radarr.call("GET", "/downloadclient")
    qb = [c for c in clients if c.get("implementation") == "QBittorrent" and c.get("enable")]
    out.append(_check("client", "qBittorrent is a download client in Radarr", qb,
                      f"{qb[0]['name']} (enabled)" if qb else "Add qBittorrent in Radarr → Settings → Download Clients and enable it."))
    dc = radarr.call("GET", "/config/downloadclient")
    out.append(_check("cdh", "Completed download handling is on", dc.get("enableCompletedDownloadHandling"),
                      "On" if dc.get("enableCompletedDownloadHandling") else "Off: Radarr won't track finished downloads.",
                      "Turn on completed download handling in Radarr"))
    mm = radarr.call("GET", "/config/mediamanagement")
    out.append(_check("recycle", "Replaced files are deleted (no recycle bin)", not mm.get("recycleBin"),
                      "Replaced files free their space straight away." if not mm.get("recycleBin")
                      else f"Radarr moves replaced files to {mm['recycleBin']}, so no space is freed until it's emptied.",
                      "Clear Radarr's recycle bin setting (replaced files are then deleted for good)", level="warn"))

    cfs = radarr.call("GET", "/customformat")
    remux = next((c for c in cfs if c["name"].lower() == "remux"), None)
    out.append(_check("remux_cf", "A 'Remux' custom format exists", remux,
                      "Matches remux release names." if remux else "Without it Radarr can't tell remuxes apart to reject them.",
                      f"Create a 'Remux' custom format (release title matches {REMUX_REGEX})"))

    profiles = radarr.call("GET", "/qualityprofile")
    used = {m.get("qualityProfileId") for m in (movies if movies is not None else radarr.movies())}
    bad = []
    for p in profiles:
        if p["id"] not in used:
            continue
        score = next((f["score"] for f in p.get("formatItems", []) if remux and f.get("format") == remux["id"]), None)
        if score is None or score > -10000 or (p.get("minFormatScore") or 0) < 0:
            bad.append(p["name"])
    names = ", ".join(p["name"] for p in profiles if p["id"] in used)
    out.append(_check("profiles", "Profiles in use reject remuxes", remux and not bad,
                      f"Remux scored -10000 with a minimum score of 0 in: {names}." if remux and not bad
                      else f"Radarr could upgrade replaced films back to remuxes in: {', '.join(bad) or names}.",
                      "Score 'Remux' -10000 and set the minimum format score to 0 in the profiles your films use"))

    defs = radarr.call("GET", "/qualitydefinition")
    over = []
    for res, names_ in SIZE_QUALITIES.items():
        cap = r["cap_2160"] if res == 2160 else r["cap_1080"]
        for d in defs:
            if d["title"] in names_ and (d.get("maxSize") is None or d["maxSize"] == 0 or d["maxSize"] > cap):
                over.append(f"{d['title']} ({d.get('maxSize') or 'no limit'})")
    out.append(_check("sizes", "Radarr's size limits match Refetcharr's", not over,
                      f"1080p up to {r['cap_1080']} and 4K up to {r['cap_2160']} MB per minute." if not over
                      else "Larger than Refetcharr allows, so Radarr could grab files Refetcharr calls oversized: " + ", ".join(over[:4]),
                      f"Set Radarr's 1080p sizes to {r['tiny_1080']}/{r['preferred_1080']}/{r['cap_1080']} and 4K to "
                      f"{r['tiny_2160']}/{r['preferred_2160']}/{r['cap_2160']} MB per minute (min/preferred/max)"))

    notes = radarr.call("GET", "/notification")
    servers = [n for n in notes if n.get("implementation") in MEDIA_SERVERS and n.get("onUpgrade")]
    out.append(_check("mediaserver", "A media server hears about replaced files", servers,
                      "Radarr tells " + ", ".join(f"{n['name']} ({MEDIA_SERVERS[n['implementation']]})" for n in servers)
                      + " on import and upgrade, so it picks up each swap." if servers
                      else "Add Plex, Emby or Jellyfin in Radarr → Settings → Connect with On Upgrade (and Update Library) on, "
                           "or the server keeps showing the old file until its next scan.", level="warn"))
    hook = _hook(notes)
    hook_ok = bool(hook and _field(hook, "url") == webhook_url() and hook.get("onDownload") and hook.get("onUpgrade"))
    out.append(_check("webhook", "Radarr tells Refetcharr about new files", hook_ok,
                      "A webhook sends each import, so new remuxes are flagged within a minute instead of at the weekly scan."
                      if hook_ok else ("The webhook's address or API key is out of date." if hook
                                       else "Without it, new files are only looked at by the weekly scan."),
                      f"Add a 'Refetcharr' webhook in Radarr → Connect (on import and upgrade) to {config.load()['connections']['refetcharr_url']}",
                      level="warn"))

    sample = [m["movieFile"]["path"] for m in (movies or [])[:200] if m.get("movieFile")][:5]
    readable = [p for p in sample if os.path.exists(p)]
    out.append(_check("media", "Refetcharr can read the films", not sample or readable,
                      "It sees files at the same paths as Radarr." if readable or not sample
                      else f"Can't see {sample[0]}: mount the folder Radarr uses as /data at the same path (MEDIA_PATH in .env).",
                      level="error"))
    out.append(_check("ffprobe", "ffprobe is available", shutil.which(config.FFPROBE),
                      "Downloads are checked before they replace anything." if shutil.which(config.FFPROBE) else "Install ffmpeg in the container."))
    return out


def fix(ids: list, movies: list | None = None) -> list:
    """Apply the fixes asked for; returns what was changed."""
    r = config.load()["rules"]
    done = []
    if "cdh" in ids:
        dc = radarr.call("GET", "/config/downloadclient")
        dc["enableCompletedDownloadHandling"] = True
        radarr.call("PUT", f"/config/downloadclient/{dc['id']}", dc)
        done.append("Turned on completed download handling")
    if "recycle" in ids:
        mm = radarr.call("GET", "/config/mediamanagement")
        mm["recycleBin"] = ""
        radarr.call("PUT", f"/config/mediamanagement/{mm['id']}", mm)
        done.append("Cleared the recycle bin setting")
    cfs = radarr.call("GET", "/customformat")
    remux = next((c for c in cfs if c["name"].lower() == "remux"), None)
    if ("remux_cf" in ids or "profiles" in ids) and not remux:
        remux = radarr.call("POST", "/customformat", {"name": "Remux", "includeCustomFormatWhenRenaming": False, "specifications": [
            {"name": "Remux", "implementation": "ReleaseTitleSpecification", "negate": False, "required": True,
             "fields": [{"name": "value", "value": REMUX_REGEX}]}]})
        done.append("Created the 'Remux' custom format")
    if "profiles" in ids:
        used = {m.get("qualityProfileId") for m in (movies if movies is not None else radarr.movies())}
        for p in radarr.call("GET", "/qualityprofile"):
            if p["id"] not in used:
                continue
            items = [f for f in p.get("formatItems", []) if f.get("format") == remux["id"]]
            if items:
                items[0]["score"] = min(items[0]["score"], -10000)
            else:
                p.setdefault("formatItems", []).append({"format": remux["id"], "name": "Remux", "score": -10000})
            p["minFormatScore"] = max(p.get("minFormatScore") or 0, 0)
            radarr.call("PUT", f"/qualityprofile/{p['id']}", p)
            done.append(f"Profile '{p['name']}': Remux -10000, minimum score {p['minFormatScore']}")
    if "sizes" in ids:
        defs = radarr.call("GET", "/qualitydefinition")
        for d in defs:
            for res, names in SIZE_QUALITIES.items():
                if d["title"] in names:
                    sfx = "2160" if res == 2160 else "1080"
                    d["minSize"], d["preferredSize"], d["maxSize"] = r[f"tiny_{sfx}"], r[f"preferred_{sfx}"], r[f"cap_{sfx}"]
        radarr.call("PUT", "/qualitydefinition/update", defs)
        done.append("Set Radarr's 1080p and 4K size limits")
    if "webhook" in ids:
        hook = _hook(radarr.call("GET", "/notification"))
        if hook is None:
            hook = next(x for x in radarr.call("GET", "/notification/schema") if x.get("implementation") == "Webhook")
            hook.update(name="Refetcharr", tags=[])
        for k in list(hook):
            if k.startswith("on") and isinstance(hook[k], bool):
                hook[k] = k in ("onDownload", "onUpgrade", "onMovieFileDelete")
        for f in hook.get("fields") or []:
            if f.get("name") == "url":
                f["value"] = webhook_url()
            elif f.get("name") == "method":
                f["value"] = 1  # POST
        # Radarr sends a test event on save, so Refetcharr must be reachable at that address
        radarr.call("PUT", f"/notification/{hook['id']}", hook) if hook.get("id") else radarr.call("POST", "/notification", hook)
        done.append(f"Radarr webhook 'Refetcharr' sends imports to {config.load()['connections']['refetcharr_url']}")
    return done
