"""Radarr's v3 API: the few calls Refetcharr needs."""

import json
import urllib.error
import urllib.request

from . import config


class RadarrError(Exception):
    pass


def call(method: str, path: str, body=None, timeout: int = 120, url: str = "", key: str = ""):
    """`url`/`key` override the saved connection (for testing values before saving them)."""
    url, key = (url or config.radarr_url()).rstrip("/"), key or config.radarr_key()
    if not key:
        raise RadarrError("No Radarr API key: add it in Settings → Connections (Radarr → Settings → General → API Key)")
    req = urllib.request.Request(url + "/api/v3" + path, method=method, headers={"X-Api-Key": key, "Content-Type": "application/json"},
                                 data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read()
            return json.loads(data) if data else None
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise RadarrError("Radarr refused the API key: check it in Settings → Connections") from None
        raise RadarrError(f"Radarr said HTTP {e.code} to {method} {path}: {e.read()[:200].decode('utf-8', 'replace')}") from None
    except (urllib.error.URLError, TimeoutError) as e:
        raise RadarrError(f"Can't reach Radarr at {url}: {getattr(e, 'reason', e)}") from None


def movies() -> list:
    return call("GET", "/movie", timeout=300)


def movie(mid: int) -> dict:
    return call("GET", f"/movie/{mid}")


def releases(mid: int) -> list:
    """Interactive search: every indexer, so it takes a minute or two."""
    return call("GET", f"/release?movieId={mid}", timeout=900) or []


def grab(rel: dict, mid: int) -> None:
    """Radarr's 'download anyway': grabs a release even when it isn't an upgrade."""
    call("POST", "/release", {"guid": rel["guid"], "indexerId": rel["indexerId"], "movieId": mid})


def poster(mid: int) -> bytes:
    """The film's poster, 250 px wide: Radarr's own cached copy (the API path; /MediaCover without /api/v3 is the web UI)."""
    url, key = config.radarr_url().rstrip("/"), config.radarr_key()
    if not key:
        raise RadarrError("No Radarr API key")
    req = urllib.request.Request(f"{url}/api/v3/MediaCover/{int(mid)}/poster-250.jpg", headers={"X-Api-Key": key})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            if not (r.headers.get("Content-Type") or "").startswith("image/"):
                raise RadarrError(f"Radarr has no poster for film {mid}")
            return r.read()
    except urllib.error.HTTPError as e:
        raise RadarrError(f"Radarr said HTTP {e.code} for film {mid}'s poster") from None
    except (urllib.error.URLError, TimeoutError) as e:
        raise RadarrError(f"Can't reach Radarr at {url}: {getattr(e, 'reason', e)}") from None


def queue() -> list:
    return call("GET", "/queue?pageSize=1000&includeMovie=true")["records"]


def manual_import_items(download_id: str, mid: int) -> list:
    """Radarr probes each file, which takes minutes over the network when the host is busy."""
    return call("GET", f"/manualimport?downloadId={download_id}&movieId={mid}&filterExistingFiles=false", timeout=600)


def manual_import(item: dict, mid: int, download_id: str) -> None:
    payload = {
        "path": item["path"],
        "movieId": mid,
        "languages": item.get("languages") or [],
        "downloadId": download_id,
        "releaseGroup": item.get("releaseGroup"),
        "indexerFlags": item.get("indexerFlags", 0),
    }
    if item.get("quality") is not None:
        payload["quality"] = item["quality"]
    call("POST", "/command", {"name": "ManualImport", "importMode": "auto", "files": [payload]})


def remove_from_queue(qid: int, blocklist: bool = True) -> None:
    call("DELETE", f"/queue/{qid}?removeFromClient=true&blocklist={'true' if blocklist else 'false'}")
