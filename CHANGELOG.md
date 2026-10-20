# Changelog

All notable changes to Pudú are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- English UI, with Spanish picked automatically from the browser language. The markdown endpoints
  follow `?lang=en|es` (default from `PUDU_LANG`).
- Demo mode (`python3 server.py --demo` or `PUDU_DEMO=1`): realistic fixture data without `gh`, for
  screenshots and for contributors without a GitHub session.
- First-run empty states and clear errors when `gh` is missing, logged out, rate limited or offline.
- Re-review detection: PRs you already reviewed that got new commits come back to your queue.
- Search and per-org groups in the repo picker.
- Snooze a PR until a date; it wakes early if the PR changes.
- Custom sections from any GitHub search query, configured in `config.json`.
- Stacked PRs: a PR based on another PR in the inbox shows which one it sits on top of.
- Account switcher across every account and host logged in to `gh`.
- MCP server (`python3 server.py --mcp`) with read-only `list_inbox` and `get_pr` tools, and
  `/pr.md` with failed checks, unresolved threads and ready-to-run `gh` commands for one PR.
- Linux install: a systemd user service plus a desktop launcher (`install.sh` on Linux).
- Merge queue position and auto-merge state on PRs.
- CI on Linux and macOS with Python 3.9 and the latest Python, plus shellcheck for `install.sh`.
- Issue and PR templates, `SECURITY.md` and this changelog.

### Changed

- Redesigned PR rows with a single status pill summarizing CI, review and conflicts.
- "Needs your attention" replaces "Changes requested": it also catches failing CI, merge conflicts
  and unresolved threads on your PRs.
- `install.sh` pins the `python3` and `gh` found in your shell, so pyenv, mise and `~/.local/bin`
  setups work under launchd.

### Fixed

- Re-running `install.sh` no longer stops a working service when a check fails, retries launchd
  bootstrap while the old agent shuts down, refuses to install when the port is taken, honors `PORT`,
  and works from paths with spaces.
- A GraphQL response with partial errors still shows the PRs that came back.
- The config file is written atomically under a lock, so concurrent writes can't corrupt it.

### Security

- Requests with an unexpected `Host` header are rejected (DNS rebinding).
- Cross-site requests are rejected using `Sec-Fetch-Site` (CSRF).

## [0.1.0] - 2026-10-20

### Added

- Local PR inbox at `http://localhost:8765`, fed by the `gh` CLI: PRs awaiting your review and the
  state of your own (CI, review decision, conflicts, unresolved threads, size, age).
- `/inbox.md` and copy buttons with one line per PR, written for Claude Code and other LLM CLIs.
- Repo picker to hide repos, applied server-side and saved in `~/.config/pudu/config.json`.
- macOS installer: launchd agent and `~/Applications/Pudú.app`.

[Unreleased]: https://github.com/crow-rojas/pudu/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/crow-rojas/pudu/releases/tag/v0.1.0
