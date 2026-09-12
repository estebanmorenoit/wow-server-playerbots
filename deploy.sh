#!/usr/bin/env bash
# Deploys the wow-server-playerbots stack: clones the source pieces the compose
# file expects on disk, generates a DB password if needed, optionally wires up
# mod-llm-chatter, and brings the stack up.
#
# Safe to re-run: it never overwrites an existing checkout, .env, or
# mod_llm_chatter.conf — only fills in what's missing.
#
# Usage:
#   ./deploy.sh
#   LLM_PROVIDER=google LLM_API_KEY=AIza... ./deploy.sh
#   LLM_PROVIDER=ollama ./deploy.sh                      # no key needed
#
#   ./deploy.sh --uninstall            Stop and remove containers/networks.
#                                       Keeps the database, client-data volumes,
#                                       and the checkout on disk — safe, reversible,
#                                       just re-run ./deploy.sh to bring it back.
#   ./deploy.sh --uninstall --purge    Also deletes the database and client-data
#                                       volumes AND the entire checkout (source,
#                                       .env, and mod_llm_chatter.conf with your
#                                       API key). PERMANENT — all characters,
#                                       guilds, and progress are gone.
#   Add -y/--yes to either to skip the confirmation prompt (for scripting).
#
# Env vars:
#   DEPLOY_DIR       Where to clone/run the server (default: ../azerothcore-playerbots
#                     relative to this script)
#   DB_ROOT_PASSWORD Set the MySQL root password instead of generating one
#                     (only used the first time .env is created)
#   LLM_PROVIDER      anthropic | openai | google | openrouter | ollama
#                     Leave unset to skip mod-llm-chatter entirely for now —
#                     you can configure and start it later, see the README.
#   LLM_API_KEY       Required unless LLM_PROVIDER=ollama. Never printed, never
#                     written anywhere but the gitignored conf file below.
#   ADMIN_ACCOUNT_NAME     Game account created on first deploy, promoted to GM
#                          (default: admin). Set to an empty string to skip
#                          account creation entirely. Only ever attempted once
#                          per checkout (tracked by a marker file) — safe to
#                          re-run without recreating or erroring on it.
#   ADMIN_ACCOUNT_PASSWORD Password for that account (default: test1234 — a
#                          simple placeholder for local testing; change it, or
#                          set your own, before exposing this server publicly).

set -euo pipefail

UNINSTALL=0
PURGE=0
ASSUME_YES=0
for arg in "$@"; do
  case "$arg" in
    --uninstall) UNINSTALL=1 ;;
    --purge) PURGE=1 ;;
    -y|--yes) ASSUME_YES=1 ;;
    -h|--help)
      sed -n '2,/^set -euo/p' "${BASH_SOURCE[0]}" | sed '$d; s/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "Unknown argument: $arg (see --help)" >&2
      exit 1
      ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="${DEPLOY_DIR:-$SCRIPT_DIR/../azerothcore-playerbots}"
LLM_DIR="$DEPLOY_DIR/modules/mod-llm-chatter"

log() { echo "==> $*"; }

if [ "$UNINSTALL" -eq 1 ]; then
  if [ ! -d "$DEPLOY_DIR" ]; then
    log "$DEPLOY_DIR doesn't exist — nothing to uninstall."
    exit 0
  fi

  echo "This will stop and remove the wow-server-playerbots containers and networks in $DEPLOY_DIR."
  if [ "$PURGE" -eq 1 ]; then
    echo
    echo "--purge also requested — this PERMANENTLY DELETES:"
    echo "  - the database volume (every character, guild, and all progress)"
    echo "  - the client-data volume"
    echo "  - the entire checkout at $DEPLOY_DIR, including .env and mod_llm_chatter.conf (your LLM API key)"
    echo
    echo "This cannot be undone."
  fi

  if [ "$ASSUME_YES" -ne 1 ]; then
    read -r -p "Type 'yes' to continue: " CONFIRM
    if [ "$CONFIRM" != "yes" ]; then
      echo "Aborted, nothing was changed."
      exit 1
    fi
  fi

  if [ -f "$DEPLOY_DIR/docker-compose.yml" ]; then
    cd "$DEPLOY_DIR"
    if [ "$PURGE" -eq 1 ]; then
      log "Stopping the stack and removing its volumes..."
      docker compose --profile llm-chatter down -v --remove-orphans
    else
      log "Stopping the stack (volumes and files are kept)..."
      docker compose --profile llm-chatter down --remove-orphans
    fi
  else
    log "No docker-compose.yml found in $DEPLOY_DIR — skipping docker compose down."
  fi

  if [ "$PURGE" -eq 1 ]; then
    log "Deleting $DEPLOY_DIR ..."
    rm -rf "$DEPLOY_DIR"
    log "Done — everything has been removed."
  else
    log "Done. Containers and networks are gone; the database, client-data, and $DEPLOY_DIR are untouched."
    log "Re-run ./deploy.sh to bring it back up, or ./deploy.sh --uninstall --purge to delete the database and checkout too."
  fi
  exit 0
fi

# 1. AzerothCore core (Playerbot branch fork)
if [ ! -d "$DEPLOY_DIR/.git" ]; then
  log "Cloning AzerothCore (playerbots fork) into $DEPLOY_DIR ..."
  git clone https://github.com/mod-playerbots/azerothcore-wotlk.git "$DEPLOY_DIR"
else
  log "Core already present at $DEPLOY_DIR — leaving it as-is (not touching a running server's checkout)."
fi

# 2. mod-llm-chatter source (needed for its conf.dist template and the bridge's
#    build context resolves this from GitHub directly, but ac-worldserver still
#    mounts this directory for module .conf.dist discovery)
if [ ! -d "$LLM_DIR/.git" ]; then
  log "Cloning mod-llm-chatter into $LLM_DIR ..."
  git clone https://github.com/Hokken/mod-llm-chatter.git "$LLM_DIR"
else
  log "mod-llm-chatter already present at $LLM_DIR — leaving it as-is."
fi

# 3. Compose file
cp "$SCRIPT_DIR/docker-compose.yml" "$DEPLOY_DIR/docker-compose.yml"
log "Copied docker-compose.yml into $DEPLOY_DIR."

# 4. .env (DB root password) — generated once, never overwritten
ENV_FILE="$DEPLOY_DIR/.env"
if [ ! -f "$ENV_FILE" ]; then
  PW="${DB_ROOT_PASSWORD:-$(openssl rand -hex 16)}"
  printf 'DB_ROOT_PASSWORD=%s\n' "$PW" > "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  log "Generated $ENV_FILE with a new DB_ROOT_PASSWORD (gitignored; never commit it)."
else
  log "$ENV_FILE already exists — leaving your DB password as-is."
fi
# shellcheck disable=SC1090
source "$ENV_FILE"

# 5. mod_llm_chatter.conf — created once from the .dist template, never
#    overwritten on re-runs so we don't clobber settings you've tuned by hand
CONF_DIR="$DEPLOY_DIR/env/dist/etc/modules"
CONF_FILE="$CONF_DIR/mod_llm_chatter.conf"
mkdir -p "$CONF_DIR"
if [ ! -f "$CONF_FILE" ]; then
  cp "$LLM_DIR/conf/mod_llm_chatter.conf.dist" "$CONF_FILE"
  # Fixes the two defaults that are always wrong once this runs in Docker:
  # the .dist template assumes a local, non-containerized MySQL.
  sed -i "s/^LLMChatter\.Database\.Host = .*/LLMChatter.Database.Host = ac-database/" "$CONF_FILE"
  sed -i "s/^LLMChatter\.Database\.Password = .*/LLMChatter.Database.Password = $DB_ROOT_PASSWORD/" "$CONF_FILE"
  log "Created $CONF_FILE from the .dist template."
else
  log "$CONF_FILE already exists — leaving your settings as-is."
fi

# 6. Wire up the chosen LLM provider, if any
START_BRIDGE=0
if [ -n "${LLM_PROVIDER:-}" ]; then
  case "$LLM_PROVIDER" in
    anthropic) KEY_FIELD="LLMChatter.Anthropic.ApiKey" ;;
    openai)    KEY_FIELD="LLMChatter.OpenAI.ApiKey" ;;
    google)    KEY_FIELD="LLMChatter.Google.ApiKey" ;;
    openrouter) KEY_FIELD="LLMChatter.OpenRouter.ApiKey" ;;
    ollama)    KEY_FIELD="" ;;
    *)
      echo "Unknown LLM_PROVIDER '$LLM_PROVIDER' (expected anthropic|openai|google|openrouter|ollama)" >&2
      exit 1
      ;;
  esac

  if [ -n "$KEY_FIELD" ] && [ -z "${LLM_API_KEY:-}" ]; then
    echo "LLM_PROVIDER=$LLM_PROVIDER requires LLM_API_KEY to be set." >&2
    exit 1
  fi

  sed -i "s/^LLMChatter\.Provider = .*/LLMChatter.Provider = $LLM_PROVIDER/" "$CONF_FILE"
  if [ -n "$KEY_FIELD" ]; then
    sed -i "s#^${KEY_FIELD} = .*#${KEY_FIELD} = ${LLM_API_KEY}#" "$CONF_FILE"
  fi
  log "Configured mod_llm_chatter.conf for provider '$LLM_PROVIDER'."
  START_BRIDGE=1
else
  log "LLM_PROVIDER not set — leaving mod-llm-chatter unconfigured for now (server runs fine without it)."
  log "Configure $CONF_FILE and re-run with LLM_PROVIDER/LLM_API_KEY set whenever you're ready, or start it manually per the README."
fi

# 7. Bring the stack up
cd "$DEPLOY_DIR"
if [ "$START_BRIDGE" -eq 1 ]; then
  log "Starting the full stack, including ac-llm-chatter-bridge..."
  docker compose --profile llm-chatter up -d
else
  log "Starting the core stack (auth/world/database)..."
  docker compose up -d
fi

# 8. Create a test game account, once. Uses a pty-wrapped `docker attach`
# since the worldserver console needs a real TTY (plain piped input is
# refused). Only attempted once per checkout, tracked by a marker file, so
# re-running deploy.sh never tries to recreate or erroneously fails on it.
ADMIN_ACCOUNT_NAME="${ADMIN_ACCOUNT_NAME-admin}"
ADMIN_ACCOUNT_PASSWORD="${ADMIN_ACCOUNT_PASSWORD:-test1234}"
ACCOUNT_MARKER="$DEPLOY_DIR/.admin-account-created"

if [ -z "$ADMIN_ACCOUNT_NAME" ]; then
  log "ADMIN_ACCOUNT_NAME set to empty — skipping account creation."
elif [ -f "$ACCOUNT_MARKER" ]; then
  log "Admin account already created previously — skipping (remove $ACCOUNT_MARKER to force another attempt)."
elif [ "${#ADMIN_ACCOUNT_NAME}" -gt 17 ]; then
  echo "ADMIN_ACCOUNT_NAME '$ADMIN_ACCOUNT_NAME' is too long (AzerothCore's client limit is 17 characters) — skipping account creation. Pick a shorter name and re-run." >&2
else
  log "Waiting for worldserver to finish starting up so it can accept console commands (can take a minute or two on first boot)..."
  if timeout 300 docker compose logs -f ac-worldserver 2>&1 | grep -qm1 "worldserver-daemon) ready\.\.\."; then
    log "Creating game account '$ADMIN_ACCOUNT_NAME' and granting GM level..."
    RESULT="$(ADMIN_NAME="$ADMIN_ACCOUNT_NAME" ADMIN_PASS="$ADMIN_ACCOUNT_PASSWORD" python3 - <<'PYEOF'
import os, pty, subprocess, select, time, sys

name = os.environ["ADMIN_NAME"]
pw = os.environ["ADMIN_PASS"]

master, slave = pty.openpty()
proc = subprocess.Popen(["docker", "attach", "ac-worldserver"], stdin=slave, stdout=slave, stderr=slave)
os.close(slave)

def drain(t=2.5):
    out = b""
    end = time.time() + t
    while time.time() < end:
        r, _, _ = select.select([master], [], [], 0.2)
        if master in r:
            try:
                chunk = os.read(master, 4096)
            except OSError:
                break
            if not chunk:
                break
            out += chunk
    return out.decode(errors="replace")

time.sleep(1)
drain()
os.write(master, f"account create {name} {pw}\r\n".encode())
create_out = drain(3)

# Only ever touch gmlevel on an account this run just created — never on one
# that already existed (creation failed here for any other reason).
gm_out = ""
if "Account created" in create_out:
    os.write(master, f"account set gmlevel {name} 3 -1\r\n".encode())
    gm_out = drain(3)

# Detach (Ctrl-P Ctrl-Q) then stop our local attach client only — this never
# touches the container itself, same as a network client disconnecting.
os.write(master, b"\x10\x11")
time.sleep(0.5)
proc.terminate()
try:
    proc.wait(timeout=5)
except Exception:
    proc.kill()

if "Account created" in create_out and "security level" in gm_out:
    print("CREATED")
elif "already exist" in create_out:
    print("ALREADY_EXISTS")
else:
    print("UNKNOWN")
    sys.stderr.write(create_out + gm_out)
PYEOF
)"
    case "$RESULT" in
      CREATED)
        touch "$ACCOUNT_MARKER"
        log "Account '$ADMIN_ACCOUNT_NAME' created with GM level 3, password '$ADMIN_ACCOUNT_PASSWORD' — this is a simple testing default, change it before exposing this server publicly."
        ;;
      ALREADY_EXISTS)
        touch "$ACCOUNT_MARKER"
        log "Account '$ADMIN_ACCOUNT_NAME' already existed on the server — left as-is."
        ;;
      *)
        echo "Couldn't confirm account creation via the console — check manually: docker attach ac-worldserver (detach with Ctrl-P Ctrl-Q, never Ctrl-C)" >&2
        ;;
    esac
  else
    echo "worldserver did not report ready within 5 minutes — skipping automatic account creation. Create one manually per the README." >&2
  fi
fi

log "Done. Tail logs with: docker logs -f ac-worldserver"
log "Bridge health (if started): docker logs ac-llm-chatter-bridge"
