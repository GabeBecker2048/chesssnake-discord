"""
The /chess command callbacks, driven with a fake Interaction against a real
chesssnake server. Discord itself is not involved: this checks that the
commands call the service correctly and answer the way they should.
"""

from __future__ import annotations

from types import SimpleNamespace

import discord
import pytest

from chesssnake_discord.bot import ChessCog
from chesssnake_discord.config import Config, LiveSection
from chesssnake_discord.live import LiveRelay
from chesssnake_discord.store import Store

from .conftest import GUILD


class FakeMember(SimpleNamespace):
    bot = False

    @property
    def mention(self):
        return f"<@{self.id}>"


class FakeGuild:
    def __init__(self, *members):
        self.id = GUILD
        self.members = list(members)
        self.chunked = True

    def get_member(self, member_id):
        return next((m for m in self.members if m.id == member_id), None)


class Response:
    def __init__(self, sent):
        self.sent = sent
        self.done = False

    def is_done(self):
        return self.done

    async def defer(self, **kwargs):
        self.done = True

    async def send_message(self, content=None, **kwargs):
        self.done = True
        self.sent.append((content, kwargs))

    async def send_modal(self, modal):
        self.done = True
        self.sent.append(("<modal>", {"modal": modal}))


class Followup:
    def __init__(self, sent):
        self.sent = sent

    async def send(self, content=None, **kwargs):
        self.sent.append((content, kwargs))


class FakeInteraction:
    type = discord.InteractionType.application_command

    def __init__(self, user, guild):
        self.user, self.guild = user, guild
        self.sent = []
        self.response = Response(self.sent)
        self.followup = Followup(self.sent)

    async def delete_original_response(self):
        pass

    @property
    def last(self):
        return self.sent[-1][0]


def manager(id_, name):
    return FakeMember(id=id_, name=name, display_name=name, guild_permissions=SimpleNamespace(manage_guild=True))


def player(id_, name):
    return FakeMember(id=id_, name=name, display_name=name, guild_permissions=SimpleNamespace(manage_guild=False))


@pytest.fixture
def setup(service):
    store = Store()
    relay = LiveRelay(service, store, LiveSection(mode="off"), None, lambda: {GUILD})
    bot = SimpleNamespace(service=service, store=store, relay=relay, config=Config(), is_owner=_never)
    cog = ChessCog(bot)
    alice, bob, mod = player(1, "alice"), player(2, "bob"), manager(3, "mod")
    guild = FakeGuild(alice, bob, mod)
    return cog, guild, alice, bob, mod


async def _never(_user):
    return False


async def run(cog, command, user, guild, **kwargs):
    """Call a command as Discord would, errors going through the cog's handler."""
    interaction = FakeInteraction(user, guild)
    cmd = (
        cog.__cog_app_commands_group__.get_command(command)
        if " " not in command
        else (cog.__cog_app_commands_group__.get_command("admin").get_command(command.split()[1]))
    )
    try:
        await cmd.callback(cog, interaction, **kwargs)
    except Exception as exc:
        await cog.report(interaction, exc)
    return interaction


async def test_full_game(setup):
    cog, guild, alice, bob, mod = setup
    i = await run(cog, "challenge", alice, guild, opponent=bob)
    assert "/chess init" in i.last  # alice has no account yet

    i = await run(cog, "init", mod, guild, everyone=True)
    assert "Made **3** account(s)" in i.last
    i = await run(cog, "init", alice, guild)
    assert "already have" in i.last

    i = await run(cog, "challenge", alice, guild, opponent=bob, color="white")
    assert "challenges you" in i.last and "view" in i.sent[-1][1]
    i = await run(cog, "challenges", bob, guild)
    assert "from <@1>" in i.last

    i = await run(cog, "accept", bob, guild)
    assert "The game is on!" in i.last and "alice (white) to move" in i.last
    assert isinstance(i.sent[-1][1]["file"], discord.File)

    i = await run(cog, "move", bob, guild, move="e5")
    assert i.last == "It's not your turn."
    assert i.sent[-1][1]["ephemeral"]
    i = await run(cog, "move", alice, guild, move="e4")
    assert "**alice** played **e4**." in i.last and "<@2>, your move." in i.last
    i = await run(cog, "move", bob, guild, move="e4")
    assert i.last.startswith("That move doesn't work")

    i = await run(cog, "board", alice, guild, perspective="mine")
    assert "Game " in i.last
    i = await run(cog, "games", bob, guild)
    assert "bob (black) to move" in i.last
    i = await run(cog, "moves", bob, guild)
    assert "`e5`" in i.last
    i = await run(cog, "export", bob, guild, format="history")
    assert "1. e4" in i.last
    i = await run(cog, "export", bob, guild, format="pgn")
    assert i.sent[-1][1]["file"].filename.endswith(".pgn")

    i = await run(cog, "draw", bob, guild, action="offer")
    assert "offers a draw" in i.last and "bob has offered a draw" in i.last
    i = await run(cog, "draw", alice, guild, action="accept")
    assert "Game over: a draw by agreement." in i.last
    i = await run(cog, "record", alice, guild, opponent=bob)
    assert "1 draw(s)" in i.last


async def test_move_autocomplete(setup):
    cog, guild, alice, bob, mod = setup
    await run(cog, "init", mod, guild, everyone=True)
    await run(cog, "challenge", alice, guild, opponent=bob, color="white")
    await run(cog, "accept", bob, guild)
    interaction = FakeInteraction(alice, guild)
    interaction.namespace = SimpleNamespace()
    choices = await cog._move_autocomplete(interaction, "N")
    assert {c.value for c in choices} == {"Na3", "Nc3", "Nf3", "Nh3"}
    interaction = FakeInteraction(bob, guild)
    interaction.namespace = SimpleNamespace()
    assert await cog._move_autocomplete(interaction, "") == []  # not bob's turn


async def test_manager_and_owner_checks(setup):
    cog, guild, alice, bob, mod = setup
    i = await run(cog, "init", alice, guild, everyone=True)
    assert "Manage Server" in i.last
    i = await run(cog, "channel", alice, guild, channel=None)
    assert "Manage Server" in i.last
    i = await run(cog, "admin disable", mod, guild, member=alice)
    assert "bot's owners" in i.last


async def test_admin_draw_and_delete(setup):
    cog, guild, alice, bob, mod = setup
    await run(cog, "init", mod, guild, everyone=True)
    await run(cog, "admin create-game", mod, guild, white=alice, black=bob)
    game = (await cog.service.games(GUILD, await cog.service.require(alice)))[0]
    i = await run(cog, "admin draw", mod, guild, game_id=game.id)
    assert "a draw by agreement" in i.last
    i = await run(cog, "admin delete-game", mod, guild, game_id=game.id)
    assert "Deleted game" in i.last


async def test_channel_setting(setup):
    cog, guild, alice, bob, mod = setup
    channel = SimpleNamespace(id=77, mention="#chess")
    i = await run(cog, "channel", mod, guild, channel=channel)
    assert "#chess" in i.last and "turned off" in i.last  # relay mode is off here
    assert cog.store.guild(GUILD).channel_id == 77


async def test_link_by_code(setup, server):
    cog, guild, alice, bob, mod = setup
    web = server.user("alice_web", password="alice-password")
    code = (await server.client(web, asynchronous=True).link_code())["code"]
    i = await run(cog, "link", alice, guild, code=code)
    assert "Linked to `alice_web`" in i.last and i.sent[-1][1]["ephemeral"]
    i = await run(cog, "link", alice, guild, code=code)
    assert "isn't valid" in i.last and i.sent[-1][1]["ephemeral"]


async def test_a_chess_moderator_uses_the_game_commands(setup):
    cog, guild, alice, bob, mod = setup
    await run(cog, "init", mod, guild, everyone=True)
    i = await run(cog, "admin create-game", alice, guild, white=alice, black=bob)
    assert "chess moderators" in i.last  # not a moderator yet
    i = await run(cog, "admin moderator", alice, guild, member=bob, action="add")
    assert "Manage Server" in i.last  # only managers make moderators
    i = await run(cog, "admin moderator", mod, guild, member=alice, action="add")
    assert "is now a chess moderator" in i.last
    i = await run(cog, "admin create-game", alice, guild, white=alice, black=bob)
    assert "started by alice" in i.last
