# chesssnake-discord

Chess for Discord, played through a [chesssnake](https://gabebecker.com/chesssnake/) server: a
**cog you can add to your own bot**, or a **bot of its own** (one Docker compose file).
The bot has no chess logic and keeps no games: chesssnake runs the rules and stores everything, and
the bot acts for each player with an administrator token. Games played here also appear in the
chesssnake web UI, and moves made there are posted back to Discord.

Each Discord server is its own chess world: its games use the server's id as the chesssnake
`group_id`. A player's chesssnake account is linked to their Discord id and is shared by every
server they meet the bot in.

Needs chesssnake 0.15.4 or newer.

> **Trust.** The bot holds an administrator token for its chesssnake server: it acts for every
> player and can manage every account there. Give it a chesssnake server of its own (the Docker
> stack below does), or one whose users all play through this bot.

## Running it with Docker

Each [release](https://github.com/GabeBecker2048/chesssnake-discord/releases) has a
`compose.yaml` that runs the bot and its own chesssnake server (the published
`ghcr.io/gabebecker2048/chesssnake` image, on SQLite). Nothing is built.

1. Set up the Discord application (step 2 under "Running it without Docker").
2. In an empty folder, put that release's `compose.yaml`, and a `.env` file with
   `DISCORD_TOKEN=...` (`.env.example` lists everything else it can hold).
3. Start the chess server and make the bot's account:

   ```sh
   docker compose up -d chesssnake
   docker compose exec chesssnake chesssnake user create discord-bot --admin --no-password
   docker compose exec chesssnake chesssnake user token discord-bot --name chesssnake-discord
   ```

4. Put that token in `.env` as `CHESSSNAKE__CLIENT__TOKEN`, then start everything, and make
   yourself an account for the web UI:

   ```sh
   docker compose up -d
   docker compose exec chesssnake chesssnake user create yourname --admin
   ```

At start-up the bot checks it can reach the chess server with an administrator's token, and says
what is wrong if not, before it logs in to Discord.

The bot reaches the server inside the stack, so `CHESSSNAKE__CLIENT__API_URL` is set for it. The
web UI is on port 8000, where players make link codes: set
`CHESSSNAKE_DISCORD__CHESSSNAKE__WEB_URL` to the address they use, and put it behind HTTPS before
opening it to the internet. To use a chesssnake server you already run, drop the `chesssnake`
service and point `CHESSSNAKE__CLIENT__API_URL` at it.

The images are tagged `0.1.0`, `0.1`, `0` and `latest`. The release's compose file pins the exact
version; before 1.0, read the CHANGELOG before moving to a new minor version.

## Adding it to your own bot

The whole thing is one discord.py cog. Install it (Python 3.12+, discord.py 2.4+):

```sh
pip install chesssnake-discord
```

Then load it, usually in your bot's `setup_hook`:

```python
await bot.load_extension("chesssnake_discord")
```

It reads its settings from the environment (and `config.toml` if there is one), exactly as the
standalone bot does, except that it needs no Discord token: set `CHESSSNAKE__CLIENT__API_URL` and
`CHESSSNAKE__CLIENT__TOKEN` (an administrator's token on your chesssnake server). To pass the
settings in code instead:

```python
from chesssnake_discord import load_config, make_cog

config = load_config(require_discord_token=False)
await bot.add_cog(make_cog(bot, config))
```

Things to know:

- **Your bot syncs the commands.** The cog never calls `tree.sync()`; do it however your bot
  already does. The commands are one group, `/chess`; set `command_name` (or
  `CHESSSNAKE_DISCORD__DISCORD__COMMAND_NAME`) if your bot already has a `/chess`.
- **The Server Members intent** is optional. Without it, making accounts for a whole server or for
  new members is refused with an explanation, and boards show usernames instead of server
  nicknames.
- **Loading** checks that the chesssnake token is an administrator's, and fails with that reason
  if not. Unloading closes everything the cog opened.
- The cog keeps a small SQLite database of its own (`storage.path`, default
  `chesssnake-discord.db` in the working directory).
- Prefer not to install a package? Copy the `chesssnake_discord` folder into your bot and install
  its dependencies (`discord.py`, `chesssnake[client]`, `python-dotenv`).

## Settings

Every setting has a default. Set one in `config.toml` (see `config.example.toml`) or as an
environment variable, `CHESSSNAKE_DISCORD__{SECTION}__{KEY}`, which wins over the file.

| Setting | Default | What it does |
| --- | --- | --- |
| `chesssnake.web_url` | the API url | The web UI's address, shown by `profile` and `password`. |
| `live.mode` | `auto` | How moves made outside Discord are relayed: `auto`, `websocket`, `poll`, `off`. |
| `live.poll_interval_seconds` | `15` | How often to poll, when polling. |
| `discord.command_name` | `chess` | The slash command group. |
| `discord.dev_guild_id` | `0` | The standalone bot registers its commands in this server only (instant). |
| `discord.owner_ids` | none | Who may use the account commands of `admin`, besides the application's owner. |
| `discord.max_guilds` | `0` | Leave servers beyond this many (`0`: no cap). |
| `discord.bulk_accounts` | `true` | Whether managers may make accounts for everyone at once, and on join. |
| `discord.notice` | empty | Posted when the bot joins a server and with every `init`. |
| `storage.path` | `chesssnake-discord.db` | The bot's own small database. |

`max_guilds`, `bulk_accounts = false` and `notice` are for running a bot that strangers can invite:
a cap well under Discord's 100-server verification threshold, accounts only for people who ask,
and a word about how long games are kept.

## Running it without Docker

1. **A chesssnake administrator account for the bot.** On the chesssnake server:

   ```sh
   chesssnake user create discord-bot --admin --no-password
   chesssnake user token discord-bot --name chesssnake-discord      # prints the token once
   ```

2. **A Discord application** (Developer Portal):
   - Bot: turn on the **Server Members Intent**. `/chess init everyone:True`, auto-init and
     display names need it.
   - Invite it with the scopes `bot` and `applications.commands`, and the permissions
     Send Messages, Attach Files and Embed Links.

3. **Settings.** `cp .env.example .env` and fill it in; everything else is under
   [Settings](#settings). Set `dev_guild_id` while developing, so commands register instantly in
   that server instead of globally.

4. **Run.**

   ```sh
   pip install chesssnake-discord
   chesssnake-discord                 # reads .env from the current directory
   ```

   The bot checks at start-up that its chesssnake token belongs to an administrator.

## Commands

Everything is under `/chess`. Errors are shown only to whoever ran the command; boards are public.

| Command | What it does |
| --- | --- |
| `init` | Make your chess account (passwordless, linked to your Discord id, named after your @username, or `name_1` and so on if that is taken). Refuses if you have one. |
| `init everyone:True` | *Manage Server.* Make accounts for every member who doesn't have one. |
| `auto-init enable/disable` | *Manage Server.* Make accounts for new members as they join. |
| `link code` | Use a chesssnake account you already have: make a link code on its Account page in the web UI (or with `chesssnake link-code`) and give it here. Only you see the command and the answer, and the bot never sees your password. If you had an auto-made account, its games stay with it. |
| `unlink` | Disconnect your Discord account from your chess account. |

Moving your link to another account is refused while your current account has games in
progress, since nobody could play them after the move: finish or resign them first (finished
games stay with the old account). The same goes for `/chess admin link`.
| `password` | Give your passwordless account a password, so you can also play in the web UI. |
| `profile [member]` | Show a chess account. |
| `challenge opponent [color]` | Challenge someone; they get Accept/Decline buttons. |
| `challenges` | Your pending challenges here. |
| `accept [challenger]`, `decline [member]` | Answer a challenge, or withdraw yours. |
| `move move [opponent]` | Play a move. Suggestions list your legal moves as you type. |
| `board [opponent] [game_id] [perspective]` | Show a board; `game_id` shows any game, finished ones too. |
| `games [member]` | Games in progress here. |
| `draw offer/accept/decline [opponent]` | Draw offers. Making a move declines one too. |
| `resign [opponent]` | Resign (asks you to confirm). |
| `moves [opponent]` | The legal moves. |
| `export pgn/fen/history [opponent] [game_id]` | PGN as a file, or the FEN or move list. |
| `record opponent [member]` | Wins, losses and draws between two players here. |
| `channel [#channel]` | *Manage Server.* Where moves made outside Discord are posted; empty turns it off. |

`opponent` is only needed when you have more than one game here.

**`/chess admin`**: for games in this server, server managers (Manage Server) and chess
moderators can `create-game`, `delete-game`, `resign` (for a player) and `draw` (end a game as
a draw). Server managers make chess moderators with `moderator member add|remove`: that makes
the member a chesssnake group admin of this server's games, so they can also manage them
outside Discord, and nothing beyond this server.
chesssnake accounts are shared by every server, so `link`, `unlink`, `disable`, `enable` and
`rename` are for the bot's owners only: the application's owner and anyone in
`[discord] owner_ids`.

## Moves made outside Discord

When a server has a channel set with `/chess channel`, moves, draws and resignations made in the
web UI (or any other chesssnake client) in that server's games are posted there. `[live] mode`
chooses how the bot hears about them:

- `auto` (default): chesssnake's websocket live updates, or polling if the server has them off.
  The bot follows every game on the server (it is an administrator), so new games and moves
  arrive as they happen, and events it sent itself are recognised and skipped. A dropped
  connection polls until it is back.
- `websocket`: the same, but it logs a warning when it has to poll.
- `poll`: always poll, every `poll_interval_seconds`.
- `off`: never.

The bot's own moves are never posted twice. Polling looks at up to 200 games in progress at once
on the chesssnake server.

## Development

```sh
uv run pytest
```

The tests use `chesssnake.testing`: a real chesssnake server on a temporary SQLite database,
served on a local port so the websocket relay is tested too (the `dev` group installs
`chesssnake[api]`). They drive the service, the relay and the command callbacks against it with
a fake Discord interaction. Nothing reaches Discord.

To run the bot from a checkout in Docker, building it instead of pulling it:

```sh
docker compose -f compose.yaml -f compose.dev.yaml up -d --build
```

## Contributing

Issues and pull requests are welcome. Fork the repository, make a branch, and open a pull request
against `main`; CI runs the linter, the tests and a Docker build, and must pass before a merge.
Pull requests are squash-merged.

## Releasing (maintainers)

1. Bump `version` in `pyproject.toml`, add a `## X.Y.Z - YYYY-MM-DD` section to `CHANGELOG.md`
   and, for a new minor version, the bot image's tag in `compose.yaml`. Merge that.
2. Tag the merge commit and push the tag: `git tag vX.Y.Z && git push origin vX.Y.Z`.

The release workflow checks the tag against `pyproject.toml` and the CHANGELOG, runs the tests,
builds the package and checks it installs cleanly, publishes it to PyPI (Trusted Publishing, no
token) and the image for amd64 and arm64 to ghcr.io, then creates the GitHub release with the
CHANGELOG section as its notes, the package, and a `compose.yaml` pinned to that version. PyPI
never accepts a version number twice: a broken release is fixed by the next version.
