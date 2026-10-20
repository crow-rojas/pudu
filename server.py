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
# why: fixture data through the real pipeline, for screenshots and contributors without gh.
DEMO = "--demo" in sys.argv[1:] or os.environ.get("PUDU_DEMO") == "1"
FAILED = {"FAILURE", "ERROR", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED"}

QUERY = """
query($q: String!, $n: Int!, $requested: Boolean!) {
  viewer { login avatarUrl }
  rateLimit { cost remaining resetAt }
  search(query: $q, type: ISSUE, first: $n) { issueCount nodes { ...pr } }
}
fragment pr on PullRequest {
  number title url isDraft createdAt updatedAt mergedAt additions deletions changedFiles
  reviewDecision mergeable mergeStateStatus baseRefName headRefName headRefOid
  viewerLatestReview { commit { oid } }
  repository { name owner { login } nameWithOwner defaultBranchRef { name } }
  author { __typename login avatarUrl }
  labels(first: 20) { nodes { name color } }
  comments { totalCount }
  reviewThreads(first: 100) { nodes { isResolved comments { totalCount } } }
  commits(last: 1) { nodes { commit { committedDate statusCheckRollup { state contexts(first: 50) { nodes {
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


# why: "returned" means "your move": blockers first, approval beats open threads, open threads beat waiting.
def bucket(pr):
  if pr["draft"]:
    return "draft"
  # why: repos without required reviews give reviewDecision null even after someone reviewed.
  states = {r["state"] for r in pr["reviews"]}
  decision = pr["decision"] or next((s for s in ("CHANGES_REQUESTED", "APPROVED") if s in states), None)
  if decision == "CHANGES_REQUESTED" or pr["checks"] in FAILED or pr["conflict"] or pr["mergeState"] == "DIRTY":
    return "returned"
  if decision == "APPROVED":
    return "approved"
  return "returned" if pr["unresolvedThreads"] else "waiting"


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
    "headCommittedAt": dig(commit, "commit", "committedDate"),
    # why: a reviewed commit that is no longer the head means new commits landed after your review.
    "rereview": bool(node.get("viewerLatestReview")) and dig(node, "viewerLatestReview", "commit", "oid") != node.get("headRefOid"),
  }
  return p


STRINGS = {
  "en": {
    "ci": {"SUCCESS": "CI ok", "FAILURE": "CI failed", "ERROR": "CI errored", "PENDING": "CI running", "EXPECTED": "CI expected"},
    "decision": {"APPROVED": "approved", "CHANGES_REQUESTED": "changes requested", "REVIEW_REQUIRED": "review required"},
    "review": {"APPROVED": "approved", "CHANGES_REQUESTED": "requested changes", "COMMENTED": "commented", "DISMISSED": "dismissed"},
    "sections": {
      "review": "Needs your review", "returned": "Needs your attention", "approved": "Approved",
      "waiting": "Waiting for review", "draft": "Drafts", "merged": "Merged this week", "bots": "Bots",
    },
    "rereview": "re-review: new commits", "pending": "pending: {}", "conflict": "conflict", "behind": "branch out of date",
    "threads": "unresolved threads: {}", "files": " in {} file", "labels": "labels: {}", "days": "days",
    "requested": "review requested {} ago", "merged": "merged {} ago", "updated": "updated {} ago",
    "untitled": "(untitled)", "title": "# PRs for @{}", "hidden": "Hidden repos: {}.", "none": "(none)",
    "nodata": "Pudú has no data yet: {}", "fetching": "querying GitHub",
  },
  "es": {
    "ci": {"SUCCESS": "CI ok", "FAILURE": "CI falló", "ERROR": "CI con error", "PENDING": "CI corriendo", "EXPECTED": "CI por partir"},
    "decision": {"APPROVED": "aprobado", "CHANGES_REQUESTED": "cambios pedidos", "REVIEW_REQUIRED": "falta review"},
    "review": {"APPROVED": "aprobó", "CHANGES_REQUESTED": "pidió cambios", "COMMENTED": "comentó", "DISMISSED": "descartada"},
    "sections": {
      "review": "Necesitan tu review", "returned": "Necesitan tu atención", "approved": "Aprobados",
      "waiting": "Esperando review", "draft": "Drafts", "merged": "Mergeados esta semana", "bots": "Bots",
    },
    "rereview": "re-review: commits nuevos", "pending": "pendiente de: {}", "conflict": "conflicto", "behind": "rama desactualizada",
    "threads": "hilos sin resolver: {}", "files": " en {} archivo", "labels": "labels: {}", "days": "días",
    "requested": "review pedida hace {}", "merged": "mergeado hace {}", "updated": "actualizado hace {}",
    "untitled": "(sin título)", "title": "# PRs de @{}", "hidden": "Repos ocultos: {}.", "none": "(ninguno)",
    "nodata": "Pudú todavía no tiene datos: {}", "fetching": "consultando GitHub",
  },
}
LANG = os.environ.get("PUDU_LANG") if os.environ.get("PUDU_LANG") in STRINGS else "en"
MD_SECTIONS = [
  ("review", ("review", "humans")), ("returned", ("mine", "returned")), ("approved", ("mine", "approved")),
  ("waiting", ("mine", "waiting")), ("draft", ("mine", "draft")), ("merged", ("merged",)), ("bots", ("review", "bots")),
]


def ago(iso, lang="en"):
  try:
    m = max(0, (time.time() - datetime.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()) / 60)
  except (AttributeError, ValueError):
    return None
  return f"{m:.0f} min" if m < 60 else f"{m / 60:.0f} h" if m < 2880 else f"{m / 1440:.0f} {STRINGS[lang]['days']}"


# why: the text a screenshot would carry, shaped for an LLM — the UI's copy buttons and /inbox.md share it.
def md_line(p, lang="en"):
  s = STRINGS[lang]
  bits = [f"@{p['author']}"]
  if p["headRef"]:
    bits.append(f"{p['headRef']} -> {p['baseRef']}")
  if p["draft"]:
    bits.append("draft")
  if p.get("rereview"):
    bits.append(s["rereview"])
  if p["checks"] in s["ci"]:
    failed = p["failedChecks"][:5]
    bits.append(s["ci"][p["checks"]] + (f" ({', '.join(failed)})" if failed else ""))
  if p["decision"] in s["decision"]:
    bits.append(s["decision"][p["decision"]])
  if p["reviews"]:
    bits.append("reviews: " + ", ".join(f"{r['login']} {s['review'].get(r['state'], str(r['state']).lower())}" for r in p["reviews"]))
  if p["pending"]:
    bits.append(s["pending"].format(", ".join(r["login"] for r in p["pending"])))
  if p["conflict"] or p["mergeState"] == "DIRTY":
    bits.append(s["conflict"])
  elif p["mergeState"] == "BEHIND":
    bits.append(s["behind"])
  if p["unresolvedThreads"]:
    bits.append(s["threads"].format(p["unresolvedThreads"]))
  if p["additions"] is not None:
    n = p["changedFiles"]
    bits.append(f"+{p['additions']} -{p['deletions']}" + (s["files"].format(n) + "s" * (n != 1) if n is not None else ""))
  if p["labels"]:
    bits.append(s["labels"].format(", ".join(l["name"] for l in p["labels"])))
  when = next((s[key].format(ago(iso, lang)) for key, iso in (
    ("requested", p["requestedAt"]), ("merged", p["mergedAt"]), ("updated", p["updatedAt"]),
  ) if ago(iso)), None)
  if when:
    bits.append(when)
  return f"- {p['title'] or s['untitled']} ({p['repo']}#{p['number']}) {p['url']}\n  " + " · ".join(bits)


def inbox_md(d, only=(), lang="en"):
  s = STRINGS[lang]
  parts = [s["title"].format(d["login"])]
  hidden = [r["repo"] for r in d.get("repos", []) if r["hidden"]]
  if hidden:
    parts.append(s["hidden"].format(", ".join(hidden)))
  for sid, path in MD_SECTIONS:
    if only and sid not in only:
      continue
    prs = dig(d, *path) or []
    parts.append(f"## {s['sections'][sid]} ({len(prs)})\n\n" + ("\n".join(p["md"] for p in prs) or s["none"]))
  return "\n\n".join(parts) + "\n"


def search(q, n, requested=False):
  out = json.loads(gh(
    "api", "graphql", "-f", f"query={QUERY}", "-f", f"q={q}", "-F", f"n={n}", "-F", f"requested={str(requested).lower()}",
    partial=True,
  ))
  if not out.get("data"):
    raise RuntimeError("; ".join(str(e.get("message", e)) for e in out.get("errors") or []) or "gh returned no data")
  return out["data"]


def build(review_raw, mine_raw, merged_raw, rereview_raw=None):
  raws = {"review": review_raw, "mine": mine_raw, "merged": merged_raw, "rereview": rereview_raw or {}}
  me = dig(review_raw, "viewer", "login")
  prs = lambda raw: [slim(n, me) for n in nodes(raw, "search") if n.get("number")]
  desc = lambda items, key: sorted(items, key=lambda p: p[key] or "", reverse=True)
  review, mine = prs(review_raw), desc(prs(mine_raw), "updatedAt")
  # why: a PR in both searches keeps its review-requested entry, which carries requestedAt.
  urls = {p["url"] for p in review}
  review += [p for p in prs(raws["rereview"]) if p["rereview"] and p["url"] not in urls]
  limits = [r["rateLimit"] for r in raws.values() if r.get("rateLimit")]
  low = min(limits, key=lambda r: r["remaining"], default=None)
  return {
    "login": me,
    "avatarUrl": dig(review_raw, "viewer", "avatarUrl"),
    "fetchedAt": time.time(),
    "rateLimit": low and {**low, "cost": sum(r["cost"] for r in limits)},
    "review": {
      # why: oldest wait on top. A re-review waits since its head commit (updatedAt if unknown);
      # createdAt covers a missing request event.
      "humans": sorted((p for p in review if not p["bot"]), key=lambda p: (
        p["requestedAt"] or (p["rereview"] and (p["headCommittedAt"] or p["updatedAt"])) or p["createdAt"] or ""
      )),
      "bots": desc((p for p in review if p["bot"]), "updatedAt"),
    },
    "mine": {k: [p for p in mine if bucket(p) == k] for k in EMPTY["mine"]},
    "merged": desc(prs(merged_raw), "mergedAt"),
    "truncated": {
      k: dig(raw, "search", "issueCount") for k, raw in raws.items()
      if (dig(raw, "search", "issueCount") or 0) > len(dig(raw, "search", "nodes") or [])
    },
  }


def demo_raws():
  now = datetime.datetime.now(datetime.timezone.utc)
  iso = lambda h: (now - datetime.timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M:%SZ")
  me = "lena-park"
  user = lambda login, kind="User": {"__typename": kind, "login": login, "avatarUrl": None}

  def pr(repo, number, title, author, hours, head, base="main", checks="SUCCESS", failed=(), decision="REVIEW_REQUIRED",
         reviews=(), pending=(), labels=(), threads=0, conflict=False, draft=False, merged=None, requested=None,
         size=(40, 12, 3), rereview=False, bot=False):
    owner, name = repo.split("/")
    node = {
      "number": number, "title": title, "url": f"https://github.com/{repo}/pull/{number}", "isDraft": draft,
      "createdAt": iso(hours + 30), "updatedAt": iso(hours), "mergedAt": merged and iso(merged),
      "additions": size[0], "deletions": size[1], "changedFiles": size[2], "reviewDecision": decision,
      "mergeable": "CONFLICTING" if conflict else "MERGEABLE", "mergeStateStatus": "DIRTY" if conflict else "CLEAN",
      "baseRefName": base, "headRefName": head, "headRefOid": "b2",
      "viewerLatestReview": {"commit": {"oid": "a1"}} if rereview else None,
      "repository": {"name": name, "owner": {"login": owner}, "nameWithOwner": repo, "defaultBranchRef": {"name": "main"}},
      "author": user(author, "Bot" if bot else "User"),
      "labels": {"nodes": [{"name": n, "color": c} for n, c in labels]},
      "comments": {"totalCount": 1 + threads},
      "reviewThreads": {"nodes": [{"isResolved": i >= threads, "comments": {"totalCount": 2}} for i in range(threads + 1)]},
      "commits": {"nodes": [{"commit": {"committedDate": iso(hours), "statusCheckRollup": checks and {
        "state": checks, "contexts": {"nodes": [{"name": f, "conclusion": "FAILURE"} for f in failed]},
      }}}]},
      "reviewRequests": {"nodes": [{"requestedReviewer": (
        {"__typename": "Team", "slug": p[5:], "name": p[5:]} if p.startswith("team:") else user(p)
      )} for p in pending]},
      "latestReviews": {"nodes": [{"state": s, "author": user(l)} for l, s in reviews]},
    }
    if requested is not None:
      who = user(me) if me in pending else {"__typename": "Team"}
      node["timelineItems"] = {"nodes": [{"createdAt": iso(requested), "requestedReviewer": who}]}
    return node

  raw = lambda *ns: {
    "viewer": {"login": me, "avatarUrl": None}, "rateLimit": {"cost": 1, "remaining": 4987, "resetAt": iso(-0.5)},
    "search": {"issueCount": len(ns), "nodes": list(ns)},
  }
  return (
    raw(
      pr("acme/api", 482, "Add idempotency keys to payment webhooks", "diego-m", 5, "feat/webhook-idempotency",
         pending=(me, "tom-h"), labels=(("payments", "1d76db"),), requested=26, size=(312, 48, 9)),
      pr("acme/web", 1290, "Migrate settings page to the new form components", "priya-k", 2, "refactor/settings-forms",
         checks="PENDING", pending=(me,), labels=(("frontend", "fbca04"),), requested=3, size=(520, 610, 22)),
      pr("octo-labs/infra", 77, "Bump Postgres to 16 in staging", "tom-h", 9, "chore/pg16-staging",
         pending=("team:platform",), reviews=(("yuki-s", "APPROVED"),), labels=(("infra", "5319e7"),), requested=50),
      pr("acme/api", 475, "Bump requests from 2.32.3 to 2.32.4", "dependabot", 8, "dependabot/pip/requests-2.32.4",
         pending=(me,), labels=(("dependencies", "0366d6"),), requested=8, size=(1, 1, 1), bot=True),
      pr("octo-labs/docs", 210, "Update dependency astro to v5.4", "renovate", 30, "renovate/astro-5.x", checks="FAILURE",
         failed=("build",), pending=(me,), requested=30, size=(14, 14, 2), bot=True),
    ),
    raw(
      pr("acme/api", 488, "Retry failed invoice exports with backoff", me, 1, "fix/invoice-export-retry", checks="FAILURE",
         failed=("test (3.12)", "lint"), reviews=(("diego-m", "COMMENTED"),), size=(96, 20, 4)),
      pr("acme/web", 1285, "Fix timezone drift in the activity feed", me, 20, "fix/feed-timezone", conflict=True,
         decision="APPROVED", reviews=(("priya-k", "APPROVED"),)),
      pr("octo-labs/infra", 80, "Split Terraform state per environment", me, 28, "refactor/tf-state-split",
         decision="CHANGES_REQUESTED", reviews=(("tom-h", "CHANGES_REQUESTED"),), threads=3, size=(210, 180, 14)),
      pr("acme/api", 484, "Add audit log table", me, 6, "feat/audit-log", decision="APPROVED",
         reviews=(("diego-m", "APPROVED"), ("yuki-s", "APPROVED")), labels=(("db", "c2e0c6"),), size=(88, 0, 3)),
      pr("acme/api", 486, "Audit log: write events from the billing service", me, 4, "feat/audit-log-billing",
         base="feat/audit-log", pending=("diego-m",), size=(143, 9, 6)),
      pr("octo-labs/docs", 212, "Document the new deploy flow", me, 12, "docs/deploy-flow", checks="PENDING",
         decision=None, pending=("team:docs",), labels=(("docs", "0075ca"),), size=(64, 5, 2)),
      pr("acme/web", 1293, "Prototype keyboard shortcuts", me, 3, "spike/shortcuts", draft=True, decision=None),
    ),
    raw(
      pr("acme/api", 479, "Speed up CSV import by streaming rows", me, 20, "perf/csv-stream", decision="APPROVED",
         merged=20, reviews=(("diego-m", "APPROVED"),)),
      pr("octo-labs/infra", 74, "Add uptime alerts for the API", me, 70, "feat/uptime-alerts", decision="APPROVED",
         merged=70, reviews=(("tom-h", "APPROVED"),)),
    ),
    raw(
      pr("acme/web", 1271, "Cache feature flags at the edge", "yuki-s", 1, "feat/edge-flags", decision="CHANGES_REQUESTED",
         reviews=((me, "CHANGES_REQUESTED"),), rereview=True, size=(77, 31, 5)),
      pr("acme/api", 460, "Rename billing cycle fields", "diego-m", 40, "chore/billing-names", reviews=((me, "COMMENTED"),)),
    ),
  )


def fetch():
  if DEMO:
    return build(*demo_raws())
  base = "is:pr is:open archived:false"
  # why: GitHub reads merged:>=DATE in UTC; a local date would shift the window near midnight.
  since = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=7)).strftime("%Y-%m-%d")
  # why: one combined query runs the searches serially and hits GitHub's ~10s timeout (502);
  # parallel calls finish in the time of the slowest.
  with ThreadPoolExecutor(4) as pool:
    jobs = [
      # why: when results overflow `first`, keep the freshest; search has no sort by merge date.
      pool.submit(search, f"{base} review-requested:@me sort:updated-desc", 100, True),
      pool.submit(search, f"{base} author:@me sort:updated-desc", 100),
      pool.submit(search, f"is:pr author:@me is:merged merged:>={since} sort:updated-desc", 20),
      pool.submit(search, f"{base} reviewed-by:@me -author:@me sort:updated-desc", 100),
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
# why: md is built here per request because the cache is shared by clients asking in different languages.
def view(d, hidden, lang="en"):
  counts = {}
  for path in LISTS:
    for p in dig(d, *path) or []:
      counts[p["repo"]] = counts.get(p["repo"], 0) + 1
  keep = lambda *path: [{**p, "md": md_line(p, lang)} for p in dig(d, *path) or [] if p["repo"] not in hidden]
  return {
    **d,
    "review": {"humans": keep("review", "humans"), "bots": keep("review", "bots")},
    "mine": {k: keep("mine", k) for k in EMPTY["mine"]},
    "merged": keep("merged"),
    "repos": [{"repo": r, "count": counts.get(r, 0), "hidden": r in hidden} for r in sorted(set(counts) | hidden)],
  }


def data(force=False, lang="en"):
  with _lock:
    age = time.time() - _cache["at"]
    if (force and age >= FORCE_MIN_SECONDS) or age > CACHE_SECONDS:
      try:
        _cache.update(data=fetch(), error=None, errorKind=None)
      except Exception as e:  # why: a network blip keeps showing the last good snapshot, flagged.
        _cache.update(error=str(e), errorKind=error_kind(e))
      _cache["at"] = time.time()
    return view({**EMPTY, **(_cache["data"] or {}), "error": _cache["error"], "errorKind": _cache["errorKind"]}, hidden_repos(), lang)


def pick_lang(qs):
  lang = (qs.get("lang") or [""])[0]
  return lang if lang in STRINGS else LANG


MISSING_FRONTEND = "<!doctype html><meta charset=utf-8><title>Pudú</title><p>Missing frontend: there is no index.html next to server.py.".encode()


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
    qs = parse_qs(url.query)
    lang = pick_lang(qs)
    if url.path == "/api/data":
      self._send(200, "application/json", json.dumps(data(qs.get("force") == ["1"], lang)).encode())
    elif url.path == "/inbox.md":
      d = data(qs.get("force") == ["1"], lang)
      if not d["login"]:
        s = STRINGS[lang]
        self._send(503, "text/plain; charset=utf-8", (s["nodata"].format(d["error"] or s["fetching"]) + "\n").encode())
      else:
        only = {s for v in qs.get("s", []) for s in v.split(",") if s}
        self._send(200, "text/markdown; charset=utf-8", inbox_md(d, only, lang).encode())
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
    url = urlsplit(self.path)
    if url.path != "/api/hidden" or not self.headers.get("Content-Type", "").startswith("application/json"):
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
    self._send(200, "application/json", json.dumps(data(lang=pick_lang(parse_qs(url.query)))).encode())

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
  es, en = md_line(p, "es"), md_line(p)
  assert es.startswith("- t (o/r#1) u\n  @dependabot · feat/x -> feat/base · CI falló (lint, ci/x) · cambios pedidos")
  assert "conflicto" in es and "hilos sin resolver: 1" in es and "+1 -2 en 1 archivo ·" in es
  assert "pendiente de: team:devs, me" in es and es.endswith("actualizado hace " + ago(node["updatedAt"], "es"))
  assert en.startswith("- t (o/r#1) u\n  @dependabot · feat/x -> feat/base · CI failed (lint, ci/x) · changes requested")
  assert "conflict ·" in en and "unresolved threads: 1" in en and "+1 -2 in 1 file ·" in en and "pending: team:devs, me" in en
  assert en.endswith("updated " + ago(node["updatedAt"]) + " ago") and ago(node["updatedAt"]).endswith(" days")
  assert "re-review" not in en and "re-review: commits nuevos" in md_line({**p, "rereview": True}, "es")
  assert "in 3 files" in md_line({**p, "changedFiles": 3}) and "(untitled)" in md_line({**p, "title": None})
  assert slim(node, "nobody")["requestedAt"] == "T2"
  assert slim({k: v for k, v in node.items() if k != "timelineItems"}, "me")["requestedAt"] is None
  assert not slim({**node, "baseRefName": "develop"})["stacked"]
  bare = slim({"number": 9, "repository": None, "author": None, "commits": None, "comments": None})
  assert bare["author"] == "ghost" and bare["checks"] is None and bare["failedChecks"] == [] and not bare["stacked"]

  clean = {**p, "decision": None, "checks": "SUCCESS", "conflict": False, "mergeState": "CLEAN", "unresolvedThreads": 0, "reviews": []}
  assert bucket(p) == "returned" and bucket({**p, "draft": True}) == "draft"
  assert bucket(clean) == "waiting" and bucket({**clean, "decision": "APPROVED"}) == "approved"
  for bad in ({"decision": "CHANGES_REQUESTED"}, {"checks": "FAILURE"}, {"checks": "ERROR"}, {"conflict": True}, {"mergeState": "DIRTY"}, {"unresolvedThreads": 2}):
    assert bucket({**clean, **bad}) == "returned", bad
  assert bucket({**clean, "decision": "APPROVED", "unresolvedThreads": 2}) == "approved"
  assert bucket({**clean, "decision": "APPROVED", "checks": "FAILURE"}) == "returned"
  assert bucket({**clean, "reviews": [{"state": "APPROVED"}]}) == "approved"
  assert bucket({**clean, "reviews": [{"state": "APPROVED"}, {"state": "CHANGES_REQUESTED"}]}) == "returned"
  assert bucket({**clean, "decision": "REVIEW_REQUIRED", "reviews": [{"state": "APPROVED"}]}) == "waiting"

  human = {
    **node, "author": {"__typename": "User", "login": "x"}, "reviewDecision": None, "mergeable": "MERGEABLE",
    "mergeStateStatus": "CLEAN", "commits": None, "reviewThreads": None, "latestReviews": None,
  }
  raw = lambda *ns, rl=None: {"viewer": {"login": "me", "avatarUrl": "av"}, "rateLimit": rl, "search": {"nodes": list(ns)}}
  out = build(
    raw({**human, "number": 1}, {**human, "number": 2, "timelineItems": {"nodes": [{"createdAt": "T0", "requestedReviewer": {"login": "me"}}]}},
        node, None, {}, rl={"cost": 7, "remaining": 100, "resetAt": "R1"}),
    raw({**human, "number": 3, "isDraft": True}, {**human, "number": 4, "updatedAt": "2026-02-01T00:00:00Z", "reviewDecision": None},
        {**human, "number": 5, "reviewDecision": "APPROVED"}, {**human, "number": 8, "mergeable": "CONFLICTING"},
        rl={"cost": 6, "remaining": 90, "resetAt": "R2"}),
    raw({**human, "number": 6, "mergedAt": "2026-01-01T00:00:00Z"}, {**human, "number": 7, "mergedAt": "2026-03-01T00:00:00Z"}),
  )
  assert out["login"] == "me" and out["avatarUrl"] == "av"
  assert out["rateLimit"] == {"cost": 13, "remaining": 90, "resetAt": "R2"}
  assert [p["number"] for p in out["review"]["humans"]] == [2, 1] and [p["number"] for p in out["review"]["bots"]] == [1]
  assert {k: [p["number"] for p in v] for k, v in out["mine"].items()} == {"returned": [8], "approved": [5], "waiting": [4], "draft": [3]}
  assert [p["number"] for p in out["merged"]] == [7, 6]
  assert set(out) | {"error"} == set(EMPTY) | {"error"}
  v = view(out, {"o/r", "gone/repo"})
  assert v["review"]["humans"] == [] and v["mine"]["approved"] == [] and v["merged"] == []
  assert v["repos"] == [{"repo": "gone/repo", "count": 0, "hidden": True}, {"repo": "o/r", "count": 9, "hidden": True}]
  ves = view(out, set(), "es")
  assert [{k: x for k, x in p.items() if k != "md"} for p in ves["review"]["humans"]] == out["review"]["humans"]
  assert ves["review"]["humans"][0]["md"] == md_line(out["review"]["humans"][0], "es")
  assert inbox_md(view(out, {"o/r", "gone/repo"}, "es"), (), "es").startswith("# PRs de @me\n\nRepos ocultos: gone/repo, o/r.\n\n## Necesitan tu review (0)")
  md = inbox_md(ves, (), "es")
  assert md.startswith("# PRs de @me\n\n## Necesitan tu review (2)\n\n- t (o/r#2)") and "## Necesitan tu atención (1)\n\n- t (o/r#8)" in md
  assert inbox_md(ves, {"approved"}, "es") == f"# PRs de @me\n\n## Aprobados (1)\n\n{ves['mine']['approved'][0]['md']}\n"
  md = inbox_md(view(out, set()))
  assert md.startswith("# PRs for @me\n\n## Needs your review (2)\n\n- t (o/r#2)") and "## Needs your attention (1)\n\n- t (o/r#8)" in md
  assert "## Merged this week (2)" in md and "merged " in md
  print("ok")


if __name__ == "__main__":
  if sys.argv[1:] == ["--test"]:
    selftest()
  else:
    print(f"Pudú on http://localhost:{PORT}" + (" (demo data)" if DEMO else ""))
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
