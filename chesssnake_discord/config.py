"""
The bot's settings: defaults, then an optional TOML file, then environment
variables (later wins). Secrets come from the environment only.

Every setting in the file can also be set as ``CHESSSNAKE_DISCORD__{SECTION}__{KEY}``,
e.g. ``CHESSSNAKE_DISCORD__LIVE__MODE=poll``, so a Docker deployment needs no file
at all. A list (``owner_ids``) is comma-separated there. The chesssnake variables
are the ones :func:`chesssnake.connect` already reads, so the client needs no
arguments.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

LIVE_MODES = ("auto", "websocket", "poll", "off")

DISCORD_TOKEN_ENV = "DISCORD_TOKEN"
API_URL_ENV = "CHESSSNAKE__CLIENT__API_URL"
API_TOKEN_ENV = "CHESSSNAKE__CLIENT__TOKEN"
#: Where the TOML file is (default ./config.toml). One underscore, so it cannot
#: be mistaken for a setting.
CONFIG_PATH_ENV = "CHESSSNAKE_DISCORD_CONFIG"
#: Prefix of the per-setting variables: CHESSSNAKE_DISCORD__{SECTION}__{KEY}.
SETTING_ENV_PREFIX = "CHESSSNAKE_DISCORD__"


def env_name(section: str, key: str) -> str:
    """The environment variable for one setting."""
    return f"{SETTING_ENV_PREFIX}{section.upper()}__{key.upper()}"


class ConfigError(Exception):
    """The config file or environment is unusable; the message says why."""


@dataclass(frozen=True)
class ChesssnakeSection:
    #: Shown by /chess profile and /chess password. Empty: the api url.
    web_url: str = ""
    #: The identity provider name under which Discord ids are linked.
    provider: str = "discord"


@dataclass(frozen=True)
class LiveSection:
    #: auto (websocket, else polling), websocket, poll, or off.
    mode: str = "auto"
    poll_interval_seconds: float = 15.0


@dataclass(frozen=True)
class DiscordSection:
    #: Nonzero: sync the commands to this one guild (instant, for development).
    dev_guild_id: int = 0
    #: Discord user ids allowed to use the account commands of /chess admin, besides
    #: the application's owner(s).
    owner_ids: tuple = ()


@dataclass(frozen=True)
class StorageSection:
    path: str = "chesssnake-discord.db"


@dataclass(frozen=True)
class Config:
    chesssnake: ChesssnakeSection = field(default_factory=ChesssnakeSection)
    live: LiveSection = field(default_factory=LiveSection)
    discord: DiscordSection = field(default_factory=DiscordSection)
    storage: StorageSection = field(default_factory=StorageSection)
    # From the environment:
    discord_token: str = ""
    api_url: str = ""
    api_token: str = ""

    @property
    def public_url(self) -> str:
        return (self.chesssnake.web_url or self.api_url).rstrip("/")


_SECTIONS = {
    "chesssnake": ChesssnakeSection,
    "live": LiveSection,
    "discord": DiscordSection,
    "storage": StorageSection,
}


def _section(name, cls, data):
    if not isinstance(data, dict):
        raise ConfigError(f"[{name}] must be a table")
    known = {f.name: f for f in fields(cls)}
    unknown = set(data) - set(known)
    if unknown:
        raise ConfigError(f"Unknown key(s) in [{name}]: {', '.join(sorted(unknown))}")
    values = {}
    for key, value in data.items():
        default = known[key].default
        expected = type(default)
        if expected is tuple:
            if not isinstance(value, list) or not all(isinstance(v, int) and not isinstance(v, bool) for v in value):
                raise ConfigError(f"[{name}] {key} must be a list of integers, not {value!r}")
            values[key] = tuple(value)
            continue
        if expected is float and isinstance(value, int) and not isinstance(value, bool):
            value = float(value)
        if not isinstance(value, expected) or isinstance(value, bool) != isinstance(default, bool):
            raise ConfigError(f"[{name}] {key} must be a {expected.__name__}, not {value!r}")
        values[key] = value
    return cls(**values)


def _from_env(text: str, default):
    """An environment string as the type of a setting's default."""
    expected = type(default)
    try:
        if expected is tuple:
            return [int(part) for part in text.split(",") if part.strip()]
        if expected is int:
            return int(text)
        if expected is float:
            return float(text)
    except ValueError:
        kind = "comma-separated integers" if expected is tuple else f"a {expected.__name__}"
        raise ConfigError(f"must be {kind}, not {text!r}") from None
    return text


def _env_settings(env) -> dict:
    """The ``CHESSSNAKE_DISCORD__SECTION__KEY`` variables, as TOML-shaped data."""
    data: dict = {}
    for name, text in env.items():
        if not name.startswith(SETTING_ENV_PREFIX):
            continue
        section, _, key = name[len(SETTING_ENV_PREFIX) :].lower().partition("__")
        cls = _SECTIONS.get(section)
        known = {f.name: f.default for f in fields(cls)} if cls else {}
        if key not in known:
            raise ConfigError(f"Unknown setting {name}: there is no [{section}] {key}")
        try:
            data.setdefault(section, {})[key] = _from_env(text, known[key])
        except ConfigError as exc:
            raise ConfigError(f"{name} {exc}") from None
    return data


def load(path: str | os.PathLike | None = None, env=None) -> Config:
    """
    Read the config file (if there is one), then the environment.

    :param path: The TOML file; default ``$CHESSSNAKE_DISCORD_CONFIG`` or ``./config.toml``.
        A missing file means every default.
    :raises ConfigError: On a malformed file, an unknown key or variable, a bad
        value, or a missing token.
    """
    env = os.environ if env is None else env
    path = Path(path or env.get(CONFIG_PATH_ENV) or "config.toml")
    data = {}
    if path.is_file():
        try:
            data = tomllib.loads(path.read_text())
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"{path}: {exc}") from None
    elif path.exists():
        raise ConfigError(f"{path} is not a file")
    unknown = set(data) - set(_SECTIONS)
    if unknown:
        raise ConfigError(f"Unknown section(s) in {path}: {', '.join(sorted(unknown))}")
    for section, values in _env_settings(env).items():
        data.setdefault(section, {}).update(values)
    sections = {name: _section(name, cls, data.get(name, {})) for name, cls in _SECTIONS.items()}

    live = sections["live"]
    if live.mode not in LIVE_MODES:
        raise ConfigError(f"[live] mode must be one of {', '.join(LIVE_MODES)}, not {live.mode!r}")
    if live.poll_interval_seconds < 1:
        raise ConfigError("[live] poll_interval_seconds must be at least 1")

    config = Config(
        **sections,
        discord_token=env.get(DISCORD_TOKEN_ENV, ""),
        api_url=env.get(API_URL_ENV, "").rstrip("/"),
        api_token=env.get(API_TOKEN_ENV, ""),
    )
    missing = [
        name
        for name, value in (
            (DISCORD_TOKEN_ENV, config.discord_token),
            (API_URL_ENV, config.api_url),
            (API_TOKEN_ENV, config.api_token),
        )
        if not value
    ]
    if missing:
        raise ConfigError(f"Missing environment variable(s) (set them in .env): {', '.join(missing)}")
    return config
