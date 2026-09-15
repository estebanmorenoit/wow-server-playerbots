# wow-server-playerbots

[![CI](https://github.com/estebanmorenoit/wow-server-playerbots/actions/workflows/ci.yml/badge.svg)](https://github.com/estebanmorenoit/wow-server-playerbots/actions/workflows/ci.yml)
[![Website](https://img.shields.io/badge/website-The%20Solo%20Realm-d4af6a)](https://estebanmorenoit.github.io/wow-server-playerbots/)
[![WotLK](https://img.shields.io/badge/WoW-3.3.5a%20(WotLK)-4a5dc7)](#connecting-a-client)
[![AzerothCore](https://img.shields.io/badge/built%20on-AzerothCore-c0392b)](https://www.azerothcore.org/)
[![Docker Compose](https://img.shields.io/badge/orchestration-Docker%20Compose-2496ed?logo=docker&logoColor=white)](./docker-compose.yml)
[![Worldserver image size](https://img.shields.io/docker/image-size/estebanmorenoit/ac-wotlk-worldserver-playerbots/master?label=worldserver%20image)](https://hub.docker.com/r/estebanmorenoit/ac-wotlk-worldserver-playerbots)
[![License: MIT](https://img.shields.io/badge/license-MIT-yellow.svg)](./LICENSE)

Self-hosted **World of Warcraft: Wrath of the Lich King (3.3.5a, build 12340)** private server, built on [AzerothCore](https://www.azerothcore.org/) — solo-play focused, populated by AI bots instead of real players.

Not affiliated with Blizzard Entertainment. For personal/private-server use.

[MIT](./LICENSE) covers what's actually authored in this repo — `deploy.sh`, `backup.sh`, `wake-proxy/`, `docker-compose.yml`, this README. It does *not* cover AzerothCore or the ten modules, which `deploy.sh` clones separately at deploy time straight from their own repos, each under its own license (mostly AGPL-3.0/GPL-2.0) — check those repos directly if that matters for your use case.

## Contents

- [What's running](#whats-running)
- [Prebuilt images](#prebuilt-images)
- [Quick start](#quick-start)
- [Connecting a client](#connecting-a-client)
- [Modules in detail](#modules-in-detail)
- [GM commands reference](#gm-commands-reference-this-build)
- [Operations](#operations)
- [Known issues](#known-issues)

---

## What's running

- **Core**: AzerothCore (fork: [`mod-playerbots/azerothcore-wotlk`](https://github.com/mod-playerbots/azerothcore-wotlk), Playerbot branch)
- **Database**: MySQL 8.4 (official `mysql:8.4` image — not customized)
- **Orchestration**: Docker Compose
- **Auto-sleep / wake-on-connect**: the game stack sits fully stopped when nobody's playing and wakes itself the moment a client connects — see [Auto-sleep / wake-on-connect](#auto-sleep--wake-on-connect). Practical effect: your *very first* login after a break shows a connection error while it boots, then works normally — see [Connecting a client](#connecting-a-client).

```mermaid
graph LR
    Client["WoW 3.3.5a client"] -- "3724 / 8085" --> Proxy["wake-proxy\n(always on)"]
    Proxy -- "wakes + relays" --> Auth["ac-authserver"]
    Proxy -- "wakes + relays" --> World["ac-worldserver"]
    Auth --> DB[("ac-database")]
    World --> DB
    World --> Bridge["ac-llm-chatter-bridge"]
    Bridge -- "API call" --> LLM["your LLM provider"]
```

**Modules** (all under `modules/`, compiled statically into `worldserver`):

| Module | What it does |
|---|---|
| [mod-playerbots](https://github.com/mod-playerbots/mod-playerbots) | AI-controlled bot characters that populate the world |
| [mod-solocraft](https://github.com/azerothcore/mod-solocraft) | Scales dungeon/raid boss stats to your actual group size |
| [mod-llm-chatter](https://github.com/Hokken/mod-llm-chatter) | Bots chat dynamically via a real LLM instead of canned lines |
| [mod-ah-bot](https://github.com/NathanHandley/mod-ah-bot-plus) | Populates the Auction House with bot-driven listings — [setup](#auction-house-bot) |
| [mod-individual-progression](https://github.com/ZhengPeiRu21/mod-individual-progression) | Gates content by era (Vanilla → TBC → WotLK) on the same client — [details](#progression) |
| [mod-npc-buffer](https://github.com/azerothcore/mod-npc-buffer) | One-click buff NPC — [setup](#npc-buffer) |
| [mod-cfbg](https://github.com/azerothcore/mod-cfbg) | Cross-faction battlegrounds, so the fixed bot population fills PvP queues |
| [mod-account-achievements](https://github.com/azerothcore/mod-account-achievements) | Shares achievement progress across every character on your account |
| [mod-instance-reset](https://github.com/azerothcore/mod-instance-reset) | Reset your own dungeon/raid lockouts on demand — [setup](#instance-reset) |
| [mod-random-enchants](https://github.com/azerothcore/mod-random-enchants) | Chance of bonus random enchantments on looted/quest/crafted/rolled items — [details](#random-enchants) |

---

## Prebuilt images

The C++ side (core + all ten modules) is baked into these images — no build toolchain needed to run the server:

| Image | Purpose |
|---|---|
| `estebanmorenoit/ac-wotlk-worldserver-playerbots` | World server (game logic), all ten modules built in |
| `estebanmorenoit/ac-wotlk-authserver-playerbots` | Auth/login server (port 3724) |
| `estebanmorenoit/ac-wotlk-db-import-playerbots` | One-shot DB bootstrap/migration (runs before the servers) |
| `estebanmorenoit/ac-wotlk-client-data-playerbots` | One-shot client data extraction (maps/vmaps/mmaps/dbc) |

All four are built from the same `apps/docker/Dockerfile` (different `target` per service), from a **personal fork** ([`estebanmorenoit/azerothcore-wotlk`](https://github.com/estebanmorenoit/azerothcore-wotlk)) with its own GitHub Actions workflow — `build-images.yml`, manual `workflow_dispatch` trigger only, never runs on push. That workflow checks out all ten modules explicitly (`modules/*` is gitignored in the core repo by design) and patches a known upstream compile bug in mod-llm-chatter before building.

Each new module gets its own image tag while under test, rather than overwriting `:master` directly — once verified working (as `:quest-loot-fix-test` was, on 2026-09-14), it gets promoted *to* `:master`, which is what's actually deployed now. `:instance-reset-test`, `:cfbg-achievements-test`, `:npc-buffer-test`, `:progression-test`, and `:ahbot-test` remain in `docker-compose.yml`'s history as earlier known-good states if a rollback is ever needed.

**Keeping the fork's core current**: [`sync-upstream.yml`](https://github.com/estebanmorenoit/azerothcore-wotlk/blob/Playerbot/.github/workflows/sync-upstream.yml) (in that fork, not this repo) runs weekly and opens a PR there whenever `mod-playerbots/azerothcore-wotlk`'s `Playerbot` branch gets new commits — detection-only, never merges or rebuilds on its own. Bringing in a change, rebuilding under a test tag, and promoting to `:master` here all stay separate, manual, deliberate steps.

**Exceptions — not Docker Hub images, built locally instead:**
- `ac-llm-chatter-bridge` — mod-llm-chatter's Python bridge, built straight from `Hokken/mod-llm-chatter` on GitHub via a git build context (no local clone needed for the build itself). `deploy.sh` still clones `modules/mod-llm-chatter` anyway, since `ac-worldserver` mounts the whole `modules/` tree for `.conf.dist` discovery.
- `wake-proxy` — a tiny stdlib-only Python relay (no dependencies to speak of), built from [`wake-proxy/`](./wake-proxy/) in *this* repo (`build: ./wake-proxy` in `docker-compose.yml`). Building the four C++ images is expensive enough to justify hosting pre-built copies on Docker Hub; this one builds from source in a couple of seconds, so there's no benefit to a registry — and building straight from committed source means the running image can never drift from what's actually in git. `docker compose up -d` builds it automatically the first time, same as any fresh deploy of the rest of the stack — nothing extra to run by hand.

---

## Quick start

**Without LLM-driven bot chat:**
```bash
./deploy.sh
```

**With it** — pick a provider and supply your real key (never committed, never baked into any image; written only to the gitignored `mod_llm_chatter.conf`):
```bash
LLM_PROVIDER=google LLM_API_KEY=<your Gemini key> ./deploy.sh
```
`LLM_PROVIDER` is one of `anthropic`, `openai`, `google`, `openrouter`, or `ollama` (`ollama` needs no key). `deploy.sh` also sets `LLMChatter.Model` to that provider's tested default (e.g. `gemini-3.1-flash-lite` for `google`) — the `.dist` template ships with an Anthropic model ID regardless of provider, so switching providers without fixing the model causes API calls to 404. Override with `LLM_MODEL` (required for `ollama`). Didn't set a provider the first time? Re-run `deploy.sh` later with `LLM_PROVIDER`/`LLM_API_KEY` set — it fills in your existing `mod_llm_chatter.conf` without touching anything else you've customized.

`deploy.sh` is **safe to re-run** — it never overwrites an existing checkout, `.env`, or any module conf; it only fills in what's missing. See the script's own header comment for the full env var list (`DEPLOY_DIR`, `DB_ROOT_PASSWORD`, etc.).

### Realm address

`deploy.sh` auto-detects this host's LAN address and points the realm at it, so remote clients can actually complete login — the database default (`127.0.0.1`) only works for a client on this exact machine; everyone else gets stuck at realm select. Override if auto-detection picks the wrong interface (multiple NICs, VPNs, behind NAT):
```bash
REALM_ADDRESS=<your public or LAN IP> ./deploy.sh
```
Re-runs on every `deploy.sh` invocation, so it stays correct if the host's IP changes. No restart needed — authserver re-reads the `realmlist` table live.

### Login account

`deploy.sh` creates a game account and promotes it to GM (level 3) once worldserver is ready — default `admin` / `test1234`. **That password is a placeholder, not something to leave in place on a server anyone else can reach.** Override it:
```bash
ADMIN_ACCOUNT_NAME=myaccount ADMIN_ACCOUNT_PASSWORD=<a real password> ./deploy.sh
```
Names are capped at 17 characters (client limit). Set `ADMIN_ACCOUNT_NAME=` (empty) to skip account creation. Only ever attempted once per checkout (tracked by a `.admin-account-created` marker) — re-running won't recreate it or touch its GM level.

To create additional accounts later:
```bash
docker attach ac-worldserver
account create <username> <password>
account set gmlevel <username> 3 -1   # optional, grants GM access
```
Detach with `Ctrl-P` then `Ctrl-Q` — **never `Ctrl-C`**, which kills the worldserver process itself.

> **Heads up if you already had a character before granting GM access**: AzerothCore reads your account's security level once, at login, and caches it on that connection for the whole session. If you grant GM access to an account that's already logged in, `.gm on` and other GM commands will fail as "unknown command" until you fully disconnect from the realm (not just log out to character select) and log back in.

### Uninstalling

```bash
./deploy.sh --uninstall            # stop/remove containers+networks; keeps DB, client-data, checkout — reversible
./deploy.sh --uninstall --purge    # also deletes the database, client-data, and checkout — PERMANENT
```
Both prompt for a typed `yes`; add `-y`/`--yes` to skip for scripting. `--purge` destroys every character, guild, and all progress with no undo — [back up the database first](#backups) if there's any chance you'll want it again.

---

## Connecting a client

Requires a **3.3.5a (build 12340)** WotLK client.

1. Find `realmlist.wtf` inside your client install: `<WoW folder>/Data/enUS/realmlist.wtf` (match your locale folder, e.g. `enGB`, `deDE`)
2. Replace its contents with:
   ```
   set realmlist <YOUR_SERVER_IP>
   ```
3. Launch the client — it authenticates on port 3724 and hands off to the world server.

**If the server has been idle for a while, your first login attempt will likely show a connection error.** That's expected — see [Auto-sleep / wake-on-connect](#auto-sleep--wake-on-connect): the stack sleeps itself when nobody's playing and wakes on your first connection attempt, which takes ~60-70s. Just wait a minute and log in again; it'll go straight through from then on.

If chat is enabled, check it actually worked: `docker logs ac-llm-chatter-bridge` should show five `[PASS]` lines (config, module enabled, provider config, database connection, live connectivity test). Any `[FAIL]` means bots won't chat until it's fixed.

---

## Modules in detail

### Playerbots

Bot behavior lives in `ac-worldserver`'s environment variables in [`docker-compose.yml`](./docker-compose.yml). Current tuning, sized for this host (2 physical cores / 4 threads — 0+2 share one core, 1+3 the other, per `lscpu -e` — shared with ~30 unrelated containers running other self-hosted services):

- **100 random bots** — ambient world population, leveled to match real players, clustered near player zones. This is separate from bots you recruit into your own party. Measured on this host: bot count in the 50-100 range didn't cleanly track CPU cost (most of `ac-worldserver`'s cost is fixed overhead — tick loop, map updates, DB polling, the LLM chatter bridge — not bot-count-proportional), so this is sized for a fuller world rather than a strict CPU ceiling.
- **Social/PvP/economy tuning** (all via `AC_AI_PLAYERBOT_*` env vars, verified live against this exact core's own config-loader log — it prints `Config: Found config value '<key>' from environment variable '<VAR>'` for each one on every boot):
  - `RandomBotEmote` — cosmetic idle emotes (wave, dance, sit), no behavioral effect.
  - `RandomBotAutoJoinBG` — bots actually queue for battlegrounds instead of only being eligible if invited; the only way `pvpstats_battlegrounds` ever gets real rows.
  - `RandomBotGuildNearby` / `RandomBotGroupNearby` — bots bias toward wandering near their own guild/group members rather than positioning independently of who they're nominally with. Gradual effect (bias on future movement, not a teleport), not instant.
  - `EnableGuildTasks` — guild "task board" system. Marked **"Deprecated Settings (yet still in use)"** in the module's own `playerbots.conf.dist` — the first thing to suspect and revert if anything guild-related looks off.
  - `EnableRandomBotTrading` — direct trade-window trading between bots (`TradeAction.cpp`), separate from and unrelated to the Auction House — see [Auction House bot](#auction-house-bot) for why AH bot-to-bot trading specifically can't happen.
  - `RandomBotJoinBG`/`RandomBotJoinLfg` (module defaults, no override needed) — bots are eligible for battlegrounds/LFG groups at all.
- **6 AI iterations/tick** (core default is 10) — per-bot AI cost cut for CPU headroom.
- `ac-worldserver` itself is capped at **3 CPUs / 12GB** via `deploy.resources.limits`, so it can't starve the rest of the host.
- **`cpuset: "1,2,3"` on every game-stack service** (`ac-database`, `ac-authserver`, `ac-worldserver`, `ac-llm-chatter-bridge`, `wake-proxy`, plus the one-shot init containers) reserves that same 3-of-4 as a *hard* boundary, not just a self-imposed ceiling — the other ~30 containers on this host were pinned live to thread 0 only (`docker update --cpuset-cpus=0`, not tracked in this repo since those containers belong to other projects), so they can no longer invade the threads reserved for the game.
- `AC_MAP_UPDATE_THREADS=3` spreads map/world ticks across those same 3 threads — though AzerothCore parallelizes per-*map*, not within one, so a single hot continent (e.g. everyone clustered near you) is still bound to one thread's clock speed regardless of how many are reserved. Tested disabling `AC_AI_PLAYERBOT_RANDOM_BOT_CONCENTRATE_IN_PLAYER_ZONE` to spread bots onto other maps and actually use that parallelism — it cut `ac-worldserver`'s own CPU (~164%→146%) but didn't move CPU pressure stall (the real contention/lag indicator, ~23% either way), so it was reverted: not worth losing bots visible near you for a change that didn't fix the felt lag.
- Turbo Boost is permanently disabled host-wide (`/etc/systemd/system/cpu-no-turbo.service`) — this is a thermal decision (package temp sits at 85-88°C even without it), not something this project's tuning should try to override.
- Built-in bot greet is disabled — mod-llm-chatter replaces it.
- mod-llm-chatter's DB poll interval is 10s (default is 1s) — a 1s loop was measurable CPU for a queue that's rarely hot.

**Commanding bots in-game:**
```
/invite <botname>                  — recruit a bot like a normal player (or right-click their portrait)
.playerbots bot addclass <class> [male|female]   — add a random bot of a class to your party
.playerbots bot add <botname>      — add a specific existing bot by name
.playerbots bot list               — list your current bots
.playerbots bot remove <botname>   — remove one AND despawn it (see note below)
```
(Top-level command is `playerbots`, plural — confirmed against the module's own command registration; `.playerbot` without the `s` doesn't exist.)

Valid classes for `addclass`: `warrior`, `paladin`, `hunter`, `rogue`, `priest`, `shaman`, `mage`, `warlock`, `druid`, `dk`.

**Uninviting a bot ≠ removing it.** A plain `/uninvite` (or right-click → Uninvite) only drops the bot from your party roster — its "follow master" order is tracked separately from group membership, so it keeps trailing behind you. Use `.playerbots bot remove <botname>` instead, which calls the bot's actual logout function and makes it disappear for good.

Once in your party, whisper a bot **"help"** for its full order list — follow, stay, equip, spec, and more.

### Auction House bot

`mod_ahbot.conf` ships with selling **disabled** (`AuctionHouseBot.EnableSeller = false`, `GUIDs = 0`) — it needs a real character GUID before it'll list anything:

1. Log into the game with a normal (non-GM, non-playerbot) character you're fine never playing again — browsing the AH with that same character can hang with "Searching for items..." forever, per the module's own docs.
2. Find its GUID: target it and run `.guid`, or query `SELECT guid, name FROM characters WHERE name = 'YourCharName';` against `acore_characters`.
3. Edit `env/dist/etc/modules/mod_ahbot.conf`: set `AuctionHouseBot.GUIDs` to that GUID and `EnableSeller = true`.
4. `.ahbot reload` (GM) to apply without a restart, then `.ahbot update` to force the first batch instead of waiting for the next cycle.

Adds `ItemsPerCycle` (75 by default) items per tick, so expect a few hours to fully populate. `.ahbot empty` clears bot-listed auctions to reset/retune pricing (player auctions untouched).

**Buyer bots are enabled** (`AC_AUCTION_HOUSE_BOT_BUYER_ENABLED=true` in `docker-compose.yml`) — bots also bid on and buy listings, including yours, so anything you list has real bidders. `BidAgainstPlayers` stays at its default (`false`): bots still bid, they just won't escalate a bidding war against you specifically.

**The buyer never buys the seller's own stock, by design** (`AddNewAuctionBuyerBotBid` excludes `itemowner`/`buyguid` matching any GUID in `AuctionHouseBot.GUIDs` — otherwise it'd just endlessly buy back its own randomly-generated inventory). With this build's single-GUID setup, that means **100% of the fake seller-driven listings are permanently unbuyable** — a completed sale only ever happens when *you* personally list something, since your character's GUID isn't excluded. Playerbots (the AI companions) also have no auction-house code at all, so there's no bot-to-bot AH trading to observe either way — everything you might see about "the market" is really just the seller bot's simulated stock, not activity between bots.

### Progression

`mod-individual-progression` gates world content by era — Vanilla → TBC → WotLK — server-side, on the same 3.3.5a client. Ships **enabled** by default, no per-character setup needed.

Two core-level prerequisites are set automatically via `docker-compose.yml` env vars (AzerothCore's generic `AC_<KEY>` config-override mechanism) rather than a manual edit:
- `AC_ENABLE_PLAYER_SETTINGS=1` — the module stores each character's phase using AzerothCore's player-settings system, off by default.
- `AC_DBC_ENFORCE_ITEM_ATTRIBUTES=0` — lets the module override item stats to period-correct Vanilla/TBC values.

Check your current tier: `.ip get [player]`. Full ladder:

| Level | Unlocks |
|---|---|
| 0 | Start — Molten Core next |
| 1 | Molten Core cleared → Blackwing Lair |
| 2 | Onyxia |
| 3 | Blackwing Lair → Zul'Gurub, AQ war effort |
| 4 | Pre-AQ → AQ gates opening |
| 5 | AQ war effort |
| 6 | AQ → Naxxramas (40) + Scourge Invasion |
| 7 | Naxx40 → Into the Breach (TBC transition) |
| 8 | Pre-TBC → Karazhan, Gruul's, Magtheridon's |
| 9–10 | TBC Tier 1–2 → Serpentshrine/Tempest Keep, then Hyjal/Black Temple |
| 12–13 | TBC Tier 4–5 → Sunwell, then WotLK Naxx/EoE/OS |
| 14–18 | WotLK Tier 1–5 → Ulduar → ToC → ICC → Ruby Sanctum |

Notes:
- **Assumes a fresh start.** Designed around progressing through content in original release order — not meant to be added retroactively onto an already-leveled save.
- `IndividualProgression.EnforceGroupRules` ships **disabled** — enabling it would restrict grouping to characters in the same phase, fragmenting solo bot parties.
- An **optional client-side `.mpq` patch** (module's `optional/` folder) adds extra period authenticity (era-correct reagents, etc.) — not required.
- See the module's own [list of changes](https://github.com/ZhengPeiRu21/mod-individual-progression/wiki/List-of-Changes) and [progression tiers](https://github.com/ZhengPeiRu21/mod-individual-progression/wiki/List-of-Progression-Tiers) wiki pages for full detail.
- Explicit **Playerbots support**, confirmed in the module's own README — why it was chosen over the alternative (NPCBots), which this server doesn't run.

### NPC Buffer

One-click buff NPC ("Buffmaster Hasselhoof"). Ships **enabled** with a solid default spell list, but only the *creature template* is added — it doesn't spawn anywhere until placed:

```
.gm on
.npc add 601016
```
(walk to wherever you want it first — a capital city is the usual choice)

Configurable in `env/dist/etc/modules/npc_buffer.conf` — spell list (`Buff.Spells`), level-scaling (`Buff.ByLevel`/`Buff.MaxLevel`), flavor text/emotes.

### Instance Reset

Talk to an NPC to reset your own dungeon/raid lockouts on demand instead of waiting out the timer. Ships **enabled and free** (`TransactionType = 0`), resets all difficulties by default. Same activation pattern:

```
.gm on
.npc add 300000
```

It **won't reset the instance you're currently standing in** — leave it first, then talk to the NPC. Configurable in `env/dist/etc/modules/instance-reset.conf` — `TransactionType` can charge money and/or a token (1/2/3) instead of free (0).

### Random Enchants

Chance of a bonus enchantment (or two, or three) on items you loot, get from quest rewards, craft via professions, or win from a group roll — stacks on top of the item's normal stats rather than replacing them. Ships **enabled**, but tuned down from upstream's defaults: the `.dist` template's 70%/65%/60% chances would enchant most eligible items, which reads as "every drop is special" rather than an occasional treat, so `deploy.sh` sets these to 15%/10%/5% on first deploy (≈15% chance of one enchant, ≈1.5% of two, ≈0.075% of three). Configurable in `env/dist/etc/modules/random_enchants.conf` — also toggles which sources apply (`RandomEnchants.OnLoot`/`OnCreate`/`OnQuestReward`/`OnGroupRoll`) and the login announcement message.

---

## GM commands reference (this build)

Verified against this exact core's command tables (`src/server/scripts/Commands/`) — some tutorials online reference commands from other cores that don't exist here (e.g. there's no `.modify xp`).

**Basics**
```
.gm on / .gm off                          — toggle GM mode
.additem <id> [count]                     — add an item to your inventory
.character level <player> <level>         — set a character's level directly
.character levelup [amount] [player]      — relative level up/down (no flat "add xp" command)
.modify speed [all|walk|run|swim|flight|backwalk] <n>  — movement speed multiplier
.learn all my / .learn all gm             — learn your class's full spellbook / the GM spellbook
.revive [player]                          — resurrect
.cheat god|cooldown|casttime|power on/off — invulnerability / no cooldowns / instant cast / unlimited resources
.reload config                            — re-read worldserver.conf live — no restart needed
.account set gmlevel <account> <level> -1 — grant GM access, all realms
.gear repair [player]                     — repair all of a player's equipped items (no plain ".repairitems" on this core)
.saveall                                  — force-save every online character now (e.g. right before a backup or restart)
```

**Teleport**
```
.tele <name>                              — teleport to a saved location (pre-populated with common zones/cities)
.tele add <name>  /  .tele del <name>     — save your current location / delete a saved one
.tele group <name>                        — teleport your whole group (including recruited bots)
.tele name npc id|guid|name <value>       — teleport to where a specific NPC spawns
.appear <player>  /  .summon <player>     — teleport to a player / teleport a player to you
.cometome                                 — bring your current target to you (e.g. an unstuck bot)
.recall                                   — return to where you were before your last teleport
.unstuck                                  — rescue yourself if stuck in geometry
.distance                                 — distance to your current target
```

**NPCs & objects**
```
.lookup item|creature|spell <name>        — find IDs for .additem/.npc add/.learn by name
.guid                                     — GUID of your current target (e.g. mod-ah-bot's activation step)
.npc add <id>  /  .npc delete             — spawn / remove an NPC at your location
.npc set level <level>                    — change a spawned NPC's level
.gobject near [radius]  /  .gobject add <id>  — list nearby objects with GUIDs / spawn one
.respawn                                  — respawn the selected creature/object now, ignoring its respawn timer
```

**Character & testing**
```
.die                                      — kill yourself instantly (fast death/revive testing)
.morph <displayid>  /  .demorph           — change your appearance
.pinfo [player]                           — account + guild info for a player (GM level, email, last IP, etc.)
.maxskill                                 — max out all of the selected player's skills for their current level
```

**Module-specific**
```
.ahbot reload / .ahbot update / .ahbot empty   — apply config changes / force a listing cycle / clear bot listings
.ip get [player]                          — check mod-individual-progression's current tier
.playerbots bot add|addclass|list|remove   — see Playerbots above
```

`Rate.XP.Kill` / `Rate.XP.Quest` / `Rate.XP.Quest.DF` / `Rate.XP.Explore` / `Rate.XP.Pet` in `worldserver.conf` control passive XP gain server-wide (core default `1`) — currently set to **`2`** (kill/quest/exploration/pet leveling roughly twice as fast; loot/drop rates are untouched, so gearing pace stays normal relative to quests). Change and run `.reload config` to apply live, no restart needed.

---

## Operations

### Backups

[`backup.sh`](./backup.sh) dumps every database (`--all-databases`) to a timestamped, gzip-compressed file under `backups/` (gitignored), then deletes local dumps older than `BACKUP_RETENTION_DAYS` (default 30).

```bash
./backup.sh
```

A cron job (installed automatically by `deploy.sh`) runs it daily at 04:00:
```
0 4 * * * /home/esteban/wow-server-playerbots/backup.sh >> /home/esteban/wow-server-playerbots/backups/backup.log 2>&1
```
If the [auto-sleep](#auto-sleep--wake-on-connect) has stopped the stack (the normal state most of the day), `backup.sh` wakes just `ac-database` for the duration of the dump and puts it back to sleep afterward — a fixed-time cron would otherwise fail on any night nobody's playing at 04:00.

That fixed-time gap is also why `wake-proxy` takes its own backup right before it puts the stack to sleep (see [Auto-sleep](#auto-sleep--wake-on-connect)) — one dump per play session, into the same `backups/` directory, rather than relying on a single daily snapshot that might land hours away from when the data last changed. The 04:00 cron stays in place as a safety net for days nobody plays at all.

Restore a dump:
```bash
gunzip -c backups/<file>.sql.gz | docker exec -i ac-database mysql -u root -p"$DB_ROOT_PASSWORD"
```

These are local backups only — if the disk itself is lost, they're gone too. Consider copying `backups/` off-host periodically for real disaster recovery.

### Auto-sleep / wake-on-connect

The `wake-proxy` service ([`wake-proxy/wake_proxy.py`](./wake-proxy/wake_proxy.py)) lets the whole game stack sit fully stopped between play sessions — no idle CPU/heat/fan noise from `ac-worldserver` running an empty world 24/7 — while staying reachable on demand:

- **Wake:** it's the only service that's always running, holding the real public `3724`/`8085` ports. The moment a WoW client tries to connect, if the real backend isn't up, it runs `docker compose up -d` and holds the connection until the backend is ready (~60-70s measured cold-boot time), then relays transparently. Once the backend is warm, it's a pure passthrough with no overhead.
- **Sleep:** it also watches `characters.online` in the database. 5 minutes after the last real player disconnects (not AFK — this is disconnect-based, since there's no reliable "idle but connected" signal without extra hooks; was 15 minutes, dropped to 5 given this host's thermal constraints — every minute here is spent with all random bots still logged in and actively AI-ticking for zero benefit once you've actually stopped), it backs up the database (see below) and stops `ac-worldserver`/`ac-authserver`/`ac-database`/`ac-llm-chatter-bridge` — itself excluded, so it's always there for the next wake.
- **Backup-before-sleep:** right before stopping `ac-database`, `run_backup()` in `wake_proxy.py` dumps every database to `backups/` (same directory and format as [`backup.sh`](./backup.sh) — one shared pool, either restore procedure works on either's output) using stdlib `gzip` rather than a piped binary, keeping the container dependency-free. This means a fresh backup happens once per play session, not just at the fixed 04:00 cron — see [Backups](#backups) for why the cron alone wasn't reliable given this stack sleeps most of the day.
- **Session recording:** the same idle-watcher loop tracks each session's start/end, peak population, and AH economy delta, writing one record to `backups/sessions.json` per session (`record_session()`) — see [Realm status page](#realm-status-page) for why this is session-based rather than a fixed-interval sample. Session bounds are keyed on the *real player's* own online status (`real_player_online_count()`), not the aggregate bot+player count the sleep decision above uses — bots take up to 300s to log off after you disconnect, and the idle timer then runs its own threshold on top of that, so using the aggregate count for session bounds would count all of that shutdown overhead as play time (a real 2-3 minute session was recording as 12 minutes before this fix).

**Known limitation:** because cold boot takes ~60-70s and the WoW 3.3.5a client's own connection timeout is shorter than that, your *first* connection attempt after the stack has slept will likely show a connection error while it boots in the background. Just retry a minute later — the client will connect immediately from then on. There's no clean fix for this without spoofing the auth protocol itself, which isn't worth the fragility for a once-per-session inconvenience.

**Fixed bug (see [Known issues](#known-issues)):** an earlier version disconnected the client specifically at "Enter World" — `socket.create_connection(..., timeout=BACKEND_CONNECT_TIMEOUT)`'s timeout stays active on the returned socket for every subsequent `recv()`, not just the connection attempt, so any >2s gap in the backend's data stream (easily hit by the burst of world state sent on entering, under this host's CPU constraints) raised `socket.timeout` — an `OSError` subclass — which the relay's `except OSError: pass` treated as a dead connection and tore down both sockets. Quick exchanges (auth, character list) stayed under 2s, which is why only world-entry broke. Fixed with an explicit `backend.settimeout(None)` after connecting.

It needs the host's Docker socket mounted to control sibling containers (`docker compose` runs *inside* the `wake-proxy` container, against the host's Docker daemon — "docker outside of docker", not real DinD) and this directory bind-mounted at the exact same absolute path it lives at on the host, since this compose file's relative volume mounts get resolved against that path and then applied by the *host's* dockerd — a mismatched path would silently break `ac-authserver`/`ac-worldserver`'s config/log mounts the next time `wake-proxy` starts them. Both are handled automatically by the `wake-proxy` service definition in `docker-compose.yml` — nothing extra to set up on a fresh deploy.

### Realm status page

A small always-on page at `http://<this-host>:8090` — population, character/guild counts, and Auction House stats (listings, total gold), read straight from `ac-database`. Same always-on tier as `wake-proxy` (also survives the game stack sleeping), but deliberately **doesn't** get Docker socket access the way `wake-proxy` does: it only ever needs read access to the database, so a failed connection *is* the "asleep" signal, rather than needing control over sibling containers to know that. When asleep, it falls back to the last successful query's numbers (cached to the `ac-status-cache` volume) rather than showing nothing, clearly marked as stale.

[`status-page/status_page.py`](./status-page/status_page.py) — stdlib `http.server`, one non-stdlib dependency (`pymysql`, pure-Python, no compiled extension) to talk to MySQL directly over `ac-network` instead of shelling out to `docker exec` the way `wake-proxy` does. Pinned to thread 0 (`cpuset: "0"`, the non-game reservation — see [Playerbots](#playerbots)) since it's a light, infrequent-query service with no reason to compete for the game's reserved cores. Also shows a dynamic "Real Players" list (any character on a non-`RNDBOT%` account with >60s playtime — no hardcoded names, picks up every alt automatically).

**No "recent activity" feed, on purpose:** an earlier version showed a Recent Auction Sales feed built from AzerothCore's own auction mail encoding, but that can only ever populate from a sale *you* personally make (see the Auction House bot section above for why) — nearly always empty, indistinguishable from broken. Replaced it with a guild join/leave/promote/demote feed (`guild_eventlog`, `Guild.h`'s `GuildEventLogTypes` enum) — which then turned out to have the same problem from a different angle: bots settle into a guild once, near realm creation, and never generate a second event. 105 real rows existed at build time; 24+ hours and several play sessions later, still 105. Also checked `guild_bank_eventlog` and `pvpstats_battlegrounds` as alternatives — both permanently empty, bots don't use guild banks or queue battlegrounds either. AzerothCore just doesn't persist an ongoing, queryable log of anything bots actually do repeatedly; **Top Guilds** (a snapshot, not a feed) is what's left, and stays honest about what it is.

**Host Health tile**: real CPU load, RAM usage, and CPU temperature, queried from the standalone Prometheus + node-exporter stack (see [Monitoring](#monitoring)) rather than mounting `/proc`/`/sys` into this container ourselves — keeps it privilege-free like the rest of this service. Requires `status-page` to be attached to the `monitoring` network (an `external: true` reference to `monitoring_monitoring` in `docker-compose.yml` — the name Compose derives from the `monitoring/` folder itself, so renaming that folder means updating this reference too) and for `cd monitoring && docker compose up -d` to have been run at least once; degrades to "unavailable" in the UI rather than erroring if that stack isn't running. Temp thresholds (warn/hot) are tuned against this specific fanless, Turbo-disabled NUC's own thermal ceiling, not a generic CPU guideline — see [Playerbots](#playerbots) for the hardware context.

Achievements were considered for this feed too, but this install's `achievement_dbc` world-table (meant to map achievement IDs to real names) only has 4 rows despite 1,500+ real completions existing in `character_achievement` — an incomplete data import somewhere in this install's setup, not something to paper over with a hardcoded ID→name table. Worth fixing before revisiting that idea. The same gap turned up in `areatable_dbc` (0 rows) when checking whether a "where are the bots right now" zone map was feasible — it isn't, without either fixing this install's DBC import or hardcoding a zone-ID→name table, so that idea's on hold too.

**Top Crafters**: highest-skill character per profession (`character_skills`, a window-function query — needs MySQL 8+, which this install runs), covering all 11 primary professions plus Cooking/First Aid/Fishing. Unlike achievements/zones, profession names are hardcoded safely (`PROFESSION_NAMES`) — they're a small, fixed, universally-known WotLK list, not extracted client data, so there's no accuracy risk the way there would be guessing achievement or zone names from memory. `character.totalKills` was checked as a candidate "top killers" stat alongside this and rejected — it reads 0 for every character checked, including high-level, wealthy ones, so it's simply not tracked by this setup rather than just low so far.

**Session history, not a time-sampled trend:** this host only runs 1-2h/day, so a fixed-interval sampler (e.g. every 5 minutes) would spend the vast majority of its points on "asleep" — and worse, a line chart connecting the last point before sleep to the first point after would draw a smooth line implying continuous change through 20+ idle hours that never happened. Instead, `wake-proxy`'s idle-watcher loop (which already knows exactly when a session starts and ends) records one summary per play session — start time, duration, peak population, AH gold/listings delta — to `backups/sessions.json` (see `record_session()` in [`wake-proxy/wake_proxy.py`](./wake-proxy/wake_proxy.py)). `status-page` mounts that same file read-only and renders it as a session list (`/api/sessions`) plus two cumulative stats (total sessions, longest session) rather than a graph.

### Monitoring

[`monitoring/docker-compose.yml`](./monitoring/docker-compose.yml) is a standalone Prometheus + Loki + Grafana stack, independent of the game stack:

```bash
cd monitoring && docker compose up -d
```

- **Grafana**: `http://<this-host>:3000` — default login `admin`/`admin` (change it if reachable beyond your own network)
- **Prometheus**: `http://<this-host>:9090` — direct query/debugging
- **cAdvisor**: per-container metrics for everything on this host, tuned with `--docker_only=true` and a 15s housekeeping interval (defaults measured at a sustained 70-125% CPU on this 2-physical-core host — tracking every systemd cgroup on top of every container was the cause)
- **node-exporter**: host-level metrics (CPU, memory, disk, load)
- **Loki + Promtail**: log aggregation. Loki isn't published on a host port — only Grafana and Promtail (both on the `monitoring` network) ever need to reach it.

Two dashboards are pre-provisioned automatically: **Node Exporter Full** ([1860](https://grafana.com/grafana/dashboards/1860)) and **Cadvisor exporter** ([14282](https://grafana.com/grafana/dashboards/14282)), pinned to specific revisions in `monitoring/grafana/provisioning/dashboards/json/`, plus a hand-built **Docker Logs** dashboard (log volume by container + a live log viewer, both filterable by a `container` template variable).

**Log lines are stripped of ANSI color codes before storage, not just displayed differently:** AzerothCore's console output is ANSI-colored (verified against real `ac-worldserver` output — `36`/cyan for the bulk of info lines, `33`/yellow for warnings, `31;1`/bold red for errors), and stored raw, every line came through Grafana's Logs panel as literal `\x1b[0m\x1b[36m...` control sequences prefixing the actual message — unreadable at a glance, forcing a click into every row's detail view just to see the text. Promtail's pipeline (`monitoring/promtail-config.yml`) now captures that color into a genuine `level` label (Grafana's Logs panel auto-color-codes on a label literally named `level`) and strips every ANSI sequence from the stored line, so what ships to Loki is just the clean message. The regex specifically matches codes `31`/`33` rather than "whichever code appears first" — info lines are prefixed with a reset code before their color (`\x1b[0m\x1b[36m...`), so grabbing the first match would have captured `"0"` instead of the real color; that happened to still default to info by luck on the first attempt, but would have silently misclassified a same-prefixed warning line as info too. Caught by testing the regex against real captured sample strings before deploying, not just reasoning through it. The Logs panel also has `showLabels: false` now — with a real `level` label plus `container`/`compose_project`/`compose_service`/`service_name` all attached to every line, showing them inline on every row was pure clutter once you've already filtered by container via the template variable; full labels are still one click away in each row's detail view.

**Promtail is scoped to this project's two compose stacks only** (`azerothcore-playerbots` + `monitoring`), not the whole host, unlike cAdvisor/node-exporter — this host also runs an unrelated, long-running media stack (`aio`) with weeks of chatty container logs. The scoping is done via `docker_sd_configs`' own `filters` (a native Docker Engine API filter, so the daemon excludes those containers before Promtail ever sees them), **not** `relabel_configs` with `action: keep` — that was tried first and silently didn't work (confirmed via Promtail's own `/targets` debug page: the config loaded exactly as written, but non-matching containers still showed `ready: true`). Also worth knowing: Docker's `label` filter **ANDs** multiple values passed as one filter's `values` list rather than OR-ing them (confirmed directly against the Docker API — no container can match two different `compose.project` values at once, so a single filter listing both projects returned zero results). The fix is two separate `docker_sd_configs` entries in [`monitoring/promtail-config.yml`](./monitoring/promtail-config.yml), one filter each, merged into the same scrape job.

Promtail also gets a read-only `docker.sock` mount — the same justified exception wake-proxy already makes elsewhere in this project (see [Auto-sleep / wake-on-connect](#auto-sleep--wake-on-connect)) — because `docker_sd_configs` is what lets log entries carry the real container name (`ac-worldserver`, not an opaque container ID) as a label; there's no way to get that from the log files alone. Read-only, since Promtail only ever lists/inspects containers, never controls them.

Loki's retention is capped at 30 days (`limits_config.retention_period` in [`monitoring/loki-config.yml`](./monitoring/loki-config.yml)) — still a deliberate bound rather than Loki's much longer default, but this host has 361G free against a real measured footprint of under 100MB for this project's own containers, so 30 days costs essentially nothing. Raising retention also raises `reject_old_samples_max_age` to match — both need to move together, since a container's own already-existing (older) log history only becomes ingestable once the rejection window covers it, and that one-time backfill can briefly exceed Loki's default 4MB/s ingestion cap (seen directly when retention first went from 7 to 30 days: real `ac-worldserver`/etc. history got rate-limited, not the unrelated stack — that's still excluded). `ingestion_rate_mb`/`ingestion_burst_size_mb` are bumped to 8/16 to give that one-time catch-up room; steady-state volume is far below either number.

> **If you ever add another community dashboard here**: a dashboard's download count says nothing about whether it matches *this* setup. Our cAdvisor (plain Docker, standalone `gcr.io/cadvisor/cadvisor` image) labels containers with `name`/`image`/`container_label_*`. Two of the most-downloaded per-container dashboards on grafana.com looked fine on paper and showed zero data here: one predates a 2018 node_exporter metric rename and hardcodes the original author's IPs; another (2M downloads, actively updated) expects `container`/`service` labels, which is a **Kubernetes**-cAdvisor convention ours doesn't produce. Verify a dashboard's actual PromQL queries against what your exporters really expose before trusting it. Also watch for a raw grafana.com export leaving `${DS_PROMETHEUS}` as a literal unsubstituted string in template-variable datasources (not panel-level ones, which default fine) — that only gets filled in by the interactive import wizard, never by file-based provisioning, and silently breaks any panel filtered by that variable.

### Migrating to another host

The Docker images are portable — the state and secrets are not:

1. **Install Docker + Compose** on the new host, clone this repo, run `./deploy.sh` just far enough to get `ac-database` healthy, then stop it (`Ctrl-C`, `docker compose stop`) *before* it imports a fresh schema — you need your restored dump in place first.
2. **Copy the database**:
   ```bash
   # on the old host
   docker exec ac-database mysqldump -u root -p"$DB_ROOT_PASSWORD" --all-databases > wow-server-backup.sql
   # on the new host, after `docker compose up -d ac-database`:
   docker exec -i ac-database mysql -u root -p"$DB_ROOT_PASSWORD" < wow-server-backup.sql
   ```
3. **Copy secrets out-of-band** (SCP, not git): `env/dist/etc/modules/mod_llm_chatter.conf` (has your real LLM API key) and the real `DB_ROOT_PASSWORD` — neither belongs in this repo.
4. **Re-check bot count against the new host's core count.** 100 bots at 6 iterations/tick used ~1.5-2 cores on the original box (2 physical cores / 4 threads) — that's what the `cpus: "3.0"` cap was sized around. A more powerful host has headroom to raise `AC_AI_PLAYERBOT_MAX_RANDOM_BOTS` (and `ITERATIONS_PER_TICK` back toward the default of 10) — just raise the `cpus`/`memory` limits to match. mod-playerbots auto-provisions however many `RNDBOT` accounts the new bot count needs, so there's no separate account-count step.
5. **Update or remove `cpuset: "1,2,3"`.** Every game-stack service in `docker-compose.yml` hardcodes this CPU pin, sized for the original host's exact 4-thread layout (see the [Playerbots](#playerbots) section). A different host will have different CPU ids available — `docker compose up -d` will fail outright if id 2 or 3 doesn't exist, or silently misbehave if it does but means something different. Re-derive the pin from the new host's own `lscpu -e` output, or delete the `cpuset` lines entirely if you don't need the hard reservation there.
6. **Open port 3724** (and 8085 for direct world-server access) in the new host's firewall/router, then update `realmlist.wtf` on any client.

---

## Known issues

**wake-proxy disconnected clients at "Enter World" (fixed):** [`wake-proxy/wake_proxy.py`](./wake-proxy/wake_proxy.py)'s relay connected to the backend with `socket.create_connection(..., timeout=BACKEND_CONNECT_TIMEOUT)` — that timeout is meant only for the connection attempt, but Python leaves it active on the returned socket for every subsequent call. Any gap longer than `BACKEND_CONNECT_TIMEOUT` (2s) between packets during relaying raised `socket.timeout` (an `OSError` subclass), which the relay's `except OSError: pass` silently treated as a dead connection, tearing down both sockets. Auth and character-list exchanges are quick enough to dodge this; the data burst on actually entering the world reliably wasn't, especially under this host's CPU constraints — so every login got through character select and died right at "Enter World." Reproduced across two different worldserver image tags with the proxy as the only common factor, confirming it wasn't a build issue. Fixed with an explicit `backend.settimeout(None)` right after connecting, before the relay threads start.

**wake-proxy could kill itself mid-wake (fixed):** `trigger_start()` woke the backend with a bare `docker compose --profile llm-chatter up -d` — no service list, meaning it targeted *every* service in the compose file, including `wake-proxy` and `status-page` themselves. If Compose ever decided either of those needed recreating (any config drift, e.g. from an earlier ad-hoc `docker compose up -d --build <service>` that didn't match the file exactly), wake-proxy ended up asking Docker to replace itself while it was the process running that very command — Docker sends the container SIGTERM, a self-referential process mid-`subprocess.run()` doesn't exit cleanly within the 10s grace period, and Docker force-kills it (exit 137). Confirmed via the Docker daemon's own logs (`hasBeenManuallyStopped=true`, forced kill after signal 15 didn't land) — reproduced live during an actual wake triggered by a real connection attempt, which is what made the realm briefly unreachable despite worldserver/authserver being fine. Fixed by scoping the wake call to `GAME_SERVICES` specifically (mirroring what the sleep side already did correctly) — Compose still auto-starts their `depends_on` chain (`ac-db-import`, `ac-client-data-init`), but wake-proxy and status-page can no longer be targeted by their own wake trigger, structurally rather than by luck.

**mod-llm-chatter compile bug (fixed upstream as of 2026-09-14):** `LLMChatterShared.cpp`'s `SendPartyMessageInstant` used to call `ChatHandler::BuildChatPacket()` with an argument order from an older AzerothCore signature, failing to compile (`fatal error: no matching function for call to 'BuildChatPacket'`). The build workflow carried a patch step (`apps/docker/patch-llm-chatter.py`) working around it — that step started failing loudly (by design: it asserts exactly one match before patching) once upstream fixed the call themselves, matching the correct 8-argument overload this core actually declares. Removed the now-unnecessary patch step from `build-images.yml`; the script itself stays in the repo, unused, in case this ever regresses.

Check if upstream has merged a fix before you build; if not, patch it yourself the same way. The build workflow already applies this automatically via `apps/docker/patch-llm-chatter.py`.
