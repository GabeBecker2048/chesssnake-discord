"""
The bot's own small state: per-guild settings and the relay's bookkeeping.

Games, users and challenges live on the chesssnake server; nothing here is
chess. Everything is called from the event loop thread only, and every
statement is tiny, so a plain ``sqlite3`` connection is enough.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

_SCHEMA = """
CREATE TABLE IF NOT EXISTS guild_settings (
    guild_id   INTEGER PRIMARY KEY,
    channel_id INTEGER,
    auto_init  INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS posted (
    game_id  INTEGER PRIMARY KEY,
    version  INTEGER NOT NULL,
    group_id INTEGER NOT NULL,
    finished INTEGER NOT NULL DEFAULT 0,
    last_move TEXT
);
"""


@dataclass(frozen=True)
class GuildSettings:
    guild_id: int
    channel_id: int | None = None
    auto_init: bool = False


class Store:
    def __init__(self, path: str = ":memory:"):
        self._db = sqlite3.connect(path)
        self._db.executescript(_SCHEMA)
        self._db.commit()

    def close(self):
        self._db.close()

    # --- guild settings -----------------------------------------------------

    def guild(self, guild_id: int) -> GuildSettings:
        row = self._db.execute(
            "SELECT channel_id, auto_init FROM guild_settings WHERE guild_id = ?", (guild_id,)
        ).fetchone()
        if row is None:
            return GuildSettings(guild_id)
        return GuildSettings(guild_id, row[0], bool(row[1]))

    def _upsert(self, guild_id: int, column: str, value):
        self._db.execute(
            f"INSERT INTO guild_settings (guild_id, {column}) VALUES (?, ?) "
            f"ON CONFLICT (guild_id) DO UPDATE SET {column} = excluded.{column}",
            (guild_id, value),
        )
        self._db.commit()

    def set_channel(self, guild_id: int, channel_id: int | None):
        self._upsert(guild_id, "channel_id", channel_id)

    def set_auto_init(self, guild_id: int, enabled: bool):
        self._upsert(guild_id, "auto_init", int(enabled))

    # --- what has been posted ----------------------------------------------

    def posted_version(self, game_id: int) -> int | None:
        row = self._db.execute("SELECT version FROM posted WHERE game_id = ?", (game_id,)).fetchone()
        return None if row is None else row[0]

    def mark_posted(
        self, game_id: int, version: int, group_id: int, finished: bool = False, last_move: str | None = None
    ):
        """Record that Discord has seen ``version`` of a game (and its last move). Never goes backwards."""
        self._db.execute(
            "INSERT INTO posted (game_id, version, group_id, finished, last_move) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT (game_id) DO UPDATE SET "
            "last_move = CASE WHEN excluded.version > version THEN excluded.last_move ELSE last_move END, "
            "version = max(version, excluded.version), finished = max(finished, excluded.finished)",
            (game_id, version, group_id, int(finished), last_move),
        )
        self._db.commit()

    def posted_last_move(self, game_id: int) -> str | None:
        row = self._db.execute("SELECT last_move FROM posted WHERE game_id = ?", (game_id,)).fetchone()
        return None if row is None else row[0]

    def tracked_games(self) -> dict[int, tuple[int, int]]:
        """Games in progress that Discord knows about: ``{game_id: (version, group_id)}``."""
        rows = self._db.execute("SELECT game_id, version, group_id FROM posted WHERE finished = 0")
        return {game_id: (version, group_id) for game_id, version, group_id in rows}

    def forget(self, game_id: int):
        self._db.execute("DELETE FROM posted WHERE game_id = ?", (game_id,))
        self._db.commit()
