"""
The standalone bot behind the ``chesssnake-discord`` command and the Docker image:
a plain discord.py bot that loads the chess cog and syncs its commands. To add
chess to a bot of your own, load the cog instead (:mod:`chesssnake_discord.cog`).
"""

from __future__ import annotations

import logging

import discord
from discord.ext import commands

from .cog import ChessCog, make_cog
from .config import Config

log = logging.getLogger(__name__)


class ChessBot(commands.Bot):
    def __init__(self, config: Config, **cog_kwargs):
        intents = discord.Intents.default()
        intents.members = True  # privileged: /chess init everyone, auto-init, display names
        super().__init__(command_prefix=commands.when_mentioned, intents=intents, help_command=None)
        self.config = config
        self._cog_kwargs = cog_kwargs
        self.chess_cog: ChessCog | None = None

    async def setup_hook(self):
        self.chess_cog = make_cog(self, self.config, **self._cog_kwargs)
        await self.add_cog(self.chess_cog)
        if self.config.discord.dev_guild_id:
            guild = discord.Object(self.config.discord.dev_guild_id)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            log.info("Synced %d command(s) to guild %s", len(synced), guild.id)
        else:
            synced = await self.tree.sync()
            log.info("Synced %d global command(s) (they can take a while to appear)", len(synced))

    async def on_ready(self):
        log.info("Logged in as %s in %d server(s)", self.user, len(self.guilds))
