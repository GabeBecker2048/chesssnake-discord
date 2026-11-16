"""
chesssnake-discord's tests run against a real chesssnake server, from chesssnake.testing:
one per session, served over HTTP (``live=True``) so the websocket relay can be
tested too, and emptied before each test. No Discord.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import pytest_asyncio
from chesssnake.testing import TestServer

GUILD = 987654321098765432  # a Discord snowflake: group ids are 64-bit


@pytest.fixture(scope="session")
def server():
    with TestServer(live=True) as running:
        yield running


@pytest_asyncio.fixture
async def service(server):
    from chesssnake_discord.chess import ChessService

    server.reset()
    service = ChessService(server.client(asynchronous=True))
    await service.me()
    yield service
    await service.client.aclose()


def member(discord_id: int, name: str):
    return SimpleNamespace(id=discord_id, name=name, display_name=name, bot=False)


@pytest.fixture
def alice():
    return member(111111111111111111, "alice")


@pytest.fixture
def bob():
    return member(222222222222222222, "bob")
