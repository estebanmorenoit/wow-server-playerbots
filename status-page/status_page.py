#!/usr/bin/env python3
"""Small always-on status page for the WoW server — population, economy,
world composition, and every actually-played real character, queried live from
ac-database over the network.

Deliberately has no docker.sock access (unlike wake-proxy): "online" is
derived from the database being reachable AND authserver/worldserver
actually accepting TCP connections (a plain socket check over ac-network,
not container inspection) — the database alone isn't proof a player could
log in, since it can be up by itself (e.g. woken for maintenance) while
the rest of the stack stays stopped or is still booting. Either check
failing falls back to the last successful query's numbers (cached to
disk) rather than showing nothing.

Session/economy history is tracked by wake-proxy, not here — see
SESSIONS_FILE below.
"""
import json
import os
import re
import socket
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pymysql

DB_HOST = os.environ.get("DB_HOST", "ac-database")
DB_PORT = int(os.environ.get("DB_PORT", "3306"))
DB_USER = os.environ.get("DB_USER", "root")
DB_PASSWORD = os.environ.get("DB_ROOT_PASSWORD", "password")
DB_CONNECT_TIMEOUT = 3.0
PORT_CHECK_TIMEOUT = 2.0

CACHE_FILE = "/data/last_stats.json"
WORLDSERVER_CONF = "/worldserver.conf"

# Written by wake-proxy's record_session() (mounted read-only here — see
# docker-compose.yml) — one entry per play session (start/end, peak
# population, AH delta), not a fixed-interval sample. This host only runs
# 1-2h/day, so a time-based sampler would spend nearly all its points on
# "asleep," and connecting across that gap with a line chart would imply
# continuous change through 20+ idle hours that never actually happened.
SESSIONS_FILE = "/backups/sessions.json"

PROMETHEUS_URL = os.environ.get("PROMETHEUS_URL", "http://prometheus:9090")
PROMETHEUS_TIMEOUT = 2.0

# "Online" means a player could actually log in right now — the database
# alone isn't enough proof of that (e.g. it can be woken by itself for
# maintenance/backups while worldserver/authserver stay stopped). Checked
# with a plain TCP connect over ac-network, not docker.sock, to keep this
# container read-only with respect to the rest of the stack.
GAME_PORTS = [("ac-authserver", 3724), ("ac-worldserver", 8085)]


def game_ports_reachable() -> bool:
    for host, port in GAME_PORTS:
        try:
            with socket.create_connection((host, port), timeout=PORT_CHECK_TIMEOUT):
                pass
        except OSError:
            return False
    return True


def read_xp_rate() -> float | None:
    """Reads the real, currently-running rate straight from worldserver's
    own config, so this can never drift from what the server actually
    does — unlike a rate hardcoded here at write time."""
    try:
        with open(WORLDSERVER_CONF) as f:
            for line in f:
                if line.strip().startswith("Rate.XP.Kill"):
                    match = re.search(r"=\s*([\d.]+)", line)
                    if match:
                        return float(match.group(1))
    except OSError:
        pass
    return None

with open(os.path.join(os.path.dirname(__file__), "index.html")) as _f:
    PAGE_HTML = _f.read()

# WotLK race IDs -> faction. Public game data (not a Blizzard asset) — the
# same enum every AzerothCore/TrinityCore install ships with.
ALLIANCE_RACES = {1, 3, 4, 7, 11}  # Human, Dwarf, Night Elf, Gnome, Draenei
HORDE_RACES = {2, 5, 6, 8, 10}  # Orc, Undead, Tauren, Troll, Blood Elf

# WotLK skill-line IDs for the profession panel — a small, fixed list that
# hasn't changed since the expansion shipped, unlike achievement_dbc/
# areatable_dbc (both empty on this install — see README): those need real
# client DBC data this core never imported, but profession names are common
# knowledge, not extracted data, so hardcoding them here carries none of
# that risk.
PROFESSION_NAMES = {
    164: "Blacksmithing", 165: "Leatherworking", 171: "Alchemy",
    182: "Herbalism", 186: "Mining", 197: "Tailoring",
    202: "Engineering", 333: "Enchanting", 393: "Skinning",
    755: "Jewelcrafting", 773: "Inscription",
    185: "Cooking", 129: "First Aid", 356: "Fishing",
}

_cache_lock = threading.Lock()


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def read_cache() -> dict | None:
    try:
        with open(CACHE_FILE) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def write_cache(stats: dict) -> None:
    with _cache_lock:
        try:
            os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
            tmp = CACHE_FILE + ".tmp"
            with open(tmp, "w") as f:
                json.dump(stats, f)
            os.replace(tmp, CACHE_FILE)
        except OSError as exc:
            log(f"failed writing cache: {exc}")


def read_sessions() -> list[dict]:
    try:
        with open(SESSIONS_FILE) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return []


def _one(cur, query: str):
    cur.execute(query)
    row = cur.fetchone()
    return row


def fetch_top_crafters(cur) -> list[dict]:
    """Highest-skill character per profession — real, populated
    character_skills data, unblocked by the missing DBC reference tables
    (see PROFESSION_NAMES above). Window function needs MySQL 8+, which
    this install runs."""
    placeholders = ",".join(["%s"] * len(PROFESSION_NAMES))
    cur.execute(
        f"""
        SELECT profession, name, skill FROM (
            SELECT cs.skill AS profession, c.name, cs.value AS skill,
                   ROW_NUMBER() OVER (PARTITION BY cs.skill ORDER BY cs.value DESC, c.name ASC) AS rn
            FROM acore_characters.character_skills cs
            JOIN acore_characters.characters c ON c.guid = cs.guid
            WHERE cs.skill IN ({placeholders})
        ) ranked
        WHERE rn = 1
        ORDER BY skill DESC
        """,
        tuple(PROFESSION_NAMES),
    )
    return [
        {"profession": PROFESSION_NAMES.get(r["profession"], f"Skill #{r['profession']}"), "name": r["name"], "skill": r["skill"]}
        for r in cur.fetchall()
    ]


def _prometheus_query(query: str) -> float | None:
    try:
        url = f"{PROMETHEUS_URL}/api/v1/query?" + urllib.parse.urlencode({"query": query})
        with urllib.request.urlopen(url, timeout=PROMETHEUS_TIMEOUT) as resp:
            data = json.load(resp)
        result = data["data"]["result"]
        return float(result[0]["value"][1]) if result else None
    except (OSError, KeyError, IndexError, ValueError, json.JSONDecodeError):
        return None


def fetch_host_health() -> dict | None:
    """Real host metrics from the standalone Prometheus + node-exporter
    stack (monitoring/docker-compose.yml) — this container has no /proc or
    /sys of its own, deliberately, so it never needs privileged access to
    show CPU/RAM/temp. Independent of the game DB, so this can populate
    even while the game stack is asleep. Returns None if that stack isn't
    reachable rather than fabricating a value."""
    cpu_percent = _prometheus_query('100 - avg(irate(node_cpu_seconds_total{mode="idle"}[5m])) * 100')
    mem_total = _prometheus_query("node_memory_MemTotal_bytes")
    mem_available = _prometheus_query("node_memory_MemAvailable_bytes")
    cpu_temp_c = _prometheus_query('max(node_hwmon_temp_celsius{chip="platform_coretemp_0"})')

    if cpu_percent is None and mem_total is None and cpu_temp_c is None:
        return None

    memory_percent = None
    if mem_total and mem_available is not None:
        memory_percent = round((1 - mem_available / mem_total) * 100, 1)

    return {
        "cpu_percent": round(cpu_percent, 1) if cpu_percent is not None else None,
        "memory_percent": memory_percent,
        "cpu_temp_c": round(cpu_temp_c, 1) if cpu_temp_c is not None else None,
    }


def query_live_stats() -> dict:
    conn = pymysql.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        connect_timeout=DB_CONNECT_TIMEOUT,
        read_timeout=DB_CONNECT_TIMEOUT,
        cursorclass=pymysql.cursors.DictCursor,
    )
    try:
        with conn.cursor() as cur:
            totals = _one(
                cur,
                """
                SELECT
                  (SELECT COUNT(*) FROM acore_characters.characters WHERE online = 1) AS online_now,
                  (SELECT COUNT(*) FROM acore_characters.characters c
                     JOIN acore_auth.account a ON a.id = c.account
                     WHERE c.online = 1 AND a.username LIKE 'RNDBOT%%') AS active_bots,
                  (SELECT COUNT(*) FROM acore_characters.characters) AS total_characters,
                  (SELECT COUNT(*) FROM acore_characters.guild) AS guilds,
                  (SELECT COUNT(*) FROM acore_characters.auctionhouse) AS ah_listings,
                  (SELECT COALESCE(ROUND(SUM(buyoutprice) / 10000), 0) FROM acore_characters.auctionhouse) AS ah_gold,
                  (SELECT COUNT(*) FROM acore_auth.account) AS accounts,
                  (SELECT COALESCE(ROUND(SUM(totaltime) / 3600), 0) FROM acore_characters.characters) AS world_playtime_hours,
                  (SELECT MIN(joindate) FROM acore_auth.account WHERE username NOT LIKE 'RNDBOT%%') AS realm_founded,
                  (SELECT MAX(c.logout_time) FROM acore_characters.characters c
                     JOIN acore_auth.account a ON a.id = c.account
                     WHERE a.username NOT LIKE 'RNDBOT%%') AS last_player_activity,
                  (SELECT SUM(level BETWEEN 1 AND 19) FROM acore_characters.characters) AS lvl_1_19,
                  (SELECT SUM(level BETWEEN 20 AND 39) FROM acore_characters.characters) AS lvl_20_39,
                  (SELECT SUM(level BETWEEN 40 AND 59) FROM acore_characters.characters) AS lvl_40_59,
                  (SELECT SUM(level BETWEEN 60 AND 79) FROM acore_characters.characters) AS lvl_60_79,
                  (SELECT SUM(level >= 80) FROM acore_characters.characters) AS lvl_80_plus
                """,
            )
            totals["xp_rate"] = read_xp_rate()

            # Class distribution (WotLK class IDs 1-9, 11 — no 10).
            cur.execute("SELECT class, COUNT(*) AS n FROM acore_characters.characters GROUP BY class")
            class_counts = {str(r["class"]): r["n"] for r in cur.fetchall()}

            # Faction split, derived from per-race counts in Python rather
            # than a SQL CASE, to keep the race->faction mapping in one place.
            cur.execute("SELECT race, COUNT(*) AS n FROM acore_characters.characters GROUP BY race")
            alliance = horde = 0
            for r in cur.fetchall():
                if r["race"] in ALLIANCE_RACES:
                    alliance += r["n"]
                elif r["race"] in HORDE_RACES:
                    horde += r["n"]

            # Every actually-played character on a non-RNDBOT account —
            # generic on purpose, so any real player (not a specific
            # hardcoded name), including every alt on the same account,
            # shows up automatically as soon as they've played at all. That
            # floor (60s) exists only to filter out mod-ah-bot's own service
            # account, which technically isn't a RNDBOT% account either but
            # never accumulates more than a few seconds of real playtime —
            # kept low deliberately so a fresh alt shows up almost
            # immediately rather than waiting on an arbitrary "played long
            # enough" bar.
            cur.execute(
                """
                SELECT c.name, c.race, c.class, c.level, c.totaltime AS playtime_seconds
                FROM acore_characters.characters c
                JOIN acore_auth.account a ON a.id = c.account
                WHERE a.username NOT LIKE 'RNDBOT%%' AND c.totaltime > 60
                ORDER BY c.totaltime DESC
                LIMIT 10
                """
            )
            real_players = cur.fetchall()

            cur.execute(
                """
                SELECT g.name, COUNT(gm.guid) AS members
                FROM acore_characters.guild g
                JOIN acore_characters.guild_member gm ON gm.guildid = g.guildid
                GROUP BY g.guildid
                ORDER BY members DESC
                LIMIT 5
                """
            )
            top_guilds = cur.fetchall()

            top_crafters = fetch_top_crafters(cur)

        # Pop the raw bucket columns out of `totals` before spreading it
        # below, so they only ever appear nested under level_buckets.
        level_buckets = {
            "1-19": totals.pop("lvl_1_19"),
            "20-39": totals.pop("lvl_20_39"),
            "40-59": totals.pop("lvl_40_59"),
            "60-79": totals.pop("lvl_60_79"),
            "80+": totals.pop("lvl_80_plus"),
        }

        result = {
            **totals,
            "class_counts": class_counts,
            "faction": {"alliance": alliance, "horde": horde},
            "level_buckets": level_buckets,
            "top_guilds": top_guilds,
            "top_crafters": top_crafters,
            "real_players": real_players,
        }
        return _decimals_to_int(result)
    finally:
        conn.close()


def _decimals_to_int(obj):
    """SUM()/ROUND() come back as decimal.Decimal via pymysql, and dates as
    datetime — neither is JSON-serializable. Every number here is a whole
    count or a rounded amount, so int() loses nothing real; dates become
    ISO strings."""
    import datetime
    import decimal

    if isinstance(obj, dict):
        return {k: _decimals_to_int(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_decimals_to_int(v) for v in obj]
    if isinstance(obj, decimal.Decimal):
        return int(obj)
    if isinstance(obj, (datetime.date, datetime.datetime)):
        return obj.isoformat()
    return obj


def summarize_sessions() -> dict:
    sessions = read_sessions()
    if not sessions:
        return {"total_sessions": 0, "longest_session_seconds": 0}
    return {
        "total_sessions": len(sessions),
        "longest_session_seconds": max(s["duration_seconds"] for s in sessions),
    }


def get_stats() -> dict:
    now = time.time()
    # Both independent of the game DB — sessions.json and Prometheus are
    # readable whether the realm's awake or asleep, so these show up either
    # way, and never get frozen into last_stats.json's stale cache below.
    session_summary = summarize_sessions()
    host_health = fetch_host_health()
    try:
        stats = query_live_stats()
        if not game_ports_reachable():
            log("DB is up but authserver/worldserver aren't accepting connections yet — not fully online")
            raise RuntimeError("game ports not reachable")
        stats.update(session_summary)
        stats["host_health"] = host_health
        payload = {"status": "awake", "as_of": now, "stats": stats}
        write_cache(payload)
        return payload
    except Exception as exc:  # pymysql raises its own exception types, plus the RuntimeError above
        log(f"Realm not fully online (stack likely asleep or still booting): {exc}")
        cached = read_cache()
        if cached is None:
            return {"status": "asleep", "as_of": now, "stats": None, "host_health": host_health, **session_summary}
        cached_stats = {**cached["stats"], **session_summary} if cached["stats"] else cached["stats"]
        if cached_stats:
            cached_stats["host_health"] = host_health
        return {"status": "asleep", "as_of": now, "stats": cached_stats, "stats_as_of": cached["as_of"]}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        log(fmt % args)

    def do_GET(self):
        if self.path == "/api/stats":
            body = json.dumps(get_stats()).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/sessions":
            body = json.dumps(read_sessions()).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/":
            body = PAGE_HTML.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def do_HEAD(self):
        if self.path == "/":
            body = PAGE_HTML.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()


if __name__ == "__main__":
    log("status-page listening on 0.0.0.0:8090")
    ThreadingHTTPServer(("0.0.0.0", 8090), Handler).serve_forever()
