# wow-server-playerbots

Self-hosted **World of Warcraft: Wrath of the Lich King (3.3.5a, build 12340)** private server, built on [AzerothCore](https://www.azerothcore.org/) with the [mod-playerbots](https://github.com/mod-playerbots/mod-playerbots), [mod-solocraft](https://github.com/azerothcore/mod-solocraft), [mod-llm-chatter](https://github.com/Hokken/mod-llm-chatter), [mod-ah-bot](https://github.com/NathanHandley/mod-ah-bot-plus), [mod-individual-progression](https://github.com/ZhengPeiRu21/mod-individual-progression), and [mod-npc-buffer](https://github.com/azerothcore/mod-npc-buffer) modules.

Not affiliated with Blizzard Entertainment. For personal/private-server use.

## What this is

- **Core**: AzerothCore (fork: [`mod-playerbots/azerothcore-wotlk`](https://github.com/mod-playerbots/azerothcore-wotlk), Playerbot branch)
- **Modules** (all under `modules/`, compiled statically into `worldserver` via AzerothCore's `ModulesLoader`):
  - [mod-playerbots](https://github.com/mod-playerbots/mod-playerbots) — AI-controlled bot characters that populate the world
  - [mod-solocraft](https://github.com/azerothcore/mod-solocraft) — scales dungeon/raid boss stats to your actual group size
  - [mod-llm-chatter](https://github.com/Hokken/mod-llm-chatter) — bots chat dynamically via a real LLM (Anthropic/OpenAI/Google/OpenRouter/Ollama) instead of canned lines
  - [mod-ah-bot](https://github.com/NathanHandley/mod-ah-bot-plus) — populates the Auction House with bot-driven listings (otherwise permanently empty with no real players) — see [Auction House bot](#auction-house-bot) below to activate it
  - [mod-individual-progression](https://github.com/ZhengPeiRu21/mod-individual-progression) — gates content server-side by era (Vanilla → TBC → WotLK) on top of the same 3.3.5a client, restoring period-accurate quests/creatures/itemization along the way — see [Progression](#progression) below
  - [mod-npc-buffer](https://github.com/azerothcore/mod-npc-buffer) — a one-click buff NPC, since solo play means no group to hand out the usual pre-pull buffs — see [NPC Buffer](#npc-buffer) below to spawn it
  - [mod-cfbg](https://github.com/azerothcore/mod-cfbg) — pools both factions into the same battleground instance, so the fixed random-bot population actually fills PvP queues instead of waiting on one faction alone
  - [mod-account-achievements](https://github.com/azerothcore/mod-account-achievements) — shares achievement progress across every character on your account, matching retail behavior since Legion
- **Database**: MySQL 8.4 (official `mysql:8.4` image — not customized)
- **Orchestration**: Docker Compose

## Prebuilt images

The C++ side (core + all eight modules) is baked into these images — no build toolchain needed to run the server:

| Image | Purpose |
|---|---|
| [`estebanmorenoit/ac-wotlk-worldserver-playerbots`](https://hub.docker.com/r/estebanmorenoit/ac-wotlk-worldserver-playerbots) | World server (game logic), with all eight modules built in |
| [`estebanmorenoit/ac-wotlk-authserver-playerbots`](https://hub.docker.com/r/estebanmorenoit/ac-wotlk-authserver-playerbots) | Auth/login server (port 3724) |
| [`estebanmorenoit/ac-wotlk-db-import-playerbots`](https://hub.docker.com/r/estebanmorenoit/ac-wotlk-db-import-playerbots) | One-shot DB bootstrap/migration (run before the servers) |
| [`estebanmorenoit/ac-wotlk-client-data-playerbots`](https://hub.docker.com/r/estebanmorenoit/ac-wotlk-client-data-playerbots) | One-shot client data extraction (maps/vmaps/mmaps/dbc) |

All four images are built from the same `apps/docker/Dockerfile`, using a different `target` per service — but from a **personal fork** ([`estebanmorenoit/azerothcore-wotlk`](https://github.com/estebanmorenoit/azerothcore-wotlk)) with its own GitHub Actions workflow (`esteban-custom-build.yml`, manual `workflow_dispatch` trigger), not the upstream project's own CI. That workflow checks out all eight modules explicitly (`modules/*` is gitignored in the core repo by design — see its `modules/CMakeLists.txt` for the generic auto-discovery mechanism modules rely on) and patches a known upstream compile bug in mod-llm-chatter before building. Images currently deployed here use the `:cfbg-achievements-test` tag rather than `:master`, so `:npc-buffer-test`, `:progression-test`, `:ahbot-test`, and the original pre-mod images stay available as known-good fallbacks — see `docker-compose.yml`.

**Exception**: mod-llm-chatter's Python bridge (`ac-llm-chatter-bridge` below) isn't one of the four prebuilt Hub images above, since it's a separate Python process from the C++ world server. Its build pulls source straight from `Hokken/mod-llm-chatter` on GitHub via a git build context — no local clone needed for this piece. (`deploy.sh` below still clones `modules/mod-llm-chatter` anyway, since `ac-worldserver` mounts the whole `modules/` tree for its `.conf.dist` templates — that's unrelated to the bridge's build.)

## Running it

[`deploy.sh`](./deploy.sh) does the whole thing in one command: clones the core and all eight modules next to itself (skipped if already present, so it's safe to re-run), copies in `docker-compose.yml`, generates a random `DB_ROOT_PASSWORD` into a gitignored `.env` (skipped if `.env` already exists), materializes each module's `.conf` from its `.dist` template, brings the stack up in the right order, points the realm at this host's address, and creates a game login account once worldserver is ready.

The C++ side of `mod-playerbots`, `mod-solocraft`, `mod-ah-bot`, `mod-individual-progression`, `mod-npc-buffer`, `mod-cfbg`, and `mod-account-achievements` is already baked into the prebuilt images, but deploy.sh still clones all seven — without their `conf/*.conf.dist` templates present on disk, `playerbots.conf`/`Solocraft.conf`/`mod_ahbot.conf`/`individualProgression.conf`/`npc_buffer.conf`/`CFBG.conf`/`mod_achievements.conf` never get generated and worldserver runs with incomplete config (missing `playerbots.conf`/`Solocraft.conf` has crashed the server outright on a prior deploy).

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

Bot behavior is controlled via environment variables on the `ac-worldserver` service — see [`docker-compose.yml`](./docker-compose.yml). Current tuning: 75 random bots (ambient world population, separate from the companions you recruit into your own party), leveled to match real players, clustered near player zones, built-in greet disabled (mod-llm-chatter handles chat instead), and per-bot AI cost cut from the core default of 10 to 6 iterations per tick — raise either if you have cores to spare (this host is 4 threads total, shared with ~30 unrelated containers, and `ac-worldserver` is capped at 3 CPUs / 6GB via `deploy.resources.limits` so it can't starve the rest of the box). `AC_MAP_UPDATE_THREADS=3` spreads map/world ticks across those same 3 cores instead of pinning them to one.

mod-llm-chatter's bridge polls the database for chat requests; `LLMChatter.Bridge.PollIntervalSeconds` in `mod_llm_chatter.conf` was raised from its 1s default to 10s — a 1s loop was a measurable chunk of the bridge's CPU for a queue that's rarely hot, and 10s is imperceptible for ambient bot chat.

## Auction House bot

`mod_ahbot.conf` ships **disabled** (`AuctionHouseBot.EnableSeller = false`, `AuctionHouseBot.GUIDs = 0`) — it needs at least one real character GUID before it'll list anything, and that has to come from you:

1. Log into the game with a normal (non-GM, non-playerbot) character you're fine never playing again — the module's own docs warn that browsing the AH with that same character can hang with "Searching for items..." forever.
2. Find its GUID: `.character info` in-game while targeting/logged in as that character, or query `SELECT guid, name FROM characters WHERE name = 'YourCharName';` against `acore_characters`.
3. Edit `env/dist/etc/modules/mod_ahbot.conf` on the host: set `AuctionHouseBot.GUIDs` to that GUID and `AuctionHouseBot.EnableSeller = true`.
4. `.ahbot reload` in-game (GM) to pick up the change without a restart, then `.ahbot update` to force the first batch of listings rather than waiting for the next cycle.

It only adds `AuctionHouseBot.ItemsPerCycle` (75 by default) items per tick, so expect the AH to take a few hours to fully populate. `.ahbot empty` clears bot-listed auctions if you want to reset and retune pricing (player auctions are untouched).

## Progression

`mod-individual-progression` gates world content by era — Vanilla → TBC → WotLK — server-side, on the same 3.3.5a client. Ships **enabled** by default (`IndividualProgression.Enable = 1`) with no per-character setup needed, unlike the AH bot above.

Two core-level prerequisites the module needs are set automatically via `docker-compose.yml` env vars rather than requiring a manual config edit:
- `AC_ENABLE_PLAYER_SETTINGS=1` — the module stores each character's progression phase using AzerothCore's player-settings system, which is off by default.
- `AC_DBC_ENFORCE_ITEM_ATTRIBUTES=0` — lets the module override item stats to their period-correct Vanilla/TBC values.

Its own README confirms explicit **Playerbots support** — this is why it was chosen over the alternative (NPCBots), which this server doesn't run.

Notes:
- **This module assumes a fresh start.** It's designed around characters progressing through content in original release order — it wasn't added retroactively onto an already-leveled save, since existing characters/zones/gear wouldn't reflect an accurate phase state.
- `IndividualProgression.EnforceGroupRules` ships **disabled** (`0`) — enabling it would restrict grouping to only characters in the same progression phase, which would fragment solo bot parties by phase. Left off for that reason.
- An **optional client-side `.mpq` patch** (in the module's `optional/` folder) adds extra period authenticity (era-correct reagents, etc.) — not required, the module works fully without it.
- See the module's own [list of changes](https://github.com/ZhengPeiRu21/mod-individual-progression/wiki/List-of-Changes) and [progression tiers](https://github.com/ZhengPeiRu21/mod-individual-progression/wiki/List-of-Progression-Tiers) for what each phase unlocks.

## NPC Buffer

`mod-npc-buffer` adds a one-click buff NPC ("Buffmaster Hasselhoof") — useful since solo play means no group to hand out the usual pre-pull buffs. Ships **enabled** (`Buff.Enable = 1`) with a solid default spell list, but the module only adds the *creature template*, not a spawn — it doesn't appear anywhere until placed:

1. `.gm on`, walk to wherever you want it (a capital city is the usual choice).
2. `.npc add 601016` to spawn it at your current location.

Configurable in `env/dist/etc/modules/npc_buffer.conf` — spell list (`Buff.Spells`), level-scaling (`Buff.ByLevel`/`Buff.MaxLevel`), and flavor text/emotes.

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

## Backups

[`backup.sh`](./backup.sh) dumps every database (`--all-databases`, so characters/guilds/world/playerbots/auth) from the running `ac-database` container to a timestamped, gzip-compressed file under `backups/` (gitignored — never committed), then deletes local dumps older than `BACKUP_RETENTION_DAYS` (default 30).

```bash
./backup.sh
```

A cron job runs it daily at 04:00, logging to `backups/backup.log`:
```
0 4 * * * /home/esteban/wow-server-playerbots/backup.sh >> /home/esteban/wow-server-playerbots/backups/backup.log 2>&1
```

Restore a dump:
```bash
gunzip -c backups/<file>.sql.gz | docker exec -i ac-database mysql -u root -p"$DB_ROOT_PASSWORD"
```

These are local backups only — if the disk itself is lost, they're gone too. Consider copying `backups/` off-host periodically for real disaster recovery.

## Monitoring

[`monitoring/docker-compose.yml`](./monitoring/docker-compose.yml) is a standalone Prometheus + Grafana stack — separate from the game stack so it can be started/stopped independently, and doesn't touch the existing homelab reverse proxy:

```bash
cd monitoring && docker compose up -d
```

- **Grafana**: `http://<this-host>:3000` — default login `admin` / `admin` (change it if this host is reachable beyond your own network)
- **Prometheus**: `http://<this-host>:9090` — mainly for direct query/debugging
- **cAdvisor** collects per-container metrics (CPU/mem/network for every container on this host, including `ac-worldserver` et al. — the same numbers `docker stats` shows, but recorded over time)
- **node-exporter** collects host-level metrics (CPU, memory, disk, load)

Two dashboards are pre-provisioned automatically (no manual import needed): **Node Exporter Full** ([1860](https://grafana.com/grafana/dashboards/1860), host metrics) and **Cadvisor exporter** ([14282](https://grafana.com/grafana/dashboards/14282), per-container). Both pinned to specific revisions in `monitoring/grafana/provisioning/dashboards/json/` — re-download a newer revision manually if you ever want an update.

Picking a working per-container dashboard took a few tries — worth knowing before adding another community dashboard here. Our cAdvisor (plain Docker + the standalone `gcr.io/cadvisor/cadvisor` image) labels containers with `name`/`image`/`container_label_*`. A dashboard's *popularity* says nothing about whether it matches that: two of the most-downloaded options on grafana.com — [10619](https://grafana.com/grafana/dashboards/10619) (6.2M downloads) and its near-duplicates — predate a 2018 node_exporter metric rename (`node_memory_MemTotal` etc. no longer exist) and reference the original author's hardcoded IPs; [15798](https://grafana.com/grafana/dashboards/15798) (2M downloads, actively updated) expects `container`/`service` labels, which is a **Kubernetes**-cAdvisor labeling convention our plain-Docker cAdvisor doesn't produce. Both looked fine on paper and showed zero data in practice. 14282 was chosen because its queries only reference `name`/`instance` — verified live against Prometheus, not just eyeballed — and it's still 8x more downloads than the alternative that happened to also work ([19792](https://grafana.com/grafana/dashboards/19792)).

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
4. **Re-check bot count against the new host's core count.** On the original 4-core box (shared with ~30 unrelated containers), 75 random bots at `AC_AI_PLAYERBOT_ITERATIONS_PER_TICK=6` already used ~2 of those cores — that's the number the `deploy.resources.limits: cpus: "3.0"` cap on `ac-worldserver` was sized around. A more powerful NUC has headroom to raise `AC_AI_PLAYERBOT_MAX_RANDOM_BOTS` (and could safely raise `AC_AI_PLAYERBOT_ITERATIONS_PER_TICK` back toward the core default of 10 for slightly sharper bot behavior) if you want a denser world — just raise the `cpus`/`memory` limits above to match, and remember mod-playerbots auto-provisions however many `RNDBOT` accounts `MaxRandomBots` needs (`AiPlayerbot.RandomBotAccountCount = 0` in `playerbots.conf`), so there's no separate account-count step.
5. **Open port 3724** (and 8085 if you want direct world-server access) in the new host's firewall/router, then update `realmlist.wtf` on any client to the new address.

## Commanding bots in-game

Invite any bot to your party like a normal player, or right-click their portrait for a recruit option. Whisper a bot **"help"** in-game for its full list of orders — follow, stay, equip, spec, and more.
