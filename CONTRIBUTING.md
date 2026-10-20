# Contributing to Pudú

Thanks for taking the time. Pudú is small on purpose, and the goal is to keep it that way.

## Principles

- **No dependencies.** The server uses only the Python standard library and runs on Python 3.9. The UI is a single `index.html` with no build step. If a change needs anything installed, open an issue first.
- **GitHub only through `gh`.** Pudú never asks for or stores a token; it uses the CLI's session.
- **Local only.** Nothing leaves the machine except `gh`'s calls to GitHub. No telemetry, no third-party hosts.
- **Less is more.** Before adding an option, ask whether someone needs it today. A smaller diff that solves the problem wins.

## Development

```sh
python3 server.py --test                 # self-test, prints "ok"
/usr/bin/python3 server.py --test        # macOS: system Python 3.9, the oldest version we support
python3 server.py --demo                 # fixture data, no gh needed
PORT=8766 python3 server.py              # real data on another port
```

- If Pudú is installed as a service, port 8765 is taken. Use another `PORT` for development.
- Point `PUDU_CONFIG` at a scratch file (`PUDU_CONFIG=/tmp/pudu-dev.json`) so hiding repos or snoozing PRs while testing doesn't touch your real config. Demo mode uses a throwaway config unless you set `PUDU_CONFIG`.
- Changes to `index.html` show up on reload. Changes to `server.py` need a server restart.
- To try `install.sh` without replacing your live install, give the copy its own name and port: `PUDU_NAME=pudu-dev PORT=8766 ./install.sh`, then `PUDU_NAME=pudu-dev ./install.sh uninstall`. On macOS both installs share `~/Applications/Pudú.app`, so re-run `./install.sh` for your main install afterwards.
- Try the MCP server by piping JSON-RPC into it:

  ```sh
  printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | python3 server.py --mcp --demo
  ```

CI runs the self-test on Linux and macOS with Python 3.9 and the latest Python, plus `bash -n` and `shellcheck` on `install.sh`.

## Code style

- 2-space indentation in Python and JavaScript. Keep the code terse and flat.
- Python 3.9 syntax only: no `match`, no `X | Y` types, no 3.10+ standard library APIs.
- Comments explain *why*, never *what*, and start with `why:`. Code that reads on its own gets no comment.
- Identifiers and comments in English.
- **UI strings** go through the `T` table in `index.html`, with an entry in both `en` and `es`, and are read with `t(key, ...args)`. English is the fallback for a missing key, but don't rely on it.
- **Markdown strings** (`md_line`, `inbox_md`, `pr_md`) live in `STRINGS` in `server.py`, also in `en` and `es`. Section titles in `index.html` must match the server's so copied text reads the same as `/inbox.md`.
- `install.sh` must pass `shellcheck` and stay safe to re-run.

## Pull requests

1. One change per PR, with a description of the problem it solves.
2. `python3 server.py --test` passes, on Python 3.9 too.
3. UI changes include before and after screenshots taken from `python3 server.py --demo`, never from your real repos.
4. If you change the agent text format (`md_line`, `inbox_md`, `pr_md`), update the self-test to cover it, in both languages.
5. New behavior in `server.py` gets a few asserts in `selftest()`.

## Reporting a bug

Open an issue with what you expected, what happened, your OS, `python3 --version` and `gh --version`, and the last lines of the log:

- macOS service: `tail -n 50 ~/Library/Logs/pudu.log`
- Linux service: `journalctl --user -u pudu -n 50`
- Run by hand: the terminal output of `python3 server.py`

**Redact private data** first: logs, screenshots and `/inbox.md` output contain repo, branch and people names. If you can reproduce the bug with `--demo`, use that instead.

Security issues go through [SECURITY.md](SECURITY.md), not public issues.
