from __future__ import annotations

import pytest

from chesssnake_discord.config import ConfigError, load
from chesssnake_discord.store import Store

ENV = {
    "DISCORD_TOKEN": "d",
    "CHESSSNAKE__CLIENT__API_URL": "http://chess.example/",
    "CHESSSNAKE__CLIENT__TOKEN": "css_x",
}


def test_defaults_without_a_file(tmp_path):
    config = load(tmp_path / "none.toml", env=ENV)
    assert config.live.mode == "auto" and config.api_url == "http://chess.example"
    assert config.public_url == "http://chess.example"


def test_example_file_is_valid():
    from pathlib import Path

    config = load(Path(__file__).parent.parent / "config.example.toml", env=ENV)
    assert config.live.mode == "auto"


def test_file_values(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text('[live]\nmode = "poll"\npoll_interval_seconds = 5\n[discord]\nowner_ids = [1, 2]\n')
    config = load(path, env=ENV)
    assert config.live.mode == "poll" and config.live.poll_interval_seconds == 5.0
    assert config.discord.owner_ids == (1, 2)


@pytest.mark.parametrize(
    "text, message",
    [
        ('[live]\nmode = "sometimes"\n', "mode must be one of"),
        ("[live]\nmodee = 1\n", "Unknown key"),
        ("[nope]\n", "Unknown section"),
        ('[discord]\ndev_guild_id = "1"\n', "must be a int"),
        ("[discord]\nowner_ids = [true]\n", "list of integers"),
    ],
)
def test_bad_files(tmp_path, text, message):
    path = tmp_path / "c.toml"
    path.write_text(text)
    with pytest.raises(ConfigError, match=message):
        load(path, env=ENV)


def test_missing_tokens(tmp_path):
    with pytest.raises(ConfigError, match="DISCORD_TOKEN"):
        load(tmp_path / "none.toml", env={})


def test_store():
    store = Store()
    assert store.guild(5).channel_id is None
    store.set_channel(5, 10)
    store.set_auto_init(5, True)
    assert store.guild(5).channel_id == 10 and store.guild(5).auto_init
    store.mark_posted(1, 4, 5)
    store.mark_posted(1, 3, 5)  # never backwards
    assert store.posted_version(1) == 4
    store.mark_posted(1, 6, 5, finished=True)
    assert store.tracked_games() == {}


async def test_the_startup_check(server):
    from chesssnake_discord.__main__ import check_chesssnake
    from chesssnake_discord.config import Config

    def config(token):
        return Config(discord_token="x", api_url=server.url, api_token=token)

    assert await check_chesssnake(config(server.token())) is None
    assert "rejected" in await check_chesssnake(config("css_wrong"))
    player = server.user("someone_" + __import__("secrets").token_hex(3))
    assert "not an administrator" in await check_chesssnake(config(server.token(player)))
    unreachable = Config(discord_token="x", api_url="http://127.0.0.1:9", api_token="css_x")
    assert "cannot reach" in await check_chesssnake(unreachable)


def test_environment_overrides_the_file(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text('[live]\nmode = "poll"\npoll_interval_seconds = 5\n')
    env = {
        **ENV,
        "CHESSSNAKE_DISCORD__LIVE__POLL_INTERVAL_SECONDS": "30",
        "CHESSSNAKE_DISCORD__DISCORD__OWNER_IDS": "1, 2,3",
        "CHESSSNAKE_DISCORD__CHESSSNAKE__WEB_URL": "https://chess.example",
        "CHESSSNAKE_DISCORD__STORAGE__PATH": "/data/bot.db",
    }
    config = load(path, env=env)
    assert config.live.mode == "poll" and config.live.poll_interval_seconds == 30.0
    assert config.discord.owner_ids == (1, 2, 3)
    assert config.public_url == "https://chess.example" and config.storage.path == "/data/bot.db"


@pytest.mark.parametrize(
    "name, value, message",
    [
        ("CHESSSNAKE_DISCORD__LIVE__MODEE", "poll", "Unknown setting"),
        ("CHESSSNAKE_DISCORD__NOPE__X", "1", "Unknown setting"),
        ("CHESSSNAKE_DISCORD__DISCORD__DEV_GUILD_ID", "abc", "must be a int"),
        ("CHESSSNAKE_DISCORD__DISCORD__OWNER_IDS", "1,x", "comma-separated integers"),
        ("CHESSSNAKE_DISCORD__LIVE__MODE", "sometimes", "mode must be one of"),
    ],
)
def test_bad_environment(tmp_path, name, value, message):
    with pytest.raises(ConfigError, match=message):
        load(tmp_path / "none.toml", env={**ENV, name: value})


def test_env_names_match_the_documented_ones():
    from pathlib import Path

    from chesssnake_discord.config import env_name

    documented = (Path(__file__).parent.parent / ".env.example").read_text()
    for section, key in (("live", "mode"), ("chesssnake", "web_url"), ("discord", "owner_ids")):
        assert env_name(section, key) in documented
