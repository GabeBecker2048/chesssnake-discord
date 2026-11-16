"""
The chess cog: everything chesssnake-discord does, as one discord.py cog that any
bot can load.

.. code-block:: python

    # in your bot, with the settings in the environment (or config.toml):
    await bot.load_extension("chesssnake_discord")

    # or explicitly:
    from chesssnake_discord import make_cog, load_config
    await bot.add_cog(make_cog(bot, load_config(require_discord_token=False)))

The cog owns its chesssnake client, its small database and the live relay: they
are made when it loads and closed when it unloads. It never syncs the command
tree; the bot does that (the standalone ``chesssnake-discord`` command does, in
:mod:`chesssnake_discord.bot`).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

import discord
from chesssnake import connect
from discord import app_commands
from discord.ext import commands

from . import render
from .chess import ChessService
from .cogs.accounts import AccountCommands
from .cogs.admin import AdminCommands
from .cogs.play import ChallengeButton, PlayCommands
from .config import Config
from .live import LiveRelay
from .store import Store

log = logging.getLogger(__name__)


class ChessCog(
    AccountCommands,
    PlayCommands,
    AdminCommands,
    commands.GroupCog,
    group_name="chess",
    group_description="Play chess",
):
    """
    The /chess commands. Build it with :func:`make_cog`, which also applies
    ``[discord] command_name``; ``ChessCog(bot, config)`` always uses /chess.

    :param service: A ready :class:`ChessService` (tests pass one); default: one
        made from ``config``'s chesssnake url and token, closed on unload.
    :param store: The bot's database; default: ``config.storage.path``.
    """

    def __init__(self, bot: commands.Bot, config: Config, *, service: ChessService | None = None, store=None):
        self.bot = bot
        self.config = config
        self._owns_service = service is None
        if service is None:
            client = connect(config.api_url, token=config.api_token, asynchronous=True)
            service = ChessService(client, provider=config.chesssnake.provider, command=config.discord.command_name)
        self.service = service
        service.command = config.discord.command_name  # for the messages that name a command
        self.store = store if store is not None else Store(config.storage.path)
        self.relay = LiveRelay(self.service, self.store, config.live, self.post_relayed, self._server_ids)
        self._relay_task: asyncio.Task | None = None
        self._games_cache = {}
        self._legal_cache = {}
        super().__init__()

    # --- loading and unloading ------------------------------------------------------

    async def cog_load(self):
        me = await self.service.me()
        if me.role != "admin":
            raise RuntimeError(
                f"The chesssnake token belongs to {me.username!r}, which is not an administrator; "
                "the bot must be one to act for players."
            )
        if not self.bot.intents.members:
            log.warning(
                "The Server Members intent is off: making accounts for a whole server or on join is "
                "unavailable, and boards show usernames rather than server nicknames."
            )
        self.bot.add_dynamic_items(ChallengeButton)
        log.info(
            "chesssnake %s as %s; commands under /%s",
            self.config.api_url,
            me.username,
            self.config.discord.command_name,
        )
        if self.bot.is_ready():  # loaded into a bot that is already running
            self._relay_task = asyncio.create_task(self._start_relay(), name="chesssnake-relay-start")

    @commands.Cog.listener()
    async def on_ready(self):
        # The relay needs the bot's servers, which are known once it is ready.
        # on_ready can fire again after a reconnect; the relay starts once.
        if self._relay_task is None:
            self._relay_task = asyncio.create_task(self._start_relay(), name="chesssnake-relay-start")

    async def _start_relay(self):
        await self.relay.start()
        log.info("Live relay: %s", self.relay.mode)

    async def cog_unload(self):
        if self._relay_task is not None:
            self._relay_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._relay_task
        await self.relay.stop()
        self.bot.remove_dynamic_items(ChallengeButton)
        if self._owns_service:
            await self.service.client.aclose()
        self.store.close()

    # Not `_guild_ids`: discord.py reads an attribute of that name from every cog.
    def _server_ids(self) -> set[int]:
        return {guild.id for guild in self.bot.guilds}

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        await self.report(interaction, getattr(error, "original", error))

    # --- servers joining ----------------------------------------------------------------

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        cap = self.config.discord.max_guilds
        if cap and len(self.bot.guilds) > cap:
            log.info("Leaving %s (%s): over the cap of %d servers", guild, guild.id, cap)
            await self._say(
                guild,
                f"Sorry: this chess bot is limited to {cap} servers and is full, so it is leaving. "
                "You can run your own: https://github.com/GabeBecker2048/chesssnake-discord",
            )
            await guild.leave()
            return
        if self.config.discord.notice:
            await self._say(guild, self.config.discord.notice)

    @staticmethod
    async def _say(guild: discord.Guild, text: str):
        """Post in the server's system channel, else the first channel the bot may write in."""
        me = guild.me
        channels = [guild.system_channel, *guild.text_channels]
        for channel in channels:
            if channel is not None and me is not None and channel.permissions_for(me).send_messages:
                with contextlib.suppress(discord.HTTPException):
                    await channel.send(text)
                    return

    # --- the relay's posts ----------------------------------------------------------------

    async def post_relayed(self, state, move, note):
        """Post a change made outside Discord in the guild's chess channel."""
        settings = self.store.guild(state.group_id)
        channel = self.bot.get_channel(settings.channel_id) if settings.channel_id else None
        if channel is None:
            return
        guild = channel.guild
        white, black = await render.display_names(self.service, guild, state)
        if move is not None:
            mover = black if render.to_move(state) == "white" else white
            lines = [f"**{mover}** played **{move['san']}** ({note})."]
        else:
            lines = [f"{render.game_label(state, white, black)}: {note}."]
        status = render.status_line(state, white, black)
        if not state.is_over:
            side_id, side_name = state.player_id(state.to_move), state.player_name(state.to_move)
            status += f" {await render.mention(self.service, guild, side_id, side_name)}, your move."
        lines.append(status)
        file = await render.board_file_async(state, white, black)
        await channel.send("\n".join(lines), file=file, allowed_mentions=discord.AllowedMentions(users=True))


def make_cog(bot: commands.Bot, config: Config, **kwargs) -> ChessCog:
    """
    The chess cog for ``bot``, its commands under ``config.discord.command_name``.

    discord.py fixes a group cog's command name when its class is made, so a name
    other than ``chess`` gets a subclass made for it here; the cog is still named
    ``ChessCog`` (``bot.get_cog("ChessCog")``).
    """
    name = config.discord.command_name
    cls = ChessCog
    if name != "chess":
        # __module__ set so that unload_extension("chesssnake_discord") recognises it as
        # this package's cog; type() would otherwise label it with discord.py's module.
        namespace = {"__module__": ChessCog.__module__, "__qualname__": "ChessCog"}
        cls = type("ChessCog", (ChessCog,), namespace, group_name=name, group_description="Play chess")
    return cls(bot, config, **kwargs)


async def setup(bot: commands.Bot):
    """``await bot.load_extension("chesssnake_discord")``: settings from the environment
    and ``config.toml``, as for the standalone bot, but no Discord token is needed."""
    from .config import load

    await bot.add_cog(make_cog(bot, load(require_discord_token=False)))
