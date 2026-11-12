"""`refetcharr`: the menu (no arguments), the server (`serve`), and one-shot commands for scripts.

  refetcharr                     the menu
  refetcharr serve               the web UI, API and worker (what the container runs)
  refetcharr status              the dashboard once
  refetcharr queue 20            queue the next 20 biggest wins     refetcharr queue --id 1234 5678
  refetcharr pause | resume      stop / restart new searches
  refetcharr retry [--id N ...] [--match TEXT]   try films that weren't replaced again
  refetcharr scan                rescan the library
  refetcharr login set [USER]    set the web UI's username and password (asks for the password; signs everyone out)
  refetcharr login reset         forget the login: the next visit to the web UI asks for a new one
  refetcharr login show          who the login is, if any
  refetcharr apikey [--new]      the API key for scripts (X-Api-Key), or a new one
"""

import argparse
import json
import sys
import urllib.error
import urllib.request

from . import VERSION, config


def api(method: str, path: str, body=None):
    """The server's API, with the API key from the settings file (the TUI and these commands run in the container)."""
    headers = {"Content-Type": "application/json", "X-Api-Key": config.load()["auth"]["api_key"]}
    req = urllib.request.Request(config.API + path, method=method, headers=headers,
                                 data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read()).get("detail")
        except Exception:
            detail = e.reason
        raise SystemExit(f"Refetcharr said: {detail}") from None
    except urllib.error.URLError:
        raise SystemExit(f"Can't reach the Refetcharr server at {config.API}. Is the container running? (docker ps)") from None


def main() -> None:
    p = argparse.ArgumentParser(prog="refetcharr", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", nargs="?", default="menu", choices=["menu", "serve", "status", "queue", "pause", "resume", "retry", "scan", "version", "login", "apikey"])
    p.add_argument("count", nargs="?", default="0", help="queue: how many of the biggest wins; login: set, reset or show")
    p.add_argument("user", nargs="?", default="", help="login set: the username")
    p.add_argument("--new", action="store_true", help="apikey: make a new key")
    p.add_argument("--password-stdin", action="store_true", help="login set: read the password from standard input")
    p.add_argument("--id", type=int, nargs="*", default=[], help="queue/retry: Radarr movie ids")
    p.add_argument("--match", default="", help="retry: only films whose title contains this")
    a = p.parse_args()
    if a.command == "login":
        return login(a.count, a.user, a.password_stdin)
    if a.command == "apikey":
        from . import auth
        print(auth.new_api_key() if a.new else auth.ensure_keys()["api_key"])
        return
    try:
        a.count = int(a.count)
    except ValueError:
        sys.exit(f"{a.count} isn't a number")
    if a.command == "serve":
        import uvicorn
        uvicorn.run("refetcharr.web:app", host="0.0.0.0", port=config.PORT, log_level="warning", proxy_headers=True, forwarded_allow_ips="*")
    elif a.command == "version":
        print(f"Refetcharr {VERSION}")
    elif a.command == "menu":
        from .tui import menu
        menu()
    elif a.command == "status":
        from .tui import Tui
        Tui().dashboard()
    elif a.command == "queue":
        if not a.count and not a.id:
            sys.exit("Say how many (refetcharr queue 20) or which (refetcharr queue --id 1234)")
        print(api("POST", "/api/queue", {"count": a.count, "ids": a.id})["message"])
    elif a.command in ("pause", "resume", "scan"):
        print(api("POST", f"/api/{a.command}")["message"])
    elif a.command == "retry":
        done = api("POST", "/api/retry", {"ids": a.id, "match": a.match})["retried"]
        print("\n".join(f"will retry: {x['title']} (was {x['was']})" for x in done) or "Nothing to retry.")


def login(action: str, user: str, from_stdin: bool) -> None:
    """Set, reset or show the web UI's one login. Writes settings.json directly, so it works with the server stopped."""
    import getpass
    from . import auth
    if action == "show":
        print(f"Login: {config.load()['auth']['username']}" if auth.is_set() else "No login set: the web UI asks for one on the next visit.")
    elif action == "reset":
        auth.reset_login()
        print("Login forgotten and everyone signed out. Open the web UI to create a new one.")
    elif action == "set":
        user = user or input("Username: ").strip()
        if from_stdin:
            password = sys.stdin.readline().rstrip("\n")
        else:
            password = getpass.getpass("Password: ")
            if getpass.getpass("Password again: ") != password:
                sys.exit("The passwords don't match; nothing changed.")
        try:
            auth.set_login(user, password)
        except ValueError as e:
            sys.exit(f"{e}; nothing changed.")
        print(f"Login set for {user.strip()}. Everyone else is signed out.")
    else:
        sys.exit("refetcharr login set [USER] | reset | show")
