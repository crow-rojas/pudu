# Security policy

## Supported versions

Only the latest `main` is supported. Fixes land there; there are no backports.

## Reporting a vulnerability

Please report vulnerabilities privately through GitHub Security Advisories: open the
[Security tab](https://github.com/crow-rojas/pudu/security) and click **Report a vulnerability**.
Don't open a public issue. You'll get an answer as soon as possible, and credit in the fix if you
want it.

## Scope

Pudú is a local server, so the interesting attacks come from web pages open in your browser and
from other processes on your machine. What it does to stay safe:

- **Binds to `127.0.0.1` only.** Nothing on your network can reach it.
- **Validates the `Host` header** (`localhost:PORT`, `127.0.0.1:PORT`, `[::1]:PORT`), which blocks
  DNS rebinding.
- **Rejects cross-site requests** using `Sec-Fetch-Site`, and requires `Content-Type:
  application/json` on writes, which blocks CSRF from other pages.
- **Stores no tokens.** GitHub access goes through the `gh` CLI and its own credential storage.
  Pudú's config file only holds preferences.

Reports about any of these protections failing, or about Pudú leaking PR data to a third party, are
in scope. Issues that require an attacker who already runs code as your user are not.
