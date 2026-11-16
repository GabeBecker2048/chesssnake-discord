"""/chess challenge, challenges, accept, decline, move, board, games, draw, resign, moves, export, record."""

from __future__ import annotations

import io
import logging
import re
import time
from typing import Literal

import discord
from chesssnake import Challenge, GameState
from discord import app_commands

from .. import render
from ..errors import BotError, describe
from .base import Base

log = logging.getLogger(__name__)

_AUTOCOMPLETE_SECONDS = 5.0


class ChallengeButton(
    discord.ui.DynamicItem[discord.ui.Button], template=r"chess:ch:(?P<action>accept|decline):(?P<id>\d+)"
):
    """Accept/Decline on a challenge message. The custom id carries the challenge
    id, so the buttons keep working after the bot restarts."""

    def __init__(self, action: str, challenge_id: int):
        style = discord.ButtonStyle.success if action == "accept" else discord.ButtonStyle.secondary
        super().__init__(
            discord.ui.Button(label=action.capitalize(), style=style, custom_id=f"chess:ch:{action}:{challenge_id}")
        )
        self.action = action
        self.challenge_id = challenge_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(match["action"], int(match["id"]))

    async def callback(self, interaction: discord.Interaction):
        cog = interaction.client.chess_cog
        try:
            await cog.answer_challenge(interaction, self.challenge_id, self.action)
        except Exception as exc:
            await cog.report(interaction, exc)


def challenge_view(challenge_id: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(ChallengeButton("accept", challenge_id))
    view.add_item(ChallengeButton("decline", challenge_id))
    return view


class ConfirmResign(discord.ui.View):
    def __init__(self, cog: PlayCommands, owner_id: int, game: GameState, me):
        super().__init__(timeout=60)
        self.cog, self.owner_id, self.game, self.me = cog, owner_id, game, me

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("That isn't your game to resign.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Resign", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, _button):
        self.stop()
        try:
            await interaction.response.edit_message(content="Resigning…", view=None)
            state = await self.cog.relay.own(self.game.id, self.cog.service.resign(self.game, self.me))
            await self.cog.send_board(interaction, state, f"**{interaction.user.display_name}** resigned.")
        except Exception as exc:
            await self.cog.report(interaction, exc)

    @discord.ui.button(label="Keep playing", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, _button):
        self.stop()
        await interaction.response.edit_message(content="Not resigned. Play on!", view=None)


class PlayCommands(Base):
    _games_cache: dict[int, tuple[float, list[GameState]]]
    _legal_cache: dict[tuple[int, int], list[dict]]

    # --- challenges --------------------------------------------------------------------

    @app_commands.command(name="challenge", description="Challenge someone to a game")
    @app_commands.describe(color="The colour you want to play")
    async def challenge(
        self,
        interaction: discord.Interaction,
        opponent: discord.Member,
        color: Literal["random", "white", "black"] = "random",
    ):
        guild = self.guild(interaction)
        me = await self.me(interaction)
        them = await self.them(opponent)
        await self.defer(interaction)
        challenge = await self.service.challenge(guild.id, me, them, color)
        side = "" if color == "random" else f" ({interaction.user.display_name} wants {color})"
        await self.reply(
            interaction,
            f"{opponent.mention}, **{interaction.user.display_name}** challenges you to a game of chess{side}!",
            view=challenge_view(challenge.id),
            allowed_mentions=discord.AllowedMentions(users=True),
        )

    async def answer_challenge(self, interaction: discord.Interaction, challenge_id: int, action: str):
        """The Accept/Decline buttons."""
        me = await self.me(interaction)
        challenge = await self.service.call("get_challenge", challenge_id)
        if action == "accept":
            if challenge.challenged_id != me.id:
                raise BotError("Only the player who was challenged can accept.")
            await interaction.response.defer()
            state = await self.relay.own(None, self.service.accept(challenge, me))
            await interaction.message.edit(view=None)
            await self.send_board(interaction, state, "The game is on!", ping=True)
        else:
            await self.service.decline(challenge, me)
            verb = "declined" if challenge.challenged_id == me.id else "withdrew"
            await interaction.response.edit_message(view=None)
            await interaction.followup.send(f"**{interaction.user.display_name}** {verb} the challenge.")

    @app_commands.command(name="challenges", description="Your pending challenges in this server")
    async def challenges(self, interaction: discord.Interaction):
        guild = self.guild(interaction)
        me = await self.me(interaction)
        await self.defer(interaction, ephemeral=True)
        found = await self.service.challenges(guild.id, me)
        if not found:
            await self.reply(interaction, "You have no pending challenges here.", ephemeral=True)
            return
        lines = []
        for c in found:
            lines.append(await self._challenge_line(guild, c, me.id))
        await self.reply(interaction, "\n".join(lines), ephemeral=True)

    async def _challenge_line(self, guild, c: Challenge, me_id: int) -> str:
        incoming = c.challenged_id == me_id
        other_id, other_name = (
            (c.challenger_id, c.challenger_name) if incoming else (c.challenged_id, c.challenged_name)
        )
        other = await render.mention(self.service, guild, other_id, other_name)
        colour = "" if c.color == "random" else f", they play {c.color}" if incoming else f", you play {c.color}"
        if incoming:
            return f"• from {other}{colour} (`/chess accept` or `/chess decline`)"
        return f"• to {other}{colour} (`/chess decline` to withdraw)"

    @app_commands.command(name="accept", description="Accept a challenge")
    @app_commands.describe(challenger="Who challenged you (needed if more than one person did)")
    async def accept(self, interaction: discord.Interaction, challenger: discord.Member | None = None):
        guild = self.guild(interaction)
        me = await self.me(interaction)
        other = await self.them(challenger) if challenger else None
        await self.defer(interaction)
        challenge = await self.service.find_challenge(guild.id, me, other, "incoming")
        state = await self.relay.own(None, self.service.accept(challenge, me))
        await self.send_board(interaction, state, "The game is on!", ping=True)

    @app_commands.command(name="decline", description="Decline a challenge, or withdraw one you made")
    @app_commands.describe(member="The other player (needed if you have more than one challenge)")
    async def decline(self, interaction: discord.Interaction, member: discord.Member | None = None):
        guild = self.guild(interaction)
        me = await self.me(interaction)
        other = await self.them(member) if member else None
        await self.defer(interaction)
        challenge = await self.service.find_challenge(guild.id, me, other, "all")
        await self.service.decline(challenge, me)
        if challenge.challenged_id == me.id:
            text = f"**{interaction.user.display_name}** declined **{challenge.challenger_name}**'s challenge."
        else:
            text = f"**{interaction.user.display_name}** withdrew their challenge to **{challenge.challenged_name}**."
        await self.reply(interaction, text)

    # --- playing -------------------------------------------------------------------------

    async def _game(self, interaction, opponent: discord.Member | None) -> tuple[GameState, object]:
        guild = self.guild(interaction)
        me = await self.me(interaction)
        other = await self.them(opponent) if opponent else None
        return await self.service.resolve_game(guild.id, me, other), me

    @app_commands.command(name="move", description="Make a move (e4, Nf3, exd5, 0-0, e8Q, or e2e4)")
    @app_commands.describe(
        move="Your move; suggestions list the legal ones", opponent="Needed if you have more than one game"
    )
    async def move(self, interaction: discord.Interaction, move: str, opponent: discord.Member | None = None):
        game, me = await self._game(interaction, opponent)
        await self.defer(interaction)
        result = await self.relay.own(game.id, self.service.move(game, me, move.strip()))
        self._games_cache.pop(interaction.user.id, None)
        text = await self.move_text(interaction.guild, result)
        await self.send_board(interaction, result.state, text, move=result, ping=True)

    @move.autocomplete("move")
    async def _move_autocomplete(self, interaction: discord.Interaction, current: str):
        try:
            game = await self._autocomplete_game(interaction)
            if game is None:
                return []
            key = (game.id, game.version)
            if key not in self._legal_cache:
                if len(self._legal_cache) > 256:
                    self._legal_cache.clear()
                self._legal_cache[key] = await self.service.legal_moves(game.id)
            sans = [m["san"] for m in self._legal_cache[key]]
        except Exception:
            return []
        typed = current.strip().lower()
        matches = [s for s in sans if s.lower().startswith(typed)] or [s for s in sans if typed in s.lower()]
        return [app_commands.Choice(name=s, value=s) for s in matches[:25]]

    async def _autocomplete_game(self, interaction: discord.Interaction) -> GameState | None:
        """The game a half-typed /chess move is for, if it is the caller's turn in it."""
        if interaction.guild is None:
            return None
        me = await self.service.user_for(interaction.user)
        if me is None:
            return None
        cached = self._games_cache.get(interaction.user.id)
        if cached is None or cached[0] < time.monotonic():
            games = await self.service.games(interaction.guild.id, me)
            self._games_cache[interaction.user.id] = (time.monotonic() + _AUTOCOMPLETE_SECONDS, games)
        else:
            games = cached[1]
        opponent = getattr(interaction.namespace, "opponent", None)
        if opponent is not None:
            other = await self.service.user_for(opponent)
            games = [g for g in games if other is not None and other.id in (g.white_id, g.black_id)]
        if len(games) != 1:
            return None
        game = games[0]
        mine = "white" if game.white_id == me.id else "black"
        return game if render.to_move(game) == mine else None

    @app_commands.command(name="board", description="Show a game's board")
    @app_commands.describe(
        opponent="Your game against this player",
        game_id="Any game by its id, finished ones too",
        perspective="Which side is at the bottom (default: both boards)",
    )
    async def board(
        self,
        interaction: discord.Interaction,
        opponent: discord.Member | None = None,
        game_id: int | None = None,
        perspective: Literal["both", "white", "black", "mine"] = "both",
    ):
        if game_id is not None:
            await self.defer(interaction)
            game = await self.service.get_game(game_id)
            viewer = await self.service.user_for(interaction.user)
        else:
            game, viewer = await self._game(interaction, opponent)
            await self.defer(interaction)
        white, black = await render.display_names(self.service, interaction.guild, game)
        view_from = render.perspective_for(game, perspective, viewer.id if viewer else None)
        await self.send_board(interaction, game, render.game_label(game, white, black), perspective=view_from)

    @app_commands.command(name="games", description="Games in progress in this server")
    @app_commands.describe(member="Whose games (default: yours)")
    async def games(self, interaction: discord.Interaction, member: discord.Member | None = None):
        guild = self.guild(interaction)
        member = member or interaction.user
        user = await self.service.require(member, you=member.id == interaction.user.id)
        await self.defer(interaction, ephemeral=True)
        found = await self.service.games(guild.id, user)
        if not found:
            await self.reply(interaction, f"{member.display_name} has no games in progress here.", ephemeral=True)
            return
        lines = []
        for g in found:
            white, black = await render.display_names(self.service, guild, g)
            lines.append(f"• **{white}** vs **{black}** (game {g.id}): {render.status_line(g, white, black)}")
        await self.reply(interaction, "\n".join(lines)[:1990], ephemeral=True)

    @app_commands.command(name="draw", description="Offer, accept or decline a draw")
    @app_commands.describe(opponent="Needed if you have more than one game")
    async def draw(
        self,
        interaction: discord.Interaction,
        action: Literal["offer", "accept", "decline"],
        opponent: discord.Member | None = None,
    ):
        game, me = await self._game(interaction, opponent)
        await self.defer(interaction)
        state = await self.relay.own(game.id, self.service.draw(game, me, action))
        name = interaction.user.display_name
        text = {
            "offer": f"**{name}** offers a draw. Answer with `/chess draw accept` or `/chess draw decline` "
            "(making a move declines it too).",
            "accept": f"**{name}** accepted the draw.",
            "decline": f"**{name}** declined the draw. Play on!",
        }[action]
        await self.send_board(interaction, state, text, ping=action == "offer")

    @app_commands.command(name="resign", description="Resign a game")
    @app_commands.describe(opponent="Needed if you have more than one game")
    async def resign(self, interaction: discord.Interaction, opponent: discord.Member | None = None):
        game, me = await self._game(interaction, opponent)
        other = game.black_name if game.white_id == me.id else game.white_name
        await self.reply(
            interaction,
            f"Resign your game against **{other}**?",
            view=ConfirmResign(self, interaction.user.id, game, me),
            ephemeral=True,
        )

    @app_commands.command(name="moves", description="List the legal moves in your game")
    @app_commands.describe(opponent="Needed if you have more than one game")
    async def moves(self, interaction: discord.Interaction, opponent: discord.Member | None = None):
        game, _me = await self._game(interaction, opponent)
        await self.defer(interaction, ephemeral=True)
        legal = await self.service.legal_moves(game.id)
        side = render.to_move(game)
        sans = " ".join(f"`{m['san']}`" for m in legal) or "none"
        await self.reply(interaction, f"Legal moves for {side} ({len(legal)}):\n{sans}"[:1990], ephemeral=True)

    @app_commands.command(name="export", description="A game's PGN, FEN or move list")
    @app_commands.describe(opponent="Your game against this player", game_id="Any game by its id")
    async def export(
        self,
        interaction: discord.Interaction,
        format: Literal["pgn", "fen", "history"],
        opponent: discord.Member | None = None,
        game_id: int | None = None,
    ):
        if game_id is not None:
            await self.defer(interaction, ephemeral=True)
            game = await self.service.get_game(game_id)
        else:
            game, _me = await self._game(interaction, opponent)
            await self.defer(interaction, ephemeral=True)
        if format == "fen":
            await self.reply(interaction, f"```\n{game.fen}\n```", ephemeral=True)
        elif format == "pgn":
            pgn = await self.service.call("pgn", game.id)
            file = discord.File(io.BytesIO(pgn.encode()), filename=f"game-{game.id}.pgn")
            await self.reply(interaction, f"Game {game.id}:", file=file, ephemeral=True)
        else:
            history = await self.service.history(game.id)
            parts = []
            for entry in history:
                ply = entry["ply"]
                if ply % 2 == 1:
                    parts.append(f"{(ply + 1) // 2}.")
                parts.append(entry["san"])
            text = " ".join(parts) or "No moves yet."
            await self.reply(interaction, f"```\n{text}\n```"[:1990], ephemeral=True)

    @app_commands.command(name="record", description="Wins, losses and draws between two players here")
    @app_commands.describe(member="The other player of the pair (default: you)")
    async def record(
        self, interaction: discord.Interaction, opponent: discord.Member, member: discord.Member | None = None
    ):
        guild = self.guild(interaction)
        member = member or interaction.user
        a = await self.service.require(member, you=member.id == interaction.user.id)
        b = await self.them(opponent)
        await self.defer(interaction)
        r = await self.service.record(guild.id, a, b)
        await self.reply(
            interaction,
            f"**{member.display_name}** vs **{opponent.display_name}**: "
            f"{r.wins} win(s), {r.losses} loss(es), {r.draws} draw(s).",
        )

    # --- error reporting -------------------------------------------------------------------

    async def report(self, interaction: discord.Interaction, exc: BaseException):
        """Answer a failure privately; a bug is logged and gets an apology."""
        message = describe(exc)
        if message is None:
            log.exception("Command failed", exc_info=exc)
            message = "Something went wrong. It has been logged."
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message(message, ephemeral=True)
                return
            if interaction.type is discord.InteractionType.application_command:
                # Deferred publicly: drop the "thinking…" message so the error stays private.
                try:
                    await interaction.delete_original_response()
                except discord.HTTPException:
                    pass
            await interaction.followup.send(message, ephemeral=True)
        except discord.HTTPException:
            log.warning("Could not report an error to %s: %s", interaction.user, message)
