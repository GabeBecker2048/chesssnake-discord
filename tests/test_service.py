"""ChessService against a real chesssnake server."""

from __future__ import annotations

import pytest
from chesssnake import Color
from chesssnake.db import errors as db_errors
from chesssnake.engine.errors import ChessError

from chesssnake_discord.errors import BotError, describe

from .conftest import GUILD, member


async def test_init_creates_once(service, alice):
    assert await service.user_for(alice) is None
    user, made = await service.create_for(alice)
    assert made and user.username == "alice" and not user.has_password
    again, made = await service.create_for(alice)
    assert not made and again.id == user.id


async def test_init_takes_a_suffixed_name_when_taken(service, server, alice):
    server.user("alice")  # someone registered "alice" in the web UI
    user, made = await service.create_for(alice)
    assert made and user.username == "alice_1"


async def test_require_explains(service, alice):
    with pytest.raises(BotError, match="/chess init"):
        await service.require(alice)
    with pytest.raises(BotError, match="alice doesn't have"):
        await service.require(alice, you=False)


async def _link_code(server, user):
    return await server.client(user, asynchronous=True).link_code()


async def test_link_moves_identity_off_auto_account(service, server, alice):
    auto, _ = await service.create_for(alice)
    web = server.user("alice_web", password="alice-password")
    with pytest.raises(BotError, match="isn't valid"):
        await service.link(alice, "AAAA-BBBB")
    made = await _link_code(server, web)
    linked, previous = await service.link(alice, made["code"])
    assert linked.id == web.id and previous.id == auto.id
    assert (await service.user_for(alice)).id == web.id
    with pytest.raises(BotError, match="isn't valid"):
        await service.link(alice, made["code"])  # used


async def test_link_refuses_an_account_linked_to_another_discord_user(service, server, alice, bob):
    shared = server.user("shared")
    await service.link(alice, (await _link_code(server, shared))["code"])
    with pytest.raises(BotError, match="another Discord account"):
        await service.link(bob, (await _link_code(server, shared))["code"])


async def test_unlink(service, alice):
    user, _ = await service.create_for(alice)
    assert (await service.unlink(alice)).id == user.id
    assert await service.user_for(alice) is None
    with pytest.raises(BotError):
        await service.unlink(alice)


async def test_first_password(service, alice):
    await service.create_for(alice)
    with pytest.raises(BotError, match="at least 8"):
        await service.set_first_password(alice, "short")
    user = await service.set_first_password(alice, "a-long-password")
    assert user.has_password
    service.forget_users()
    with pytest.raises(BotError, match="already has a password"):
        await service.set_first_password(alice, "another-long-one")


async def test_admin_link_by_username(service, server, alice):
    dave = server.user("dave")
    linked, previous = await service.admin_link(alice, "dave")
    assert linked.id == dave.id and previous is None
    with pytest.raises(BotError, match="no chess account called"):
        await service.admin_link(alice, "nobody")


async def test_moderators(service, alice):
    await service.create_for(alice)
    user = await service.set_moderator(GUILD, alice, True)
    assert user.admin_groups == [GUILD]
    assert (await service.user_for(alice)).admin_groups == [GUILD]
    assert (await service.set_moderator(GUILD, alice, False)).admin_groups == []


async def _game(service, alice, bob, color="white"):
    a, _ = await service.create_for(alice)
    b, _ = await service.create_for(bob)
    challenge = await service.challenge(GUILD, a, b, color)
    assert challenge.group_id == GUILD
    found = await service.find_challenge(GUILD, b, None, "incoming")
    assert found.id == challenge.id
    with pytest.raises(BotError, match="Only the player who was challenged"):
        await service.accept(challenge, a)
    state = await service.accept(challenge, b)
    return a, b, state


async def test_challenge_accept_move_resign(service, alice, bob):
    a, b, state = await _game(service, alice, bob)
    assert state.group_id == GUILD and state.white_id == a.id
    game = await service.resolve_game(GUILD, a)
    assert game.id == state.id
    assert await service.resolve_game(GUILD, b, a) == game

    with pytest.raises(db_errors.NotYourTurnError):
        await service.move(game, b, "e5")
    result = await service.move(game, a, "e4")
    assert result.san == "e4" and result.state.to_move is Color.BLACK
    assert result.state.last_move["san"] == "e4"

    with pytest.raises(db_errors.VersionConflictError):
        await service.move(game, b, "e5")  # stale version
    game = await service.resolve_game(GUILD, b)
    with pytest.raises(ChessError) as exc:
        await service.move(game, b, "e4")
    assert describe(exc.value).startswith("That move doesn't work")

    assert any(m["san"] == "e5" for m in await service.legal_moves(game.id))
    state = await service.resign(game, b)
    assert state.winner is Color.WHITE and state.termination == "resignation"
    assert (await service.record(GUILD, a, b)).wins == 1
    with pytest.raises(BotError, match="don't have a game"):
        await service.resolve_game(GUILD, a)


async def test_games_are_per_guild(service, alice, bob):
    a, b, _ = await _game(service, alice, bob)
    assert await service.games(GUILD + 1, a) == []
    with pytest.raises(BotError):
        await service.resolve_game(GUILD + 1, a)


async def test_draw(service, alice, bob):
    a, b, game = await _game(service, alice, bob)
    state = await service.draw(game, a, "offer")
    assert state.draw_offered_by is Color.WHITE
    state = await service.draw(state, b, "accept")
    assert state.is_over and state.winner is None and state.termination == "agreement"


async def test_decline_and_withdraw(service, alice, bob):
    a, _ = await service.create_for(alice)
    b, _ = await service.create_for(bob)
    c = await service.challenge(GUILD, a, b, "random")
    await service.decline(c, a)  # withdraw
    assert await service.challenges(GUILD, b) == []
    with pytest.raises(BotError, match="yourself"):
        await service.challenge(GUILD, a, a, "random")


async def test_discord_id_for(service, alice):
    user, _ = await service.create_for(alice)
    assert await service.discord_id_for(user.id) == alice.id


async def test_live_games_lists_ours(service, alice, bob):
    _, _, state = await _game(service, alice, bob)
    assert state.id in {g.id for g in await service.live_games()}


async def test_a_member_name_that_is_all_digits(service):
    user, made = await service.create_for(member(333, "1234"))
    assert made and user.username == "discord_333"


async def test_relinking_is_refused_while_the_old_account_has_games(service, server, alice, bob):
    a, b, game = await _game(service, alice, bob)
    web = server.user("alice_web", password="alice-password")
    with pytest.raises(BotError) as refused:
        await service.link(alice, (await _link_code(server, web))["code"], here=GUILD)
    message = str(refused.value)
    assert f"`{a.username}` has 1 game(s) in progress" in message
    assert f"game {game.id} against {b.username}, here" in message
    assert (await service.user_for(alice)).id == a.id, "nothing moved"
    with pytest.raises(BotError, match="in another server"):
        await service.link(alice, (await _link_code(server, web))["code"], here=GUILD + 1)
    with pytest.raises(BotError, match="alice's chess account"):
        await service.admin_link(alice, "alice_web", here=GUILD)

    await service.resign(game, b)  # finished games may stay behind
    linked, previous = await service.link(alice, (await _link_code(server, web))["code"])
    assert linked.id == web.id and previous.id == a.id
