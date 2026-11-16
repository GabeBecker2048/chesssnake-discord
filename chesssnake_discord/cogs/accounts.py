"""/chess init, link, unlink, password, profile, auto-init, channel."""

from __future__ import annotations

import logging
from typing import Literal

import discord
from discord import app_commands
from discord.ext import commands

from ..errors import BotError, describe
from .base import Base

log = logging.getLogger(__name__)


class _Modal(discord.ui.Modal):
    """A modal whose failures are answered privately, like the commands'."""

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        message = describe(error)
        if message is None:
            log.exception("Modal %s failed", type(self).__name__, exc_info=error)
            message = "Something went wrong. It has been logged."
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


class PasswordModal(_Modal, title="Set a password for the web UI"):
    password = discord.ui.TextInput(label="New password", min_length=8, max_length=256)
    confirm = discord.ui.TextInput(label="Same password again", min_length=8, max_length=256)

    def __init__(self, cog: AccountCommands):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        if self.password.value != self.confirm.value:
            raise BotError("The two passwords don't match.")
        await interaction.response.defer(ephemeral=True, thinking=True)
        user = await self.cog.service.set_first_password(interaction.user, self.password.value)
        url = self.cog.config.public_url
        await interaction.followup.send(
            f"Done. Log in at {url} as `{user.username}` with that password.", ephemeral=True
        )


class AccountCommands(Base):
    # --- accounts ----------------------------------------------------------------

    @app_commands.command(name="init", description="Make your chess account (or everyone's, for server managers)")
    @app_commands.describe(everyone="Make accounts for every member who doesn't have one (Manage Server)")
    async def init(self, interaction: discord.Interaction, everyone: bool = False):
        guild = self.guild(interaction)
        if not everyone:
            user = await self.service.user_for(interaction.user)
            if user is not None:
                raise BotError(f"You already have a chess account: `{user.username}`.")
            await self.defer(interaction, ephemeral=True)
            user, _ = await self.service.create_for(interaction.user)
            await self.reply(
                interaction,
                f"Your chess account is `{user.username}`. Challenge someone with {self.cmd('challenge')}.\n"
                f"Use {self.cmd('password')} if you also want to play in the web UI." + self._notice(),
                ephemeral=True,
            )
            return

        self.require_manager(interaction)
        self._require_bulk()
        await self.defer(interaction, ephemeral=True)
        if not guild.chunked:
            await guild.chunk()
        created, existing, failed = [], 0, []
        for member in guild.members:
            if member.bot:
                continue
            try:
                user, made = await self.service.create_for(member)
            except Exception as exc:
                failed.append(f"{member.display_name}: {describe(exc) or type(exc).__name__}")
                if describe(exc) is None:
                    log.exception("init everyone: %s", member)
                continue
            if made:
                created.append(f"{member.display_name} → `{user.username}`")
            else:
                existing += 1
        lines = [f"Made **{len(created)}** account(s); **{existing}** member(s) already had one."]
        if created:
            lines.append("\n".join(created[:30]) + ("\n…" if len(created) > 30 else ""))
        if failed:
            lines.append(f"**{len(failed)}** failed:\n" + "\n".join(failed[:10]))
        notice = self._notice()
        await self.reply(interaction, "\n".join(lines)[: 1990 - len(notice)] + notice, ephemeral=True)

    def _notice(self) -> str:
        """``[discord] notice``, as a paragraph to end a reply with, or nothing."""
        return f"\n\n{self.config.discord.notice}" if self.config.discord.notice else ""

    def _require_bulk(self):
        """Making accounts for many members at once needs the setting and the intent."""
        if not self.config.discord.bulk_accounts:
            raise BotError(f"This bot doesn't make accounts in bulk: each member runs {self.cmd('init')} themselves.")
        if not self.bot.intents.members:
            raise BotError(
                "This bot can't see the server's member list (its Server Members intent is off), "
                f"so each member runs {self.cmd('init')} themselves."
            )

    @app_commands.command(
        name="auto-init", description="Make chess accounts for new members as they join (Manage Server)"
    )
    async def auto_init(self, interaction: discord.Interaction, setting: Literal["enable", "disable"]):
        guild = self.guild(interaction)
        self.require_manager(interaction)
        if setting == "enable":
            self._require_bulk()
        self.store.set_auto_init(guild.id, setting == "enable")
        state = "will now get" if setting == "enable" else "will no longer get"
        await self.reply(interaction, f"New members {state} a chess account when they join.", ephemeral=True)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot or not self.config.discord.bulk_accounts or not self.store.guild(member.guild.id).auto_init:
            return
        try:
            user, made = await self.service.create_for(member)
        except Exception:
            log.exception("auto-init failed for %s in %s", member, member.guild)
            return
        if made:
            log.info("auto-init: %s -> %s", member, user.username)

    @app_commands.command(name="link", description="Use a chess account you already have, with a link code")
    @app_commands.describe(code="From your Account page in the chess web UI (or `chesssnake link-code`)")
    async def link(self, interaction: discord.Interaction, code: str):
        # Answered privately, so nobody else sees the code (not even the command line).
        await self.defer(interaction, ephemeral=True)
        here = interaction.guild.id if interaction.guild else None
        linked, previous = await self.service.link(interaction.user, code, here)
        text = f"Linked to `{linked.username}`. Your Discord games now use that account."
        if previous is not None:
            text += f"\nYou were linked to `{previous.username}` before; its games stay with it."
        await self.reply(interaction, text, ephemeral=True)

    @app_commands.command(name="unlink", description="Disconnect your Discord account from your chess account")
    async def unlink(self, interaction: discord.Interaction):
        await self.defer(interaction, ephemeral=True)
        user = await self.service.unlink(interaction.user)
        await self.reply(
            interaction,
            f"Unlinked from `{user.username}`. Use {self.cmd('link')} or {self.cmd('init')} to play again.",
            ephemeral=True,
        )

    @app_commands.command(name="password", description="Set a password so you can also play in the web UI")
    async def password(self, interaction: discord.Interaction):
        user = await self.me(interaction)
        if user.has_password:
            raise BotError("Your account already has a password. Change it in the web UI.")
        await interaction.response.send_modal(PasswordModal(self))

    @app_commands.command(name="profile", description="Show a chess account")
    async def profile(self, interaction: discord.Interaction, member: discord.Member | None = None):
        member = member or interaction.user
        user = await self.service.require(member, you=member.id == interaction.user.id)
        embed = discord.Embed(title=user.username, url=self.config.public_url or None)
        embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)
        embed.add_field(name="chesssnake id", value=str(user.id))
        embed.add_field(name="Role", value=user.role)
        embed.add_field(name="Web login", value="yes" if user.has_password else f"no ({self.cmd('password')})")
        if user.disabled_at:
            embed.add_field(name="Disabled", value=user.disabled_at)
        await self.reply(interaction, embed=embed, ephemeral=True)

    # --- server settings -------------------------------------------------------------

    @app_commands.command(name="channel", description="Where moves made outside Discord are posted (Manage Server)")
    @app_commands.describe(channel="Leave empty to stop posting them")
    async def channel(self, interaction: discord.Interaction, channel: discord.TextChannel | None = None):
        guild = self.guild(interaction)
        self.require_manager(interaction)
        self.store.set_channel(guild.id, channel.id if channel else None)
        mode = self.relay.mode
        if channel is None:
            text = "Moves made outside Discord won't be posted any more."
        else:
            text = f"Moves made outside Discord (in the web UI, say) will be posted in {channel.mention}."
            if mode == "off":
                text += "\nNote: the live relay is turned off in the bot's config, so nothing will be posted yet."
        await self.reply(interaction, text, ephemeral=True)
