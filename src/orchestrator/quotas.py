"""Subscription quota windows (5-hour, weekly, ...) for every signed-in agent account.

Sources, best first; a provider is taken from the first source that reports it:
  clanker-usage --json   usage-v1 documents (stable schema), 10 providers incl. Z.ai and Muse
  quota-cli usage --json Copilot, Grok, Kiro and others clanker does not cover
  Codex session logs     rate_limits recorded in ~/.codex/sessions (no network call)
"""

import glob
import json
import os
from datetime import datetime, timezone

from orchestrator.machines import _json, _run

ALIASES = {"antigravity": "agy", "antigravity-cli": "agy", "claude-code": "claude", "anthropic": "claude",
           "github copilot": "copilot", "opencode go": "opencode", "z.ai": "zcode", "zai": "zcode"}
LABEL_SECONDS = {"5h": 18000, "5-hour": 18000, "wk": 604800, "weekly": 604800, "week": 604800,
                 "mo": 2592000, "month": 2592000, "daily": 86400, "day": 86400}


def provider_id(name):
    key = (name or "").strip().lower()
    return ALIASES.get(key, key)


def _iso(epoch):
    if epoch is None:
        return None
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _window(name, used_pct, resets_at, seconds=None, scope=None):
    used = None if used_pct is None else max(0.0, min(100.0, float(used_pct)))
    return {"name": name, "seconds": seconds if seconds is not None else LABEL_SECONDS.get((name or "").lower()),
            "scope": scope, "used_pct": used, "remaining_pct": None if used is None else round(100 - used, 1),
            "resets_at": resets_at}


def from_clanker(documents):
    accounts = []
    for doc in documents or []:
        if doc.get("schema") != "usage-v1":
            continue
        accounts.append({
            "provider": provider_id(doc.get("provider")), "account": doc.get("account"), "plan": doc.get("plan"),
            "status": doc.get("status"), "error": doc.get("error"), "retrieved_at": doc.get("retrieved_at"),
            "windows": [_window(w.get("name"), w.get("used_pct"), w.get("resets_at"), w.get("seconds"), w.get("scope"))
                        for w in doc.get("windows") or []],
            "balance": doc.get("balance"), "source": "clanker-usage"})
    return accounts


def from_quota_cli(records):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    accounts = []
    for rec in records or []:
        windows = []
        for w in rec.get("windows") or []:
            remaining = w.get("remainingPercent")
            windows.append(_window(w.get("label"), None if remaining is None else 100 - remaining, _iso(w.get("resetAt"))))
        accounts.append({"provider": provider_id(rec.get("provider")), "account": rec.get("accountLabel"), "plan": None,
                         "status": "live" if windows else "unavailable", "error": rec.get("note"),
                         "retrieved_at": now, "windows": windows, "balance": None, "source": "quota-cli"})
    return accounts


def from_codex_logs(home=None):
    """Latest rate_limits event in the newest Codex session file."""
    home = home or os.path.expanduser("~")
    files = sorted(glob.glob(os.path.join(home, ".codex", "sessions", "**", "rollout-*.jsonl"), recursive=True),
                   key=os.path.getmtime, reverse=True)[:5]
    for path in files:
        latest = None
        with open(path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if '"rate_limits"' in line:
                    latest = line
        if not latest:
            continue
        entry = json.loads(latest)
        limits = (entry.get("payload") or {}).get("rate_limits") or {}
        windows = []
        for key in ("primary", "secondary"):
            w = limits.get(key)
            if isinstance(w, dict):
                minutes = w.get("window_minutes")
                name = {300: "5-hour", 10080: "weekly"}.get(minutes, f"{minutes}m")
                windows.append(_window(name, w.get("used_percent"), _iso(w.get("resets_at")),
                                       minutes * 60 if minutes else None))
        if windows:
            return [{"provider": "codex", "account": None, "plan": limits.get("plan_type"), "status": "live",
                     "error": None, "retrieved_at": entry.get("timestamp"), "windows": windows,
                     "balance": None, "source": "codex-logs"}]
    return []


def collect():
    accounts, errors = [], {}
    ok, out, err = _run(["clanker-usage", "--json", "--max-age", "300"], timeout=90)
    if out.strip():
        accounts += from_clanker(_json(out))
    if not ok:
        errors["clanker-usage"] = err
    ok, out, err = _run(["quota-cli", "usage", "--json"], timeout=90)
    if out.strip():
        have = {a["provider"] for a in accounts}
        accounts += [a for a in from_quota_cli(_json(out)) if a["provider"] not in have]
    if not ok:
        errors["quota-cli"] = err
    if "codex" not in {a["provider"] for a in accounts}:
        try:
            accounts += from_codex_logs()
        except (OSError, ValueError) as exc:
            errors["codex-logs"] = str(exc)
    accounts += collect_configured_accounts(errors)
    return {"schema": "quotas-v1", "accounts": accounts, "errors": errors,
            "collected_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}


# clanker-usage reads these variables to find a provider's credentials.
CLANKER_HOME_VARS = {"claude": "CLAUDE_CONFIG_DIR", "codex": "CODEX_HOME"}


def collect_configured_accounts(errors):
    """Quota for each account in .env whose CLI clanker-usage can read from a custom home."""
    import os

    from orchestrator.config import accounts as load_accounts

    out = []
    for account in load_accounts():
        var = CLANKER_HOME_VARS.get(account.agent)
        if not var or not account.home or "local" not in account.hosts:
            continue
        env = {**os.environ, var: account.expanded_home}
        ok, text, err = _run(["clanker-usage", account.agent, "json", "--max-age", "300"], timeout=90, env=env)
        docs = _json(text) if text.strip() else None
        if isinstance(docs, dict):
            docs = [docs]
        for doc in from_clanker(docs or []):
            doc["aao_account"] = account.id
            doc["plan"] = doc.get("plan") or account.plan
            out.append(doc)
        if not ok:
            errors[f"clanker-usage:{account.id}"] = err
    return out


def project(history, account_key, window_name, resets_at, now_epoch):
    """Linear run-out projection from snapshots of the same window period.

    history: list of (epoch, used_pct) for this account/window/resets_at.
    Returns seconds until 100% at the current burn rate, or None.
    """
    points = sorted(history)
    if len(points) < 2:
        return None
    (t0, u0), (t1, u1) = points[0], points[-1]
    if t1 <= t0 or u1 <= u0:
        return None
    rate = (u1 - u0) / (t1 - t0)
    return max(0.0, (100 - u1) / rate)
