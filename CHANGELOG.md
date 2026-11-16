# Changelog

All notable changes, newest first. Versions follow [semantic versioning](https://semver.org/);
before 1.0, a minor version may break compatibility.

## 0.1.0 - 2026-11-16

### Added
- A Discord bot for chesssnake: every command under `/chess`, with challenges answered by
  buttons, moves suggested as you type, boards with the last move highlighted, draws,
  resignations, records, and exports to PGN and FEN.
- Accounts made for one member, a whole server, or new members as they join, and linking an
  existing chesssnake account with a link code from its web UI, so the bot never sees a password.
- Moves made outside Discord, such as in chesssnake's web UI, posted to a chosen channel as they
  happen, with polling when the server has live updates off.
- Chess moderators per server, and admin commands for games and accounts.
- A container image at `ghcr.io/gabebecker2048/chesssnake-discord`, and a compose file that runs
  the bot with a chesssnake server. Every setting can come from an environment variable.
