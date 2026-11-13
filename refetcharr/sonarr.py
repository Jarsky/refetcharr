"""Sonarr's v3 API (Sonarr 4 and 5): the calls Refetcharr needs for TV. Errors are RadarrError's subclass, so everything
that already copes with Radarr being unreachable copes with Sonarr too."""

import json
import urllib.error
import urllib.parse
import urllib.request

from . import config
from .radarr import RadarrError


class SonarrError(RadarrError):
    pass


def call(method: str, path: str, body=None, timeout: int = 120, url: str = "", key: str = ""):
    """`url`/`key` override the saved connection (for testing values before saving them)."""
    url, key = (url or config.sonarr_url()).rstrip("/"), key or config.sonarr_key()
    if not key:
        raise SonarrError("No Sonarr API key: add it in Settings → Sonarr (Sonarr → Settings → General → API Key)")
    req = urllib.request.Request(url + "/api/v3" + path, method=method, headers={"X-Api-Key": key, "Content-Type": "application/json"},
                                 data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read()
            return json.loads(data) if data else None
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise SonarrError("Sonarr refused the API key: check it in Settings → Sonarr") from None
        raise SonarrError(f"Sonarr said HTTP {e.code} to {method} {path}: {e.read()[:200].decode('utf-8', 'replace')}") from None
    except (urllib.error.URLError, TimeoutError) as e:
        raise SonarrError(f"Can't reach Sonarr at {url}: {getattr(e, 'reason', e)}") from None


def series_list() -> list:
    return call("GET", "/series", timeout=300)


def series(sid: int) -> dict:
    return call("GET", f"/series/{int(sid)}")


def episode_files(sid: int) -> list:
    return call("GET", f"/episodefile?seriesId={int(sid)}", timeout=300) or []


def episodes(sid: int) -> list:
    return call("GET", f"/episode?seriesId={int(sid)}", timeout=300) or []


def episode(eid: int) -> dict:
    return call("GET", f"/episode/{int(eid)}")


def episode_file(fid: int) -> dict:
    return call("GET", f"/episodefile/{int(fid)}")


def season_releases(sid: int, number: int) -> list:
    """Interactive search for a season: every indexer. Season packs and single episodes both come back."""
    return call("GET", f"/release?seriesId={int(sid)}&seasonNumber={int(number)}", timeout=900) or []


def episode_releases(eid: int) -> list:
    return call("GET", f"/release?episodeId={int(eid)}", timeout=900) or []


def grab(rel: dict) -> None:
    """Sonarr's 'download anyway' for a release from a recent search."""
    call("POST", "/release", {"guid": rel["guid"], "indexerId": rel["indexerId"]})


def poster(sid: int) -> bytes:
    url, key = config.sonarr_url().rstrip("/"), config.sonarr_key()
    if not key:
        raise SonarrError("No Sonarr API key")
    req = urllib.request.Request(f"{url}/api/v3/MediaCover/{int(sid)}/poster-250.jpg", headers={"X-Api-Key": key})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            if not (r.headers.get("Content-Type") or "").startswith("image/"):
                raise SonarrError(f"Sonarr has no poster for series {sid}")
            return r.read()
    except urllib.error.HTTPError as e:
        raise SonarrError(f"Sonarr said HTTP {e.code} for series {sid}'s poster") from None
    except (urllib.error.URLError, TimeoutError) as e:
        raise SonarrError(f"Can't reach Sonarr at {url}: {getattr(e, 'reason', e)}") from None


def queue() -> list:
    """One record per episode: a season pack shows once for each of its episodes, all with the same downloadId."""
    return call("GET", "/queue?pageSize=2000&includeSeries=false&includeEpisode=false")["records"]


def manual_import_items(download_id: str, sid: int, number: int | None = None) -> list:
    q = {"downloadId": download_id, "seriesId": int(sid), "filterExistingFiles": "false"}
    if number is not None:
        q["seasonNumber"] = int(number)
    return call("GET", "/manualimport?" + urllib.parse.urlencode(q), timeout=600) or []


def manual_import(items: list, sid: int, download_id: str) -> None:
    """Import these files over the episodes they map to (the same override as Radarr's manual import)."""
    files = []
    for it in items:
        f = {"path": it["path"], "seriesId": int(sid), "episodeIds": [e["id"] for e in it.get("episodes") or []],
             "languages": it.get("languages") or [], "downloadId": download_id, "releaseGroup": it.get("releaseGroup"),
             "indexerFlags": it.get("indexerFlags", 0), "releaseType": it.get("releaseType") or "unknown"}
        if it.get("quality") is not None:
            f["quality"] = it["quality"]
        files.append(f)
    call("POST", "/command", {"name": "ManualImport", "importMode": "auto", "files": files})
