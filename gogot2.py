"""
Fortnite Tracker PRO  (Windows, bez dalších knihoven - jen Python + tkinter)

Co umí:
  * Globální klávesy (fungují i ve hře, nic neblokují) - lze je přemapovat
      výchozí:  F1 = +1 chestka   F2 = +1 hra   F3 = start/pauza času   F4 = vrátit poslední akci
  * Sessions: každé hraní se ukládá do historie (datum, čas, chestky, hry, chestky/hod)
  * Měření času jednotlivých her (průměr, poslední, nejdelší, nejkratší)
  * Denní cíl chestek s progress barem, série dnů se splněným cílem
  * Statistiky za všechny sessions + graf posledních 7 dní
  * Plovoucí overlay (bez rámečku, vždy nahoře, průhledný, přesouvatelný)
  * Automatická detekce spuštěného Fortnite (auto start/stop času)
  * Zvuky, vždy nahoře, export do CSV, bezpečné ukládání se zálohou
"""

import csv
import json
import os
import secrets
import shutil
import socket
import subprocess
import threading
import time
import tkinter as tk
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

APP_TITLE = "Fortnite Tracker PRO"
DATA_FILE = Path(__file__).with_name("fortnite_tracker_data.json")
FORTNITE_EXE = "FortniteClient-Win64-Shipping.exe"
IS_WINDOWS = os.name == "nt"

if IS_WINDOWS:
    import ctypes
    import winsound

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    user32.GetAsyncKeyState.restype = ctypes.c_short
    from ctypes import wintypes
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongW.restype = ctypes.c_long
    user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
    user32.SetWindowLongW.restype = ctypes.c_long
    try:  # ostré písmo na HiDPI monitorech
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000


def set_click_through(win, enabled):
    """Průklepné okno: myš (i crosshair ve hře) jím projde a nebere focus."""
    if not IS_WINDOWS:
        return
    try:
        win.update_idletasks()
        wid = win.winfo_id()
        hwnd = user32.GetAncestor(wid, 2) or wid  # GA_ROOT
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        style |= WS_EX_LAYERED | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW
        style = (style | WS_EX_TRANSPARENT) if enabled else (style & ~WS_EX_TRANSPARENT)
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
    except Exception:
        pass


# ----------------------------------------------------------------------------
# Barvy
# ----------------------------------------------------------------------------
BG = "#0e1222"
PANEL = "#1a2140"
PANEL_2 = "#283456"
BORDER = "#303d68"
TEXT = "#f3f5ff"
MUTED = "#9aa6cb"
PURPLE = "#7185ff"
GOLD = "#f4d16b"
BLUE = "#8fc2ff"
GREEN = "#36c99a"
RED = "#e0627e"

WEEKDAYS = ["Po", "Út", "St", "Čt", "Pá", "So", "Ne"]

DEFAULT_SETTINGS = {
    "hotkeys": {"chest": 0x70, "game": 0x71, "timer": 0x72, "undo": 0x73,
                "overlay": 0x74, "lock": 0x75},
    "always_on_top": False,
    "sound": True,
    "auto_timer": False,
    "require_fortnite": False,
    "daily_goal": 50,
    "overlay_alpha": 85,
    "overlay_pos": [40, 40],
    "chest_overlay_pos": [40, 170],
    "click_through": True,
    "net_name": "Hráč",
    "net_key": "",
    "net_mode": "host",
    "net_host": "",
    "net_server": "",
    "net_port": 47600,
    "net_interval": 30,
    "net_auto": False,
}

ACTION_NAMES = {
    "chest": "Přidat chestku",
    "game": "Přidat odehranou hru",
    "timer": "Spustit / pozastavit čas",
    "undo": "Vrátit poslední akci",
    "overlay": "Zobrazit / skrýt chest overlay",
    "lock": "Zamknout / odemknout overlay (průklepný)",
}

# typ -> (kód události, klíč základní hodnoty)
KIND = {"chest": ("c", "base_chests"), "game": ("g", "base_games")}

# klávesy, které nelze přiřadit (myš, modifikátory, Win)
IGNORED_VKS = {0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x10, 0x11, 0x12,
               0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0x5B, 0x5C}


# ----------------------------------------------------------------------------
# Pomocné funkce
# ----------------------------------------------------------------------------
def lighten(hex_color, amount=0.12):
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    r, g, b = (min(255, int(c + (255 - c) * amount)) for c in (r, g, b))
    return f"#{r:02x}{g:02x}{b:02x}"


def fmt_time(seconds):
    total = max(0, int(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def fmt_short(seconds):
    if seconds is None:
        return "—"
    total = max(0, int(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def fmt_rate(value):
    return "—" if value is None else f"{value:.1f}"


def vk_name(vk):
    if 0x70 <= vk <= 0x87:
        return f"F{vk - 0x6F}"
    if 0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A:
        return chr(vk)
    if 0x60 <= vk <= 0x69:
        return f"Num{vk - 0x60}"
    names = {
        0x20: "Space", 0x09: "Tab", 0x0D: "Enter", 0x08: "Backspace",
        0x2D: "Insert", 0x2E: "Delete", 0x24: "Home", 0x23: "End",
        0x21: "PageUp", 0x22: "PageDown", 0x26: "↑", 0x28: "↓",
        0x25: "←", 0x27: "→", 0x6A: "Num*", 0x6B: "Num+", 0x6D: "Num-",
        0x6E: "Num.", 0x6F: "Num/", 0xC0: "`", 0x14: "CapsLock",
        0x13: "Pause", 0x90: "NumLock", 0x91: "ScrollLock",
    }
    return names.get(vk, f"VK_{vk:02X}")


def new_session():
    return {
        "id": int(time.time() * 1000),
        "start_ts": time.time(),
        "end_ts": None,
        "elapsed": 0.0,
        "origin": 0.0,        # čas, od kterého se měří první hra
        "base_chests": 0,     # počty převzaté ze staré verze
        "base_games": 0,
        "events": [],         # [kód, uplynulý herní čas, reálný čas]
    }


def count(session, kind):
    code, base = KIND[kind]
    return session.get(base, 0) + sum(1 for e in session["events"] if e[0] == code)


def game_durations(session):
    prev = session.get("origin", 0.0)
    out = []
    for e in session["events"]:
        if e[0] == "g":
            d = e[1] - prev
            prev = e[1]
            if d >= 1:
                out.append(d)
    return out


def day_totals(sessions):
    """Vrátí {datum: (chestky, hry)} podle skutečného času událostí."""
    out = {}
    for s in sessions:
        d0 = datetime.fromtimestamp(s["start_ts"]).date()
        c, g = out.get(d0, (0, 0))
        out[d0] = (c + s.get("base_chests", 0), g + s.get("base_games", 0))
        for e in s["events"]:
            d = datetime.fromtimestamp(e[2]).date()
            c, g = out.get(d, (0, 0))
            out[d] = (c + 1, g) if e[0] == "c" else (c, g + 1)
    return out


def sanitize_session(raw):
    s = new_session()
    try:
        s["id"] = int(raw.get("id", s["id"]))
        s["start_ts"] = float(raw.get("start_ts", s["start_ts"]))
        end = raw.get("end_ts")
        s["end_ts"] = float(end) if end else None
        s["elapsed"] = max(0.0, float(raw.get("elapsed", 0)))
        s["origin"] = max(0.0, float(raw.get("origin", 0)))
        s["base_chests"] = max(0, int(raw.get("base_chests", 0)))
        s["base_games"] = max(0, int(raw.get("base_games", 0)))
        events = []
        for e in raw.get("events", []):
            if e[0] in ("c", "g"):
                events.append([e[0], float(e[1]), float(e[2])])
        s["events"] = events
    except (TypeError, ValueError, IndexError, AttributeError):
        pass
    return s


# ----------------------------------------------------------------------------
# Synchronizace s kamarády
# ----------------------------------------------------------------------------
class SyncManager:
    """Synchronizace počtu chestek mezi hráči přes TCP (JSON po řádcích).

    Jeden hráč hostuje, ostatní se připojí. Každých N sekund každý pošle své
    chestky a host rozešle všem přehled (roster).
    """

    def __init__(self, app):
        self.app = app
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.players = {}      # jméno -> [chestky, čas poslední aktualizace]
        self.clients = set()
        self.server = None
        self.sock = None
        self.cfg = {}
        self.mode = "off"
        self.status = "Odpojeno"
        self.linked = False

    # --- ovládání
    def start(self, cfg):
        self.stop()
        self.cfg = dict(cfg)
        self.mode = cfg["mode"]
        self.stop_event = threading.Event()
        self.status = "Spouštím…"
        target = {"host": self.run_host, "relay": self.run_relay}.get(self.mode, self.run_client)
        threading.Thread(target=target, args=(self.stop_event,), daemon=True).start()

    def stop(self):
        self.stop_event.set()
        with self.lock:
            for c in list(self.clients):
                self.close(c)
            self.clients.clear()
            self.players = {}
        self.close(self.server)
        self.close(self.sock)
        self.server = self.sock = None
        self.mode = "off"
        self.linked = False
        self.status = "Odpojeno"

    @staticmethod
    def close(sock):
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    @staticmethod
    def send(sock, msg):
        sock.sendall((json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8"))

    def set_status(self, ev, text):
        if not ev.is_set():
            self.status = text

    # --- dotazy z UI
    def connected(self):
        return bool(self.clients) if self.mode == "host" else self.linked

    def status_text(self):
        return self.status

    def friends(self):
        limit = self.cfg.get("interval", 30) * 3 + 15
        now = time.time()
        with self.lock:
            items = [(n, c) for n, (c, seen) in self.players.items()
                     if now - seen <= limit]
        return sorted(items)

    def roster_msg(self):
        with self.lock:
            roster = {n: c for n, (c, _) in self.players.items()}
        roster[self.cfg["name"]] = self.app.my_chests
        return {"t": "roster", "players": roster}

    def stats_msg(self):
        return {"t": "stats", "name": self.cfg["name"], "key": self.cfg["key"],
                "chests": self.app.my_chests}

    # --- host
    def run_host(self, ev):
        port = self.cfg["port"]
        try:
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind(("", port))
            srv.listen(8)
            srv.settimeout(1.0)
        except OSError as e:
            if not ev.is_set():
                self.status = f"Chyba: port {port} nelze otevřít ({e.strerror or e})"
                self.mode = "off"
            return
        self.server = srv
        self.mode = "host"
        threading.Thread(target=self.broadcast_loop, args=(ev,), daemon=True).start()
        while not ev.is_set():
            self.set_status(ev, f"Hostuji na portu {port} • připojeno kamarádů: {len(self.clients)}")
            try:
                conn, _ = srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            conn.settimeout(self.cfg["interval"] * 3 + 10)
            with self.lock:
                self.clients.add(conn)
            threading.Thread(target=self.handle_client, args=(conn, ev), daemon=True).start()
        self.close(srv)

    def handle_client(self, conn, ev):
        name = None
        try:
            f = conn.makefile("r", encoding="utf-8", newline="\n")
            while not ev.is_set():
                line = f.readline(4096)
                if not line:
                    break
                msg = json.loads(line)
                if msg.get("key", "") != self.cfg["key"]:
                    self.send(conn, {"t": "error", "text": "Špatný kód místnosti"})
                    break
                if msg.get("t") == "stats":
                    name = str(msg.get("name", "?"))[:24]
                    chests = max(0, int(msg.get("chests", 0)))
                    with self.lock:
                        if name in self.players or len(self.players) < 10:
                            self.players[name] = [chests, time.time()]
                    self.send(conn, self.roster_msg())
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        finally:
            with self.lock:
                self.clients.discard(conn)
                if name:
                    self.players.pop(name, None)
            self.close(conn)

    def broadcast_loop(self, ev):
        while not ev.wait(self.cfg["interval"]):
            msg = self.roster_msg()
            with self.lock:
                targets = list(self.clients)
            for c in targets:
                try:
                    self.send(c, msg)
                except OSError:
                    with self.lock:
                        self.clients.discard(c)
                    self.close(c)

    # --- klient
    def run_client(self, ev):
        host, port = self.cfg["host"], self.cfg["port"]
        me = self.cfg["name"]
        self.mode = "client"
        while not ev.is_set():
            sock = None
            try:
                self.set_status(ev, f"Připojuji se k {host}:{port}…")
                sock = socket.create_connection((host, port), timeout=6)
                sock.settimeout(self.cfg["interval"] * 3 + 10)
                self.sock = sock
                self.send(sock, self.stats_msg())
                threading.Thread(target=self.client_sender, args=(sock, ev), daemon=True).start()
                f = sock.makefile("r", encoding="utf-8", newline="\n")
                while not ev.is_set():
                    line = f.readline(65536)
                    if not line:
                        break
                    msg = json.loads(line)
                    if msg.get("t") == "roster":
                        now = time.time()
                        roster = {str(n)[:24]: [max(0, int(c)), now]
                                  for n, c in msg["players"].items() if n != me}
                        with self.lock:
                            self.players = roster
                        self.linked = True
                        self.set_status(ev, f"Připojeno k {host}:{port}")
                    elif msg.get("t") == "error":
                        self.set_status(ev, f"Hostitel odmítl: {msg.get('text', '?')}")
            except (OSError, ValueError, TypeError, AttributeError):
                pass
            finally:
                self.close(sock)
                self.linked = False
                with self.lock:
                    self.players = {}
            if ev.is_set():
                break
            if not self.status.startswith("Hostitel odmítl"):
                self.set_status(ev, "Odpojeno - zkouším znovu…")
            ev.wait(5)

    # --- přes relay server (kód místnosti)
    def run_relay(self, ev):
        self.mode = "relay"
        url = self.cfg["server"].rstrip("/") + "/sync"
        me = self.cfg["name"]
        limit = self.cfg["interval"] * 3 + 15
        while not ev.is_set():
            try:
                body = json.dumps({"room": self.cfg["key"], "name": me,
                                   "chests": self.app.my_chests}).encode("utf-8")
                req = urllib.request.Request(
                    url, data=body, method="POST",
                    headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=45) as resp:
                    data = json.loads(resp.read(65536).decode("utf-8"))
                now = time.time()
                players = {str(n)[:24]: [max(0, int(v["c"])), now - float(v["age"])]
                           for n, v in data["players"].items()
                           if n != me and float(v["age"]) <= limit}
                with self.lock:
                    self.players = players
                self.linked = True
                self.set_status(ev, f"Připojeno přes server • kamarádů online: {len(players)}")
            except urllib.error.HTTPError as e:
                self.linked = False
                self.set_status(ev, f"Server odmítl požadavek (kód {e.code})")
            except (OSError, ValueError, TypeError, KeyError, AttributeError):
                self.linked = False
                self.set_status(ev, "Server nedostupný - zkouším znovu…")
            ev.wait(self.cfg["interval"])

    def client_sender(self, sock, ev):
        while not ev.wait(self.cfg["interval"]):
            try:
                self.send(sock, self.stats_msg())
            except OSError:
                break


# ----------------------------------------------------------------------------
# Aplikace
# ----------------------------------------------------------------------------
class FortniteTracker:
    def __init__(self, root):
        self.root = root
        root.title(APP_TITLE)
        root.geometry("900x860")
        root.minsize(780, 740)
        root.configure(bg=BG)

        self.settings = json.loads(json.dumps(DEFAULT_SETTINGS))
        self.sessions = []          # archivované sessions
        self.session = new_session()  # aktuální session
        self.started_at = None      # monotonic čas spuštění časovače
        self.key_state = {}
        self.capturing = None
        self.capture_ignore = set()
        self.fortnite_running = False
        self.prev_fortnite = False
        self.stop_event = threading.Event()
        self.save_job = None
        self.toast_job = None
        self.backed_up = False
        self.tick_count = 0
        self.overlay = None
        self.chest_overlay = None
        self.my_chests = 0
        self.chest_img = None
        self.current_page = None
        self.sync = SyncManager(self)

        self.load_data()
        self.setup_styles()
        try:
            self.chest_img = tk.PhotoImage(data=CHEST_PNG_B64)
        except tk.TclError:
            self.chest_img = None
        self.build_ui()
        self.apply_window_settings()
        self.show_page("dashboard")
        if self.settings["net_auto"] and (self.settings["net_mode"] == "host"
                                          or self.settings["net_host"]
                                          or self.settings["net_server"]):
            self.sync.start(self.net_cfg())

        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.refresh_dashboard()
        self.poll_keys()
        self.tick()
        threading.Thread(target=self.detect_loop, daemon=True).start()

    # ------------------------------------------------------------------ data
    def load_data(self):
        if not DATA_FILE.exists():
            return
        try:
            data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
            if "version" not in data:
                self.migrate_v1(data)
                return
            saved = data.get("settings", {})
            for key, default in DEFAULT_SETTINGS.items():
                if key == "hotkeys":
                    hk = dict(default)
                    for action, vk in saved.get("hotkeys", {}).items():
                        if action in hk and isinstance(vk, int) and 8 <= vk < 255:
                            hk[action] = vk
                    self.settings["hotkeys"] = hk
                elif key in saved and isinstance(saved[key], type(default)):
                    self.settings[key] = saved[key]
            self.sessions = [sanitize_session(s) for s in data.get("sessions", [])]
            if data.get("current"):
                self.session = sanitize_session(data["current"])
        except (OSError, ValueError, TypeError, AttributeError):
            try:  # poškozený soubor nepřepíšeme bez zálohy
                shutil.copy2(DATA_FILE, DATA_FILE.with_suffix(".corrupt.json"))
            except OSError:
                pass

    def migrate_v1(self, data):
        """Převod dat ze staré verze trackeru."""
        s = new_session()
        s["base_chests"] = max(0, int(data.get("chests", 0)))
        s["base_games"] = max(0, int(data.get("games", 0)))
        s["elapsed"] = max(0.0, float(data.get("elapsed_seconds", 0)))
        s["origin"] = s["elapsed"]
        self.session = s

    def snapshot(self):
        cur = json.loads(json.dumps(self.session))
        cur["elapsed"] = self.current_elapsed()
        return {
            "version": 2,
            "settings": self.settings,
            "sessions": self.sessions,
            "current": cur,
        }

    def save_now(self):
        self.save_job = None
        try:
            if DATA_FILE.exists() and not self.backed_up:
                shutil.copy2(DATA_FILE, DATA_FILE.with_suffix(".bak.json"))
                self.backed_up = True
            tmp = DATA_FILE.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(self.snapshot(), ensure_ascii=False, indent=1),
                encoding="utf-8",
            )
            os.replace(tmp, DATA_FILE)
            self.save_label.config(
                text=f"✓ Uloženo {datetime.now():%H:%M:%S}", fg=MUTED
            )
        except OSError:
            self.save_label.config(text="⚠ Nepodařilo se uložit data", fg=RED)

    def mark_dirty(self):
        if self.save_job is None:
            self.save_job = self.root.after(700, self.save_now)

    # --------------------------------------------------------------- časovač
    def current_elapsed(self):
        base = self.session["elapsed"]
        if self.started_at is None:
            return base
        return base + (time.monotonic() - self.started_at)

    def session_elapsed(self, s):
        return self.current_elapsed() if s is self.session else s["elapsed"]

    def start_timer(self, quiet=False):
        if self.started_at is None:
            self.started_at = time.monotonic()
            if not quiet:
                self.toast("▶ Čas spuštěn", GREEN)
                self.beep("timer_on")
            self.refresh_dashboard()
            self.mark_dirty()

    def pause_timer(self, quiet=False):
        if self.started_at is not None:
            self.session["elapsed"] = self.current_elapsed()
            self.started_at = None
            if not quiet:
                self.toast("⏸ Čas pozastaven", GOLD)
                self.beep("timer_off")
            self.refresh_dashboard()
            self.mark_dirty()

    def toggle_timer(self):
        if self.started_at is None:
            self.start_timer()
        else:
            self.pause_timer()

    # ----------------------------------------------------------------- akce
    def add(self, kind):
        s = self.session
        s["events"].append(
            [KIND[kind][0], round(self.current_elapsed(), 2), time.time()]
        )
        if kind == "chest":
            self.toast(f"📦 +1 chestka  (celkem {count(s, 'chest')})", GOLD)
            self.beep("chest")
        else:
            durs = game_durations(s)
            extra = f"  •  hra trvala {fmt_short(durs[-1])}" if durs else ""
            self.toast(f"🎮 +1 hra  (celkem {count(s, 'game')}){extra}", BLUE)
            self.beep("game")
        self.refresh_dashboard()
        self.mark_dirty()
        if kind == "chest":
            goal = max(1, self.settings["daily_goal"])
            if self.today_chests() == goal:
                self.toast(f"🎉 Denní cíl {goal} chestek splněn!", GREEN)
                self.beep("goal")

    def remove(self, kind):
        s = self.session
        code, base = KIND[kind]
        for i in range(len(s["events"]) - 1, -1, -1):
            if s["events"][i][0] == code:
                s["events"].pop(i)
                break
        else:
            if s.get(base, 0) > 0:
                s[base] -= 1
            else:
                return
        self.toast("− odebráno", MUTED)
        self.beep("undo")
        self.refresh_dashboard()
        self.mark_dirty()

    def undo(self):
        if not self.session["events"]:
            self.toast("Není co vrátit", MUTED)
            return
        e = self.session["events"].pop()
        what = "chestka" if e[0] == "c" else "hra"
        self.toast(f"↶ Vráceno: {what}", MUTED)
        self.beep("undo")
        self.refresh_dashboard()
        self.mark_dirty()

    def run_action(self, action):
        if action == "chest":
            self.add("chest")
        elif action == "game":
            self.add("game")
        elif action == "timer":
            self.toggle_timer()
        elif action == "undo":
            self.undo()
        elif action == "overlay":
            self.toggle_chest_overlay()
        elif action == "lock":
            self.toggle_lock()

    def end_session(self):
        s = self.session
        has_data = (self.current_elapsed() >= 1 or count(s, "chest")
                    or count(s, "game"))
        if not has_data:
            self.toast("Session je prázdná", MUTED)
            return
        self.pause_timer(quiet=True)
        s["end_ts"] = time.time()
        self.sessions.append(s)
        self.session = new_session()
        self.toast("✔ Session uložena do historie", GREEN)
        self.refresh_dashboard()
        self.refresh_history()
        self.save_now()

    def discard_session(self):
        if messagebox.askyesno(
            "Zahodit session",
            "Opravdu zahodit aktuální session (chestky, hry i čas)?\n"
            "Historie zůstane zachována.",
        ):
            self.started_at = None
            self.session = new_session()
            self.refresh_dashboard()
            self.save_now()

    # --------------------------------------------------------------- statistiky
    def all_sessions(self):
        return self.sessions + [self.session]

    def today_chests(self):
        return day_totals(self.all_sessions()).get(date.today(), (0, 0))[0]

    def goal_streak(self):
        totals = day_totals(self.all_sessions())
        goal = max(1, self.settings["daily_goal"])
        day = date.today()
        if totals.get(day, (0, 0))[0] < goal:
            day -= timedelta(days=1)
        streak = 0
        while totals.get(day, (0, 0))[0] >= goal:
            streak += 1
            day -= timedelta(days=1)
        return streak

    # -------------------------------------------------------------- zvuk/toast
    def beep(self, kind):
        if not (IS_WINDOWS and self.settings["sound"]):
            return
        seqs = {
            "chest": [(990, 70)], "game": [(660, 70), (880, 90)],
            "timer_on": [(520, 70), (780, 90)], "timer_off": [(780, 70), (520, 90)],
            "undo": [(400, 90)],
            "goal": [(660, 110), (880, 110), (1180, 220)],
        }

        def play():
            try:
                for freq, dur in seqs[kind]:
                    winsound.Beep(freq, dur)
            except Exception:
                pass

        threading.Thread(target=play, daemon=True).start()

    def toast(self, text, color=TEXT):
        self.toast_label.config(text=text, fg=color)
        if self.toast_job:
            self.root.after_cancel(self.toast_job)
        self.toast_job = self.root.after(3500, self.reset_toast)

    def reset_toast(self):
        self.toast_job = None
        self.toast_label.config(
            text="Globální zkratky fungují i během hraní Fortnite.", fg=MUTED
        )

    # -------------------------------------------------------- klávesy a detekce
    def key_is_down(self, vk):
        if not IS_WINDOWS:
            return False
        return bool(user32.GetAsyncKeyState(vk) & 0x8000)

    def poll_keys(self):
        try:
            if IS_WINDOWS:
                if self.capturing:
                    self.poll_capture()
                else:
                    allowed = (not self.settings["require_fortnite"]
                               or self.fortnite_running)
                    for action, vk in self.settings["hotkeys"].items():
                        down = self.key_is_down(vk)
                        if down and not self.key_state.get(action, False) and allowed:
                            self.run_action(action)
                        self.key_state[action] = down
        finally:
            self.root.after(30, self.poll_keys)

    def start_capture(self, action):
        if not IS_WINDOWS:
            messagebox.showinfo(APP_TITLE, "Přemapování kláves funguje jen ve Windows.")
            return
        self.capturing = action
        self.capture_ignore = {vk for vk in range(8, 255) if self.key_is_down(vk)}
        for a, btn in self.hotkey_buttons.items():
            btn.config(text="Stiskni klávesu… (Esc = zrušit)" if a == action
                       else vk_name(self.settings["hotkeys"][a]))

    def poll_capture(self):
        for vk in range(8, 255):
            if vk in IGNORED_VKS or vk in self.capture_ignore:
                continue
            if self.key_is_down(vk):
                action = self.capturing
                self.capturing = None
                if vk != 0x1B:
                    clash = [a for a, v in self.settings["hotkeys"].items()
                             if v == vk and a != action]
                    if clash:
                        self.toast(
                            f"Klávesa {vk_name(vk)} už je použitá "
                            f"({ACTION_NAMES[clash[0]]})", RED)
                    else:
                        self.settings["hotkeys"][action] = vk
                        self.mark_dirty()
                self.key_state = {a: self.key_is_down(v)
                                  for a, v in self.settings["hotkeys"].items()}
                self.refresh_hotkey_texts()
                return

    def detect_loop(self):
        while not self.stop_event.is_set():
            self.fortnite_running = self.check_fortnite()
            self.stop_event.wait(4)

    @staticmethod
    def check_fortnite():
        if not IS_WINDOWS:
            return False
        try:
            out = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {FORTNITE_EXE}", "/NH"],
                capture_output=True, text=True, timeout=6,
                creationflags=0x08000000,
            ).stdout
            return FORTNITE_EXE.lower() in out.lower()
        except Exception:
            return False

    # -------------------------------------------------------------- hlavní smyčka
    def tick(self):
        self.tick_count += 1
        self.timer_label.config(text=fmt_time(self.current_elapsed()))

        running = self.fortnite_running
        if running != self.prev_fortnite:
            self.prev_fortnite = running
            if self.settings["auto_timer"]:
                if running:
                    self.start_timer(quiet=True)
                    self.toast("Fortnite spuštěn - čas běží automaticky", GREEN)
                else:
                    self.pause_timer(quiet=True)
                    self.toast("Fortnite zavřen - čas pozastaven", GOLD)
        if IS_WINDOWS:
            self.fn_label.config(
                text="● Fortnite běží" if running else "○ Fortnite neběží",
                fg=GREEN if running else MUTED)
        else:
            self.fn_label.config(text="Detekce jen ve Windows", fg=MUTED)

        self.update_overlay()
        self.update_chest_overlay()
        if self.tick_count % 8 == 0:
            self.keep_overlays_on_top()
        if self.tick_count % 4 == 0:
            self.refresh_dashboard()
            if self.current_page == "stats":
                self.refresh_stats()
            elif self.current_page == "friends":
                self.refresh_friends()
        if self.tick_count % 120 == 0 and self.started_at is not None:
            self.save_now()
        self.root.after(250, self.tick)

    # --------------------------------------------------------------------- UI
    def setup_styles(self):
        st = ttk.Style()
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        st.configure("Treeview", background=PANEL, fieldbackground=PANEL,
                     foreground=TEXT, rowheight=27, borderwidth=0,
                     font=("Segoe UI", 9))
        st.configure("Treeview.Heading", background=PANEL_2, foreground=TEXT,
                     font=("Segoe UI", 9, "bold"), relief="flat")
        st.map("Treeview", background=[("selected", PURPLE)])
        st.map("Treeview.Heading", background=[("active", PANEL_2)])

    def label(self, parent, text="", size=10, bold=False, fg=TEXT, bg=PANEL, **kw):
        return tk.Label(parent, text=text, bg=bg, fg=fg,
                        font=("Segoe UI", size, "bold" if bold else "normal"), **kw)

    def button(self, parent, text, command, color=PANEL_2, padx=13, pady=8, size=9):
        b = tk.Button(parent, text=text, command=command, bg=color, fg="white",
                      activebackground=lighten(color, 0.2), activeforeground="white",
                      relief="flat", bd=0, font=("Segoe UI", size, "bold"),
                      padx=padx, pady=pady, cursor="hand2")
        b.bind("<Enter>", lambda e: b.config(bg=lighten(color)))
        b.bind("<Leave>", lambda e: b.config(bg=color))
        return b

    def card(self, parent, padx=14, pady=12):
        return tk.Frame(parent, bg=PANEL, padx=padx, pady=pady,
                        highlightthickness=1, highlightbackground=BORDER)

    def build_ui(self):
        outer = tk.Frame(self.root, bg=BG, padx=22, pady=16)
        outer.pack(fill="both", expand=True)

        # hlavička
        header = tk.Frame(outer, bg=BG)
        header.pack(fill="x")
        left = tk.Frame(header, bg=BG)
        left.pack(side="left")
        self.label(left, "FORTNITE TRACKER PRO", 21, True, TEXT, BG).pack(anchor="w")
        self.toast_label = self.label(
            left, "Globální zkratky fungují i během hraní Fortnite.", 9, False, MUTED, BG)
        self.toast_label.pack(anchor="w", pady=(2, 0))

        right = tk.Frame(header, bg=BG)
        right.pack(side="right")
        self.button(right, "📦 Chest overlay", self.toggle_chest_overlay, PANEL_2,
                    padx=10, pady=6).grid(row=0, column=0, padx=3, pady=2, sticky="ew")
        self.button(right, "▣ Info overlay", self.toggle_overlay, PANEL_2,
                    padx=10, pady=6).grid(row=0, column=1, padx=3, pady=2, sticky="ew")
        self.lock_button = self.button(right, "", self.toggle_lock, PANEL_2, padx=10, pady=6)
        self.lock_button.grid(row=1, column=0, padx=3, pady=2, sticky="ew")
        self.pin_button = self.button(right, "", self.toggle_pin, PANEL_2, padx=10, pady=6)
        self.pin_button.grid(row=1, column=1, padx=3, pady=2, sticky="ew")

        # navigace
        nav = tk.Frame(outer, bg=BG)
        nav.pack(fill="x", pady=(12, 10))
        self.nav_buttons = {}
        for key, title in (("dashboard", "Přehled"), ("stats", "Statistiky"),
                           ("friends", "Spolu"), ("history", "Historie"),
                           ("settings", "Nastavení")):
            b = tk.Button(nav, text=title, command=lambda k=key: self.show_page(k),
                          relief="flat", bd=0, font=("Segoe UI", 10, "bold"),
                          padx=18, pady=8, cursor="hand2")
            b.pack(side="left", padx=(0, 6))
            self.nav_buttons[key] = b

        # patička (před stránkami, aby zůstala vidět)
        footer = tk.Frame(outer, bg=BG)
        footer.pack(side="bottom", fill="x", pady=(8, 0))
        self.save_label = self.label(footer, "Data se ukládají automaticky.", 8, False, MUTED, BG)
        self.save_label.pack(side="left")
        self.button(footer, "Zahodit session", self.discard_session, "#5a2d44",
                    pady=6).pack(side="right")

        self.container = tk.Frame(outer, bg=BG)
        self.container.pack(fill="both", expand=True)
        self.pages = {
            "dashboard": self.build_dashboard(),
            "stats": self.build_stats(),
            "friends": self.build_friends(),
            "history": self.build_history(),
            "settings": self.build_settings(),
        }
        self.refresh_hotkey_texts()

    def show_page(self, name):
        self.current_page = name
        for key, page in self.pages.items():
            page.pack_forget()
        self.pages[name].pack(fill="both", expand=True)
        for key, b in self.nav_buttons.items():
            active = key == name
            b.config(bg=PURPLE if active else PANEL,
                     fg="white" if active else MUTED,
                     activebackground=PURPLE if active else PANEL_2,
                     activeforeground="white")
        if name == "stats":
            self.root.after(30, self.refresh_stats)
        elif name == "history":
            self.refresh_history()
        elif name == "friends":
            self.refresh_friends()

    # --- Přehled
    def build_dashboard(self):
        page = tk.Frame(self.container, bg=BG)

        timer = self.card(page, pady=12)
        timer.pack(fill="x", pady=(0, 10))
        self.label(timer, "ČAS HRANÍ SESSION", 9, True, MUTED).pack()
        self.timer_label = self.label(timer, "00:00:00", 36, True, TEXT)
        self.timer_label.pack()
        info = tk.Frame(timer, bg=PANEL)
        info.pack(pady=(0, 8))
        self.status_label = self.label(info, "● STOPNUTO", 9, True, MUTED)
        self.status_label.pack(side="left", padx=10)
        self.fn_label = self.label(info, "", 9, True, MUTED)
        self.fn_label.pack(side="left", padx=10)
        btns = tk.Frame(timer, bg=PANEL)
        btns.pack()
        self.timer_button = self.button(btns, "▶ Spustit", self.toggle_timer, GREEN, padx=20)
        self.timer_button.pack(side="left", padx=4)
        self.button(btns, "✔ Ukončit session", self.end_session, PURPLE).pack(side="left", padx=4)

        cards = tk.Frame(page, bg=BG)
        cards.pack(fill="x")
        cards.columnconfigure(0, weight=1)
        cards.columnconfigure(1, weight=1)
        self.hint_labels = {}
        self.value_labels = {}
        self.counter_card(cards, 0, "📦", "OTEVŘENÉ CHESTKY", "chest", GOLD)
        self.counter_card(cards, 1, "🎮", "ODEHRANÉ HRY", "game", BLUE)

        self.dash_tiles = {}
        tiles = tk.Frame(page, bg=BG)
        tiles.pack(fill="x", pady=(10, 0))
        for i, (key, title) in enumerate((
                ("cph", "Chestek / hod"), ("cpg", "Chestek / hru"),
                ("gph", "Her / hod"), ("avg", "Průměrná hra"),
                ("last", "Poslední hra"), ("longest", "Nejdelší hra"))):
            self.make_tile(tiles, self.dash_tiles, key, title, i // 3, i % 3, 3)

        goal = self.card(page, pady=10)
        goal.pack(fill="x", pady=(10, 0))
        top = tk.Frame(goal, bg=PANEL)
        top.pack(fill="x")
        self.goal_label = self.label(top, "", 10, True, TEXT)
        self.goal_label.pack(side="left")
        self.streak_label = self.label(top, "", 9, False, MUTED)
        self.streak_label.pack(side="right")
        self.goal_canvas = tk.Canvas(goal, height=14, bg=PANEL_2, bd=0,
                                     highlightthickness=0)
        self.goal_canvas.pack(fill="x", pady=(8, 0))
        self.goal_canvas.bind("<Configure>", lambda e: self.draw_goal())
        return page

    def counter_card(self, parent, column, emoji, title, kind, color):
        card = self.card(parent, pady=12)
        card.grid(row=0, column=column, sticky="nsew",
                  padx=(0, 6) if column == 0 else (6, 0))
        self.label(card, emoji, 22).pack()
        self.label(card, title, 10, True, TEXT).pack(pady=(2, 0))
        self.hint_labels[kind] = self.label(card, "", 8, False, MUTED)
        self.hint_labels[kind].pack()
        self.value_labels[kind] = self.label(card, "0", 40, True, color)
        self.value_labels[kind].pack(pady=4)
        row = tk.Frame(card, bg=PANEL)
        row.pack()
        self.button(row, "−", lambda: self.remove(kind), PANEL_2, padx=16).pack(side="left", padx=3)
        self.button(row, "+ Přidat", lambda: self.add(kind), PURPLE, padx=18).pack(side="left", padx=3)

    def make_tile(self, parent, store, key, title, row, col, ncols):
        parent.columnconfigure(col, weight=1)
        tile = self.card(parent, padx=10, pady=8)
        tile.grid(row=row, column=col, sticky="nsew",
                  padx=(0 if col == 0 else 4, 0 if col == ncols - 1 else 4),
                  pady=(0 if row == 0 else 8, 0))
        self.label(tile, title.upper(), 8, True, MUTED).pack()
        store[key] = self.label(tile, "—", 16, True, TEXT)
        store[key].pack()

    # --- Statistiky
    def build_stats(self):
        page = tk.Frame(self.container, bg=BG)
        tiles = tk.Frame(page, bg=BG)
        tiles.pack(fill="x")
        self.stat_tiles = {}
        items = (("chests", "Chestek celkem"), ("games", "Her celkem"),
                 ("time", "Čas celkem"), ("sessions", "Sessions"),
                 ("cph", "Chestek / hod"), ("best", "Nejlepší session"),
                 ("bestday", "Nejlepší den"), ("streak", "Série dnů s cílem"))
        for i, (key, title) in enumerate(items):
            self.make_tile(tiles, self.stat_tiles, key, title, i // 4, i % 4, 4)

        chart_card = self.card(page, pady=10)
        chart_card.pack(fill="both", expand=True, pady=(10, 0))
        self.label(chart_card, "POSLEDNÍCH 7 DNÍ", 9, True, MUTED).pack(anchor="w")
        self.chart = tk.Canvas(chart_card, bg=PANEL, bd=0, highlightthickness=0, height=260)
        self.chart.pack(fill="both", expand=True)
        self.chart.bind("<Configure>", lambda e: self.draw_chart())
        return page

    # --- Historie
    def build_history(self):
        page = tk.Frame(self.container, bg=BG)
        bar = tk.Frame(page, bg=BG)
        bar.pack(fill="x", pady=(0, 8))
        self.button(bar, "Smazat vybranou", self.delete_selected, "#5a2d44").pack(side="left")
        self.button(bar, "⭳ Export do CSV", self.export_csv, PURPLE).pack(side="right")

        wrap = tk.Frame(page, bg=BG)
        wrap.pack(fill="both", expand=True)
        cols = ("date", "dur", "chests", "games", "cph", "avg")
        heads = ("Datum", "Délka", "Chestky", "Hry", "Chestek/hod", "Průměr. hra")
        widths = (170, 100, 90, 80, 110, 110)
        self.tree = ttk.Treeview(wrap, columns=cols, show="headings", selectmode="extended")
        for c, h, w in zip(cols, heads, widths):
            self.tree.heading(c, text=h)
            self.tree.column(c, width=w, anchor="center")
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        return page

    # --- Nastavení
    def build_settings(self):
        page = tk.Frame(self.container, bg=BG)

        keys = self.card(page, pady=12)
        keys.pack(fill="x")
        self.label(keys, "KLÁVESOVÉ ZKRATKY", 10, True, TEXT).pack(anchor="w")
        self.label(keys, "Klikni na tlačítko a stiskni novou klávesu. Pozor na písmena - "
                         "budou počítat i při psaní. Doporučeno F1-F12.",
                   8, False, MUTED, wraplength=780, justify="left").pack(anchor="w", pady=(2, 8))
        self.hotkey_buttons = {}
        for action, name in ACTION_NAMES.items():
            row = tk.Frame(keys, bg=PANEL)
            row.pack(fill="x", pady=2)
            self.label(row, name, 10).pack(side="left")
            b = self.button(row, "", lambda a=action: self.start_capture(a), PANEL_2, pady=5)
            b.config(width=26)
            b.pack(side="right")
            self.hotkey_buttons[action] = b

        opts = self.card(page, pady=12)
        opts.pack(fill="x", pady=(10, 0))
        self.label(opts, "MOŽNOSTI", 10, True, TEXT).pack(anchor="w", pady=(0, 4))
        self.vars = {}
        for key, text in (
                ("sound", "Zvukové potvrzení akcí"),
                ("always_on_top", "Okno vždy nahoře"),
                ("click_through", "Overlay je průklepný (myš i crosshair jím ve hře projde)"),
                ("auto_timer", "Automaticky spouštět / zastavovat čas podle Fortnite"),
                ("require_fortnite", "Zkratky fungují jen když Fortnite běží (zabrání omylem při jiných hrách)")):
            var = tk.BooleanVar(value=self.settings[key])
            self.vars[key] = var
            tk.Checkbutton(opts, text=text, variable=var, command=self.on_settings_changed,
                           bg=PANEL, fg=TEXT, selectcolor=BG, activebackground=PANEL,
                           activeforeground=TEXT, font=("Segoe UI", 10),
                           anchor="w", bd=0, highlightthickness=0).pack(fill="x", pady=1)

        goal_row = tk.Frame(opts, bg=PANEL)
        goal_row.pack(fill="x", pady=(8, 0))
        self.label(goal_row, "Denní cíl chestek:", 10).pack(side="left")
        self.goal_spin = tk.Spinbox(goal_row, from_=1, to=9999, width=6,
                                    command=self.on_goal_changed, bg=PANEL_2, fg=TEXT,
                                    buttonbackground=PANEL_2, insertbackground=TEXT,
                                    relief="flat", font=("Segoe UI", 10))
        self.goal_spin.delete(0, "end")
        self.goal_spin.insert(0, str(self.settings["daily_goal"]))
        self.goal_spin.pack(side="left", padx=8)
        self.goal_spin.bind("<Return>", lambda e: self.on_goal_changed())
        self.goal_spin.bind("<FocusOut>", lambda e: self.on_goal_changed())

        alpha_row = tk.Frame(opts, bg=PANEL)
        alpha_row.pack(fill="x", pady=(8, 0))
        self.label(alpha_row, "Průhlednost overlaye:", 10).pack(side="left")
        self.alpha_scale = tk.Scale(alpha_row, from_=30, to=100, orient="horizontal",
                                    length=220, bg=PANEL, fg=TEXT, troughcolor=PANEL_2,
                                    highlightthickness=0, bd=0, command=self.on_alpha_changed)
        self.alpha_scale.set(self.settings["overlay_alpha"])
        self.alpha_scale.pack(side="left", padx=8)

        data = self.card(page, pady=12)
        data.pack(fill="x", pady=(10, 0))
        self.label(data, "DATA", 10, True, TEXT).pack(anchor="w", pady=(0, 6))
        row = tk.Frame(data, bg=PANEL)
        row.pack(anchor="w")
        self.button(row, "⭳ Export do CSV", self.export_csv, PURPLE).pack(side="left", padx=(0, 6))
        self.button(row, "📂 Otevřít složku s daty", self.open_folder, PANEL_2).pack(side="left")
        return page

    # --------------------------------------------------------------- obnovy UI
    def refresh_hotkey_texts(self):
        hk = self.settings["hotkeys"]
        for a, b in self.hotkey_buttons.items():
            b.config(text=vk_name(hk[a]))
        self.hint_labels["chest"].config(text=f"{vk_name(hk['chest'])} = přidat chestku")
        self.hint_labels["game"].config(text=f"{vk_name(hk['game'])} = přidat hru")
        self.refresh_dashboard()

    def refresh_dashboard(self):
        s = self.session
        chests, games = count(s, "chest"), count(s, "game")
        self.my_chests = chests
        elapsed = self.current_elapsed()
        hours = elapsed / 3600 if elapsed >= 60 else None
        durs = game_durations(s)

        self.value_labels["chest"].config(text=str(chests))
        self.value_labels["game"].config(text=str(games))
        t = self.dash_tiles
        t["cph"].config(text=fmt_rate(chests / hours if hours else None))
        t["cpg"].config(text=f"{chests / games:.1f}" if games else "—")
        t["gph"].config(text=fmt_rate(games / hours if hours else None))
        t["avg"].config(text=fmt_short(sum(durs) / len(durs)) if durs else "—")
        t["last"].config(text=fmt_short(durs[-1]) if durs else "—")
        t["longest"].config(text=fmt_short(max(durs)) if durs else "—")

        running = self.started_at is not None
        self.status_label.config(text="● ČAS BĚŽÍ" if running else "● STOPNUTO",
                                 fg=GREEN if running else MUTED)
        hk = vk_name(self.settings["hotkeys"]["timer"])
        self.timer_button.config(
            text=f"⏸ Pozastavit ({hk})" if running else f"▶ Spustit ({hk})")
        color = GOLD if running else GREEN
        self.timer_button.config(bg=color, fg="#10131f" if running else "white",
                                 activebackground=lighten(color, 0.2))
        self.timer_button.bind("<Enter>", lambda e, c=color: self.timer_button.config(bg=lighten(c)))
        self.timer_button.bind("<Leave>", lambda e, c=color: self.timer_button.config(bg=c))
        self.draw_goal()

    def draw_goal(self):
        cv = self.goal_canvas
        cv.delete("all")
        w, h = cv.winfo_width(), cv.winfo_height()
        goal = max(1, self.settings["daily_goal"])
        today = self.today_chests()
        frac = min(1.0, today / goal)
        if frac > 0 and w > 2:
            cv.create_rectangle(0, 0, w * frac, h, fill=GREEN if frac >= 1 else PURPLE, outline="")
        self.goal_label.config(
            text=f"Denní cíl: {today} / {goal} chestek  ({int(frac * 100)} %)")
        streak = self.goal_streak()
        self.streak_label.config(
            text=f"🔥 Série: {streak} {'den' if streak == 1 else 'dní'}" if streak else "")

    def refresh_stats(self):
        sessions = self.all_sessions()
        chests = sum(count(s, "chest") for s in sessions)
        games = sum(count(s, "game") for s in sessions)
        total_t = sum(self.session_elapsed(s) for s in sessions)
        totals = day_totals(sessions)
        best = max((count(s, "chest") for s in sessions), default=0)
        best_day = max(totals.items(), key=lambda kv: kv[1][0], default=None)
        t = self.stat_tiles
        t["chests"].config(text=str(chests))
        t["games"].config(text=str(games))
        t["time"].config(text=fmt_time(total_t))
        t["sessions"].config(text=str(len([s for s in sessions
                                           if self.session_elapsed(s) >= 1 or count(s, 'chest')])))
        t["cph"].config(text=fmt_rate(chests / (total_t / 3600) if total_t >= 60 else None))
        t["best"].config(text=f"{best} chestek")
        t["bestday"].config(
            text=f"{best_day[1][0]} ({best_day[0].day}.{best_day[0].month}.)"
            if best_day and best_day[1][0] else "—")
        t["streak"].config(text=str(self.goal_streak()))
        self.draw_chart()

    def draw_chart(self):
        cv = self.chart
        cv.delete("all")
        w, h = cv.winfo_width(), cv.winfo_height()
        if w < 120 or h < 100:
            return
        totals = day_totals(self.all_sessions())
        today = date.today()
        days = [today - timedelta(days=i) for i in range(6, -1, -1)]
        vals = [totals.get(d, (0, 0)) for d in days]
        maxv = max(1, max(max(v) for v in vals))
        left, right, top, bottom = 16, 16, 30, 38
        base = h - bottom
        slot = (w - left - right) / 7
        bar_w = min(30, slot * 0.3)

        cv.create_line(left, base, w - right, base, fill=BORDER)
        cv.create_rectangle(left, 8, left + 10, 18, fill=GOLD, outline="")
        cv.create_text(left + 16, 13, text="Chestky", anchor="w", fill=MUTED, font=("Segoe UI", 8))
        cv.create_rectangle(left + 80, 8, left + 90, 18, fill=BLUE, outline="")
        cv.create_text(left + 96, 13, text="Hry", anchor="w", fill=MUTED, font=("Segoe UI", 8))

        for i, (d, (c, g)) in enumerate(zip(days, vals)):
            cx = left + slot * (i + 0.5)
            for j, (val, color) in enumerate(((c, GOLD), (g, BLUE))):
                x0 = cx - bar_w - 2 if j == 0 else cx + 2
                bh = (base - top) * val / maxv
                if val:
                    cv.create_rectangle(x0, base - bh, x0 + bar_w, base, fill=color, outline="")
                    cv.create_text(x0 + bar_w / 2, base - bh - 8, text=str(val),
                                   fill=TEXT, font=("Segoe UI", 8, "bold"))
            is_today = d == today
            cv.create_text(cx, base + 12, text=WEEKDAYS[d.weekday()],
                           fill=TEXT if is_today else MUTED,
                           font=("Segoe UI", 9, "bold" if is_today else "normal"))
            cv.create_text(cx, base + 26, text=f"{d.day}.{d.month}.",
                           fill=MUTED, font=("Segoe UI", 8))

    def session_row(self, s):
        c, g = count(s, "chest"), count(s, "game")
        el = self.session_elapsed(s)
        durs = game_durations(s)
        return (
            datetime.fromtimestamp(s["start_ts"]).strftime("%d.%m.%Y %H:%M"),
            fmt_time(el), c, g,
            fmt_rate(c / (el / 3600) if el >= 60 else None),
            fmt_short(sum(durs) / len(durs)) if durs else "—",
        )

    def refresh_history(self):
        self.tree.delete(*self.tree.get_children())
        for s in sorted(self.sessions, key=lambda x: x["start_ts"], reverse=True):
            self.tree.insert("", "end", iid=str(s["id"]), values=self.session_row(s))

    def delete_selected(self):
        sel = self.tree.selection()
        if not sel:
            return
        if messagebox.askyesno("Smazat", f"Opravdu smazat {len(sel)} vybraných sessions?"):
            ids = {int(i) for i in sel}
            self.sessions = [s for s in self.sessions if s["id"] not in ids]
            self.refresh_history()
            self.refresh_dashboard()
            self.save_now()

    def export_csv(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".csv", filetypes=[("CSV", "*.csv")],
            initialfile="fortnite_tracker_historie.csv")
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f, delimiter=";")
                w.writerow(["Datum", "Délka", "Chestky", "Hry", "Chestek/hod", "Průměrná hra"])
                for s in sorted(self.all_sessions(), key=lambda x: x["start_ts"]):
                    w.writerow(self.session_row(s))
            self.toast("✓ Export dokončen", GREEN)
        except OSError:
            messagebox.showerror(APP_TITLE, "Soubor se nepodařilo uložit.")

    def open_folder(self):
        try:
            if IS_WINDOWS:
                os.startfile(DATA_FILE.parent)
            else:
                subprocess.Popen(["xdg-open", str(DATA_FILE.parent)])
        except Exception:
            pass

    # ---------------------------------------------------------------- nastavení
    def on_settings_changed(self):
        for key, var in self.vars.items():
            self.settings[key] = bool(var.get())
        self.apply_window_settings()
        self.mark_dirty()

    def on_goal_changed(self):
        try:
            self.settings["daily_goal"] = max(1, min(9999, int(self.goal_spin.get())))
        except ValueError:
            pass
        self.goal_spin.delete(0, "end")
        self.goal_spin.insert(0, str(self.settings["daily_goal"]))
        self.refresh_dashboard()
        self.mark_dirty()

    def on_alpha_changed(self, value):
        self.settings["overlay_alpha"] = int(float(value))
        for ov in (self.overlay, self.chest_overlay):
            if self.alive(ov):
                ov.attributes("-alpha", self.settings["overlay_alpha"] / 100)
        self.mark_dirty()

    def toggle_pin(self):
        self.settings["always_on_top"] = not self.settings["always_on_top"]
        self.vars["always_on_top"].set(self.settings["always_on_top"])
        self.apply_window_settings()
        self.mark_dirty()

    def apply_window_settings(self):
        on_top = self.settings["always_on_top"]
        self.root.attributes("-topmost", on_top)
        self.pin_button.config(text="📌 Nahoře: ZAP" if on_top else "📌 Nahoře: VYP")
        locked = self.settings["click_through"]
        self.lock_button.config(text="🔒 Overlay zamčen" if locked else "🔓 Overlay odemčen")
        self.apply_lock()

    # ------------------------------------------------------------------ overlay
    @staticmethod
    def alive(win):
        try:
            return win is not None and bool(win.winfo_exists())
        except tk.TclError:
            return False

    def create_overlay(self, pos_key, border):
        ov = tk.Toplevel(self.root)
        ov.overrideredirect(True)
        ov.attributes("-topmost", True)
        ov.attributes("-alpha", self.settings["overlay_alpha"] / 100)
        x, y = self.settings[pos_key]
        ov.geometry(f"+{x}+{y}")
        frame = tk.Frame(ov, bg=PANEL, padx=16, pady=8,
                         highlightthickness=1, highlightbackground=border)
        frame.pack()
        return ov, frame

    def make_draggable(self, ov, widgets, pos_key, closer):
        """Přesouvání myší funguje jen když je overlay odemčený."""
        drag = {}

        def press(e):
            drag["x"], drag["y"] = e.x_root - ov.winfo_x(), e.y_root - ov.winfo_y()

        def motion(e):
            if drag:
                ov.geometry(f"+{e.x_root - drag['x']}+{e.y_root - drag['y']}")

        def release(e):
            self.settings[pos_key] = [ov.winfo_x(), ov.winfo_y()]
            self.mark_dirty()

        for w in [ov, *widgets]:
            w.bind("<ButtonPress-1>", press)
            w.bind("<B1-Motion>", motion)
            w.bind("<ButtonRelease-1>", release)
            w.bind("<Button-3>", lambda e: closer())

    def finish_overlay(self, ov):
        ov.update_idletasks()
        self.apply_lock()
        ov.after(250, self.apply_lock)

    def apply_lock(self):
        for ov in (self.overlay, self.chest_overlay):
            if self.alive(ov):
                set_click_through(ov, self.settings["click_through"])

    def keep_overlays_on_top(self):
        for ov in (self.overlay, self.chest_overlay):
            if self.alive(ov):
                ov.attributes("-topmost", True)
                ov.lift()

    def toggle_lock(self):
        self.settings["click_through"] = not self.settings["click_through"]
        self.vars["click_through"].set(self.settings["click_through"])
        self.apply_window_settings()
        self.mark_dirty()
        if self.settings["click_through"]:
            self.toast("🔒 Overlay zamčen - myš jím prochází, nelze ho přesunout", GREEN)
        else:
            self.toast("🔓 Overlay odemčen - přetáhni ho myší, pravé tlačítko = zavřít", GOLD)

    def together_text(self):
        friends = self.sync.friends()
        if not friends:
            return ""
        total = count(self.session, "chest") + sum(c for _, c in friends)
        names = "  •  ".join(f"{n} {c}" for n, c in friends[:3])
        return f"👥 Spolu: {total}   ({names})"

    # --- malý chest overlay: číslo + obrázek bedny + čas
    def toggle_chest_overlay(self):
        if self.alive(self.chest_overlay):
            self.close_chest_overlay()
            return
        ov, frame = self.create_overlay("chest_overlay_pos", GOLD)
        self.chest_overlay = ov
        top = tk.Frame(frame, bg=PANEL)
        top.pack()
        self.co_num = self.label(top, "0", 40, True, GOLD)
        self.co_num.pack(side="left", padx=(0, 12))
        if self.chest_img is not None:
            self.co_img = tk.Label(top, image=self.chest_img, bg=PANEL)
        else:
            self.co_img = self.label(top, "📦", 32)
        self.co_img.pack(side="left")
        self.co_time = self.label(frame, "00:00:00", 20, True, TEXT)
        self.co_time.pack()
        self.co_text = self.label(frame, "", 10, True, BLUE)
        self.make_draggable(ov, [frame, top, self.co_num, self.co_img, self.co_time,
                                 self.co_text], "chest_overlay_pos", self.close_chest_overlay)
        self.finish_overlay(ov)
        self.update_chest_overlay()
        if self.settings["click_through"]:
            self.toast("Chest overlay zapnut (zamčený). Odemknutí: F6 nebo tlačítko nahoře", MUTED)

    def close_chest_overlay(self):
        if self.alive(self.chest_overlay):
            self.chest_overlay.destroy()
        self.chest_overlay = None

    def update_chest_overlay(self):
        if not self.alive(self.chest_overlay):
            return
        self.co_num.config(text=str(count(self.session, "chest")))
        self.co_time.config(text=fmt_time(self.current_elapsed()),
                            fg=TEXT if self.started_at else MUTED)
        text = self.together_text()
        if text:
            self.co_text.config(text=text)
            if not self.co_text.winfo_ismapped():
                self.co_text.pack()
        elif self.co_text.winfo_ismapped():
            self.co_text.pack_forget()

    # --- větší info overlay
    def toggle_overlay(self):
        if self.alive(self.overlay):
            self.close_overlay()
            return
        ov, frame = self.create_overlay("overlay_pos", PURPLE)
        self.overlay = ov
        self.ov_timer = self.label(frame, "00:00:00", 22, True, TEXT)
        self.ov_timer.pack()
        self.ov_stats = self.label(frame, "", 11, True, GOLD)
        self.ov_stats.pack()
        self.ov_goal = self.label(frame, "", 9, False, MUTED)
        self.ov_goal.pack()
        self.ov_net = self.label(frame, "", 10, True, BLUE)
        self.make_draggable(ov, [frame, self.ov_timer, self.ov_stats, self.ov_goal,
                                 self.ov_net], "overlay_pos", self.close_overlay)
        self.finish_overlay(ov)
        self.update_overlay()

    def close_overlay(self):
        if self.alive(self.overlay):
            self.overlay.destroy()
        self.overlay = None

    def update_overlay(self):
        if not self.alive(self.overlay):
            return
        s = self.session
        chests, games = count(s, "chest"), count(s, "game")
        el = self.current_elapsed()
        cph = fmt_rate(chests / (el / 3600) if el >= 60 else None)
        self.ov_timer.config(text=fmt_time(el), fg=TEXT if self.started_at else MUTED)
        self.ov_stats.config(text=f"📦 {chests}   🎮 {games}   ⚡ {cph}/h")
        self.ov_goal.config(
            text=f"Dnes {self.today_chests()} / {self.settings['daily_goal']} chestek")
        text = self.together_text()
        self.ov_net.config(text=text)
        if text and not self.ov_net.winfo_ismapped():
            self.ov_net.pack()
        elif not text and self.ov_net.winfo_ismapped():
            self.ov_net.pack_forget()

    # ------------------------------------------------------------- spolu (síť)
    def net_cfg(self):
        s = self.settings
        mode = {"host": "host", "relay": "relay"}.get(s["net_mode"], "client")
        return {"mode": mode, "name": s["net_name"], "key": s["net_key"],
                "host": s["net_host"], "server": s["net_server"],
                "port": s["net_port"], "interval": s["net_interval"]}

    def build_friends(self):
        page = tk.Frame(self.container, bg=BG)
        s = self.settings
        box = self.card(page, pady=12)
        box.pack(fill="x")
        self.label(box, "SPOLEČNÉ CHESTKY S KAMARÁDEM", 10, True, TEXT).pack(anchor="w")
        self.label(box, "Jeden z vás hostuje, ostatní se připojí. Každých N sekund si pošlete "
                        "počet chestek ze session - vidíte je v overlayi jako „Spolu“. "
                        "Posílají se jen chestky a jméno.",
                   8, False, MUTED, wraplength=800, justify="left").pack(anchor="w", pady=(2, 8))

        grid = tk.Frame(box, bg=PANEL)
        grid.pack(fill="x")
        self.net_vars = {
            "name": tk.StringVar(value=s["net_name"]),
            "key": tk.StringVar(value=s["net_key"]),
            "mode": tk.StringVar(value=s["net_mode"]),
            "host": tk.StringVar(value=s["net_host"]),
            "server": tk.StringVar(value=s["net_server"]),
            "port": tk.StringVar(value=str(s["net_port"])),
            "interval": tk.StringVar(value=str(s["net_interval"])),
        }

        def entry(r, text, key, show="", width=24):
            self.label(grid, text, 10).grid(row=r, column=0, sticky="w", pady=3)
            tk.Entry(grid, textvariable=self.net_vars[key], width=width, show=show,
                     bg=PANEL_2, fg=TEXT, insertbackground=TEXT, relief="flat",
                     font=("Segoe UI", 10)).grid(row=r, column=1, sticky="w", padx=10, pady=3)

        entry(0, "Tvoje jméno:", "name")
        entry(1, "Kód místnosti (heslo):", "key")
        self.label(grid, "Režim:", 10).grid(row=2, column=0, sticky="w", pady=3)
        modes = tk.Frame(grid, bg=PANEL)
        modes.grid(row=2, column=1, sticky="w", padx=10)
        for value, text in (("host", "Hostuji já"), ("join", "Připojím se"),
                            ("relay", "Přes server (kód)")):
            tk.Radiobutton(modes, text=text, value=value, variable=self.net_vars["mode"],
                           bg=PANEL, fg=TEXT, selectcolor=BG, activebackground=PANEL,
                           activeforeground=TEXT, font=("Segoe UI", 10), bd=0,
                           highlightthickness=0).pack(side="left", padx=(0, 12))
        entry(3, "IP hostitele (při připojení):", "host")
        entry(4, "Port:", "port", width=8)
        entry(5, "Synchronizace každých (s):", "interval", width=8)
        entry(6, "Adresa serveru (režim server):", "server", width=44)

        row = tk.Frame(box, bg=PANEL)
        row.pack(fill="x", pady=(8, 0))
        self.button(row, "▶ Spustit / Připojit", self.start_sync, GREEN).pack(side="left", padx=(0, 6))
        self.button(row, "■ Odpojit", self.stop_sync, "#5a2d44").pack(side="left", padx=(0, 6))
        self.button(row, "Zjistit moji IP", self.show_ip, PANEL_2).pack(side="left", padx=(0, 6))
        self.button(row, "🎲 Nový kód", self.gen_code, PANEL_2).pack(side="left", padx=(0, 6))
        self.button(row, "📋 Kopírovat kód", self.copy_code, PANEL_2).pack(side="left")
        self.net_auto_var = tk.BooleanVar(value=s["net_auto"])
        tk.Checkbutton(box, text="Spojit automaticky po spuštění aplikace",
                       variable=self.net_auto_var, command=self.on_net_auto,
                       bg=PANEL, fg=TEXT, selectcolor=BG, activebackground=PANEL,
                       activeforeground=TEXT, font=("Segoe UI", 10), bd=0,
                       highlightthickness=0).pack(anchor="w", pady=(8, 0))
        self.net_status = self.label(box, "Odpojeno", 10, True, MUTED)
        self.net_status.pack(anchor="w", pady=(6, 0))

        board = self.card(page, pady=12)
        board.pack(fill="x", pady=(10, 0))
        self.net_total = self.label(board, "SPOLU: 0 📦", 26, True, GOLD)
        self.net_total.pack()
        self.net_board = self.label(board, "", 12, False, TEXT, justify="center")
        self.net_board.pack(pady=(4, 0))

        help_card = self.card(page, pady=10)
        help_card.pack(fill="x", pady=(10, 0))
        self.label(help_card,
                   "Jak se spojit:  Hostitel klikne „Spustit“ a pošle kamarádovi svou IP "
                   "(tlačítko „Zjistit moji IP“) + kód. Kamarád zvolí „Připojím se“, vloží "
                   "IP a kód a klikne „Spustit“.\n"
                   "• Ve stejné Wi-Fi / síti to funguje hned.\n"
                   "• Přes internet použijte VPN typu Tailscale, Radmin VPN nebo Hamachi "
                   "(IP z VPN), případně přesměrujte TCP port na routeru.\n"
                   "• Režim „Přes server (kód)“: oba zadáte stejnou adresu serveru a stejný "
                   "kód - bez VPN a bez nastavování routeru. Server (soubor relay_server.py) "
                   "stačí nasadit jednou.\n"
                   "• Windows se může zeptat na firewall - povol to pro Python.",
                   8, False, MUTED, wraplength=800, justify="left").pack(anchor="w")
        return page

    def gen_code(self):
        self.net_vars["key"].set(secrets.token_urlsafe(9))
        self.toast("Nový kód vytvořen - pošli ho kamarádovi", GREEN)

    def copy_code(self):
        code = self.net_vars["key"].get()
        if code:
            self.root.clipboard_clear()
            self.root.clipboard_append(code)
            self.toast("Kód zkopírován do schránky", GREEN)

    def on_net_auto(self):
        self.settings["net_auto"] = bool(self.net_auto_var.get())
        self.mark_dirty()

    def start_sync(self):
        v = self.net_vars
        try:
            port = max(1, min(65535, int(v["port"].get())))
            interval = max(5, min(300, int(v["interval"].get())))
        except ValueError:
            messagebox.showerror(APP_TITLE, "Port a interval musí být čísla.")
            return
        mode = v["mode"].get()
        host = v["host"].get().strip()
        if mode == "join" and not host:
            messagebox.showinfo(APP_TITLE, "Zadej IP adresu hostitele.")
            return
        server = v["server"].get().strip()
        if server and "://" not in server:
            server = "https://" + server
        server = server.rstrip("/")
        if mode == "relay":
            if not server:
                messagebox.showinfo(APP_TITLE, "Zadej adresu serveru.")
                return
            if len(v["key"].get()) < 8:
                messagebox.showinfo(APP_TITLE, "Kód místnosti musí mít aspoň 8 znaků "
                                               "(použij „Nový kód“).")
                return
        v["server"].set(server)
        self.settings.update(
            net_name=v["name"].get().strip()[:24] or "Hráč", net_key=v["key"].get(),
            net_mode=mode, net_host=host, net_server=server, net_port=port,
            net_interval=interval)
        v["port"].set(str(port))
        v["interval"].set(str(interval))
        self.mark_dirty()
        self.sync.start(self.net_cfg())
        self.toast("Synchronizace spuštěna", GREEN)
        self.refresh_friends()

    def stop_sync(self):
        self.sync.stop()
        self.toast("Synchronizace vypnuta", MUTED)
        self.refresh_friends()

    @staticmethod
    def local_ip():
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.connect(("8.8.8.8", 80))  # nic se neodesílá, jen zjistí rozhraní
            ip = sock.getsockname()[0]
            sock.close()
            return ip
        except OSError:
            return "nezjištěno"

    def show_ip(self):
        messagebox.showinfo(
            APP_TITLE,
            f"Tvoje IP v lokální síti: {self.local_ip()}\n"
            f"Port: {self.settings['net_port']}\n\n"
            "Při hraní přes internet použij IP z Tailscale / Radmin VPN / Hamachi.")

    def refresh_friends(self):
        self.net_status.config(
            text=self.sync.status_text(),
            fg=GREEN if self.sync.connected() else MUTED)
        mine = count(self.session, "chest")
        friends = self.sync.friends()
        lines = [f"{self.settings['net_name']} (já): {mine}"]
        lines += [f"{n}: {c}" for n, c in friends]
        self.net_board.config(text="\n".join(lines))
        self.net_total.config(text=f"SPOLU: {mine + sum(c for _, c in friends)} 📦")

    # ------------------------------------------------------------------- konec
    def on_close(self):
        self.stop_event.set()
        self.sync.stop()
        if self.save_job:
            self.root.after_cancel(self.save_job)
        self.save_now()
        self.root.destroy()


# Obrázek bedny (PNG, base64) pro chest overlay
CHEST_PNG_B64 = (
    "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"
)


if __name__ == "__main__":
    app_root = tk.Tk()
    FortniteTracker(app_root)
    app_root.mainloop()
