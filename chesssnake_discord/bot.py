"""The bot: one /chess command group, backed by a chesssnake server."""

from __future__ import annotations

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
    def __init__(self, bot: ChessBot):
        self.bot = bot
        self._games_cache = {}
        self._legal_cache = {}
        super().__init__()

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        await self.report(interaction, getattr(error, "original", error))


class ChessBot(commands.Bot):
    def __init__(self, config: Config, *, service: ChessService | None = None):
        intents = discord.Intents.default()
        intents.members = True  # privileged: /chess init everyone, auto-init, display names
        super().__init__(command_prefix=commands.when_mentioned, intents=intents, help_command=None)
        self.config = config
        if service is None:
            client = connect(config.api_url, token=config.api_token, asynchronous=True)
            service = ChessService(client, provider=config.chesssnake.provider)
        self.service = service
        self.store = Store(config.storage.path)
        self.relay = LiveRelay(
            self.service, self.store, config.live, self._post_relayed, lambda: {g.id for g in self.guilds}
        )
        self.chess_cog: ChessCog | None = None
        self._relay_started = False

    async def setup_hook(self):
        me = await self.service.me()
        if me.role != "admin":
            raise RuntimeError(
                f"The chesssnake token belongs to {me.username!r}, which is not an administrator; "
                "the bot must be one to act for players."
            )
        log.info("chesssnake: %s as %s", self.config.api_url, me.username)
        self.chess_cog = ChessCog(self)
        await self.add_cog(self.chess_cog)
        self.add_dynamic_items(ChallengeButton)
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
        if not self._relay_started:
            self._relay_started = True
            await self.relay.start()
            log.info("Live relay: %s", self.relay.mode)

    async def close(self):
        await self.relay.stop()
        await super().close()
        await self.service.client.aclose()
        self.store.close()

    async def _post_relayed(self, state, move, note):
        """Post a change made outside Discord in the guild's chess channel."""
        settings = self.store.guild(state.group_id)
        channel = self.get_channel(settings.channel_id) if settings.channel_id else None
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
