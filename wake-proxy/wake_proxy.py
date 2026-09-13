#!/usr/bin/env python3
"""Always-on TCP relay + idle-shutdown watcher for the WoW client ports.

Runs as its own docker-compose service (`wake-proxy`), the only game-stack
service that never gets stopped. Two jobs:

1. Relay: listens on the real public 3724 (auth) and 8085 (world). If the
   real backend container isn't up, triggers `docker compose up -d` and
   holds the client connection until it is, then relays bytes transparently.
   Never inspects or modifies game traffic.

2. Idle watcher: every IDLE_CHECK_INTERVAL, checks how many characters are
   online. Once that's been 0 continuously for IDLE_THRESHOLD_SECONDS,
   stops the other game services (not itself) via `docker compose stop`.

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
IDLE_THRESHOLD_SECONDS = 15 * 60

DB_ROOT_PASSWORD = os.environ.get("DB_ROOT_PASSWORD", "password")

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
    log(f"[{name}] backend down — running `docker compose --profile llm-chatter up -d`")
    try:
        result = compose("--profile", "llm-chatter", "up", "-d")
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


def idle_watcher() -> None:
    idle_since: float | None = None
    while True:
        time.sleep(IDLE_CHECK_INTERVAL)
        if not container_running("ac-worldserver"):
            idle_since = None
            continue

        online = online_character_count()
        if online is None:
            continue

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

        log(f"[idle] idle for {idle_for:.0f}s — stopping game stack (leaving wake-proxy up)")
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
