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

**Exception**: mod-llm-chatter's Python bridge (`ac-llm-chatter-bridge` below) isn't one of the four prebuilt Hub images above, since it's a separate Python process from the C++ world server. Its build pulls source straight from `Hokken/mod-llm-chatter` on GitHub via a git build context — no local clone needed for this piece. (`deploy.sh` below still clones `modules/mod-llm-chatter` anyway, since `ac-worldserver` mounts the whole `modules/` tree for its `.conf.dist` templates — that's unrelated to the bridge's build.)

## Running it

[`deploy.sh`](./deploy.sh) does the whole thing in one command: clones the core and all three modules next to itself (skipped if already present, so it's safe to re-run), copies in `docker-compose.yml`, generates a random `DB_ROOT_PASSWORD` into a gitignored `.env` (skipped if `.env` already exists), materializes each module's `.conf` from its `.dist` template, brings the stack up in the right order, points the realm at this host's address, and creates a game login account once worldserver is ready.

The C++ side of `mod-playerbots` and `mod-solocraft` is already baked into the prebuilt images, but deploy.sh still clones both — without their `conf/*.conf.dist` templates present on disk, `playerbots.conf`/`Solocraft.conf` never get generated and worldserver runs with incomplete bot config (this has crashed the server outright on a prior deploy).

**Without LLM-driven bot chat:**
```bash
./deploy.sh
```

**With it** — pick a provider and supply your real key (never committed, never baked into any image; it's written only to the gitignored `mod_llm_chatter.conf` on your disk):
```bash
LLM_PROVIDER=google LLM_API_KEY=<your Gemini key> ./deploy.sh
```
`LLM_PROVIDER` is one of `anthropic`, `openai`, `google`, `openrouter`, or `ollama` (`ollama` needs no key). `deploy.sh` also sets `LLMChatter.Model` to that provider's tested default (e.g. `gemini-3.1-flash-lite` for `google`) — the `.dist` template ships with an Anthropic model ID regardless of provider, so switching providers without also fixing the model causes API calls to 404. Override with `LLM_MODEL` (required for `ollama`, since that depends on what you've pulled locally). See the script's header comment for all options, including overriding `DEPLOY_DIR` or `DB_ROOT_PASSWORD`.

Didn't set a provider the first time? Re-run `deploy.sh` later with `LLM_PROVIDER`/`LLM_API_KEY` set — it'll fill those two fields into your existing `mod_llm_chatter.conf` and start the bridge, without touching anything else you've since customized in that file (`docker compose --profile llm-chatter up -d` also works directly if you'd rather edit the conf by hand).

### Realm address

`deploy.sh` points the realm at this host's auto-detected LAN address (via its default route) so remote clients can actually complete login — the database's default, `127.0.0.1`, only works for a client running on this exact machine. Everyone else gets stuck at realm select: auth succeeds, but the world-server handoff then tries to reach `127.0.0.1` on *their* machine and fails.

Behind NAT, or auto-detection picks the wrong interface (multiple NICs, VPNs, etc.)? Override it:
```bash
REALM_ADDRESS=<your public or LAN IP> ./deploy.sh
```
This re-runs on every `deploy.sh` invocation (not gated by a marker), so it stays correct if the host's IP changes. No restart needed either way — authserver re-reads the `realmlist` table live.

### Login account

`deploy.sh` also creates a game account and promotes it to GM (level 3) once worldserver reports ready — by default `admin` / `test1234`. **That password is a placeholder for local testing, not something to leave in place on a server anyone else can reach.** Override it:
```bash
ADMIN_ACCOUNT_NAME=myaccount ADMIN_ACCOUNT_PASSWORD=<a real password> ./deploy.sh
```
Account names are capped at 17 characters (AzerothCore's client limit) — the script checks this upfront and skips creation with a clear error if you go over. Set `ADMIN_ACCOUNT_NAME=` (empty) to skip account creation entirely. Like everything else in this script, it only runs once per checkout (tracked by a `.admin-account-created` marker file) — re-running `deploy.sh` won't try to recreate it or touch its GM level, and if the name already existed on the server, it's left completely alone rather than modified.

To create additional accounts later, use the worldserver console directly:
```bash
docker attach ac-worldserver
account create <username> <password>
account set gmlevel <username> 3 -1   # optional, grants GM access
```
Detach with `Ctrl-P` then `Ctrl-Q` — **never `Ctrl-C`**, which sends SIGINT to the worldserver process itself and will kill the server.

### Uninstalling

```bash
./deploy.sh --uninstall            # stop and remove containers/networks; keeps the database, client-data, and checkout — reversible, just re-run ./deploy.sh
./deploy.sh --uninstall --purge    # also deletes the database + client-data volumes and the entire checkout (including your API key) — permanent
```
Both prompt for a typed `yes` before doing anything; add `-y`/`--yes` to skip that for scripting. `--purge` destroys every character, guild, and all progress — there's no undo, so back up the database first (see "Migrating to another host" below) if there's any chance you'll want it again.

Check the bridge worked: `docker logs ac-llm-chatter-bridge` should show five `[PASS]` lines (config, module enabled, LLM provider config, database connection, LLM connectivity live test) ending in a report written to `/logs/healthcheck.log`. Any `[FAIL]` means bots will not chat until it's fixed.

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

1. **Install Docker + Compose** on the new host, clone this repo, then run `./deploy.sh` once just far enough to get the pieces in place and the database up — stop it (`Ctrl-C`, then `docker compose stop`) right after `ac-database` becomes healthy, before it imports a fresh schema. (Migration needs your restored dump in place first, so don't let it run to completion here — that's the one case where the one-shot script isn't the right tool.)
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
