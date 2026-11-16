"""
The /chess command callbacks, driven with a fake Interaction against a real
chesssnake server. Discord itself is not involved: this checks that the
commands call the service correctly and answer the way they should.
"""

from __future__ import annotations

from types import SimpleNamespace

import discord
import pytest

from chesssnake_discord.cog import make_cog
from chesssnake_discord.config import Config, DiscordSection, LiveSection
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


def fake_bot(members_intent=True):
    return SimpleNamespace(is_owner=_never, intents=SimpleNamespace(members=members_intent), guilds=[])


def build(service, *, members_intent=True, **discord_settings):
    """The cog on a stand-in bot, its relay off, with some [discord] settings changed."""
    config = Config(live=LiveSection(mode="off"), discord=DiscordSection(**discord_settings))
    cog = make_cog(fake_bot(members_intent), config, service=service, store=Store())
    alice, bob, mod = player(1, "alice"), player(2, "bob"), manager(3, "mod")
    return cog, FakeGuild(alice, bob, mod), alice, bob, mod


@pytest.fixture
def setup(service):
    return build(service)


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


# --- the settings for bots that are not yours alone -------------------------------------


async def test_a_renamed_command_group(service):
    cog, guild, alice, bob, mod = build(service, command_name="xiangqi")
    assert cog.__cog_app_commands_group__.name == "xiangqi"
    i = await run(cog, "challenge", alice, guild, opponent=bob)
    assert "`/xiangqi init`" in i.last  # messages name the command as it is here


async def test_the_notice_comes_with_init(service):
    cog, guild, alice, bob, mod = build(service, notice="Games on this demo are not kept.")
    i = await run(cog, "init", alice, guild)
    assert i.last.endswith("Games on this demo are not kept.")
    i = await run(cog, "init", mod, guild, everyone=True)
    assert i.last.endswith("Games on this demo are not kept.")


async def test_bulk_accounts_can_be_turned_off(service):
    cog, guild, alice, bob, mod = build(service, bulk_accounts=False)
    i = await run(cog, "init", mod, guild, everyone=True)
    assert "doesn't make accounts in bulk" in i.last
    i = await run(cog, "auto-init", mod, guild, setting="enable")
    assert "doesn't make accounts in bulk" in i.last
    i = await run(cog, "auto-init", mod, guild, setting="disable")  # turning it off is always fine
    assert "no longer" in i.last
    i = await run(cog, "init", alice, guild)  # one's own account still works
    assert "Your chess account is" in i.last


async def test_without_the_members_intent(service):
    cog, guild, alice, bob, mod = build(service, members_intent=False)
    i = await run(cog, "init", mod, guild, everyone=True)
    assert "Server Members intent is off" in i.last
    i = await run(cog, "init", alice, guild)
    assert "Your chess account is" in i.last


class FakeChannel:
    def __init__(self):
        self.sent = []

    def permissions_for(self, _member):
        return SimpleNamespace(send_messages=True)

    async def send(self, text):
        self.sent.append(text)


class JoinedGuild:
    def __init__(self, id_):
        self.id = id_
        self.me = object()
        self.system_channel = FakeChannel()
        self.text_channels = []
        self.left = False

    async def leave(self):
        self.left = True


async def test_the_server_cap_and_the_join_notice(service):
    cog, *_ = build(service, max_guilds=2, notice="Games here are not kept.")
    first, second, third = JoinedGuild(1), JoinedGuild(2), JoinedGuild(3)
    cog.bot.guilds = [first, second]
    await cog.on_guild_join(second)
    assert not second.left and second.system_channel.sent == ["Games here are not kept."]
    cog.bot.guilds = [first, second, third]
    await cog.on_guild_join(third)
    assert third.left and "limited to 2 servers" in third.system_channel.sent[0]


# --- loaded into a bot that is not ours ---------------------------------------------------


async def test_load_extension_into_a_plain_bot(server, monkeypatch):
    """The way someone adds chess to their own bot: no ChessBot, no login, no sync."""
    import sys

    from discord.ext import commands

    # unload_extension drops the package's modules from sys.modules (that is how a
    # reload works); put them back, so later tests and these share one set of classes.
    modules = {name: module for name, module in sys.modules.items() if name.startswith("chesssnake_discord")}
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)

    monkeypatch.setenv("CHESSSNAKE__CLIENT__API_URL", server.url)
    monkeypatch.setenv("CHESSSNAKE__CLIENT__TOKEN", server.token())
    monkeypatch.setenv("CHESSSNAKE_DISCORD__DISCORD__COMMAND_NAME", "chessgame")
    monkeypatch.setenv("CHESSSNAKE_DISCORD__STORAGE__PATH", ":memory:")
    monkeypatch.delenv("DISCORD_TOKEN", raising=False)
    monkeypatch.setenv("CHESSSNAKE_DISCORD_CONFIG", "/nonexistent/config.toml")

    bot = commands.Bot(command_prefix="!", intents=discord.Intents.default())
    await bot.load_extension("chesssnake_discord")
    cog = bot.get_cog("ChessCog")
    assert cog is not None and bot.tree.get_command("chessgame") is not None
    assert cog.service.bot_user_id == server.admin.id
    await bot.unload_extension("chesssnake_discord")
    assert bot.get_cog("ChessCog") is None and bot.tree.get_command("chessgame") is None
    assert cog.service.client._http.is_closed


async def test_loading_refuses_a_token_that_is_not_an_admins(server, service):
    from discord.ext import commands

    from chesssnake_discord import load_config

    player_token = server.token(server.user("someone_" + __import__("secrets").token_hex(3)))
    config = load_config(
        env={"CHESSSNAKE__CLIENT__API_URL": server.url, "CHESSSNAKE__CLIENT__TOKEN": player_token},
        path="/nonexistent/config.toml",
        require_discord_token=False,
    )
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.default())
    with pytest.raises(RuntimeError, match="not an administrator"):
        await bot.add_cog(make_cog(bot, config, store=Store()))
