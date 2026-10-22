# Changelog

All notable changes to Pudú are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- English UI, with Spanish picked automatically from the browser language. The markdown follows
  `?lang=en|es`, with the default from `PUDU_LANG`.
- Demo mode (`python3 server.py --demo` or `PUDU_DEMO=1`): fixture PRs through the real pipeline,
  without `gh`, for screenshots and for contributors without a GitHub session.
- First-run empty states with a fix for each failure (`gh` missing, signed out, rate limited,
  offline), driven by a new `errorKind` field in `/api/data`.
- Re-review detection: PRs you already reviewed that got new commits come back to "Needs your review".
- Truncation hints: when GitHub has more results than Pudú fetched, the section says "100 of 134"
  and links to the full search. `/api/data` reports it in `truncated`.
- Repo picker that scales: repos grouped by owner with a toggle per owner, a filter box past 8 repos,
  and the `p` shortcut.
- Shortcuts: `[` and `]` jump between sections, `x` marks a PR as seen.
- `/pr.md?pr=<url | owner/repo#N>`: one PR in depth, with failed checks and their links, unresolved
  review threads flagged as untrusted text, and ready-to-run `gh pr checkout`, `diff` and `checks`.
- MCP server over stdio (`python3 server.py --mcp`) with read-only `list_inbox` and `get_pr` tools.
  It speaks both the `initialize` handshake and per-request protocol versions.
- Snooze: `POST /api/snooze` hides a PR until a time, stored in `config.json`. A snoozed PR leaves
  every section, the tab badge, notifications and the markdown, and wakes early on any activity.
- Custom sections from any GitHub search, set in `config.json` under `sections`. They appear in
  `/api/data` (`custom`), `/inbox.md` (also via `?s=<id>`) and the MCP tools, and a broken query
  only fails its own section.
- Accounts: Pudú lists every account and host logged in to `gh` (`accounts` in `/api/data`), and
  `POST /api/account` or `account` in `config.json` runs `gh` as one of them, GitHub Enterprise
  included.
- PR fields `key`, `requestedVia`, `mergeQueue`, `autoMerge` and `stackParent`. The agent text says
  "requested via team:x", "in merge queue (#2)", "auto-merge on" and "on top of #484".
- `staleHours` in `config.json`: review wait times turn amber past it and red past three times it.
- Snooze from the UI: `s` or the row's hover button (1 h, 3 h, tomorrow 9:00, next Monday 9:00,
  custom), and a folded "Snoozed" section to wake PRs early.
- `a` copies a prompt for your coding agent tailored to the PR: review it, fix what's blocking it,
  or check it's ready to merge, with the `gh` commands and the `/pr.md` link.
- Account switcher in the header when `gh` has more than one account logged in.
- Desktop notifications when one of your PRs turns CI red, gets a conflict, changes requested or new
  unresolved threads.
- Stacked PRs drawn under their parent, a ring for PRs updated since you last opened them, and tags
  for team requests, merge queue position and auto-merge.
- Linux install: a systemd user service plus a desktop launcher. On WSL the launcher is skipped.
- CI on Linux and macOS with Python 3.9 and the latest Python, plus shellcheck for `install.sh`.
- Issue and PR templates, `SECURITY.md` and this changelog.

### Changed

- PR rows lead with one status pill, worst first, summarizing conflicts, CI, review and threads.
- "Needs your attention" replaces "Changes requested": it also catches failing CI, merge conflicts
  and unresolved threads on your PRs. When `reviewDecision` is empty, the latest reviews decide.
- Quieter section headers: the copy button shows on hover.
- `force=1` is ignored when the last fetch is under 5 seconds old.
- `install.sh` pins the `python3` and `gh` found in your shell, so pyenv, mise and `~/.local/bin`
  setups work under launchd and systemd. The server also looks for `gh` in the Homebrew and
  Linuxbrew directories when started with a bare `PATH` (Dock, MCP hosts).
- Shortcuts no longer fire while the repo picker is open.

### Fixed

- A PR whose CI is green no longer shows red because of a stale run: when a check name has several
  runs (a re-run, or one cancelled by a concurrency group), only the latest counts, as in GitHub's
  required checks. A cancelled latest run still counts as a failure.
- Re-running `install.sh` no longer stops a working service when a check fails, retries launchd
  bootstrap while the old agent shuts down, refuses to install when the port is taken, honors `PORT`,
  and works from paths with spaces.
- A GraphQL response with partial errors still shows the PRs that came back.
- A `gh` 502 reports `gh`'s error instead of a JSON parse error.
- GitHub Enterprise versions without merge queue no longer fail the whole query.
- "error connecting to" is classified as a network error.
- Your own PRs are never flagged as re-reviews.
- The config file is written atomically under a lock, keeping keys Pudú doesn't know.
- Rejected POSTs get their 4xx reply instead of a connection reset.
- Faint text meets WCAG AA contrast in both themes.

### Security

- Requests with an unexpected `Host` header are rejected (DNS rebinding).
- Cross-site requests are rejected using `Sec-Fetch-Site` (CSRF).
- POST bodies with an invalid or oversized `Content-Length` are rejected.

## [0.1.0] - 2026-10-20

### Added

- Local PR inbox at `http://localhost:8765`, fed by the `gh` CLI: PRs awaiting your review and the
  state of your own (CI, review decision, conflicts, unresolved threads, size, age).
- `/inbox.md` and copy buttons with one line per PR, written for Claude Code and other LLM CLIs.
- Repo picker to hide repos, applied server-side and saved in `~/.config/pudu/config.json`.
- macOS installer: launchd agent and `~/Applications/Pudú.app`.

[Unreleased]: https://github.com/crow-rojas/pudu/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/crow-rojas/pudu/releases/tag/v0.1.0
