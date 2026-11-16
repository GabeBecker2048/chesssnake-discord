"""Turning failures into one sentence a player can act on."""

from __future__ import annotations

import logging

from chesssnake.db import errors as db_errors
from chesssnake.engine.errors import ChessError

log = logging.getLogger(__name__)


class BotError(Exception):
    """A refusal with a message written for the player, shown as is."""


def describe(exc: BaseException) -> str | None:
    """
    The message for a failure, or ``None`` if it is a bug (logged by the caller
    with its traceback, and answered with a generic apology).
    """
    if isinstance(exc, BotError):
        return str(exc)
    if isinstance(exc, ChessError):
        # The server's messages are written for players ("There is no piece on e5").
        return f"That move doesn't work: {exc}"
    if isinstance(exc, db_errors.NotYourTurnError):
        return "It's not your turn."
    if isinstance(exc, db_errors.VersionConflictError):
        return "The game changed while you were moving. Look at the board and try again."
    if isinstance(exc, db_errors.AuthError):
        log.error("chesssnake rejected the bot's token: %s", exc)
        return "The bot's chesssnake token was rejected. Ask whoever runs the bot to check it."
    if isinstance(exc, db_errors.DatabaseBusyError):
        return "The chess server is busy. Try again in a moment."
    if isinstance(exc, db_errors.GameError):
        return str(exc) or type(exc).__name__
    try:
        import requests
    except ImportError:  # pragma: no cover - requests comes with chesssnake[client]
        requests = None
    if requests is not None and isinstance(exc, requests.RequestException):
        log.warning("chesssnake is unreachable: %s", exc)
        return "The chess server can't be reached right now."
    return None
