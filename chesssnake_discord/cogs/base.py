"""What every /chess command needs: the service, replies, boards, permission checks."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from chesssnake import GameState, MoveResult, User

from .. import render
from ..errors import BotError

if TYPE_CHECKING:
    from ..bot import ChessBot


class Base:
    bot: ChessBot

    @property
    def service(self):
        return self.bot.service

    @property
    def store(self):
        return self.bot.store

    @property
    def relay(self):
        return self.bot.relay

    # --- context ---------------------------------------------------------------

    @staticmethod
    def guild(interaction: discord.Interaction) -> discord.Guild:
        if interaction.guild is None:
            raise BotError("Chess commands only work in a server.")
        return interaction.guild

    @staticmethod
    def require_manager(interaction: discord.Interaction):
        """Server managers only (Manage Server), checked per command: Discord's own
        permission setting applies to /chess as a whole, not to its subcommands."""
        perms = getattr(interaction.user, "guild_permissions", None)
        if perms is None or not perms.manage_guild:
            raise BotError("Only server managers (Manage Server) can do that.")

    async def require_game_manager(self, interaction: discord.Interaction):
        """Server managers, or chess moderators of this server (chesssnake group admins
        of its group, made with ``/chess admin moderator``)."""
        perms = getattr(interaction.user, "guild_permissions", None)
        if perms is not None and perms.manage_guild:
            return
        user = await self.service.user_for(interaction.user)
        guild = interaction.guild
        if user is not None and guild is not None and (user.is_admin or guild.id in user.admin_groups):
            return
        raise BotError("Only server managers (Manage Server) and chess moderators can do that.")

    async def require_owner(self, interaction: discord.Interaction):
        """The bot's owners only: the application's owner(s) and ``[discord] owner_ids``.
        For actions on chesssnake accounts, which every server the bot is in shares."""
        if interaction.user.id in self.bot.config.discord.owner_ids or await self.bot.is_owner(interaction.user):
            return
        raise BotError("Only the bot's owners can change chess accounts; they are shared by every server.")

    async def me(self, interaction: discord.Interaction) -> User:
        return await self.service.require(interaction.user)

    async def them(self, member: discord.abc.User) -> User:
        if member.bot:
            raise BotError(f"{member.display_name} is a bot.")
        return await self.service.require(member, you=False)

    # --- replies -----------------------------------------------------------------

    @staticmethod
    async def defer(interaction: discord.Interaction, *, ephemeral: bool = False):
        if not interaction.response.is_done():
            await interaction.response.defer(thinking=True, ephemeral=ephemeral)

    @staticmethod
    async def reply(interaction: discord.Interaction, content=None, *, ephemeral=False, **kwargs):
        if interaction.response.is_done():
            await interaction.followup.send(content, ephemeral=ephemeral, **kwargs)
        else:
            await interaction.response.send_message(content, ephemeral=ephemeral, **kwargs)

    async def send_board(
        self,
        interaction: discord.Interaction,
        state: GameState,
        content: str = "",
        *,
        move=None,
        perspective=None,
        ping: bool = False,
        view: discord.ui.View | None = None,
    ):
        """Post a board with its status line. ``ping`` mentions whoever is to move."""
        guild = interaction.guild
        white, black = await render.display_names(self.service, guild, state)
        lines = [content] if content else []
        status = render.status_line(state, white, black)
        if ping and not state.is_over:
            side_id, side_name = state.player_id(state.to_move), state.player_name(state.to_move)
            status += f" {await render.mention(self.service, guild, side_id, side_name)}, your move."
        lines.append(status)
        file = await render.board_file_async(state, white, black, move=move, perspective=perspective)
        kwargs = {"file": file, "allowed_mentions": discord.AllowedMentions(users=True)}
        if view is not None:
            kwargs["view"] = view
        await self.reply(interaction, "\n".join(lines), **kwargs)

    async def move_text(self, guild, result: MoveResult) -> str:
        state = result.state
        white, black = await render.display_names(self.service, guild, state)
        mover = black if state.to_move.name == "WHITE" else white
        return f"**{mover}** played **{result.san}**."
