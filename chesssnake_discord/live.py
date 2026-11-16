"""
Relaying moves made outside Discord (in the web UI, the terminal client, another
bot) into each guild's chess channel.

Two ways to hear about them, chosen by ``[live] mode``:

- **websocket**: chesssnake's live updates. The bot is an administrator, so it
  ``follow``s every game on the server: moves, draws, resignations and new games
  arrive as they happen, with nothing to subscribe to or discover. Each event says
  who sent it (``requested_by``), so the bot's own actions are recognised and
  skipped.
- **poll**: list the games in progress every ``poll_interval_seconds`` and compare
  versions; a tracked game that drops off the list is fetched once to see how it
  ended.

``auto`` tries the websocket and falls back to polling, as does a dropped socket
until it reconnects. Either way, every state goes through :meth:`LiveRelay.handle`,
which posts it only if its version is newer than anything Discord has seen. The
bot's own actions go through :meth:`LiveRelay.own`, which records their result as
seen and, for polling, holds the game's changes until then, so a move made with
``/chess move`` is never posted twice.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable

from chesssnake import GameState, MoveResult
from chesssnake.db import errors as db_errors
from chesssnake.remote.realtime import RealtimeUnavailable

from .chess import ChessService
from .config import LiveSection
from .store import Store

log = logging.getLogger(__name__)

#: ``post(state, move, note)``: send a relayed change to the guild's channel.
#: ``move`` is ``{"san", "from", "to", ...}``, or ``None`` for a draw offer, a
#: resignation or a new game.
Poster = Callable[[GameState, "dict | None", str], Awaitable[None]]


class LiveRelay:
    def __init__(
        self,
        service: ChessService,
        store: Store,
        settings: LiveSection,
        poster: Poster,
        guild_ids: Callable[[], set[int]],
    ):
        self.service = service
        self.store = store
        self.settings = settings
        self.poster = poster
        self.guild_ids = guild_ids
        self.mode = "off"  # what is actually running: websocket, poll, or off
        self._stream = None
        self._polling = False  # polling as a fallback while the socket is down
        self._tasks: list[asyncio.Task] = []
        self._busy: dict[int, int] = {}  # game id -> bot actions in flight
        self._held: dict[int, tuple[GameState, dict | None]] = {}
        self._seeded = False

    # --- the bot's own actions ---------------------------------------------------

    async def own(self, game_id: int | None, action: Awaitable):
        """
        Run one of the bot's own actions on a game and record its result as seen.

        Changes to the game noticed meanwhile are held, then dropped if the
        action's result is as new as they are.
        """
        if game_id is not None:
            self._busy[game_id] = self._busy.get(game_id, 0) + 1
        try:
            result = await action
            state = result.state if isinstance(result, MoveResult) else result
            if isinstance(state, GameState) and state.id is not None:
                self.seen(state)
            return result
        finally:
            if game_id is not None:
                self._busy[game_id] -= 1
                if not self._busy[game_id]:
                    del self._busy[game_id]
                    held = self._held.pop(game_id, None)
                    if held is not None:
                        await self.handle(*held)

    def seen(self, state: GameState):
        """Discord has been shown this state (the bot posted it itself)."""
        if state.group_id in self.guild_ids():
            self.store.mark_posted(
                state.id, state.version, state.group_id, finished=state.is_over, last_move=_move_key(state)
            )

    # --- relaying ---------------------------------------------------------------

    async def handle(self, state: GameState, move: dict | None = None):
        """Post a state if it is in one of our guilds and newer than what Discord saw."""
        if state.id is None or state.group_id not in self.guild_ids():
            return
        if state.id in self._busy:
            self._held[state.id] = (state, move)
            return
        posted = self.store.posted_version(state.id)
        if posted is not None and state.version <= posted:
            return
        if posted is None and not self._seeded:
            # The first poll after start-up: games already under way are not news.
            self.seen(state)
            return
        if move is not None:
            note = "played outside Discord"
        elif posted is None:
            note = "a game started outside Discord"
        else:
            note = "updated outside Discord"
        channel = self.store.guild(state.group_id).channel_id
        if channel is not None:
            try:
                await self.poster(state, move, note)
            except Exception:
                log.exception("Could not post game %s to channel %s", state.id, channel)
        self.seen(state)

    # --- starting and stopping -----------------------------------------------------

    async def start(self):
        mode = self.settings.mode
        if mode == "off":
            log.info("Live relay is off ([live] mode = off)")
            return
        if mode in ("auto", "websocket"):
            if await self._open_stream():
                self.mode = "websocket"
                self._seeded = True  # the feed only carries what happens from now on
                self._tasks.append(asyncio.create_task(self._read_stream(), name="live-feed"))
                self._tasks.append(asyncio.create_task(self._poll_loop(fallback=True), name="live-fallback"))
                return
            if mode == "websocket":
                log.warning("Websocket live updates are unavailable; polling instead")
        self.mode = "poll"
        self._tasks.append(asyncio.create_task(self._poll_loop(fallback=False), name="live-poll"))

    async def stop(self):
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._tasks.clear()
        await self._close_stream()
        self.mode = "off"

    async def _open_stream(self) -> bool:
        stream = self.service.client.events()
        try:
            await stream.connect()
        except RealtimeUnavailable as exc:
            log.info("No websocket live updates (%s)", exc)
            return False
        except db_errors.AuthError as exc:
            log.error("chesssnake refused the bot's token for live updates: %s", exc)
            return False
        await stream.follow(all=True)
        self._stream = stream
        log.info("Live relay: following every game at %s", stream.url)
        return True

    async def _close_stream(self):
        stream, self._stream = self._stream, None
        if stream is not None:
            with contextlib.suppress(Exception):
                await stream.close()

    # --- the websocket ---------------------------------------------------------------

    async def _read_stream(self):
        async for event in self._stream:
            try:
                await self._on_event(event)
            except Exception:
                log.exception("Could not handle live event %r", event.get("type"))
        # The stream gave up (ended or auth_error): poll from now on.
        self._polling = True
        self.mode = "poll"

    async def _on_event(self, event: dict):
        kind = event.get("type")
        if kind in ("game", "game_started"):
            state = GameState.from_dict(event["state"])
            if event.get("requested_by") == self.service.bot_user_id:
                self.seen(state)  # the bot did this itself, and has posted it
                return
            await self.handle(state, event.get("move") if kind == "game" else None)
        elif kind == "game_removed":
            self.store.forget(int(event["game"]))
        elif kind == "following":
            log.info("Live relay: following %s", "every game" if event.get("all") else f"group {event.get('group')}")
        elif kind == "error":
            log.warning("Live updates refused something: %s", event)
        elif kind == "disconnected":
            log.warning("Live updates disconnected (code %s); polling until they are back", event.get("code"))
            self._polling = True
        elif kind == "reconnected":
            log.info("Live updates reconnected")
            self._polling = False
        elif kind == "ended":
            log.warning("Live updates ended (%s); polling from now on", event.get("reason"))
        elif kind == "auth_error":
            log.error("chesssnake stopped accepting the bot's token: %s", event.get("detail"))

    # --- polling ------------------------------------------------------------------------

    async def _poll_loop(self, fallback: bool):
        while True:
            if not fallback or self._polling:
                try:
                    await self.poll()
                except Exception:
                    log.exception("Polling for game changes failed")
            await asyncio.sleep(self.settings.poll_interval_seconds)

    async def poll(self):
        """One round of polling: every game in our guilds, and the tracked ones that ended."""
        guilds = self.guild_ids()
        live = {s.id: s for s in await self.service.live_games() if s.group_id in guilds}
        for state in live.values():
            await self.handle(state, self._last_move(state))
        for game_id in set(self.store.tracked_games()) - set(live):
            try:
                state = await self.service.get_game(game_id)
            except db_errors.GameNotFoundError:
                self.store.forget(game_id)
                continue
            await self.handle(state, self._last_move(state))
        self._seeded = True

    def _last_move(self, state: GameState) -> dict | None:
        """The move to show for a polled state, if a move is what changed since Discord last saw it."""
        key = _move_key(state)
        if key is None or key == self.store.posted_last_move(state.id):
            return None  # no move yet, or a draw offer or resignation after the move already shown
        return state.last_move


def _move_key(state: GameState) -> str | None:
    """The last move, identified: its ply count and squares, so a repeated move still differs."""
    move = state.last_move
    if move is None:
        return None
    return f"{state.fen.split()[-1]}{state.to_move.name[0]}:{move['from']}{move['to']}{move.get('promotion') or ''}"
