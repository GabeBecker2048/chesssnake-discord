"""``uv run chesssnake-discord`` (or ``python -m chesssnake_discord``): load .env and config.toml, then run the bot."""

from __future__ import annotations

import asyncio
import logging
import sys

import discord
from chesssnake import connect
from chesssnake.db import errors as db_errors
from dotenv import find_dotenv, load_dotenv

from .bot import ChessBot
from .config import Config, ConfigError, load

log = logging.getLogger("chesssnake_discord")


async def check_chesssnake(config: Config) -> str | None:
    """
    Make sure the chess server is reachable and the token is an administrator's,
    before logging in to Discord (discord.py logs in first, so a chesssnake problem
    would otherwise only show once the Discord side works).

    :return: Why the bot cannot start, or ``None`` if it can.
    """
    client = connect(config.api_url, token=config.api_token, asynchronous=True)
    try:
        info = await client.info()
        me = await client.me()
    except db_errors.AuthError:
        return f"{config.api_url} rejected CHESSSNAKE__CLIENT__TOKEN"
    except db_errors.GameError as exc:
        return f"{config.api_url} answered with an error: {exc}"
    except Exception as exc:  # httpx's connection errors, DNS, TLS...
        return f"cannot reach the chesssnake server at {config.api_url} ({type(exc).__name__}: {exc})"
    finally:
        await client.aclose()
    if me.role != "admin":
        return f"the chesssnake token belongs to {me.username!r}, which is not an administrator"
    log.info(
        "chesssnake %s at %s: logged in as %s (admin); live updates %s",
        info.get("version"),
        config.api_url,
        me.username,
        "on" if info.get("websocket") else "off",
    )
    return None


def main() -> int:
    # The .env in the directory the bot is started from (not next to the installed
    # package, which is where python-dotenv looks by default).
    load_dotenv(find_dotenv(usecwd=True))
    discord.utils.setup_logging(level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per request otherwise
    try:
        config = load()
    except ConfigError as exc:
        print(f"chesssnake-discord: {exc}", file=sys.stderr)
        return 2
    problem = asyncio.run(check_chesssnake(config))
    if problem is not None:
        print(f"chesssnake-discord: {problem}", file=sys.stderr)
        return 1
    bot = ChessBot(config)
    try:
        bot.run(config.discord_token, log_handler=None)
    except discord.LoginFailure:
        print("chesssnake-discord: Discord rejected DISCORD_TOKEN", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
