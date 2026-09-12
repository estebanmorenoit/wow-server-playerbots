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

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="${DEPLOY_DIR:-$SCRIPT_DIR/../azerothcore-playerbots}"
LLM_DIR="$DEPLOY_DIR/modules/mod-llm-chatter"

log() { echo "==> $*"; }

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

log "Done. Tail logs with: docker logs -f ac-worldserver"
log "Bridge health (if started): docker logs ac-llm-chatter-bridge"
