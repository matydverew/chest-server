#!/usr/bin/env python3
"""
Fortnite Tracker - jednoduchý relay server (bez knihoven, jen Python 3).

Kamarádi se spojí přes stejný KÓD MÍSTNOSTI. Server si v paměti drží jen jméno
a počet chestek, nic neukládá na disk a po 15 minutách nečinnosti vše zapomene.

Spuštění:   python relay_server.py          (port z proměnné PORT, výchozí 8080)
"""
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TTL = 900          # po kolika sekundách se hráč zapomene
MAX_ROOMS = 2000
MAX_PLAYERS = 10   # na jednu místnost
MAX_BODY = 1024

rooms = {}         # kód -> {jméno: [chestky, čas]}
lock = threading.Lock()


def cleanup(now):
    for code in list(rooms):
        players = rooms[code]
        for name in [n for n, (_, ts) in players.items() if now - ts > TTL]:
            del players[name]
        if not players:
            del rooms[code]


class Handler(BaseHTTPRequestHandler):
    server_version = "TrackerRelay/1.0"

    def reply(self, code, payload, ctype="application/json"):
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self.reply(200, b"Fortnite Tracker relay OK", "text/plain; charset=utf-8")

    def do_POST(self):
        if self.path != "/sync":
            return self.reply(404, {"error": "not found"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_BODY:
                return self.reply(413, {"error": "bad size"})
            msg = json.loads(self.rfile.read(length).decode("utf-8"))
            room, name = msg["room"], str(msg["name"]).strip()[:24]
            chests = int(msg["chests"])
            if not (isinstance(room, str) and 8 <= len(room) <= 64 and name
                    and 0 <= chests <= 1_000_000):
                raise ValueError
        except (ValueError, KeyError, TypeError, UnicodeDecodeError):
            return self.reply(400, {"error": "bad request"})

        now = time.time()
        with lock:
            cleanup(now)
            if room not in rooms and len(rooms) >= MAX_ROOMS:
                return self.reply(503, {"error": "server full"})
            players = rooms.setdefault(room, {})
            if name in players or len(players) < MAX_PLAYERS:
                players[name] = [chests, now]
            others = {n: {"c": c, "age": round(now - ts, 1)}
                      for n, (c, ts) in players.items() if n != name}
        self.reply(200, {"players": others})

    def log_message(self, *args):  # žádné logy (ať se nepíšou kódy místností)
        pass


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    print(f"Relay server běží na portu {port}")
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
