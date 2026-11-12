"""The HTTP API and the web UI. The TUI uses the same API, so both always show and do the same things."""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import VERSION, auth, config, quality, radarr, rules, setup
from .engine import Engine

STATIC = os.path.join(os.path.dirname(__file__), "static")
engine = Engine()


@asynccontextmanager
async def lifespan(_app):
    auth.ensure_keys()  # the cookie secret and the API key the TUI and CLI use
    if os.environ.get("REFETCHARR_WORKER", "1") == "1":  # tests run the API without the worker
        engine.start()
    yield
    engine.stop()


app = FastAPI(title="Refetcharr", version=VERSION, docs_url="/api/docs", redoc_url=None, lifespan=lifespan)


OPEN = {"/login", "/api/health", "/api/auth/state", "/api/auth/login", "/api/auth/setup", "/static/login.js", "/static/style.css", "/static/logo-64.png",
        "/static/logo-192.png", "/static/favicon.ico"}


@app.middleware("http")
async def _signed_in(request: Request, call_next):
    """Everything but the sign-in page and the health check needs a session cookie or the API key (X-Api-Key, as the
    TUI and CLI send). With no login set yet, the sign-in page asks for one."""
    path = request.url.path
    if path in OPEN or auth.session_ok(request.cookies.get(auth.COOKIE)) or auth.key_ok(request.headers.get("x-api-key")) \
            or (path == setup.WEBHOOK_PATH and auth.key_ok(request.query_params.get("apikey"))):  # Radarr can't send headers
        return await call_next(request)
    if path.startswith("/api/"):
        return JSONResponse({"detail": "Sign in first"}, status_code=401)
    return RedirectResponse("/login", status_code=303)


@app.middleware("http")
async def _revalidate_page(request, call_next):
    """The page and its code are checked with the server on every load, so a new version shows after a refresh."""
    response = await call_next(request)
    if not request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


class QueueIn(BaseModel):
    count: int = Field(0, ge=0, le=500)
    ids: list[int] = Field(default_factory=list, max_length=500)


class ConnectionsIn(BaseModel):
    radarr_url: str = Field("", max_length=300)
    radarr_api_key: str = Field("", max_length=200)


class FixIn(BaseModel):
    ids: list[str] = Field(default_factory=list, max_length=20)


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=200)
    remember: bool = False


class ChangeLoginIn(BaseModel):
    current: str = Field(min_length=1, max_length=200)
    username: str = Field(min_length=1, max_length=64)
    password: str = Field("", max_length=200)  # blank: keep it


class IgnoreIn(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=5000)
    ignore: bool = True


class IdsIn(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=2000)


class PickIn(BaseModel):
    id: int
    guid: str = Field(min_length=1, max_length=2000)


class RetryIn(BaseModel):
    ids: list[int] = Field(default_factory=list, max_length=5000)
    match: str = Field("", max_length=200)


def _check_rules(r: dict) -> None:
    """Refuse values the rules can't work with, by the type each setting has in the defaults. Lists are tidied in place."""
    defaults = config.DEFAULTS["rules"]
    for k, v in r.items():
        want = defaults.get(k)
        if want is None:
            continue  # unknown keys are dropped when saving
        if isinstance(want, bool):
            if not isinstance(v, bool):
                raise HTTPException(400, f"{k} must be on or off")
        elif isinstance(want, str):
            if k == "min_picture" and v not in quality.MIN_PICTURE:
                raise HTTPException(400, f"min_picture must be one of {', '.join(quality.MIN_PICTURE)}")
            if k == "prefer" and v not in rules.PREFER:
                raise HTTPException(400, f"prefer must be one of {', '.join(rules.PREFER)}")
        elif isinstance(want, list):
            if not isinstance(v, list) or len(v) > 30 or not all(isinstance(x, str) and len(x.strip()) <= 40 for x in v):
                raise HTTPException(400, f"{k} must be a list of up to 30 short names")
            seen: dict = {}
            for x in v:
                if x.strip():
                    seen.setdefault(x.strip().lower(), x.strip())
            r[k] = list(seen.values())
        elif isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
            raise HTTPException(400, f"{k} must be a positive number")
    if "max_ratio" in r and not 0.1 <= r["max_ratio"] <= 1:
        raise HTTPException(400, "max_ratio must be between 0.1 and 1")
    if "stall_warn_hours" in r and r["stall_warn_hours"] > 720:
        raise HTTPException(400, "Warn about stalled downloads within 720 hours (30 days), or 0 for never")
    merged = {**config.load()["rules"], **r}
    for res, label in (("1080", "1080p"), ("2160", "4K")):
        order = [merged[f"{k}_{res}"] for k in ("tiny", "min", "preferred", "cap")]
        if order != sorted(order):
            raise HTTPException(400, f"{label} sizes must go up in order: too small, minimum, preferred, maximum")


TIMING_MAX = {"missing_minutes": 1440, "import_wait_minutes": 1440, "no_video_hours": 72, "finish_warn_minutes": 1440,
              "library_minutes": 1440, "setup_minutes": 1440}


def _check_quality(q: dict) -> None:
    """The quality thresholds: two rising numbers per resolution (and for audio), a weight, and codec factors."""
    pair = lambda v: isinstance(v, list) and len(v) == 2 and all(isinstance(x, (int, float)) and not isinstance(x, bool) and x > 0 for x in v) and v[0] < v[1]
    for k, v in (q.get("video") or {}).items():
        if k not in quality.DEFAULTS["video"] or not pair(v):
            raise HTTPException(400, f"Picture thresholds for {k}: two rising numbers in Mbps (top of LOW, top of MEDIUM)")
    if "audio" in q and not pair(q["audio"]):
        raise HTTPException(400, "Audio thresholds: two rising numbers in kbps (top of LOW, top of MEDIUM)")
    wt = q.get("video_weight", 70)
    if isinstance(wt, bool) or not isinstance(wt, (int, float)) or not 0 <= wt <= 100:
        raise HTTPException(400, "The picture's weight must be from 0 to 100 (%)")
    for k, v in (q.get("codec_factor") or {}).items():
        if k not in quality.CODEC_FACTOR or isinstance(v, bool) or not isinstance(v, (int, float)) or not 0.2 <= v <= 5:
            raise HTTPException(400, f"Codec factor for {k} must be from 0.2 to 5")


def _check_other(patch: dict) -> None:
    """The ignore lists, the timings, the quality thresholds and the addresses."""
    _check_quality(patch.get("quality") or {})
    for k in ("radarr_link", "refetcharr_url"):
        v = (patch.get("connections") or {}).get(k)
        if v and not str(v).startswith(("http://", "https://")):
            raise HTTPException(400, f"{k} must start with http:// or https://")
    ig = patch.get("ignore") or {}
    for k in ("file_groups", "release_groups"):
        if k in ig:
            v = ig[k]
            if not isinstance(v, list) or len(v) > 200 or not all(isinstance(x, str) and len(x.strip()) <= 60 for x in v):
                raise HTTPException(400, f"{k} must be a list of up to 200 release group names")
            seen: dict = {}
            for x in v:
                if x.strip():
                    seen.setdefault(x.strip().lower(), x.strip())  # the first spelling wins
            ig[k] = list(seen.values())
    if "films" in ig and (not isinstance(ig["films"], list) or not all(isinstance(x, int) and not isinstance(x, bool) for x in ig["films"])):
        raise HTTPException(400, "films must be a list of Radarr movie ids")
    for k, v in (patch.get("timing") or {}).items():
        if k in TIMING_MAX and (isinstance(v, bool) or not isinstance(v, (int, float)) or not 1 <= v <= TIMING_MAX[k]):
            raise HTTPException(400, f"{k} must be from 1 to {TIMING_MAX[k]}")
    link = (patch.get("connections") or {}).get("radarr_link")
    if link and not str(link).startswith(("http://", "https://")):
        raise HTTPException(400, "The Radarr link address must start with http:// or https://")
    if isinstance(link, str):
        patch["connections"]["radarr_link"] = link.strip().rstrip("/")


@app.get("/api/health")
def health():
    return {"ok": True, "version": VERSION}


# ── sign-in ──────────────────────────────────────────────────────────────
def _client(request: Request) -> str:
    return request.client.host if request.client else "?"


def _start_session(request: Request, remember: bool) -> JSONResponse:
    r = JSONResponse({"ok": True})
    r.set_cookie(auth.COOKIE, auth.make_session(), max_age=auth.SESSION_DAYS * 86400 if remember else None, httponly=True,
                 samesite="lax", secure=request.url.scheme == "https", path="/")
    return r


@app.get("/api/auth/state")
def auth_state(request: Request):
    """Whether a login exists yet (the sign-in page shows "create" or "sign in") and whether this browser is signed in."""
    return {"set": auth.is_set(), "signed_in": auth.session_ok(request.cookies.get(auth.COOKIE))}


@app.post("/api/auth/setup")
def auth_setup(body: LoginIn, request: Request):
    """The first login, only while none exists (after `refetcharr login reset`, or on a new install)."""
    if auth.is_set():
        raise HTTPException(409, "A login already exists. Sign in, or reset it from the container: refetcharr login reset")
    try:
        auth.set_login(body.username, body.password)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    from . import store
    store.log("setup", f"Sign-in created for {body.username.strip()} from {_client(request)}")
    return _start_session(request, body.remember)


@app.post("/api/auth/login")
def auth_login(body: LoginIn, request: Request):
    ip = _client(request)
    if auth.locked(ip):
        raise HTTPException(429, f"Too many failed sign-ins: try again in {auth.FAIL_WINDOW // 60} minutes")
    if not auth.is_set():
        raise HTTPException(409, "No login yet: create one")
    if not auth.verify(body.username, body.password):
        auth.failed(ip)
        raise HTTPException(401, "Wrong username or password")
    auth.cleared(ip)
    return _start_session(request, body.remember)


@app.post("/api/auth/logout")
def auth_logout():
    r = JSONResponse({"ok": True})
    r.delete_cookie(auth.COOKIE, path="/")
    return r


@app.post("/api/auth/change")
def auth_change(body: ChangeLoginIn, request: Request):
    """New username and/or password, with the current password. Every other session is signed out; this one carries on."""
    if not auth.verify(auth._auth()["username"], body.current):
        auth.failed(_client(request))
        raise HTTPException(401, "The current password is wrong")
    try:
        auth.set_login(body.username, body.password or body.current)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    from . import store
    store.log("setup", f"Sign-in changed (user {body.username.strip()})")
    return _start_session(request, True)


@app.get("/api/auth/key")
def auth_key():
    return {"api_key": auth.ensure_keys()["api_key"]}


@app.post("/api/auth/key")
def auth_new_key():
    return {"api_key": auth.new_api_key()}


@app.get("/login")
def login_page():
    return FileResponse(os.path.join(STATIC, "login.html"))


@app.get("/api/status")
def status():
    return engine.status()


@app.get("/api/films")
def films():
    return engine.films()


@app.get("/api/activity")
def activity(n: int = 500):
    """The activity log, newest first (Activity → Events)."""
    from . import store
    return {"events": store.history(max(1, min(n, 20000)))}


@app.get("/api/replaced")
def replaced():
    """Every replaced film, with its ratings and anything wrong with it now."""
    return engine.replaced()


@app.post("/api/requeue")
def requeue(body: IdsIn):
    return {"message": engine.requeue(body.ids)}


@app.post("/api/suggest")
def suggest(body: IdsIn):
    if len(body.ids) > 200:
        raise HTTPException(400, "Find replacements for up to 200 films at a time: each is a search on every indexer")
    return {"message": engine.suggest(body.ids)}


@app.get("/api/suggestions")
def suggestions():
    return engine.suggestions()


@app.post("/api/suggestions/dismiss")
def dismiss(body: IdsIn):
    return {"message": engine.dismiss(body.ids)}


@app.post("/api/pick/best")
def pick_best(body: IdsIn):
    return {"message": engine.pick_best(body.ids)}


@app.post("/api/pick")
def pick(body: PickIn):
    try:
        return {"message": engine.pick(body.id, body.guid)}
    except ValueError as e:
        raise HTTPException(409, str(e)) from None


@app.get("/api/films/{mid}")
def film_detail(mid: int):
    """One film's drop-down: before and after, why it was flagged, why that release, what was turned down."""
    return engine.detail(mid)


@app.post(setup.WEBHOOK_PATH)
def radarr_webhook(body: dict):
    """Radarr's Connect → Webhook: a film got a new file (or lost one). Read it again within a few seconds."""
    event = body.get("eventType") or ""
    mid = (body.get("movie") or {}).get("id")
    if event in ("Download", "MovieFileDelete", "Rename") and isinstance(mid, int) and not isinstance(mid, bool):
        why = {"Download": "upgraded by Radarr" if body.get("isUpgrade") else "imported by Radarr",
               "MovieFileDelete": "file deleted", "Rename": "renamed"}[event]
        engine.jobs.append(("film", mid, why))
    return {"ok": True, "event": event}


@app.get("/api/settings")
def get_settings():
    return {"values": config.public(config.load()), "defaults": config.public(config.DEFAULTS), "catalog": rules.catalog()}


@app.patch("/api/settings")
def patch_settings(patch: dict):
    if "auth" in patch:
        raise HTTPException(400, "Change the sign-in under Settings → Security (or refetcharr login in the container)")
    q = (patch.get("queue") or {})
    for k in ("max_active", "max_per_day"):
        if k in q and not (isinstance(q[k], int) and 0 <= q[k] <= 1000):
            raise HTTPException(400, f"{k} must be a whole number from 0 to 1000")
    if q.get("max_active") == 0:
        raise HTTPException(400, "Downloads at once must be at least 1 (use Pause to stop)")
    _check_rules(patch.get("rules") or {})
    _check_other(patch)
    url = (patch.get("connections") or {}).get("radarr_url")
    if url and not str(url).startswith(("http://", "https://")):
        raise HTTPException(400, "The Radarr address must start with http:// or https://")
    values = config.save(patch)
    if "rules" in patch or "queue" in patch or "timing" in patch:
        engine._last_import = 0  # a new stall limit shows at the next check, not two minutes later
    if "connections" in patch:  # a new connection: read the library again and recheck Radarr's setup
        engine.movies_at = 0
        engine._last_setup = 0
    return {"values": config.public(values)}


@app.post("/api/connections/test")
def test_connections(body: ConnectionsIn):
    """Try the values in the form (blank fields fall back to what's saved) without saving them."""
    try:
        st = radarr.call("GET", "/system/status", url=body.radarr_url, key=body.radarr_api_key)
        return {"ok": True, "message": f"Connected to Radarr {st.get('version')}"}
    except radarr.RadarrError as e:
        return {"ok": False, "message": str(e)}


@app.get("/api/setup")
def get_setup():
    return {"checks": engine.check_setup()}


@app.post("/api/setup/fix")
def fix_setup(body: FixIn):
    try:
        movies = engine.library()
        done = setup.fix(body.ids, movies)
    except radarr.RadarrError as e:
        raise HTTPException(502, str(e)) from None
    from . import store
    for d in done:
        store.log("setup", f"Radarr setup: {d}")
    return {"done": done, "checks": engine.check_setup()}


@app.post("/api/queue")
def queue(body: QueueIn):
    if not body.count and not body.ids:
        raise HTTPException(400, "Give a number of films or pick some")
    return {"message": engine.queue_now(body.count, body.ids)}


@app.post("/api/ignore")
def ignore(body: IgnoreIn):
    return {"message": engine.ignore(body.ids, body.ignore)}


@app.post("/api/queue/clear")
def queue_clear():
    return {"message": engine.clear_manual()}


@app.post("/api/pause")
def pause():
    config.save({"queue": {"paused": True}})
    return {"message": "Paused: no new searches start. Downloads already running finish and are imported."}


@app.post("/api/resume")
def resume():
    config.save({"queue": {"paused": False}})
    return {"message": "Resumed."}


@app.post("/api/scan")
def scan():
    return {"message": engine.request_scan()}


@app.post("/api/retry")
def retry(body: RetryIn):
    return engine.retry(body.ids, body.match)


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC, "index.html"))


app.mount("/static", StaticFiles(directory=STATIC), name="static")
