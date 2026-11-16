# chesssnake-discord: the bot alone. It needs a chesssnake api-endpoint to talk
# to; compose.yaml runs one beside it, from the published chesssnake image.

FROM python:3.12-slim

LABEL org.opencontainers.image.title="chesssnake-discord" \
      org.opencontainers.image.description="A Discord chess bot that plays through a chesssnake server" \
      org.opencontainers.image.source="https://github.com/GabeBecker2048/chesssnake-discord" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_ROOT_USER_ACTION=ignore

WORKDIR /src
COPY pyproject.toml README.md ./
COPY chesssnake_discord ./chesssnake_discord
RUN pip install .

# Not root. /data holds the bot's own small database (chesssnake-discord.db,
# found relative to the working directory) and is the volume in compose.yaml.
RUN useradd --create-home --uid 10002 bot && mkdir -p /data && chown bot /data
USER bot
WORKDIR /data

CMD ["chesssnake-discord"]
