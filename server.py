#!/usr/bin/env python3
"""PR inbox on localhost: what awaits my review and where my own PRs stand, fed by the gh CLI."""
import datetime
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

PORT = int(os.environ.get("PORT", 8765))
HERE = Path(__file__).parent
CONFIG = Path(os.environ.get("PUDU_CONFIG") or Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "pudu" / "config.json")
# why: several open tabs share one gh call instead of each burning GraphQL rate limit.
CACHE_SECONDS = 20
# why: a held-down refresh key or a looping script can't turn force=1 into a gh call per request.
FORCE_MIN_SECONDS = 5
MAX_BODY = 1_000_000
FAILED = {"FAILURE", "ERROR", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED"}

QUERY = """
query($q: String!, $n: Int!, $requested: Boolean!) {
  viewer { login avatarUrl }
  rateLimit { cost remaining resetAt }
  search(query: $q, type: ISSUE, first: $n) { issueCount nodes { ...pr } }
}
fragment pr on PullRequest {
  number title url isDraft createdAt updatedAt mergedAt additions deletions changedFiles
  reviewDecision mergeable mergeStateStatus baseRefName headRefName
  repository { name owner { login } nameWithOwner defaultBranchRef { name } }
  author { __typename login avatarUrl }
  labels(first: 20) { nodes { name color } }
  comments { totalCount }
  reviewThreads(first: 100) { nodes { isResolved comments { totalCount } } }
  commits(last: 1) { nodes { commit { statusCheckRollup { state contexts(first: 50) { nodes {
    ... on CheckRun { name conclusion } ... on StatusContext { context state }
  } } } } } }
  reviewRequests(first: 20) { nodes { requestedReviewer {
    __typename ... on User { login avatarUrl } ... on Bot { login avatarUrl }
    ... on Mannequin { login avatarUrl } ... on Team { slug name }
  } } }
  latestReviews(first: 20) { nodes { state author { login avatarUrl } } }
  timelineItems(itemTypes: [REVIEW_REQUESTED_EVENT], last: 20) @include(if: $requested) {
    nodes { ... on ReviewRequestedEvent { createdAt requestedReviewer { __typename ... on User { login } } } }
  }
}
"""

EMPTY = {
  "login": None, "avatarUrl": None, "fetchedAt": None, "rateLimit": None,
  "review": {"humans": [], "bots": []},
  "mine": {"returned": [], "approved": [], "waiting": [], "draft": []},
  "merged": [],
  "truncated": {},
}

# why: readers wait on a running fetch instead of starting their own; that wait is what dedupes gh calls.
_lock = threading.Lock()
_config_lock = threading.Lock()
_cache = {"at": 0.0, "data": None, "error": None, "errorKind": None}
# why: the UI shows a fix per kind (install gh, gh auth login, wait, check the network) instead of raw stderr.
ERROR_KINDS = [
  ("gh_auth", ("gh auth login", "http 401", "authentication", "bad credentials")),
  ("rate_limit", ("rate limit",)),
  ("network", ("could not resolve", "timeout", "timed out", "connection", "network", "no such host", "dial tcp")),
]


def error_kind(e):
  if isinstance(e, FileNotFoundError):
    return "gh_missing"
  if isinstance(e, subprocess.TimeoutExpired):
    return "network"
  s = str(e).lower()
  return next((kind for kind, words in ERROR_KINDS if any(w in s for w in words)), "other")


def gh(*args, partial=False):
  out = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=60)
  # why: `gh api graphql` exits 1 on partial GraphQL errors while still printing usable data.
  if out.returncode and not (partial and out.stdout.strip()):
    raise RuntimeError(out.stderr.strip() or "gh failed")
  return out.stdout


def dig(d, *path):
  for k in path:
    d = d.get(k) if isinstance(d, dict) else None
  return d


def nodes(d, *path):
  return [n for n in dig(d, *path, "nodes") or [] if n]


def bucket(pr):
  if pr["draft"]:
    return "draft"
  if pr["decision"] == "CHANGES_REQUESTED":
    return "returned"
  if pr["decision"] == "APPROVED":
    return "approved"
  return "waiting"


def requested_at(node, me):
  events = nodes(node, "timelineItems")
  mine = [e for e in events if dig(e, "requestedReviewer", "login") == me]
  teams = [e for e in events if dig(e, "requestedReviewer", "__typename") == "Team"]
  last = (mine or teams or [None])[-1]
  return last and last.get("createdAt")


def reviewer(r):
  if r.get("__typename") == "Team":
    return {"login": f"team:{r.get('slug') or r.get('name')}", "avatar": None}
  return {"login": r.get("login"), "avatar": r.get("avatarUrl")}


def slim(node, me=None):
  repo = node.get("repository") or {}
  author = node.get("author") or {"login": "ghost", "__typename": "User"}
  commit = (nodes(node, "commits") or [{}])[0]
  rollup = dig(commit, "commit", "statusCheckRollup")
  default = dig(repo, "defaultBranchRef", "name")
  threads = nodes(node, "reviewThreads")
  p = {
    "repo": repo.get("nameWithOwner"),
    "owner": dig(repo, "owner", "login"),
    "name": repo.get("name"),
    "number": node.get("number"),
    "title": node.get("title"),
    "url": node.get("url"),
    "author": author.get("login"),
    "authorAvatar": author.get("avatarUrl"),
    "bot": author.get("__typename") == "Bot",
    "draft": bool(node.get("isDraft")),
    "createdAt": node.get("createdAt"),
    "updatedAt": node.get("updatedAt"),
    "mergedAt": node.get("mergedAt"),
    "additions": node.get("additions"),
    "deletions": node.get("deletions"),
    "changedFiles": node.get("changedFiles"),
    "decision": node.get("reviewDecision"),
    "checks": dig(rollup, "state"),
    # why: a matrix job repeats the same check name; the UI only needs it once.
    "failedChecks": list(dict.fromkeys(
      c.get("name") or c.get("context") for c in nodes(rollup, "contexts")
      if (c.get("conclusion") or c.get("state")) in FAILED
    )),
    "mergeState": node.get("mergeStateStatus"),
    "conflict": node.get("mergeable") == "CONFLICTING",
    "baseRef": node.get("baseRefName"),
    "headRef": node.get("headRefName"),
    "stacked": bool(default) and node.get("baseRefName") != default,
    "labels": [{"name": l.get("name"), "color": l.get("color")} for l in nodes(node, "labels")],
    "comments": (dig(node, "comments", "totalCount") or 0) + sum(dig(t, "comments", "totalCount") or 0 for t in threads),
    "unresolvedThreads": sum(1 for t in threads if t.get("isResolved") is False),
    "threads": len(threads),
    "pending": [
      reviewer(r["requestedReviewer"]) for r in nodes(node, "reviewRequests")
      if (r.get("requestedReviewer") or {}).get("login") or dig(r, "requestedReviewer", "__typename") == "Team"
    ],
    "reviews": [
      {"login": r["author"].get("login"), "state": r.get("state"), "avatar": r["author"].get("avatarUrl")}
      for r in nodes(node, "latestReviews") if r.get("author")
    ],
    "requestedAt": requested_at(node, me) if "timelineItems" in node else None,
  }
  p["md"] = md_line(p)
  return p


CI_MD = {"SUCCESS": "CI ok", "FAILURE": "CI falló", "ERROR": "CI con error", "PENDING": "CI corriendo", "EXPECTED": "CI por partir"}
DECISION_MD = {"APPROVED": "aprobado", "CHANGES_REQUESTED": "cambios pedidos", "REVIEW_REQUIRED": "falta review"}
REVIEW_MD = {"APPROVED": "aprobó", "CHANGES_REQUESTED": "pidió cambios", "COMMENTED": "comentó", "DISMISSED": "descartada"}
MD_SECTIONS = [
  ("review", "Necesitan tu review", ("review", "humans")),
  ("returned", "Te devolvieron cambios", ("mine", "returned")),
  ("approved", "Aprobados", ("mine", "approved")),
  ("waiting", "Esperando review", ("mine", "waiting")),
  ("draft", "Drafts", ("mine", "draft")),
  ("merged", "Mergeados esta semana", ("merged",)),
  ("bots", "Bots", ("review", "bots")),
]


def ago(iso):
  try:
    m = max(0, (time.time() - datetime.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()) / 60)
  except (AttributeError, ValueError):
    return None
  return f"{m:.0f} min" if m < 60 else f"{m / 60:.0f} h" if m < 2880 else f"{m / 1440:.0f} días"


# why: the text a screenshot would carry, shaped for an LLM — the UI's copy buttons and /inbox.md share it.
def md_line(p):
  bits = [f"@{p['author']}"]
  if p["headRef"]:
    bits.append(f"{p['headRef']} -> {p['baseRef']}")
  if p["draft"]:
    bits.append("draft")
  if p["checks"] in CI_MD:
    failed = p["failedChecks"][:5]
    bits.append(CI_MD[p["checks"]] + (f" ({', '.join(failed)})" if failed else ""))
  if p["decision"] in DECISION_MD:
    bits.append(DECISION_MD[p["decision"]])
  if p["reviews"]:
    bits.append("reviews: " + ", ".join(f"{r['login']} {REVIEW_MD.get(r['state'], str(r['state']).lower())}" for r in p["reviews"]))
  if p["pending"]:
    bits.append("pendiente de: " + ", ".join(r["login"] for r in p["pending"]))
  if p["conflict"] or p["mergeState"] == "DIRTY":
    bits.append("conflicto")
  elif p["mergeState"] == "BEHIND":
    bits.append("rama desactualizada")
  if p["unresolvedThreads"]:
    bits.append(f"hilos sin resolver: {p['unresolvedThreads']}")
  if p["additions"] is not None:
    bits.append(f"+{p['additions']} -{p['deletions']}" + (f" en {p['changedFiles']} archivo{'s' * (p['changedFiles'] != 1)}" if p["changedFiles"] is not None else ""))
  if p["labels"]:
    bits.append("labels: " + ", ".join(l["name"] for l in p["labels"]))
  when = next(((label, ago(iso)) for label, iso in (
    ("review pedida hace", p["requestedAt"]), ("mergeado hace", p["mergedAt"]), ("actualizado hace", p["updatedAt"]),
  ) if ago(iso)), None)
  if when:
    bits.append(" ".join(when))
  return f"- {p['title'] or '(sin título)'} ({p['repo']}#{p['number']}) {p['url']}\n  " + " · ".join(bits)


def inbox_md(d, only=()):
  parts = [f"# PRs de @{d['login']}"]
  hidden = [r["repo"] for r in d.get("repos", []) if r["hidden"]]
  if hidden:
    parts.append(f"Repos ocultos: {', '.join(hidden)}.")
  for sid, title, path in MD_SECTIONS:
    if only and sid not in only:
      continue
    prs = dig(d, *path) or []
    parts.append(f"## {title} ({len(prs)})\n\n" + ("\n".join(p["md"] for p in prs) or "(ninguno)"))
  return "\n\n".join(parts) + "\n"


def search(q, n, requested=False):
  out = json.loads(gh(
    "api", "graphql", "-f", f"query={QUERY}", "-f", f"q={q}", "-F", f"n={n}", "-F", f"requested={str(requested).lower()}",
    partial=True,
  ))
  if not out.get("data"):
    raise RuntimeError("; ".join(str(e.get("message", e)) for e in out.get("errors") or []) or "gh returned no data")
  return out["data"]


def build(review_raw, mine_raw, merged_raw):
  me = dig(review_raw, "viewer", "login")
  prs = lambda raw: [slim(n, me) for n in nodes(raw, "search") if n.get("number")]
  desc = lambda items, key: sorted(items, key=lambda p: p[key] or "", reverse=True)
  review, mine = prs(review_raw), desc(prs(mine_raw), "updatedAt")
  limits = [r["rateLimit"] for r in (review_raw, mine_raw, merged_raw) if r.get("rateLimit")]
  low = min(limits, key=lambda r: r["remaining"], default=None)
  return {
    "login": me,
    "avatarUrl": dig(review_raw, "viewer", "avatarUrl"),
    "fetchedAt": time.time(),
    "rateLimit": low and {**low, "cost": sum(r["cost"] for r in limits)},
    "review": {
      # why: oldest request on top, it's the one that has waited longest; createdAt covers a missing event.
      "humans": sorted((p for p in review if not p["bot"]), key=lambda p: p["requestedAt"] or p["createdAt"] or ""),
      "bots": desc((p for p in review if p["bot"]), "updatedAt"),
    },
    "mine": {k: [p for p in mine if bucket(p) == k] for k in EMPTY["mine"]},
    "merged": desc(prs(merged_raw), "mergedAt"),
    "truncated": {
      k: dig(raw, "search", "issueCount") for k, raw in (("review", review_raw), ("mine", mine_raw), ("merged", merged_raw))
      if (dig(raw, "search", "issueCount") or 0) > len(dig(raw, "search", "nodes") or [])
    },
  }


def fetch():
  base = "is:pr is:open archived:false"
  # why: GitHub reads merged:>=DATE in UTC; a local date would shift the window near midnight.
  since = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=7)).strftime("%Y-%m-%d")
  # why: one combined query runs the three searches serially and hits GitHub's ~10s timeout (502);
  # three parallel calls finish in the time of the slowest.
  with ThreadPoolExecutor(3) as pool:
    jobs = [
      # why: when results overflow `first`, keep the freshest; search has no sort by merge date.
      pool.submit(search, f"{base} review-requested:@me sort:updated-desc", 100, True),
      pool.submit(search, f"{base} author:@me sort:updated-desc", 100),
      pool.submit(search, f"is:pr author:@me is:merged merged:>={since} sort:updated-desc", 20),
    ]
    return build(*(j.result() for j in jobs))


def read_config():
  try:
    c = json.loads(CONFIG.read_text())
  except (OSError, ValueError):
    return {}
  return c if isinstance(c, dict) else {}


# why: read-modify-write keeps keys this version doesn't know; temp file + os.replace never leaves half a file.
def update_config(change):
  with _config_lock:
    c = read_config()
    change(c)
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=CONFIG.parent, prefix=".config.", suffix=".tmp")
    with os.fdopen(fd, "w") as f:
      f.write(json.dumps(c, indent=2) + "\n")
    os.replace(tmp, CONFIG)


def hidden_repos():
  h = read_config().get("hidden")
  return {r for r in h if isinstance(r, str)} if isinstance(h, list) else set()


def save_hidden(repos):
  update_config(lambda c: c.update(hidden=sorted(repos)))


LISTS = [("review", "humans"), ("review", "bots"), *(("mine", k) for k in EMPTY["mine"]), ("merged",)]


# why: hiding happens here, not in the page, so the tab badge, notifications and /inbox.md all agree.
def view(d, hidden):
  counts = {}
  for path in LISTS:
    for p in dig(d, *path) or []:
      counts[p["repo"]] = counts.get(p["repo"], 0) + 1
  keep = lambda *path: [p for p in dig(d, *path) or [] if p["repo"] not in hidden]
  return {
    **d,
    "review": {"humans": keep("review", "humans"), "bots": keep("review", "bots")},
    "mine": {k: keep("mine", k) for k in EMPTY["mine"]},
    "merged": keep("merged"),
    "repos": [{"repo": r, "count": counts.get(r, 0), "hidden": r in hidden} for r in sorted(set(counts) | hidden)],
  }


def data(force=False):
  with _lock:
    age = time.time() - _cache["at"]
    if (force and age >= FORCE_MIN_SECONDS) or age > CACHE_SECONDS:
      try:
        _cache.update(data=fetch(), error=None, errorKind=None)
      except Exception as e:  # why: a network blip keeps showing the last good snapshot, flagged.
        _cache.update(error=str(e), errorKind=error_kind(e))
      _cache["at"] = time.time()
    return view({**EMPTY, **(_cache["data"] or {}), "error": _cache["error"], "errorKind": _cache["errorKind"]}, hidden_repos())


MISSING_FRONTEND = "<!doctype html><meta charset=utf-8><title>Pudú</title><p>Falta el frontend: no existe index.html junto a server.py.".encode()


class Handler(BaseHTTPRequestHandler):
  # why: the Host allowlist stops DNS rebinding and Sec-Fetch-Site stops other sites' pages; curl sends no Sec-Fetch-Site.
  def _allowed(self):
    hosts = {f"{h}:{self.server.server_port}" for h in ("localhost", "127.0.0.1", "[::1]")}
    return (self.headers.get("Host") or "").lower() in hosts and self.headers.get("Sec-Fetch-Site") in (None, "same-origin", "none")

  def _body(self):
    try:
      n = int(self.headers.get("Content-Length") or 0)
    except ValueError:
      n = -1
    return self.rfile.read(n) if 0 <= n <= MAX_BODY else None

  def do_GET(self):
    if not self._allowed():
      return self._send(403, "text/plain", b"forbidden")
    url = urlsplit(self.path)
    if url.path == "/api/data":
      force = parse_qs(url.query).get("force") == ["1"]
      self._send(200, "application/json", json.dumps(data(force)).encode())
    elif url.path == "/inbox.md":
      qs = parse_qs(url.query)
      d = data(qs.get("force") == ["1"])
      if not d["login"]:
        self._send(503, "text/plain; charset=utf-8", f"Pudú todavía no tiene datos: {d['error'] or 'consultando GitHub'}\n".encode())
      else:
        only = {s for v in qs.get("s", []) for s in v.split(",") if s}
        self._send(200, "text/markdown; charset=utf-8", inbox_md(d, only).encode())
    elif url.path == "/":
      page = HERE / "index.html"
      self._send(200, "text/html; charset=utf-8", page.read_bytes() if page.exists() else MISSING_FRONTEND)
    elif url.path == "/favicon.svg" and (HERE / "favicon.svg").exists():
      self._send(200, "image/svg+xml", (HERE / "favicon.svg").read_bytes())
    else:
      self._send(404, "text/plain", b"not found")

  def do_POST(self):
    if not self._allowed():
      return self._send(403, "text/plain", b"forbidden")
    # why: demanding JSON forces a CORS preflight, so another web page can't rewrite the config.
    if urlsplit(self.path).path != "/api/hidden" or not self.headers.get("Content-Type", "").startswith("application/json"):
      return self._send(404, "text/plain", b"not found")
    body = self._body()
    if body is None:
      return self._send(400, "text/plain", b"bad request")
    try:
      repos = json.loads(body)["hidden"]
    except (ValueError, KeyError, TypeError):
      repos = None
    if not isinstance(repos, list) or len(repos) > 1000 or not all(isinstance(r, str) and 0 < len(r) < 200 for r in repos):
      return self._send(400, "text/plain", b"bad request")
    save_hidden(set(repos))
    self._send(200, "application/json", json.dumps(data()).encode())

  def _send(self, code, ctype, body):
    self.send_response(code)
    self.send_header("Content-Type", ctype)
    self.send_header("Cache-Control", "no-store")
    self.end_headers()
    self.wfile.write(body)

  def log_message(self, *_args):
    pass


def selftest():
  node = {
    "number": 1, "title": "t", "url": "u", "isDraft": False, "createdAt": "2026-01-01T00:00:00Z",
    "updatedAt": "2026-01-02T00:00:00Z", "mergedAt": None, "additions": 1, "deletions": 2, "changedFiles": 1,
    "reviewDecision": "CHANGES_REQUESTED", "mergeable": "CONFLICTING", "mergeStateStatus": "DIRTY",
    "baseRefName": "feat/base", "headRefName": "feat/x",
    "repository": {"name": "r", "owner": {"login": "o"}, "nameWithOwner": "o/r", "defaultBranchRef": {"name": "develop"}},
    "author": {"__typename": "Bot", "login": "dependabot", "avatarUrl": "a"},
    "labels": {"nodes": [{"name": "bug", "color": "d73a4a"}, None]},
    "comments": {"totalCount": 2},
    "reviewThreads": {"nodes": [{"isResolved": False, "comments": {"totalCount": 3}}, {"isResolved": True, "comments": None}]},
    "commits": {"nodes": [{"commit": {"statusCheckRollup": {"state": "FAILURE", "contexts": {"nodes": [
      {"name": "lint", "conclusion": "FAILURE"}, {"name": "lint", "conclusion": "FAILURE"},
      {"name": "ok", "conclusion": "SUCCESS"}, {"context": "ci/x", "state": "ERROR"}, {},
    ]}}}}]},
    "reviewRequests": {"nodes": [
      {"requestedReviewer": {"__typename": "Team", "slug": "devs", "name": "Devs"}},
      {"requestedReviewer": {"__typename": "User", "login": "me", "avatarUrl": "m"}},
      {"requestedReviewer": None}, {"requestedReviewer": {}},
    ]},
    "latestReviews": {"nodes": [{"state": "APPROVED", "author": {"login": "a", "avatarUrl": "x"}}, {"state": "COMMENTED", "author": None}]},
    "timelineItems": {"nodes": [
      {"createdAt": "T1", "requestedReviewer": {"__typename": "User", "login": "me"}},
      {"createdAt": "T2", "requestedReviewer": {"__typename": "Team"}},
      {"createdAt": "T3", "requestedReviewer": {"__typename": "User", "login": "me"}},
      {"createdAt": "T4", "requestedReviewer": {"__typename": "User", "login": "other"}},
    ]},
  }
  p = slim(node, "me")
  assert p["bot"] and p["conflict"] and p["stacked"] and p["checks"] == "FAILURE"
  assert p["failedChecks"] == ["lint", "ci/x"]
  assert p["comments"] == 5 and p["unresolvedThreads"] == 1
  assert p["labels"] == [{"name": "bug", "color": "d73a4a"}]
  assert p["pending"] == [{"login": "team:devs", "avatar": None}, {"login": "me", "avatar": "m"}]
  assert p["reviews"] == [{"login": "a", "state": "APPROVED", "avatar": "x"}]
  assert p["requestedAt"] == "T3"
  assert p["threads"] == 2
  assert p["md"].startswith("- t (o/r#1) u\n  @dependabot · feat/x -> feat/base · CI falló (lint, ci/x) · cambios pedidos")
  assert "conflicto" in p["md"] and "hilos sin resolver: 1" in p["md"] and "+1 -2 en 1 archivo ·" in p["md"]
  assert "pendiente de: team:devs, me" in p["md"] and p["md"].endswith("actualizado hace " + ago(node["updatedAt"]))
  assert slim(node, "nobody")["requestedAt"] == "T2"
  assert slim({k: v for k, v in node.items() if k != "timelineItems"}, "me")["requestedAt"] is None
  assert not slim({**node, "baseRefName": "develop"})["stacked"]
  bare = slim({"number": 9, "repository": None, "author": None, "commits": None, "comments": None})
  assert bare["author"] == "ghost" and bare["checks"] is None and bare["failedChecks"] == [] and not bare["stacked"]

  assert bucket(p) == "returned"
  assert bucket({**p, "draft": True}) == "draft"
  assert bucket({**p, "decision": "APPROVED"}) == "approved"
  assert bucket({**p, "decision": None}) == "waiting"

  human = {**node, "author": {"__typename": "User", "login": "x"}}
  raw = lambda *ns, rl=None: {"viewer": {"login": "me", "avatarUrl": "av"}, "rateLimit": rl, "search": {"nodes": list(ns)}}
  out = build(
    raw({**human, "number": 1}, {**human, "number": 2, "timelineItems": {"nodes": [{"createdAt": "T0", "requestedReviewer": {"login": "me"}}]}},
        node, None, {}, rl={"cost": 7, "remaining": 100, "resetAt": "R1"}),
    raw({**human, "number": 3, "isDraft": True}, {**human, "number": 4, "updatedAt": "2026-02-01T00:00:00Z", "reviewDecision": None},
        {**human, "number": 5, "reviewDecision": "APPROVED"}, rl={"cost": 6, "remaining": 90, "resetAt": "R2"}),
    raw({**human, "number": 6, "mergedAt": "2026-01-01T00:00:00Z"}, {**human, "number": 7, "mergedAt": "2026-03-01T00:00:00Z"}),
  )
  assert out["login"] == "me" and out["avatarUrl"] == "av"
  assert out["rateLimit"] == {"cost": 13, "remaining": 90, "resetAt": "R2"}
  assert [p["number"] for p in out["review"]["humans"]] == [2, 1] and [p["number"] for p in out["review"]["bots"]] == [1]
  assert {k: [p["number"] for p in v] for k, v in out["mine"].items()} == {"returned": [], "approved": [5], "waiting": [4], "draft": [3]}
  assert [p["number"] for p in out["merged"]] == [7, 6]
  assert set(out) | {"error"} == set(EMPTY) | {"error"}
  v = view(out, {"o/r", "gone/repo"})
  assert v["review"]["humans"] == [] and v["mine"]["approved"] == [] and v["merged"] == []
  assert v["repos"] == [{"repo": "gone/repo", "count": 0, "hidden": True}, {"repo": "o/r", "count": 8, "hidden": True}]
  assert view(out, set())["review"] == out["review"]
  assert inbox_md(v).startswith("# PRs de @me\n\nRepos ocultos: gone/repo, o/r.\n\n## Necesitan tu review (0)")
  md = inbox_md(out)
  assert md.startswith("# PRs de @me\n\n## Necesitan tu review (2)\n\n- t (o/r#2)") and "## Te devolvieron cambios (0)\n\n(ninguno)" in md
  assert inbox_md(out, {"approved"}) == f"# PRs de @me\n\n## Aprobados (1)\n\n{out['mine']['approved'][0]['md']}\n"
  print("ok")


if __name__ == "__main__":
  if sys.argv[1:] == ["--test"]:
    selftest()
  else:
    print(f"Pudú en http://localhost:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
