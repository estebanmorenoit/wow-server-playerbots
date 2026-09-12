# wow-server-playerbots

Self-hosted **World of Warcraft: Wrath of the Lich King (3.3.5a, build 12340)** private server, built on [AzerothCore](https://www.azerothcore.org/) with the [mod-playerbots](https://github.com/mod-playerbots/mod-playerbots), [mod-solocraft](https://github.com/azerothcore/mod-solocraft), and [mod-llm-chatter](https://github.com/Hokken/mod-llm-chatter) modules.

Not affiliated with Blizzard Entertainment. For personal/private-server use.

## What this is

- **Core**: AzerothCore (fork: [`mod-playerbots/azerothcore-wotlk`](https://github.com/mod-playerbots/azerothcore-wotlk), Playerbot branch)
- **Modules** (all under `modules/`, compiled statically into `worldserver` via AzerothCore's `ModulesLoader`):
  - [mod-playerbots](https://github.com/mod-playerbots/mod-playerbots) — AI-controlled bot characters that populate the world
  - [mod-solocraft](https://github.com/azerothcore/mod-solocraft) — scales dungeon/raid boss stats to your actual group size
  - [mod-llm-chatter](https://github.com/Hokken/mod-llm-chatter) — bots chat dynamically via a real LLM (Anthropic/OpenAI/Google/OpenRouter/Ollama) instead of canned lines
- **Database**: MySQL 8.4 (official `mysql:8.4` image — not customized)
- **Orchestration**: Docker Compose

## Prebuilt images

The C++ side (core + all three modules) is baked into these images — no build toolchain needed to run the server:

| Image | Purpose |
|---|---|
| [`estebanmorenoit/ac-wotlk-worldserver-playerbots`](https://hub.docker.com/r/estebanmorenoit/ac-wotlk-worldserver-playerbots) | World server (game logic), with all three modules built in |
| [`estebanmorenoit/ac-wotlk-authserver-playerbots`](https://hub.docker.com/r/estebanmorenoit/ac-wotlk-authserver-playerbots) | Auth/login server (port 3724) |
| [`estebanmorenoit/ac-wotlk-db-import-playerbots`](https://hub.docker.com/r/estebanmorenoit/ac-wotlk-db-import-playerbots) | One-shot DB bootstrap/migration (run before the servers) |
| [`estebanmorenoit/ac-wotlk-client-data-playerbots`](https://hub.docker.com/r/estebanmorenoit/ac-wotlk-client-data-playerbots) | One-shot client data extraction (maps/vmaps/mmaps/dbc) |

All four are built from the same `apps/docker/Dockerfile` in the AzerothCore playerbots fork, using a different `target` per service.

**Exception**: mod-llm-chatter's Python bridge (`ac-llm-chatter-bridge` below) isn't one of the four prebuilt Hub images above, since it's a separate Python process from the C++ world server. It does build its own image locally, though — from the `Dockerfile` shipped in `mod-llm-chatter/tools/`, which bakes its Python dependencies in at build time (`docker compose build` / first `docker compose up`) instead of installing them on every container start. You still need a local clone of `mod-llm-chatter` for that Dockerfile and source; see below.

## Running it

1. Get the pieces the compose file expects to find on disk next to it:
   ```bash
   git clone https://github.com/mod-playerbots/azerothcore-wotlk.git azerothcore-playerbots
   cd azerothcore-playerbots
   git clone https://github.com/Hokken/mod-llm-chatter.git modules/mod-llm-chatter
   ```
   (`env/dist/etc/` doesn't need to be pre-populated — each container copies its own `.conf.dist` template into `.conf` on first boot if one isn't already there.)
2. Copy this repo's [`docker-compose.yml`](./docker-compose.yml) into that directory.
3. Bring up the database and let it become healthy, then run the one-shot init containers, then start the servers:
   ```bash
   docker compose up -d ac-database
   docker compose up ac-db-import ac-client-data-init
   docker compose up -d ac-authserver ac-worldserver
   ```
4. **Configure mod-llm-chatter** (skip this and the `ac-llm-chatter-bridge` service entirely if you don't want LLM-driven bot chat):
   - Edit `env/dist/etc/modules/mod_llm_chatter.conf` (created from the `.dist` template after step 3):
     - `LLMChatter.Provider` — pick `anthropic`, `openai`, `google`, `openrouter`, or `ollama`
     - The matching `LLMChatter.<Provider>.ApiKey` — your real key. **Never commit this file with a real key in it.**
     - `LLMChatter.Database.Host = ac-database` — the `.dist` template ships with `localhost`, which is wrong once this runs in its own container on the compose network. This one bit us during setup; fix it or the bridge's health check fails with `[FAIL] Database connection` even though everything else passes.
     - `LLMChatter.Database.Password` — match whatever `DB_ROOT_PASSWORD` you're using (default `password`), not the `.dist` template's placeholder.
   - Then: `docker compose up -d ac-llm-chatter-bridge`
   - Check it worked: `docker logs ac-llm-chatter-bridge` should show five `[PASS]` lines (config, module enabled, LLM provider config, database connection, LLM connectivity live test) ending in a report written to `/logs/healthcheck.log`. Any `[FAIL]` means bots will not chat until it's fixed.

Connect with a **3.3.5a (12340)** WotLK client, pointed at this server's address on port 3724 (see below).

### Known upstream issue in mod-llm-chatter (fixed locally)

As of the commit this was built against, `LLMChatterShared.cpp`'s `SendPartyMessageInstant` calls `ChatHandler::BuildChatPacket()` with an argument order from an older AzerothCore signature, which fails to compile against this core (`fatal error: no matching function for call to 'BuildChatPacket'`). The fix (matching the pattern used everywhere else in the module and in core itself, e.g. `Player.cpp`'s `Say`/`Yell`):

```cpp
// before (broken):
ChatHandler::BuildChatPacket(data, CHAT_MSG_PARTY, message, LANG_UNIVERSAL,
                              CHAT_TAG_NONE, bot->GetGUID(), bot->GetName());

// after (matches current core signature):
ChatHandler::BuildChatPacket(data, CHAT_MSG_PARTY, LANG_UNIVERSAL, bot, bot, message);
```

Check if upstream has merged a fix before you build; if not, you'll need to patch this yourself the same way.

## Pointing the client at this server

The WotLK client reads which realm to connect to from a text file called `realmlist.wtf`.

1. Find it inside your WoW 3.3.5a client install:
   ```
   <WoW install folder>/Data/enUS/realmlist.wtf
   ```
   (use the folder matching your client locale, e.g. `enGB`, `deDE`, etc. — most English clients use `enUS`)
2. Open it in a text editor and replace its contents with:
   ```
   set realmlist <YOUR_SERVER_IP>
   ```
   replacing `<YOUR_SERVER_IP>` with the IP address or hostname where `ac-authserver` is reachable (port 3724 must be open on that host).
3. Save the file and launch the client — it will authenticate against port 3724 and hand off to the world server on connect.

## Playerbot configuration

Bot behavior is controlled via environment variables on the `ac-worldserver` service — see [`docker-compose.yml`](./docker-compose.yml). Current tuning: 100 random bots, leveled to match real players, clustered near player zones, built-in greet disabled (mod-llm-chatter handles chat instead), and per-bot AI cost cut from the core default of 10 to 6 iterations per tick to keep CPU headroom on a 4-core host — raise that last one if you have cores to spare.

## GM commands reference (this build)

Verified against this exact core's command tables (`src/server/scripts/Commands/`) — some tutorials online reference commands from other cores that don't exist here (e.g. there's no `.modify xp`):

```
.gm on / .gm off                          — toggle GM mode
.tele <name>  /  .tele add <name>         — teleport to / save a location
.additem <id> [count]                     — add an item to your inventory
.character level <player> <level>         — set a character's level directly
.character levelup [amount] [player]      — relative level up/down (no flat "add xp" command exists)
.modify speed [all|walk|run|swim|flight|backwalk] <n>  — movement speed multiplier
.learn all my / .learn all gm             — learn your class's full spellbook / the GM spellbook
.revive [player]                          — resurrect
.cheat god|cooldown|casttime|power on/off — invulnerability / no cooldowns / instant cast / unlimited resources
.reload config                            — re-read worldserver.conf live (e.g. after changing Rate.XP.* values) — no restart needed
.account set gmlevel <account> <level> -1 — grant GM access, all realms
```

Rate.XP.Kill / Rate.XP.Quest / Rate.XP.Explore / etc. in `worldserver.conf` control passive XP gain server-wide (default `1`); change them and run `.reload config` to apply without restarting.

## Migrating to another host (e.g. a more powerful NUC)

The Docker images are portable — the state and secrets are not, and need to move separately:

1. **Install Docker + Compose** on the new host, clone this repo plus the source pieces from "Running it" above.
2. **Copy the database**, don't start fresh, unless you want to:
   ```bash
   # on the old host
   docker exec ac-database mysqldump -u root -p"$DB_ROOT_PASSWORD" --all-databases > wow-server-backup.sql
   # copy wow-server-backup.sql to the new host, then, after `docker compose up -d ac-database` there:
   docker exec -i ac-database mysql -u root -p"$DB_ROOT_PASSWORD" < wow-server-backup.sql
   ```
3. **Copy secrets manually, out-of-band** (SCP, not git): `env/dist/etc/modules/mod_llm_chatter.conf` (has your real LLM API key) and the `DB_ROOT_PASSWORD` you're actually using — neither belongs in this repo.
4. **Re-check bot count against the new host's core count.** On the original 4-core box, 100 bots used roughly one full core; a more powerful NUC has headroom to raise `AC_AI_PLAYERBOT_MAX_RANDOM_BOTS` (and could safely raise `AC_AI_PLAYERBOT_ITERATIONS_PER_TICK` back toward the core default of 10 for slightly sharper bot behavior) if you want a denser world.
5. **Open port 3724** (and 8085 if you want direct world-server access) in the new host's firewall/router, then update `realmlist.wtf` on any client to the new address.

## Commanding bots in-game

Invite any bot to your party like a normal player, or right-click their portrait for a recruit option. Whisper a bot **"help"** in-game for its full list of orders — follow, stay, equip, spec, and more.
