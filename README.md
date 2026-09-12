# wow-server-playerbots

Self-hosted **World of Warcraft: Wrath of the Lich King (3.3.5a, build 12340)** private server, built on [AzerothCore](https://www.azerothcore.org/) with the [mod-playerbots](https://github.com/mod-playerbots/mod-playerbots) module compiled in.

Not affiliated with Blizzard Entertainment. For personal/private-server use.

## What this is

- **Core**: AzerothCore (fork: [`mod-playerbots/azerothcore-wotlk`](https://github.com/mod-playerbots/azerothcore-wotlk), Playerbot branch)
- **Module**: [mod-playerbots](https://github.com/mod-playerbots/mod-playerbots), added under `modules/mod-playerbots` and compiled statically into `worldserver` (the standard AzerothCore module pattern — modules dropped into `modules/` are auto-registered by the CMake `ModulesLoader`)
- **Database**: MySQL 8.4 (official `mysql:8.4` image — not customized)
- **Orchestration**: Docker Compose

Playerbots gives you AI-controlled bot characters that populate the world — useful for playing a private server solo or with a small group while still having a populated world.

## Prebuilt images

Instead of building from source, you can pull the prebuilt images directly:

| Image | Purpose |
|---|---|
| [`estebanmorenoit/ac-wotlk-worldserver-playerbots`](https://hub.docker.com/r/estebanmorenoit/ac-wotlk-worldserver-playerbots) | World server (game logic), with mod-playerbots built in |
| [`estebanmorenoit/ac-wotlk-authserver-playerbots`](https://hub.docker.com/r/estebanmorenoit/ac-wotlk-authserver-playerbots) | Auth/login server (port 3724) |
| [`estebanmorenoit/ac-wotlk-db-import-playerbots`](https://hub.docker.com/r/estebanmorenoit/ac-wotlk-db-import-playerbots) | One-shot DB bootstrap/migration (run before the servers) |
| [`estebanmorenoit/ac-wotlk-client-data-playerbots`](https://hub.docker.com/r/estebanmorenoit/ac-wotlk-client-data-playerbots) | One-shot client data extraction (maps/vmaps/mmaps/dbc) |

All four are built from the same `apps/docker/Dockerfile` in the AzerothCore playerbots fork, using a different `target` per service.

## Running it

See [`docker-compose.yml`](./docker-compose.yml) for a ready-to-use stack based on the prebuilt images above. Bring it up with:

```bash
docker compose up -d
```

The init containers (`ac-db-import`, `ac-client-data-init`) run once and exit; `ac-worldserver` and `ac-authserver` wait on them via `depends_on` health/completion conditions before starting.

Connect with a **3.3.5a (12340)** WotLK client, pointed at this server's address on port 3724 (see below).

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

Bot behavior is controlled via environment variables on the `ac-worldserver` service:

```yaml
environment:
  AC_AI_PLAYERBOT_RANDOM_BOT_AUTOLOGIN: "1"
  AC_AI_PLAYERBOT_MIN_RANDOM_BOTS: "80"
  AC_AI_PLAYERBOT_MAX_RANDOM_BOTS: "80"
  AC_AI_PLAYERBOT_SYNC_LEVEL_WITH_PLAYERS: "1"
  AC_AI_PLAYERBOT_RANDOM_BOT_CONCENTRATE_IN_PLAYER_ZONE: "1"
  AC_AI_PLAYERBOT_ENABLE_GREET: "1"
```

This config keeps 80 random bots logged in at all times, leveled to match real players, clustered near player zones, with greetings enabled.

## Notes on host setup

The host disk was originally undersized for the world/character databases and client data; it was grown online with `growpart` → `pvresize` → `lvextend` → `resize2fs` (no downtime, no reboot) — standard procedure for extending an LVM-on-partition root filesystem after growing the underlying disk.
