"""Boards and the sentences that go with them."""

from __future__ import annotations

import asyncio
from io import BytesIO

import discord
from chesssnake import Color, GameState
from chesssnake.engine import Board, render_board

from .chess import ChessService

TERMINATIONS = {
    "checkmate": "by checkmate",
    "resignation": "by resignation",
    "stalemate": "by stalemate",
    "threefold": "by threefold repetition",
    "fifty_move": "by the fifty-move rule",
    "insufficient_material": "by insufficient material",
    "agreement": "by agreement",
}


async def display_names(service: ChessService, guild: discord.Guild | None, state: GameState) -> tuple[str, str]:
    """White's and black's names as this guild knows them, else their chess usernames."""

    async def one(user_id, fallback):
        discord_id = await service.discord_id_for(user_id)
        member = guild.get_member(discord_id) if guild is not None and discord_id else None
        return member.display_name if member is not None else fallback

    return await one(state.white_id, state.white_name), await one(state.black_id, state.black_name)


async def mention(service: ChessService, guild: discord.Guild | None, user_id: int, fallback: str) -> str:
    discord_id = await service.discord_id_for(user_id)
    if guild is not None and discord_id and guild.get_member(discord_id) is not None:
        return f"<@{discord_id}>"
    return f"**{fallback}**"


def board_file(state: GameState, white: str, black: str, move=None, perspective=None) -> discord.File:
    """
    The board as a PNG attachment, with Discord display names and the last move
    highlighted (``move`` if given, a ``MoveResult`` or ``{"from", "to"}``, else
    the state's own ``last_move``).
    """
    move = move if move is not None else state.last_move
    image = render_board(Board.from_fen(state.fen), white, black, move=move, perspective=perspective)
    buffer = BytesIO()
    image.save(buffer, "PNG", compress_level=1)
    buffer.seek(0)
    return discord.File(buffer, filename=f"game-{state.id}.png")


async def board_file_async(*args, **kwargs) -> discord.File:
    """:func:`board_file` off the event loop (rendering takes tens of milliseconds)."""
    return await asyncio.to_thread(board_file, *args, **kwargs)


def to_move(state: GameState) -> str:
    """``"white"`` or ``"black"``."""
    return state.to_move.name.lower()


def status_line(state: GameState, white: str, black: str) -> str:
    """Who is to move, any draw offer, or how the game ended."""
    names = {Color.WHITE: white, Color.BLACK: black}
    if not state.is_over:
        line = f"{names[state.to_move]} ({to_move(state)}) to move."
        if state.draw_offered_by is not None:
            line += f" {names[state.draw_offered_by]} has offered a draw."
        return line
    how = TERMINATIONS.get(state.termination or "", "")
    if state.winner is None:
        return f"Game over: a draw {how}.".replace(" .", ".")
    return f"Game over: {names[state.winner]} wins {how}.".replace(" .", ".")


def game_label(state: GameState, white: str, black: str) -> str:
    return f"Game {state.id}: {white} (white) vs {black} (black)"


def perspective_for(state: GameState, choice: str | None, viewer_id: int | None):
    """The renderer's ``perspective`` for a /chess board choice."""
    if choice in ("white", "black"):
        return choice
    if choice == "mine" and viewer_id is not None:
        color = state.color_of(viewer_id)
        return color.name.lower() if color is not None else None
    return None
