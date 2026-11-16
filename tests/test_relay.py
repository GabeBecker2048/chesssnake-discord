"""LiveRelay, polling and websocket, against a real chesssnake server."""

from __future__ import annotations

import asyncio

import pytest

from chesssnake_discord.config import LiveSection
from chesssnake_discord.live import LiveRelay
from chesssnake_discord.store import Store

from .conftest import GUILD, member


def make_relay(service, mode):
    store = Store()
    store.set_channel(GUILD, 1)
    posts = []

    async def poster(state, move, note):
        posts.append((state.id, state.version, move["san"] if move else None, note))

    relay = LiveRelay(service, store, LiveSection(mode=mode, poll_interval_seconds=1), poster, lambda: {GUILD})
    relay.posts = posts
    return relay


@pytest.fixture
def relay(service):
    return make_relay(service, "poll")


async def _players(service, alice, bob):
    a, _ = await service.create_for(alice)
    b, _ = await service.create_for(bob)
    return a, b


async def _game(service, relay, a, b, group=GUILD):
    challenge = await service.challenge(group, a, b, "white")
    return await relay.own(None, service.accept(challenge, b))


def _outside(server, user):
    """A client for ``user`` themselves: a move made in the web UI, not by the bot."""
    return server.client(user, asynchronous=True)


# --- polling ------------------------------------------------------------------------------


async def test_own_actions_are_not_relayed(service, relay, alice, bob):
    a, b = await _players(service, alice, bob)
    await relay.poll()  # seeding
    game = await _game(service, relay, a, b)
    await relay.own(game.id, service.move(game, a, "e4"))
    await relay.poll()
    assert relay.posts == []


async def test_outside_moves_are_relayed_once(service, server, relay, alice, bob):
    a, b = await _players(service, alice, bob)
    game = await _game(service, relay, a, b)
    await relay.poll()
    result = await _outside(server, a).move(game, "d4")
    await relay.poll()
    await relay.poll()
    assert relay.posts == [(game.id, result.state.version, "d4", "played outside Discord")]


async def test_a_draw_offer_is_not_mistaken_for_a_move(service, server, relay, alice, bob):
    a, b = await _players(service, alice, bob)
    game = await _game(service, relay, a, b)
    await relay.own(game.id, service.move(game, a, "e4"))
    await relay.poll()
    state = await _outside(server, b).offer_draw(game)
    await _outside(server, a).decline_draw(state)  # back to no offer, still no new move
    await relay.poll()
    assert [(p[2], p[3]) for p in relay.posts] == [(None, "updated outside Discord")]


async def test_games_already_running_at_startup_are_not_news(service, relay, alice, bob):
    a, b = await _players(service, alice, bob)
    challenge = await service.challenge(GUILD, a, b, "white")
    await service.accept(challenge, b)
    await relay.poll()
    assert relay.posts == []


async def test_new_outside_game_is_announced(service, relay, alice, bob):
    a, b = await _players(service, alice, bob)
    await relay.poll()
    challenge = await service.challenge(GUILD, a, b, "white")
    game = await service.accept(challenge, b)
    await relay.poll()
    assert [(p[0], p[3]) for p in relay.posts] == [(game.id, "a game started outside Discord")]


async def test_outside_ending_is_relayed(service, server, relay, alice, bob):
    a, b = await _players(service, alice, bob)
    game = await _game(service, relay, a, b)
    await relay.poll()
    await _outside(server, b).resign(game)  # the game leaves the live list
    await relay.poll()
    assert len(relay.posts) == 1 and relay.posts[0][0] == game.id
    assert relay.store.tracked_games() == {}


async def test_other_guilds_are_ignored(service, server, relay, alice, bob):
    a, b = await _players(service, alice, bob)
    await relay.poll()
    game = await _game(service, relay, a, b, group=GUILD + 1)
    await _outside(server, a).move(game, "e4")
    await relay.poll()
    assert relay.posts == []


async def test_held_changes_during_own_action(service, relay, alice, bob):
    a, b = await _players(service, alice, bob)
    await relay.poll()
    game = await _game(service, relay, a, b)

    async def move_and_poll():
        result = await service.move(game, a, "e4")
        await relay.poll()  # a poll that lands before the action has returned
        return result

    await relay.own(game.id, move_and_poll())
    assert relay.posts == []


# --- the websocket feed ---------------------------------------------------------------------


async def _until(condition, seconds=5.0):
    deadline = asyncio.get_running_loop().time() + seconds
    while not condition():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("timed out")
        await asyncio.sleep(0.02)


async def test_the_feed_relays_outside_changes_and_skips_the_bots_own(service, server, alice, bob):
    relay = make_relay(service, "auto")
    a, b = await _players(service, alice, bob)
    await relay.start()
    try:
        assert relay.mode == "websocket"
        game = await _game(service, relay, a, b)  # the bot's own accept: not news
        await relay.own(game.id, service.move(game, a, "e4"))  # the bot's own move
        result = await _outside(server, b).move(game, "c5")  # bob, in the browser
        await _until(lambda: relay.posts)
        await asyncio.sleep(0.2)  # anything else would have arrived by now
        assert relay.posts == [(game.id, result.state.version, "c5", "played outside Discord")]

        # A game started outside Discord is announced, without polling for it.
        carol, _ = await service.create_for(member(333, "carol"))
        challenge = await _outside(server, a).challenge(carol, group_id=GUILD)
        new = await _outside(server, carol).accept_challenge(challenge)
        await _until(lambda: len(relay.posts) == 2)
        assert relay.posts[1][0] == new.id and relay.posts[1][3] == "a game started outside Discord"
    finally:
        await relay.stop()
