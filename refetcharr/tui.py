"""The terminal UI, in media-health's style: a neon banner, rounded panels, and a numbered menu. Everything goes
through the server's API, so the TUI and the web UI can be used side by side."""

import time

from rich import box
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.prompt import Confirm, IntPrompt, Prompt
from rich.table import Table
from rich.text import Text

from . import VERSION
from .cli import api

C = {"violet": "#9b5cff", "blue": "#3fa0ff", "pink": "#ff2bd6", "mint": "#22f5c8", "cyan": "#22d3ee", "amber": "#ffb020", "dim": "grey62"}
GROUPS = {"remux": "1080p remuxes", "big4k": "4K over the cap", "remux4k": "4K remuxes", "big1080": "1080p over the cap"}
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_FIG = {}


def gb(n) -> str:
    n = (n or 0) / 1e9
    return f"{n / 1000:.2f} TB" if n >= 1000 else f"{n:.1f} GB"


def banner(width: int) -> Text:
    """Figlet header in a violet-to-cyan gradient; a plain title on narrow terminals."""
    try:
        import pyfiglet
        font = "ansi_shadow" if width >= 92 else "small"
        art = _FIG.setdefault(font, pyfiglet.figlet_format("REFETCHARR", font=font, width=400).rstrip("\n"))
    except Exception:
        art = "REFETCHARR"
    lines = art.splitlines()
    if max(map(len, lines)) > width - 2:
        art, lines = "REFETCHARR", ["REFETCHARR"]
    out = Text()
    stops = [(155, 92, 255), (63, 160, 255), (34, 211, 238)]
    for line in lines:
        for i, ch in enumerate(line):
            t = i / max(len(line) - 1, 1)
            a, b = (stops[0], stops[1]) if t < 0.5 else (stops[1], stops[2])
            k = t * 2 if t < 0.5 else (t - 0.5) * 2
            rgb = tuple(int(a[j] + (b[j] - a[j]) * k) for j in range(3))
            out.append(ch, style=f"bold rgb({rgb[0]},{rgb[1]},{rgb[2]})")
        out.append("\n")
    out.append(f"swap oversized films for smaller releases that keep the quality · v{VERSION}", style=C["dim"])
    return out


class Tui:
    def __init__(self):
        self.c = Console()

    # ── the dashboard ───────────────────────────────────────────────────────
    def view(self, s: dict) -> Group:
        counts = s["counts"]
        state = "[bold #ff2bd6]paused[/]" if s["paused"] else ("[bold #22f5c8]auto-queue on[/]" if s["auto"] else "[#ffb020]manual[/]")
        if s["auto"] and not s["in_window"] and not s["paused"]:
            state += " [grey62](outside its schedule)[/]"
        head = Table.grid(expand=True, padding=(0, 3))
        for _ in range(5):
            head.add_column(justify="center")
        head.add_row(f"[bold {C['mint']}]{gb(s['freed'])}[/]\n[grey62]freed[/]",
                     f"[bold]{counts.get('replaced', 0)}[/]\n[grey62]replaced[/]",
                     f"[bold {C['cyan']}]{len(s['active'])}[/][grey62]/{s['limits']['max_active']}[/]\n[grey62]downloading[/]",
                     f"[bold]{s['today']}[/][grey62]/{s['limits']['max_per_day'] or '∞'}[/]\n[grey62]grabbed today[/]",
                     f"[bold {C['pink']}]{len(s['failed'])}[/]\n[grey62]not replaced[/]")
        info = f"{state}   [grey62]·[/]   " + (f"[{C['cyan']}]{s['busy']}…[/]" if s["busy"] else "[grey62]idle[/]")
        w = s["waiting"]
        if w["ids"] or w["count"]:
            info += f"   [grey62]·[/]   waiting to search: {len(w['ids']) + w['count']}"
        if s["error"]:
            info += f"\n[bold red]Problem:[/] {s['error'].strip().splitlines()[-1][:160]}"
        if s.get("setup_issues"):
            info += f"\n[bold #ff2bd6]Radarr setup needs attention:[/] {'; '.join(s['setup_issues'])} [grey62](menu: Radarr connection and setup)[/]"
        parts = [Panel(Group(head, "", Text.from_markup(info)), title="[bold]refetcharr[/]", border_style=C["violet"], box=box.ROUNDED)]

        t = Table(expand=True, box=None, show_edge=False, header_style=C["dim"])
        t.add_column("film", ratio=4, overflow="ellipsis", no_wrap=True)
        t.add_column("progress", ratio=2)
        t.add_column("left", justify="right")
        t.add_column("now → new", justify="right")
        for a in sorted(s["active"], key=lambda a: -(a["progress"] or 0)):
            pct = a["progress"]
            bar = "[grey50]waiting[/]" if pct is None else f"[{C['cyan']}]{'━' * (pct // 10)}[/][grey30]{'━' * (10 - pct // 10)}[/] {pct}%"
            if a.get("state") in ("importPending", "importBlocked"):
                bar = f"[{C['mint']}]ready to import[/]"
            if a.get("finishing"):
                bar = f"[{'#ffb020' if a.get('finish_warn') else C['cyan']}]downloaded, waiting on qBittorrent[/]"
            if a.get("stalled"):
                bar = f"[#ffb020]stalled {a.get('stalled_hours') or 0:.0f} h[/] [grey62]at {pct or 0}%[/]"
            t.add_row(f"{a['title']} [grey62]({a['year']})[/]", bar, a.get("timeleft") or "", f"{gb(a['old_size'])} → [{C['mint']}]{gb(a['new_size'])}[/]")
        if not s["active"]:
            t.add_row("[grey62]nothing downloading[/]", "", "", "")
        parts.append(Panel(t, title="[bold]downloads[/]", border_style=C["blue"], box=box.ROUNDED))

        act = Table(expand=True, box=None, show_header=False, show_edge=False)
        act.add_column(style=C["dim"], no_wrap=True)
        act.add_column(overflow="ellipsis", no_wrap=True, ratio=1)
        colour = {"grab": C["cyan"], "done": C["mint"], "fail": C["pink"], "skip": C["dim"], "error": "red"}
        for x in s["activity"][:8]:
            act.add_row(x["time"][5:16], f"[{colour.get(x['kind'], 'white')}]{x['text']}[/]")
        parts.append(Panel(act, title="[bold]recent[/]", border_style=C["pink"], box=box.ROUNDED))
        return Group(*parts)

    def dashboard(self) -> None:
        self.c.print(self.view(api("GET", "/api/status")))

    def live(self) -> None:
        self.c.print("[grey62]Refreshing every 3 s. Ctrl+C to go back to the menu.[/]")
        try:
            with Live(self.view(api("GET", "/api/status")), console=self.c, refresh_per_second=2, screen=False) as lv:
                while True:
                    time.sleep(3)
                    lv.update(self.view(api("GET", "/api/status")))
        except KeyboardInterrupt:
            pass

    # ── actions ─────────────────────────────────────────────────────────────
    def queue_count(self) -> None:
        n = IntPrompt.ask("How many of the biggest wins", default=10)
        if n > 0:
            self.c.print(api("POST", "/api/queue", {"count": n})["message"])

    def queue_pick(self) -> None:
        films = [f for f in api("GET", "/api/films") if f["group"] in GROUPS and not f["refetch"]]
        q = Prompt.ask("Title contains (blank = biggest wins)", default="").lower()
        films = [f for f in films if q in f["title"].lower()][:20]
        if not films:
            self.c.print("[grey62]No oversized films match (or they've all been tried).[/]")
            return
        t = Table(box=box.ROUNDED, border_style=C["blue"], header_style=C["dim"])
        for col in ("#", "film", "group", "size", "saves about"):
            t.add_column(col, justify="right" if col in ("#", "size", "saves about") else "left")
        for i, f in enumerate(films, 1):
            t.add_row(str(i), f"{f['title']} ({f['year']})", GROUPS[f["group"]], f"{f['size_gb']:.1f} GB", f"{max(f['saving_gb'], 0):.1f} GB")
        self.c.print(t)
        pick = Prompt.ask("Which (numbers, comma-separated; blank = none)", default="")
        ids = [films[int(x) - 1]["id"] for x in pick.replace(" ", "").split(",") if x.isdigit() and 0 < int(x) <= len(films)]
        if ids:
            self.c.print(api("POST", "/api/queue", {"ids": ids})["message"])

    def toggle_auto(self) -> None:
        s = api("GET", "/api/settings")["values"]
        on = not s["queue"]["auto"]
        api("PATCH", "/api/settings", {"queue": {"auto": on}})
        self.c.print(f"Auto-queue is now [bold]{'on' if on else 'off'}[/]." + (" It keeps the queue topped up within your limits." if on else ""))

    def pause_resume(self) -> None:
        paused = api("GET", "/api/settings")["values"]["queue"]["paused"]
        self.c.print(api("POST", "/api/resume" if paused else "/api/pause")["message"])

    def limits(self) -> None:
        q = api("GET", "/api/settings")["values"]["queue"]
        a = IntPrompt.ask("Downloads at once", default=q["max_active"])
        d = IntPrompt.ask("Films grabbed per day (0 = no limit)", default=q["max_per_day"])
        api("PATCH", "/api/settings", {"queue": {"max_active": max(a, 1), "max_per_day": max(d, 0)}})
        self.c.print("Saved.")

    def groups(self) -> None:
        g = api("GET", "/api/settings")["values"]["queue"]["groups"]
        for k, label in {**GROUPS, "tiny": "tiny x265 files (swapped for bigger ones: uses more space)"}.items():
            g[k] = Confirm.ask(f"Go after {label}?", default=g.get(k, False))
        api("PATCH", "/api/settings", {"queue": {"groups": g}})
        self.c.print("Saved.")

    def window(self) -> None:
        w = api("GET", "/api/settings")["values"]["queue"]["window"]
        w["enabled"] = Confirm.ask("Only queue during set days and hours?", default=w["enabled"])
        if w["enabled"]:
            cur = ",".join(DAYS[d] for d in w["days"])
            days = Prompt.ask("Days (e.g. Mon,Tue or all)", default=cur)
            w["days"] = list(range(7)) if days.strip().lower() == "all" else [DAYS.index(d.strip().title()[:3]) for d in days.split(",") if d.strip().title()[:3] in DAYS]
            w["start"] = Prompt.ask("From (HH:MM)", default=w["start"])
            w["end"] = Prompt.ask("Until (HH:MM, can run past midnight)", default=w["end"])
        api("PATCH", "/api/settings", {"queue": {"window": w}})
        self.c.print("Saved.")

    def failed(self) -> None:
        f = api("GET", "/api/status")["failed"]
        if not f:
            self.c.print("[grey62]Every film tried so far was replaced or is still in progress.[/]")
            return
        t = Table(box=box.ROUNDED, border_style=C["pink"], header_style=C["dim"])
        for col in ("#", "film", "status", "why"):
            t.add_column(col)
        for i, x in enumerate(f, 1):
            t.add_row(str(i), f"{x['title']} ({x['year']})", x["status"], (x["problem"] or "")[:70])
        self.c.print(t)
        pick = Prompt.ask("Retry which (numbers, 'all', or blank for none)", default="")
        ids = [x["id"] for x in f] if pick.strip().lower() == "all" else \
              [f[int(n) - 1]["id"] for n in pick.replace(" ", "").split(",") if n.isdigit() and 0 < int(n) <= len(f)]
        if ids:
            done = api("POST", "/api/retry", {"ids": [int(i) for i in ids]})["retried"]
            self.c.print(f"{len(done)} film(s) will be tried again.")

    def oversized(self) -> None:
        s = api("GET", "/api/status")["scan"]
        if not s["summary"]:
            self.c.print("[grey62]No scan yet: choose 'Rescan the library'.[/]")
            return
        t = Table(box=box.ROUNDED, border_style=C["mint"], header_style=C["dim"], title=f"library scan {s['at'].replace('T', ' ')}")
        for col in ("group", "films", "space", "to win"):
            t.add_column(col, justify="right" if col != "group" else "left")
        for k, label in {**GROUPS, "tiny": "Tiny x265 (need bigger)"}.items():
            v = s["summary"].get(k)
            if v:
                t.add_row(label, str(v["films"]), f"{v['gb'] / 1000:.1f} TB", "-" if k == "tiny" else f"{v['saving_gb'] / 1000:.1f} TB")
        self.c.print(t)

    def radarr_setup(self) -> None:
        values = api("GET", "/api/settings")["values"]
        c = values["connections"]
        self.c.print(f"Radarr: [bold]{c['radarr_url'] or values['env']['radarr_url']}[/]   API key: "
                     f"{'[#22f5c8]set[/]' if values['secrets_set']['radarr_api_key'] else '[#ff2bd6]not set[/]'}")
        if Confirm.ask("Change the Radarr connection?", default=False):
            url = Prompt.ask("Address", default=c["radarr_url"] or values["env"]["radarr_url"])
            key = Prompt.ask("API key (blank keeps the saved one)", default="", password=True, show_default=False)
            t = api("POST", "/api/connections/test", {"radarr_url": url, "radarr_api_key": key})
            self.c.print(("[#22f5c8]" if t["ok"] else "[#ff2bd6]") + t["message"] + "[/]")
            if t["ok"]:
                api("PATCH", "/api/settings", {"connections": {"radarr_url": url, "radarr_api_key": key}})
                self.c.print("Saved.")
        checks = api("GET", "/api/setup")["checks"]
        t = Table(box=box.ROUNDED, border_style=C["violet"], header_style=C["dim"], title="Radarr setup")
        t.add_column("", width=2)
        t.add_column("check")
        t.add_column("detail", style=C["dim"])
        for x in checks:
            icon = "[#22f5c8]✓[/]" if x["ok"] else ("[#ffb020]![/]" if x["level"] == "warn" else "[#ff2bd6]✕[/]")
            t.add_row(icon, x["label"], x["detail"] + (f"\n[#22d3ee]Fix: {x['fix']}[/]" if x["fix"] else ""))
        self.c.print(t)
        fixes = [x for x in checks if x["fix"]]
        if fixes and Confirm.ask(f"Apply the {len(fixes)} fix(es) listed? This changes Radarr's settings", default=False):
            r = api("POST", "/api/setup/fix", {"ids": [x["id"] for x in fixes]})
            self.c.print("\n".join(f"[#22f5c8]✓[/] {d}" for d in r["done"]) or "Nothing needed changing.")

    def rules(self) -> None:
        r = api("GET", "/api/settings")["values"]["rules"]
        r["min_seeders"] = IntPrompt.ask("Minimum seeders", default=r["min_seeders"])
        r["max_ratio"] = float(Prompt.ask("New file at most this share of the old one (0.1–1)", default=str(r["max_ratio"])))
        r["min_1080"] = IntPrompt.ask("Smallest 1080p replacement, MB per minute", default=r["min_1080"])
        r["min_2160"] = IntPrompt.ask("Smallest 4K replacement, MB per minute", default=r["min_2160"])
        api("PATCH", "/api/settings", {"rules": r})
        self.c.print("Saved.")


MENU = [
    ("Dashboard (live)", "live"), ("Queue the next N biggest wins", "queue_count"), ("Queue particular films", "queue_pick"),
    ("Auto-queue on / off", "toggle_auto"), ("Pause / resume", "pause_resume"), ("Limits: at once and per day", "limits"),
    ("Which kinds of win to go after", "groups"), ("Schedule (days and hours)", "window"), ("Films not replaced: retry", "failed"),
    ("Oversized films in the library", "oversized"), ("Rescan the library", "scan"), ("Rules: seeders and sizes", "rules"),
    ("Radarr connection and setup", "radarr_setup"),
]


def menu() -> None:
    t = Tui()
    t.c.print(banner(t.c.width))
    while True:
        try:
            t.dashboard()
            grid = Table(box=None, show_header=False, padding=(0, 2), expand=True)
            grid.add_column(style=f"bold {C['cyan']}", justify="right")
            grid.add_column()
            grid.add_column(style=f"bold {C['cyan']}", justify="right")
            grid.add_column()
            half = (len(MENU) + 1) // 2
            for i in range(half):
                left = (str(i + 1), MENU[i][0])
                right = (str(i + 1 + half), MENU[i + half][0]) if i + half < len(MENU) else ("", "")
                grid.add_row(*left, *right)
            grid.add_row("q", "Quit", "", "")
            t.c.print(Panel(grid, title="[bold]what next[/]", border_style=C["blue"], box=box.ROUNDED))
            choice = Prompt.ask("Choose", default="1").strip().lower()
            if choice in ("q", "quit", "exit"):
                return
            if choice.isdigit() and 0 < int(choice) <= len(MENU):
                action = MENU[int(choice) - 1][1]
                if action == "scan":
                    if Confirm.ask("Rescan the whole library? It reads every film from Radarr and can take some time", default=False):
                        t.c.print(api("POST", "/api/scan")["message"])
                else:
                    getattr(t, action)()
                if action != "live":
                    Prompt.ask("[grey62]Enter to go back[/]", default="", show_default=False)
        except SystemExit as e:  # the server said no, or isn't reachable: show it and stay in the menu
            t.c.print(f"[bold red]{e}[/]")
            Prompt.ask("[grey62]Enter to go back[/]", default="", show_default=False)
        except (KeyboardInterrupt, EOFError):
            t.c.print()
            return
