"""
chesssnake-discord: chess for Discord, played through a chesssnake server.

As a cog in your own bot (settings from the environment or ``config.toml``):

.. code-block:: python

    await bot.load_extension("chesssnake_discord")

or explicitly, with :func:`make_cog` and :func:`load_config`. As a bot of its own:
the ``chesssnake-discord`` command, or the Docker image.
"""

from importlib.metadata import PackageNotFoundError, version

from .cog import ChessCog, make_cog, setup
from .config import Config, ConfigError
from .config import load as load_config

try:
    __version__ = version("chesssnake-discord")
except PackageNotFoundError:  # a copied-in folder, not an installed package
    __version__ = "0+unknown"

__all__ = ["ChessCog", "Config", "ConfigError", "__version__", "load_config", "make_cog", "setup"]
