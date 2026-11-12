"""Sign-in: one user, like Sonarr and Radarr. The password is stored as a PBKDF2 hash in settings.json (mode 600), with a
secret that signs session cookies and an API key for the TUI, the CLI and scripts. Nothing here is ever sent to the
browser except the username. Reset from the container: `refetcharr login reset` (or `login set <user>`)."""

import base64
import collections
import hashlib
import hmac
import secrets
import threading
import time

from . import config

ITERATIONS = 600_000
COOKIE = "refetcharr_session"
SESSION_DAYS = 30
MAX_FAILS = 10  # failed sign-ins from one address...
FAIL_WINDOW = 900  # ...within this many seconds lock it out for the rest of the window
MIN_PASSWORD = 8

_fails: dict = collections.defaultdict(collections.deque)
_fail_lock = threading.Lock()


def _auth() -> dict:
    return config.load()["auth"]


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def _check_hash(password: str, stored: str) -> bool:
    try:
        algo, its, salt, digest = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        got = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.b64decode(salt), int(its))
        return hmac.compare_digest(got, base64.b64decode(digest))
    except (ValueError, TypeError):
        return False


def ensure_keys() -> dict:
    """The cookie secret and the API key, made the first time they're needed."""
    a = _auth()
    patch = {}
    if not a["secret"]:
        patch["secret"] = secrets.token_hex(32)
    if not a["api_key"]:
        patch["api_key"] = secrets.token_hex(16)
    return config.save({"auth": patch})["auth"] if patch else a


def is_set() -> bool:
    a = _auth()
    return bool(a["username"] and a["password_hash"])


def check_password(password: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise ValueError(f"The password must be at least {MIN_PASSWORD} characters")


def set_login(username: str, password: str) -> None:
    """Set (or replace) the one login. Every existing session is signed out."""
    username = username.strip()
    if not username or len(username) > 64:
        raise ValueError("The username must be 1 to 64 characters")
    check_password(password)
    ensure_keys()
    config.save({"auth": {"username": username, "password_hash": hash_password(password), "epoch": _auth()["epoch"] + 1}})


def reset_login() -> None:
    """Forget the login: the next visit to the web UI asks for a new one. Every session is signed out."""
    config.save({"auth": {"username": "", "password_hash": "", "epoch": _auth()["epoch"] + 1}})


def new_api_key() -> str:
    key = secrets.token_hex(16)
    config.save({"auth": {"api_key": key}})
    return key


def verify(username: str, password: str) -> bool:
    a = _auth()
    same_user = hmac.compare_digest(username.strip().lower().encode(), a["username"].lower().encode())
    return _check_hash(password, a["password_hash"]) and same_user  # the hash runs either way: no timing hint


def locked(ip: str) -> bool:
    with _fail_lock:
        q = _fails[ip]
        while q and q[0] < time.time() - FAIL_WINDOW:
            q.popleft()
        return len(q) >= MAX_FAILS


def failed(ip: str) -> None:
    with _fail_lock:
        _fails[ip].append(time.time())


def cleared(ip: str) -> None:
    with _fail_lock:
        _fails.pop(ip, None)


def _sign(body: str, secret: str) -> str:
    return hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()


def make_session() -> str:
    a = ensure_keys()
    body = f"{a['epoch']}.{int(time.time()) + SESSION_DAYS * 86400}"
    return f"{body}.{_sign(body, a['secret'])}"


def session_ok(value: str | None) -> bool:
    """A cookie we signed, not expired, from the current login (changing or resetting it bumps the epoch)."""
    if not value or not is_set():
        return False
    a = _auth()
    try:
        epoch, expires, sig = value.split(".")
        ok = hmac.compare_digest(sig, _sign(f"{epoch}.{expires}", a["secret"]))
        return ok and int(epoch) == a["epoch"] and int(expires) > time.time()
    except ValueError:
        return False


def key_ok(value: str | None) -> bool:
    key = _auth()["api_key"]
    return bool(value and key) and hmac.compare_digest(value.encode(), key.encode())
