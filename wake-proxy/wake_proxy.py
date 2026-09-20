#!/usr/bin/env python3
"""Always-on TCP relay + idle-shutdown watcher for the WoW client ports.

Runs as its own docker-compose service (`wake-proxy`), the only game-stack
service that never gets stopped. Three jobs:

1. Relay: listens on the real public 3724 (auth) and 8085 (world). If the
   real backend container isn't up, triggers `docker compose up -d` and
   holds the client connection until it is, then relays bytes transparently.
   Never inspects or modifies game traffic.

2. Idle watcher: every IDLE_CHECK_INTERVAL, checks how many characters are
   online. Once that's been 0 continuously for IDLE_THRESHOLD_SECONDS,
   backs up the database (see run_backup — ac-database is still up at this
   point, so this is cheaper than backup.sh's fixed-04:00 cron waking it
   back up later) and stops the other game services (not itself) via
   `docker compose stop`.

3. Session recorder: the same idle-watcher loop tracks each play session's
   start/end, peak population, and AH economy delta (see record_session),
   writing one record per session to sessions.json rather than sampling on
   a fixed interval — this host only runs 1-2h/day, so a time-based
   sampler would spend nearly all its points on "asleep," and a naive line
   chart connecting across that gap would draw a smooth line implying
   continuous change through 20+ idle hours. status-page reads this same
   file (mounted read-only there) to render session history.

   Session start/end is keyed on real_player_online_count(), not the
   aggregate online_character_count() the sleep decision below uses —
   after a real player disconnects, bots take up to 300s to log off and
   the idle timer then runs its own threshold on top of that, so the
   aggregate count doesn't reach 0 until well after the session actually
   ended. Using it for session bounds would count that shutdown overhead
   as play time (a real 2-3 minute session was recording as 12 minutes).

Needs the host's Docker socket mounted to control sibling containers, and
the deploy directory mounted at the SAME absolute path it lives at on the
host — docker-compose.yml's bind mounts (./env/dist/etc etc.) are relative
paths resolved against that file's location, but actually applied by the
HOST's dockerd, so the path this container sees and the path the host's
dockerd sees must be identical or those bind mounts silently point at the
wrong place. See the `wake-proxy` service definition in docker-compose.yml.

Deliberately stdlib-only (socket/threading/subprocess) — this is the one
thing standing between "the game is reachable at all" and "nothing's
listening", so it stays as simple and dependency-free as possible.
"""
import gzip
import json
import os
import socket
import subprocess
import sys
import threading
import time

DEPLOY_DIR = "/home/esteban/azerothcore-playerbots"
COMPOSE_FILE = f"{DEPLOY_DIR}/docker-compose.yml"
PROJECT_NAME = "azerothcore-playerbots"

# (name, public_port, backend_host, backend_port) — backend_host is the
# compose service name, resolved over the shared ac-network.
ROUTES = [
    ("auth", 3724, "ac-authserver", 3724),
    ("world", 8085, "ac-worldserver", 8085),
]

GAME_SERVICES = ["ac-worldserver", "ac-authserver", "ac-database", "ac-llm-chatter-bridge"]

BACKEND_CONNECT_TIMEOUT = 2.0
BOOT_WAIT_TIMEOUT = 120.0
BOOT_POLL_INTERVAL = 1.0
START_COOLDOWN = 15.0  # don't re-trigger `compose up` more often than this

IDLE_CHECK_INTERVAL = 60.0
# Was 15 min; dropped to 5 (2026-09-14) — this host is thermally constrained
# (Turbo Boost permanently disabled, package temp ~88°C even at idle-ish
# load), and every minute in this window is spent with all random bots
# still logged in and actively AI-ticking (the same ~150-230% worldserver
# CPU measured all through tonight's load testing) for zero benefit once
# you've actually stopped playing. 5 min still comfortably absorbs a brief
# alt-tab or network hiccup without triggering an unwanted sleep/wake cycle
# (waking back up takes ~60-70s and the first reconnect attempt always
# shows a connection error — see "Known limitation" below).
IDLE_THRESHOLD_SECONDS = 5 * 60

DB_ROOT_PASSWORD = os.environ.get("DB_ROOT_PASSWORD", "password")

# Same directory backup.sh writes to on the host (bind-mounted read-write
# below) — one shared pool of dumps, one retention policy, either restore
# procedure works on either's output.
BACKUP_DIR = "/backups"

# One record per play session (not a fixed-interval sampler): this host
# only runs 1-2h/day, so a time-sampled trend would spend >95% of its
# points on "asleep" and — worse — a naive line chart would connect the
# last point before sleep to the first point after, drawing a smooth line
# across a 20+ hour gap as if something changed continuously overnight.
# status-page reads this same file (mounted read-only there) to render
# session history instead.
SESSIONS_FILE = f"{BACKUP_DIR}/sessions.json"
SESSIONS_MAX = 200
BACKUP_RETENTION_DAYS = int(os.environ.get("BACKUP_RETENTION_DAYS", "30"))

_start_lock = threading.Lock()
_last_start_attempt = 0.0


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def compose(*args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "compose", "-f", COMPOSE_FILE, "-p", PROJECT_NAME, *args],
        cwd=DEPLOY_DIR,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def backend_reachable(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=BACKEND_CONNECT_TIMEOUT):
            return True
    except OSError:
        return False


def trigger_start(name: str) -> None:
    global _last_start_attempt
    with _start_lock:
        now = time.monotonic()
        if now - _last_start_attempt < START_COOLDOWN:
            return
        _last_start_attempt = now
    log(f"[{name}] backend down — waking {', '.join(GAME_SERVICES)}")
    try:
        # Scoped to GAME_SERVICES specifically — never a bare `up -d`
        # covering the whole file. A wake-proxy or status-page picked up as
        # needing a recreate (any config drift, e.g. from an earlier
        # ad-hoc rebuild) would make this container ask Docker to replace
        # itself while it's the one running the command: Docker sends
        # SIGTERM, the self-referential process doesn't exit cleanly within
        # the grace period, and it gets force-killed mid-wake — which is
        # exactly what happened once already. Excluding the always-on tier
        # here makes that structurally impossible, not just unlikely.
        result = compose("--profile", "llm-chatter", "up", "-d", *GAME_SERVICES)
        if result.returncode != 0:
            log(f"[{name}] docker compose up -d failed: {result.stderr.strip()}")
    except Exception as exc:  # noqa: BLE001 - never let this thread die
        log(f"[{name}] failed to invoke docker compose: {exc}")


def relay(src: socket.socket, dst: socket.socket) -> None:
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    finally:
        for sock in (src, dst):
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def handle_client(client: socket.socket, name: str, backend_host: str, backend_port: int) -> None:
    if not backend_reachable(backend_host, backend_port):
        trigger_start(name)
        deadline = time.monotonic() + BOOT_WAIT_TIMEOUT
        ready = False
        while time.monotonic() < deadline:
            if backend_reachable(backend_host, backend_port):
                ready = True
                break
            time.sleep(BOOT_POLL_INTERVAL)
        if not ready:
            log(f"[{name}] backend didn't come up within {BOOT_WAIT_TIMEOUT:.0f}s — dropping client")
            client.close()
            return
        log(f"[{name}] backend ready — proxying held connection through")

    try:
        backend = socket.create_connection((backend_host, backend_port), timeout=BACKEND_CONNECT_TIMEOUT)
    except OSError as exc:
        log(f"[{name}] could not connect to backend after it reported ready: {exc}")
        client.close()
        return
    # create_connection()'s timeout arg only governs the connection attempt,
    # but the returned socket keeps that timeout active for every subsequent
    # recv() unless cleared — which silently broke long-lived relaying. A
    # >BACKEND_CONNECT_TIMEOUT gap between packets (e.g. the burst of data
    # the server gathers and sends on "Enter World", easily >2s under this
    # host's CPU constraints) raised socket.timeout, an OSError subclass,
    # which relay()'s `except OSError: pass` treated as a dead connection
    # and tore down both sockets — disconnecting the client mid-session.
    # Quick exchanges (auth, character list) stayed under the timeout, which
    # is why only "Enter World" specifically broke.
    backend.settimeout(None)

    t1 = threading.Thread(target=relay, args=(client, backend), daemon=True)
    t2 = threading.Thread(target=relay, args=(backend, client), daemon=True)
    t1.start()
    t2.start()
    t1.join()
    t2.join()


def serve(name: str, public_port: int, backend_host: str, backend_port: int) -> None:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("0.0.0.0", public_port))
    listener.listen(64)
    log(f"[{name}] listening on 0.0.0.0:{public_port} -> {backend_host}:{backend_port}")
    while True:
        client, _addr = listener.accept()
        # During a cold-boot wait, this connection can sit idle for a while
        # with zero bytes flowing while the game client patiently waits for
        # a response — keepalive stops a router NAT table or ISP middlebox
        # from silently dropping it as "inactive" before the backend is
        # ready, which is a different failure mode than the client's own
        # application-level timeout.
        client.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        for opt, val in (
            ("TCP_KEEPIDLE", 10),
            ("TCP_KEEPINTVL", 10),
            ("TCP_KEEPCNT", 6),
        ):
            if hasattr(socket, opt):
                client.setsockopt(socket.IPPROTO_TCP, getattr(socket, opt), val)
        threading.Thread(
            target=handle_client, args=(client, name, backend_host, backend_port), daemon=True
        ).start()


def container_running(name: str) -> bool:
    result = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", name],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


def online_character_count() -> int | None:
    result = subprocess.run(
        [
            "docker", "exec", "ac-database", "mysql", "-uroot", f"-p{DB_ROOT_PASSWORD}",
            "-N", "-B", "acore_characters", "-e",
            "SELECT COUNT(*) FROM characters WHERE online=1;",
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode != 0:
        log(f"[idle] DB query failed: {result.stderr.strip()}")
        return None
    try:
        return int(result.stdout.strip())
    except ValueError:
        return None


def real_player_online_count() -> int | None:
    """Same as online_character_count() but excludes RNDBOT% accounts —
    used to bound a *session* (login to logout) precisely, as distinct
    from online_character_count()'s use in the sleep decision. The two
    diverge on purpose: after a real player disconnects, bots take up to
    300s to log off, then the idle timer runs its own threshold on top —
    the aggregate count (and thus the sleep decision) shouldn't reach 0
    until well after the session has genuinely ended, but the session
    record needs to stop counting at the actual logout, not whenever the
    server gets around to sleeping."""
    result = subprocess.run(
        [
            "docker", "exec", "ac-database", "mysql", "-uroot", f"-p{DB_ROOT_PASSWORD}",
            "-N", "-B", "acore_characters", "-e",
            "SELECT COUNT(*) FROM characters c JOIN acore_auth.account a ON a.id = c.account "
            "WHERE c.online = 1 AND a.username NOT LIKE 'RNDBOT%';",
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode != 0:
        log(f"[session] real-player online query failed: {result.stderr.strip()}")
        return None
    try:
        return int(result.stdout.strip())
    except ValueError:
        return None


def fetch_economy_snapshot() -> dict | None:
    result = subprocess.run(
        [
            "docker", "exec", "ac-database", "mysql", "-uroot", f"-p{DB_ROOT_PASSWORD}",
            "-N", "-B", "acore_characters", "-e",
            "SELECT (SELECT COUNT(*) FROM auctionhouse), "
            "(SELECT COALESCE(ROUND(SUM(buyoutprice) / 10000), 0) FROM auctionhouse);",
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode != 0:
        log(f"[session] economy query failed: {result.stderr.strip()}")
        return None
    try:
        listings, gold = result.stdout.strip().split("\t")
        return {"ah_listings": int(listings), "ah_gold": int(gold)}
    except (ValueError, IndexError):
        return None


# Skill-line IDs for the profession panel — a small, fixed list that hasn't
# changed since WotLK shipped, unlike achievement_dbc/areatable_dbc (see
# status-page/status_page.py, which documents the same list): those need
# real client DBC data this core never imported, but profession names are
# common knowledge, not extracted data, so hardcoding them here carries none
# of that risk. Mirrored here rather than imported since this container and
# status-page are built from separate, independent images.
PROFESSION_NAMES = {
    164: "Blacksmithing", 165: "Leatherworking", 171: "Alchemy",
    182: "Herbalism", 186: "Mining", 197: "Tailoring",
    202: "Engineering", 333: "Enchanting", 393: "Skinning",
    755: "Jewelcrafting", 773: "Inscription",
    185: "Cooking", 129: "First Aid", 356: "Fishing",
}


def fetch_character_snapshot(guids: list[int] | None = None) -> dict[int, dict]:
    """Per-character progress snapshot for the real player(s), keyed by
    guid — used to compute what YOUR character actually did in a session
    (level/gold/quests/xp/exploration), as opposed to
    fetch_economy_snapshot()'s realm-wide AH numbers. With no guids given,
    auto-detects whichever real (non-bot) characters are online right now —
    used at session start. At session end the real player has already
    logged out (online=0 is literally the signal that ended the session),
    so the end snapshot instead re-queries the exact guids captured at
    start, regardless of their online flag — the row's stats are still
    their last-saved state either way."""
    guid_filter = f"c.guid IN ({','.join(str(g) for g in guids)})" if guids else "c.online = 1"
    result = subprocess.run(
        [
            "docker", "exec", "ac-database", "mysql", "-uroot", f"-p{DB_ROOT_PASSWORD}",
            "-N", "-B", "acore_characters", "-e",
            f"SELECT c.guid, c.name, c.level, c.money, c.xp, c.exploredZones, "
            "(SELECT COUNT(*) FROM character_queststatus_rewarded WHERE guid = c.guid) "
            "FROM characters c JOIN acore_auth.account a ON a.id = c.account "
            f"WHERE a.username NOT LIKE 'RNDBOT%' AND {guid_filter};",
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode != 0:
        log(f"[session] character snapshot query failed: {result.stderr.strip()}")
        return {}
    snapshot = {}
    for line in result.stdout.strip().splitlines():
        try:
            guid_s, name, level, money, xp, explored, quests = line.split("\t")
            snapshot[int(guid_s)] = {
                "name": name, "level": int(level), "money": int(money), "xp": int(xp),
                "explored": explored, "quests": int(quests),
            }
        except ValueError:
            continue
    return snapshot


def _fetch_guid_keyed_counts(table: str, id_col: str, value_col: str,
                              guids: list[int]) -> dict[int, dict[int, int]]:
    """Shared helper for character_reputation and character_skills — both
    are guid + id + value tables, just naming the id/value columns
    differently (faction/standing vs skill/value)."""
    if not guids:
        return {}
    result = subprocess.run(
        [
            "docker", "exec", "ac-database", "mysql", "-uroot", f"-p{DB_ROOT_PASSWORD}",
            "-N", "-B", "acore_characters", "-e",
            f"SELECT guid, {id_col}, {value_col} FROM {table} "
            f"WHERE guid IN ({','.join(str(g) for g in guids)});",
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode != 0:
        log(f"[session] {table} query failed: {result.stderr.strip()}")
        return {}
    by_guid: dict[int, dict[int, int]] = {}
    for line in result.stdout.strip().splitlines():
        try:
            guid_s, id_s, value_s = line.split("\t")
            by_guid.setdefault(int(guid_s), {})[int(id_s)] = int(value_s)
        except ValueError:
            continue
    return by_guid


def count_new_explored_bits(before: str, after: str) -> int:
    """exploredZones is a space-separated array of uint32 bitmask chunks
    (same encoding as taximask) — one bit per client-side explorable area.
    Counting newly-set bits (present in `after`, not in `before`) needs no
    DBC data since it's pure bit arithmetic, unlike naming which *area* each
    bit represents (which would need AreaTable.dbc's exploration-bit
    mapping — not attempted here, same reasoning as skipping faction/skill
    names outside the known-safe PROFESSION_NAMES list)."""
    try:
        before_words = [int(w) for w in before.split()]
        after_words = [int(w) for w in after.split()]
    except (ValueError, AttributeError):
        return 0
    total = 0
    for i in range(max(len(before_words), len(after_words))):
        b = before_words[i] if i < len(before_words) else 0
        a = after_words[i] if i < len(after_words) else 0
        total += bin(a & ~b).count("1")
    return total


def fetch_achievement_counts(guids: list[int], start_time: float, end_time: float) -> dict[int, int]:
    """Achievements earned per guid within [start_time, end_time] — uses
    character_achievement's own `date` column directly rather than a
    before/after delta, since it's timestamped (unlike quests/level/money,
    which need the snapshot-diff approach above)."""
    if not guids:
        return {}
    result = subprocess.run(
        [
            "docker", "exec", "ac-database", "mysql", "-uroot", f"-p{DB_ROOT_PASSWORD}",
            "-N", "-B", "acore_characters", "-e",
            "SELECT guid, COUNT(*) FROM character_achievement "
            f"WHERE guid IN ({','.join(str(g) for g in guids)}) "
            f"AND date BETWEEN {int(start_time)} AND {int(end_time)} "
            "GROUP BY guid;",
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode != 0:
        log(f"[session] achievement query failed: {result.stderr.strip()}")
        return {}
    counts = {}
    for line in result.stdout.strip().splitlines():
        try:
            guid_s, count = line.split("\t")
            counts[int(guid_s)] = int(count)
        except ValueError:
            continue
    return counts


def fetch_session_start_state() -> dict:
    """Everything needed to diff against at session end, captured once at
    session start — reputation/skills need their own start-of-session
    snapshot the same way start_chars does, since record_session() only
    runs once, at the end, and can't recover what they were at the start
    otherwise."""
    chars = fetch_character_snapshot()
    guids = list(chars.keys())
    return {
        "chars": chars,
        "reputation": _fetch_guid_keyed_counts("character_reputation", "faction", "standing", guids),
        "skills": _fetch_guid_keyed_counts("character_skills", "skill", "value", guids),
    }


def record_session(start_time: float, end_time: float, peak_population: int,
                    start_econ: dict | None, end_econ: dict | None,
                    start_state: dict | None = None) -> None:
    session = {
        "started_at": start_time,
        "ended_at": end_time,
        "duration_seconds": round(end_time - start_time),
        "peak_population": peak_population,
    }
    if start_econ and end_econ:
        session["ah_gold_delta"] = end_econ["ah_gold"] - start_econ["ah_gold"]
        session["ah_listings_delta"] = end_econ["ah_listings"] - start_econ["ah_listings"]

    start_chars = start_state.get("chars") if start_state else None
    if start_chars:
        guids = list(start_chars.keys())
        end_chars = fetch_character_snapshot(guids=guids)
        achievements = fetch_achievement_counts(guids, start_time, end_time)
        start_reputation = start_state["reputation"]
        end_reputation = _fetch_guid_keyed_counts("character_reputation", "faction", "standing", guids)
        start_skills = start_state["skills"]
        end_skills = _fetch_guid_keyed_counts("character_skills", "skill", "value", guids)
        characters = []
        for guid, start in start_chars.items():
            end = end_chars.get(guid, start)

            xp_gained = None
            if end["level"] == start["level"] and end["xp"] > start["xp"]:
                xp_gained = end["xp"] - start["xp"]

            start_rep = start_reputation.get(guid, {})
            end_rep = end_reputation.get(guid, {})
            reputations_gained = sum(1 for fid, val in end_rep.items() if val > start_rep.get(fid, 0))

            start_sk = start_skills.get(guid, {})
            end_sk = end_skills.get(guid, {})
            skills_gained = [
                {"name": name, "delta": end_sk.get(skill_id, 0) - start_sk.get(skill_id, 0)}
                for skill_id, name in PROFESSION_NAMES.items()
                if end_sk.get(skill_id, 0) > start_sk.get(skill_id, 0)
            ]

            characters.append({
                "name": start["name"],
                "level_start": start["level"],
                "level_end": end["level"],
                # Raw copper, not pre-rounded to gold — 1g = 100s = 10000c.
                # A 50-silver session used to round to "+0g" and vanish
                # entirely; the frontend formats this into g/s/c itself.
                "money_delta": end["money"] - start["money"],
                "quests_completed": max(0, end["quests"] - start["quests"]),
                "achievements_earned": achievements.get(guid, 0),
                "xp_gained": xp_gained,
                "reputations_gained": reputations_gained,
                "skills_gained": skills_gained,
                "areas_explored": count_new_explored_bits(start["explored"], end.get("explored", start["explored"])),
            })
        if characters:
            session["characters"] = characters

    try:
        sessions = []
        if os.path.exists(SESSIONS_FILE):
            with open(SESSIONS_FILE) as f:
                sessions = json.load(f)
        sessions.append(session)
        sessions = sessions[-SESSIONS_MAX:]
        tmp = SESSIONS_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(sessions, f)
        os.replace(tmp, SESSIONS_FILE)
        log(f"[session] recorded: {session['duration_seconds']}s, peak population {peak_population}")
    except (OSError, json.JSONDecodeError) as exc:
        log(f"[session] failed to record: {exc}")


def run_backup() -> None:
    """Dump every database while ac-database is still up, right before the
    idle-shutdown stops it. gzip via stdlib, not a piped `gzip` binary, to
    keep this container dependency-free like the rest of the file."""
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    out_path = f"{BACKUP_DIR}/wow-backup-{timestamp}.sql.gz"
    tmp_path = f"{out_path}.tmp"
    log(f"[backup] dumping all databases to {out_path} before sleep ...")
    result = subprocess.run(
        [
            "docker", "exec", "ac-database", "mysqldump", "-uroot", f"-p{DB_ROOT_PASSWORD}",
            "--all-databases", "--single-transaction", "--quick",
        ],
        capture_output=True,
        timeout=300,
    )
    if result.returncode != 0:
        log(f"[backup] mysqldump failed: {result.stderr.decode(errors='replace').strip()}")
        return
    try:
        with gzip.open(tmp_path, "wb") as f:
            f.write(result.stdout)
        os.replace(tmp_path, out_path)
        log(f"[backup] done: {out_path}")
    except OSError as exc:
        log(f"[backup] failed writing {out_path}: {exc}")
        return

    cutoff = time.time() - BACKUP_RETENTION_DAYS * 86400
    try:
        for name in os.listdir(BACKUP_DIR):
            if name.startswith("wow-backup-") and name.endswith(".sql.gz"):
                path = f"{BACKUP_DIR}/{name}"
                if os.path.getmtime(path) < cutoff:
                    os.remove(path)
                    log(f"[backup] pruned old backup {name}")
    except OSError as exc:
        log(f"[backup] pruning old backups failed: {exc}")


def idle_watcher() -> None:
    idle_since: float | None = None
    session_start_time: float | None = None
    session_start_econ: dict | None = None
    session_start_state: dict = {}
    peak_population = 0
    while True:
        time.sleep(IDLE_CHECK_INTERVAL)
        if not container_running("ac-worldserver"):
            # Also drop any in-progress session tracking — e.g. a redeploy
            # recreating the container mid-session shouldn't produce a
            # session record spanning the outage with a stale start time.
            idle_since = None
            session_start_time = None
            session_start_econ = None
            session_start_state = {}
            peak_population = 0
            continue

        online = online_character_count()
        if online is None:
            continue

        # Session start/end is keyed on the real player specifically, not
        # the aggregate count below — bots take up to 300s to log off after
        # the player disconnects, and the sleep decision then runs its own
        # multi-minute threshold on top of that. If sessions used the same
        # aggregate signal, a session record would count all of that
        # shutdown overhead as if it were play time (confirmed: a 2-3
        # minute play session recorded as 12 minutes). Recording the moment
        # the real player's own online count hits 0 reflects actual play
        # time instead.
        real_online = real_player_online_count()
        if real_online is not None:
            if real_online > 0 and session_start_time is None:
                session_start_time = time.time()
                session_start_econ = fetch_economy_snapshot()
                session_start_state = fetch_session_start_state()
                peak_population = online
                log(f"[session] started (population {online})")
            elif real_online > 0:
                peak_population = max(peak_population, online)
            elif real_online == 0 and session_start_time is not None:
                log("[session] real player logged out — recording session")
                record_session(session_start_time, time.time(), peak_population, session_start_econ,
                                fetch_economy_snapshot(), session_start_state)
                session_start_time = None
                session_start_econ = None
                session_start_state = {}
                peak_population = 0

        if online > 0:
            idle_since = None
            continue

        now = time.monotonic()
        if idle_since is None:
            idle_since = now
            log("[idle] 0 players online — starting idle timer")
            continue

        idle_for = now - idle_since
        if idle_for < IDLE_THRESHOLD_SECONDS:
            log(f"[idle] 0 players online — idle for {idle_for:.0f}s (threshold {IDLE_THRESHOLD_SECONDS:.0f}s)")
            continue

        log(f"[idle] idle for {idle_for:.0f}s — backing up before stopping game stack")
        run_backup()
        log("[idle] stopping game stack (leaving wake-proxy up)")
        result = compose("stop", *GAME_SERVICES, timeout=90)
        if result.returncode != 0:
            log(f"[idle] docker compose stop failed: {result.stderr.strip()}")
        idle_since = None


def main() -> None:
    threads = []
    for name, public_port, backend_host, backend_port in ROUTES:
        t = threading.Thread(target=serve, args=(name, public_port, backend_host, backend_port), daemon=True)
        t.start()
        threads.append(t)

    watcher = threading.Thread(target=idle_watcher, daemon=True)
    watcher.start()
    threads.append(watcher)

    for t in threads:
        t.join()


if __name__ == "__main__":
    sys.exit(main())
