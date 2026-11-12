"""What Refetcharr remembers: one entry per film it has tried (state.json, same format as the old radarr-cleanup
scripts), and an activity log. Only the server writes these; the TUI and web UI go through its API."""

import collections
import datetime as dt
import json
import os
import threading

from . import config

PENDING = ("grabbed", "downloading", "importing")
DONE = ("replaced",)

_lock = threading.RLock()
_log: collections.deque = collections.deque(maxlen=300)
_loaded = False  # the activity history is read from disk once, before anything new is added


def _file(name: str) -> str:
    return os.path.join(config.DATA, name)


def load() -> dict:
    with _lock:
        try:
            with open(_file("state.json")) as f:
                return json.load(f)
        except FileNotFoundError:
            return {}


def save(state: dict) -> None:
    with _lock:
        os.makedirs(config.DATA, exist_ok=True)
        tmp = _file("state.json.tmp")
        with open(tmp, "w") as f:
            json.dump(state, f, indent=1)
        os.replace(tmp, _file("state.json"))


def update(mid, **fields) -> dict:
    """Change one film's entry (creating it if needed) and save, under the lock."""
    with _lock:
        state = load()
        entry = state.setdefault(str(mid), {})
        entry.update(fields)
        save(state)
        return entry


def remove(mids) -> list:
    with _lock:
        state = load()
        gone = [state.pop(str(m)) | {"id": str(m)} for m in mids if str(m) in state]
        save(state)
        return gone


def freed(entry: dict) -> int:
    """Space freed by this film, counting earlier replacements of it (a film put right again keeps its first saving)."""
    own = entry.get("saved") or (entry["old_size"] - entry.get("new_size", entry["old_size"]))
    prev = entry.get("previous")
    return own + (freed(prev) if prev and prev.get("status") == "replaced" else 0)


def grabbed_on(day: dt.date) -> int:
    """Films actually grabbed that day (searches that found nothing don't count against the daily limit)."""
    stamp = day.isoformat()
    return sum(1 for s in load().values() if s.get("grabbed", "").startswith(stamp) and s.get("new_title"))


def _load_log() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    try:
        with open(_file("activity.jsonl")) as f:
            for line in f.readlines()[-_log.maxlen:]:
                _log.append(json.loads(line))
    except (FileNotFoundError, json.JSONDecodeError):
        pass


def log(kind: str, text: str, **extra) -> None:
    """Something worth showing in the activity feed."""
    item = {"time": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "kind": kind, "text": text, **extra}
    with _lock:
        _load_log()
        _log.append(item)
        os.makedirs(config.DATA, exist_ok=True)
        with open(_file("activity.jsonl"), "a") as f:
            f.write(json.dumps(item) + "\n")


def history(n: int = 2000) -> list:
    """The last n entries of the whole activity log on disk, newest first (the feed only keeps the last 300)."""
    with _lock:
        try:
            with open(_file("activity.jsonl")) as f:
                lines = collections.deque(f, maxlen=n)
        except FileNotFoundError:
            return []
    out = []
    for line in reversed(lines):
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def recent(n: int = 50) -> list:
    with _lock:
        _load_log()
        return list(_log)[-n:][::-1]
