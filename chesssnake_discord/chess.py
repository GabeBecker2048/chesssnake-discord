"""
ChessService: everything the bot asks of chesssnake.

A thin layer over chesssnake's :class:`~chesssnake.AsyncApiClient` (an admin's
token): it maps Discord members to chesssnake users through linked identities,
and turns "which game do you mean" into a game. Every chess rule, and every
"not your turn", comes from the server.

Players are anything with ``.id`` (the Discord id) and ``.name`` (the Discord
@username): a ``discord.Member`` in the bot, a ``SimpleNamespace`` in the tests.
Nothing here imports discord, which is what lets the tests drive it against a
real chesssnake server.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol

from chesssnake import AsyncApiClient, Challenge, GameState, MoveResult, User
from chesssnake.db import errors as db_errors

from .errors import BotError

_USER_CACHE_SECONDS = 60.0


class Person(Protocol):
    id: int
    name: str


@dataclass(frozen=True)
class Record:
    wins: int
    losses: int
    draws: int


class ChessService:
    def __init__(self, client: AsyncApiClient, *, provider: str = "discord", command: str = "chess"):
        self.client = client
        self.provider = provider
        #: The bot's slash command group, for messages that name a command.
        self.command = command
        self._users: dict[int, tuple[float, User | None]] = {}
        self._discord_ids: dict[int, int | None] = {}
        #: The bot's own chesssnake user id, once :meth:`me` has run.
        self.bot_user_id: int | None = None

    async def call(self, method: str, *args, **kwargs):
        """``await client.<method>(*args, **kwargs)``, for the odd call the cogs make directly."""
        return await getattr(self.client, method)(*args, **kwargs)

    def forget_users(self):
        self._users.clear()
        self._discord_ids.clear()

    # --- who is who ----------------------------------------------------------

    async def me(self) -> User:
        """The bot's own chesssnake account."""
        user = await self.client.me()
        self.bot_user_id = user.id
        return user

    async def user_for(self, person: Person) -> User | None:
        """The chesssnake account linked to a Discord user, or ``None``."""
        cached = self._users.get(person.id)
        if cached is not None and cached[0] > time.monotonic():
            return cached[1]
        try:
            user = await self.client.get_user_by_identity(self.provider, str(person.id))
        except db_errors.UserNotFoundError:
            user = None
        self._users[person.id] = (time.monotonic() + _USER_CACHE_SECONDS, user)
        return user

    async def require(self, person: Person, *, you: bool = True) -> User:
        """The linked account, or a :class:`BotError` saying how to get one."""
        user = await self.user_for(person)
        if user is None:
            if you:
                raise BotError(
                    f"You don't have a chess account yet. Run `/{self.command} init` to make one, "
                    f"or `/{self.command} link` to use the one you already have."
                )
            raise BotError(
                f"{person.name} doesn't have a chess account yet. They can make one with `/{self.command} init`."
            )
        if user.disabled_at:
            raise BotError(f"The chess account `{user.username}` is disabled.")
        return user

    async def discord_id_for(self, user_id: int) -> int | None:
        """The Discord id linked to a chesssnake user, if any."""
        if user_id not in self._discord_ids:
            try:
                user = await self.client.get_user(user_id)
            except db_errors.UserNotFoundError:
                self._discord_ids[user_id] = None
            else:
                ids = [i["external_id"] for i in user.identities if i.get("provider") == self.provider]
                self._discord_ids[user_id] = int(ids[0]) if ids and ids[0].isdigit() else None
        return self._discord_ids[user_id]

    def player(self, user: User):
        """A chesssnake ``PlayerClient`` acting as ``user``."""
        return self.client.as_user(user)

    # --- accounts --------------------------------------------------------------

    async def create_for(self, person: Person) -> tuple[User, bool]:
        """
        A passwordless account for a Discord user, linked to them and named after
        their @username (``name_1``, ``name_2``, ... if that is taken).

        :return: ``(user, created)``; ``created`` is False if one was already linked.
        """
        existing = await self.user_for(person)
        if existing is not None:
            return existing, False
        user = await self.client.get_or_create_user_by_identity(
            self.provider, str(person.id), person.name, on_conflict="suffix"
        )
        self._users.pop(person.id, None)
        self._discord_ids.pop(user.id, None)
        return user, True

    async def _refuse_moving_games(self, person: Person, here: int | None, *, you: bool = True) -> None:
        """
        Refuse to move a Discord account's link while its current chess account has
        games in progress: those games stay with that account, and nobody could
        play them any more. Finished games are fine to leave behind.

        Checked before the move rather than by the server, so a game that starts in
        the moment between the check and the link is not caught; that is accepted.

        :param here: This guild's id, to say which games are in this server.
        """
        current = await self.user_for(person)
        if current is None:
            return
        games = await self.client.current_games(current.id)
        if not games:
            return
        lines = []
        for game in games[:10]:
            opponent = game.player_name(game.color_of(current.id).opponent)
            where = "here" if game.group_id == here else "in another server"
            lines.append(f"- game {game.id} against {opponent}, {where}")
        if len(games) > 10:
            lines.append(f"- and {len(games) - 10} more")
        whose = "Your" if you else f"{person.name}'s"
        raise BotError(
            f"{whose} chess account `{current.username}` has {len(games)} game(s) in progress, which would be "
            "left behind with it:\n" + "\n".join(lines) + "\nFinish or resign them first, then link again. "
            f"(If the account you are linking is `{current.username}` itself, it is already linked.)"
        )

    async def link(self, person: Person, code: str, here: int | None = None) -> tuple[User, User | None]:
        """
        Link a Discord user to the chesssnake account that made ``code`` (a link
        code from the web UI's Account page, or ``chesssnake link-code``).

        Refused while their current account has games in progress (see
        :meth:`_refuse_moving_games`).

        :return: ``(linked account, the account they were linked to before or None)``.
        """
        await self._refuse_moving_games(person, here)
        try:
            user, previous_id = await self.client.redeem_link_code(self.provider, str(person.id), code.strip())
        except db_errors.LinkCodeError:
            raise BotError(
                "That link code isn't valid: it may be mistyped, used or expired. "
                "Make a new one on your Account page and try again."
            ) from None
        except db_errors.UserExistsError:
            raise BotError(
                "That chess account is already linked to another Discord account. Unlink it on the web "
                "UI's Account page first."
            ) from None
        finally:
            self.forget_users()
        previous = await self.client.get_user(previous_id) if previous_id is not None else None
        return user, previous

    async def admin_link(self, person: Person, username: str, here: int | None = None) -> tuple[User, User | None]:
        """
        Link without a code: the bot's owners vouch for it. Moves the link off any
        other account, refused while that account has games in progress.
        """
        try:
            target = await self.client.get_user(username.strip())
        except db_errors.UserNotFoundError:
            raise BotError(f"There is no chess account called `{username}`.") from None
        current = await self.user_for(person)
        if current is not None and current.id == target.id:
            raise BotError(f"Already linked to `{target.username}`.")
        await self._refuse_moving_games(person, here, you=False)
        if any(i.get("provider") == self.provider for i in target.identities):
            raise BotError(f"`{target.username}` is already linked to another Discord account.")
        external_id = str(person.id)
        try:
            if current is not None:
                await self.client.unlink_identity(current.id, self.provider, external_id)
            linked = await self.client.link_identity(target.id, self.provider, external_id)
        finally:
            self.forget_users()
        return linked, current

    async def unlink(self, person: Person) -> User:
        user = await self.user_for(person)
        if user is None:
            raise BotError("That Discord account isn't linked to a chess account.")
        try:
            return await self.client.unlink_identity(user.id, self.provider, str(person.id))
        finally:
            self.forget_users()

    async def set_first_password(self, person: Person, password: str) -> User:
        """Give a passwordless account a password, so its owner can use the web UI."""
        user = await self.require(person)
        if user.has_password:
            raise BotError("Your account already has a password. Change it in the web UI.")
        if user.role == "admin":
            raise BotError("The bot won't set passwords on chess admin accounts.")
        if len(password) < 8:
            raise BotError("Use a password of at least 8 characters.")
        return await self.client.update_user(user.id, password=password)

    async def set_moderator(self, group_id: int, person: Person, enabled: bool) -> User:
        """Make a member a chesssnake group admin of this guild's games, or not."""
        user = await self.require(person, you=False)
        change = self.client.add_group_admin if enabled else self.client.remove_group_admin
        try:
            return await change(group_id, user)
        finally:
            self.forget_users()  # the cached User's admin_groups changed

    # --- games -----------------------------------------------------------------

    async def games(self, group_id: int, user: User) -> list[GameState]:
        return await self.client.current_games(user.id, group_id=group_id)

    async def resolve_game(self, group_id: int, me: User, opponent: User | None = None) -> GameState:
        """The game ``me`` means: the one against ``opponent``, or their only game here."""
        games = await self.games(group_id, me)
        if opponent is not None:
            for game in games:
                if opponent.id in (game.white_id, game.black_id):
                    return game
            raise BotError(f"You don't have a game in progress with {opponent.username} here.")
        if len(games) == 1:
            return games[0]
        if not games:
            raise BotError("You don't have a game in progress here.")
        raise BotError("You have more than one game here, so say which opponent you mean.")

    async def get_game(self, game_id: int) -> GameState:
        return await self.client.get_game(game_id)

    async def move(self, game: GameState, me: User, text: str) -> MoveResult:
        return await self.player(me).move(game, text, expected_version=game.version)

    async def resign(self, game: GameState, player: User | int) -> GameState:
        return await self.client.resign(game, player_id=_uid(player), expected_version=game.version)

    async def draw(self, game: GameState, player: User | int, action: str) -> GameState:
        method = {"offer": "offer_draw", "accept": "accept_draw", "decline": "decline_draw"}[action]
        return await getattr(self.client, method)(game, player_id=_uid(player), expected_version=game.version)

    async def legal_moves(self, game_id: int) -> list[dict]:
        return await self.client.legal_moves(game_id)

    async def history(self, game_id: int) -> list[dict]:
        return await self.client.history(game_id)

    async def record(self, group_id: int, a: User, b: User) -> Record:
        data = await self.player(a).record_against(b, group_id=group_id)
        return Record(int(data["player1_wins"]), int(data["player2_wins"]), int(data["draws"]))

    async def live_games(self) -> list[GameState]:
        """Every game in progress on the server, for the polling fallback."""
        return await self.client.live_games(limit=200)

    # --- challenges --------------------------------------------------------------

    async def challenge(self, group_id: int, me: User, opponent: User, color: str) -> Challenge:
        if me.id == opponent.id:
            raise BotError("You can't challenge yourself.")
        return await self.player(me).challenge(opponent, group_id=group_id, color=color)

    async def challenges(self, group_id: int, me: User, direction: str = "all") -> list[Challenge]:
        return await self.player(me).challenges(direction, group_id=group_id)

    async def find_challenge(self, group_id: int, me: User, other: User | None, direction: str) -> Challenge:
        """The one challenge ``me`` means, incoming or outgoing, optionally with ``other``."""
        found = await self.challenges(group_id, me, direction)
        if other is not None:
            found = [c for c in found if other.id in (c.challenger_id, c.challenged_id)]
        if len(found) == 1:
            return found[0]
        what = {"incoming": "challenge to you", "outgoing": "challenge from you", "all": "challenge"}[direction]
        with_whom = f" with {other.username}" if other is not None else ""
        if not found:
            raise BotError(f"There's no pending {what}{with_whom} here.")
        raise BotError(f"There's more than one pending {what} here, so say whose you mean.")

    async def accept(self, challenge: Challenge, me: User) -> GameState:
        if challenge.challenged_id != me.id:
            raise BotError("Only the player who was challenged can accept.")
        return await self.player(me).accept_challenge(challenge)

    async def decline(self, challenge: Challenge, me: User):
        if me.id not in (challenge.challenger_id, challenge.challenged_id):
            raise BotError("That challenge isn't yours.")
        await self.client.decline_challenge(challenge)


def _uid(player) -> int:
    return player if isinstance(player, int) else player.id
