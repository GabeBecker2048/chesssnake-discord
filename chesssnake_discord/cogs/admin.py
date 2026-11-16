"""/chess admin: for server managers, acting on other people's games and accounts."""

from __future__ import annotations

from typing import Literal

import discord
from discord import app_commands

from ..errors import BotError
from .base import Base


class AdminCommands(Base):
    admin = app_commands.Group(name="admin", description="Server managers and chess moderators: manage games")

    async def _admin_game(self, interaction: discord.Interaction, game_id: int):
        guild = self.guild(interaction)
        await self.require_game_manager(interaction)
        game = await self.service.get_game(game_id)
        if game.group_id != guild.id:
            raise BotError(f"Game {game_id} isn't a game in this server.")
        return game

    # --- games ---------------------------------------------------------------------------

    @admin.command(name="create-game", description="Start a game between two members, no challenge needed")
    async def create_game(self, interaction: discord.Interaction, white: discord.Member, black: discord.Member):
        guild = self.guild(interaction)
        await self.require_game_manager(interaction)
        if white.id == black.id:
            raise BotError("A game needs two different players.")
        w, b = await self.them(white), await self.them(black)
        await self.defer(interaction)
        state = await self.relay.own(None, self.service.call("create_game", w.id, b.id, group_id=guild.id))
        await self.send_board(
            interaction, state, f"Game {state.id} started by {interaction.user.display_name}.", ping=True
        )

    @admin.command(name="delete-game", description="Delete a game for good, in progress or finished")
    async def delete_game(self, interaction: discord.Interaction, game_id: int):
        game = await self._admin_game(interaction, game_id)
        await self.defer(interaction, ephemeral=True)
        await self.service.call("delete_game", game.id)
        self.store.forget(game.id)
        await self.reply(
            interaction, f"Deleted game {game.id} ({game.white_name} vs {game.black_name}).", ephemeral=True
        )

    @admin.command(name="resign", description="Resign a game on a player's behalf (an abandoned game, say)")
    @app_commands.describe(player="The player who resigns")
    async def resign_for(self, interaction: discord.Interaction, game_id: int, player: discord.Member):
        game = await self._admin_game(interaction, game_id)
        user = await self.them(player)
        if user.id not in (game.white_id, game.black_id):
            raise BotError(f"{player.display_name} isn't playing game {game_id}.")
        await self.defer(interaction)
        state = await self.relay.own(game.id, self.service.resign(game, user))
        await self.send_board(
            interaction,
            state,
            f"{interaction.user.display_name} resigned game {game.id} for **{player.display_name}**.",
        )

    @admin.command(name="draw", description="End a game in progress as a draw")
    async def draw_for(self, interaction: discord.Interaction, game_id: int):
        game = await self._admin_game(interaction, game_id)
        if game.status != 0:
            raise BotError(f"Game {game_id} is already over.")
        await self.defer(interaction)

        async def settle():
            # A draw is one player's offer and the other's acceptance.
            current = game
            if current.draw_offered_by is None:
                current = await self.service.draw(current, current.player_id(current.to_move), "offer")
            accepter = current.player_id(current.draw_offered_by.opponent)
            return await self.service.draw(current, accepter, "accept")

        state = await self.relay.own(game.id, settle())
        await self.send_board(interaction, state, f"{interaction.user.display_name} ended game {game.id} as a draw.")

    @admin.command(name="moderator", description="Let a member manage this server's chess games in the web UI too")
    @app_commands.describe(member="Who", action="add or remove")
    async def moderator(
        self, interaction: discord.Interaction, member: discord.Member, action: Literal["add", "remove"]
    ):
        guild = self.guild(interaction)
        self.require_manager(interaction)
        await self.defer(interaction, ephemeral=True)
        user = await self.service.set_moderator(guild.id, member, action == "add")
        if action == "add":
            text = (
                f"`{user.username}` ({member.mention}) is now a chess moderator here: they can use the "
                "`/chess admin` game commands, and manage this server's games through the chess server's API."
            )
        else:
            text = f"`{user.username}` ({member.mention}) is no longer a chess moderator here."
        await self.reply(interaction, text, ephemeral=True)

    # --- accounts ------------------------------------------------------------------------
    # chesssnake accounts are shared by every server the bot is in, so these are for
    # the bot's owners, not each server's managers.

    @admin.command(name="link", description="Bot owners: link a member to a chess account without its password")
    @app_commands.describe(username="The chesssnake username")
    async def link_for(self, interaction: discord.Interaction, member: discord.Member, username: str):
        self.guild(interaction)
        await self.require_owner(interaction)
        await self.defer(interaction, ephemeral=True)
        linked, previous = await self.service.admin_link(member, username, interaction.guild.id)
        text = f"Linked {member.mention} to `{linked.username}`."
        if previous is not None:
            text += f" (They were linked to `{previous.username}` before.)"
        await self.reply(interaction, text, ephemeral=True)

    @admin.command(name="unlink", description="Bot owners: disconnect a member from their chess account")
    async def unlink_for(self, interaction: discord.Interaction, member: discord.Member):
        self.guild(interaction)
        await self.require_owner(interaction)
        await self.defer(interaction, ephemeral=True)
        user = await self.service.unlink(member)
        await self.reply(interaction, f"Unlinked {member.mention} from `{user.username}`.", ephemeral=True)

    async def _set_disabled(self, interaction, member: discord.Member, disabled: bool):
        self.guild(interaction)
        await self.require_owner(interaction)
        user = await self.service.user_for(member)
        if user is None:
            raise BotError(f"{member.display_name} doesn't have a chess account.")
        if user.role == "admin":
            raise BotError("The bot won't disable or enable chess admin accounts.")
        await self.defer(interaction, ephemeral=True)
        await self.service.call("update_user", user.id, disabled=disabled)
        self.service.forget_users()
        state = "disabled" if disabled else "enabled"
        await self.reply(interaction, f"`{user.username}` ({member.mention}) is {state}.", ephemeral=True)

    @admin.command(name="disable", description="Bot owners: disable a member's chess account (in every server)")
    async def disable(self, interaction: discord.Interaction, member: discord.Member):
        await self._set_disabled(interaction, member, True)

    @admin.command(name="enable", description="Bot owners: re-enable a member's chess account")
    async def enable(self, interaction: discord.Interaction, member: discord.Member):
        await self._set_disabled(interaction, member, False)

    @admin.command(name="rename", description="Bot owners: change a member's chesssnake username")
    async def rename(self, interaction: discord.Interaction, member: discord.Member, username: str):
        self.guild(interaction)
        await self.require_owner(interaction)
        user = await self.service.require(member, you=False)
        await self.defer(interaction, ephemeral=True)
        renamed = await self.service.call("update_user", user.id, username=username.strip())
        self.service.forget_users()
        await self.reply(interaction, f"`{user.username}` is now `{renamed.username}`.", ephemeral=True)
