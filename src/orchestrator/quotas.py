"""Subscription quota windows (5-hour, weekly, ...) for every signed-in agent account.

Sources, best first:
  Agent status screens   each CLI's own /usage or /status through herdr (agents.json quota_screen):
                         Claude, Codex, Cursor, Kiro, Antigravity
  quota-cli usage --json Copilot, Grok and others without a quota screen; replaced by a readable screen
  Codex session logs     rate_limits recorded in ~/.codex/sessions when no Codex screen was read
"""

import glob
import json
import os
import shutil
from datetime import datetime, timezone

from orchestrator.machines import _json, _run

ALIASES = {"antigravity": "agy", "antigravity-cli": "agy", "claude-code": "claude", "anthropic": "claude",
           "github copilot": "copilot", "opencode go": "opencode", "z.ai": "zcode", "zai": "zcode"}
# Providers never shown on the Quotas tab, whichever source reports them.
EXCLUDED = {"deepseek", "devin"}
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
    # Windows SSL monitors can point this variable at a named pipe, crashing
    # OpenSSL in external Python collectors just as they do in our own HTTP client.
    tool_env = {k: v for k, v in os.environ.items() if k.upper() != "SSLKEYLOGFILE"}
    ok, out, err = _run(["quota-cli", "usage", "--json"], timeout=90, env=tool_env)
    if out.strip():
        accounts += from_quota_cli(_json(out))
    if not ok:
        errors["quota-cli"] = err
    accounts = collect_screen_accounts(accounts, errors)
    if not any(a["provider"] == "codex" and not a.get("aao_account") and a.get("windows") for a in accounts):
        try:
            fallback = from_codex_logs()
            if fallback:
                accounts = [a for a in accounts if a["provider"] != "codex" or a.get("aao_account")] + fallback
        except (OSError, ValueError) as exc:
            errors["codex-logs"] = str(exc)
    accounts = [a for a in accounts if a["provider"] not in EXCLUDED]
    return {"schema": "quotas-v1", "accounts": accounts, "errors": errors,
            "collected_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}


def collect_screen_accounts(accounts, errors):
    """Read each installed agent's built-in quota screen; a readable screen replaces quota-cli rows."""
    from orchestrator.config import Settings, catalog
    from orchestrator.config import accounts as load_accounts
    from orchestrator.quota_screens import read_quota

    known = catalog()
    enabled = Settings().list("AAO_QUOTA_SCREEN_AGENTS", [k for k, m in known.items() if m.get("quota_screen")])
    if enabled == ["none"]:
        return accounts
    configured = load_accounts()
    for cli in enabled:
        if cli not in known or not known[cli].get("quota_screen"):
            errors[f"{cli}-screen"] = "no supported quota_screen in agents.json"
            continue
        if not shutil.which(known[cli]["binary"]):
            continue
        candidates = [None] + [a for a in configured if a.agent == cli and a.home and "local" in a.hosts and a.tier != "api"]
        for account in candidates:
            aid = account.id if account else None
            existing = [a for a in accounts if a["provider"] == provider_id(cli) and a.get("aao_account") == aid]
            result = read_quota(cli, account)
            if result["accounts"]:
                accounts = [a for a in accounts if a not in existing] + result["accounts"]
            if result["error"]:
                errors[f"{cli}-screen" + (f":{aid}" if aid else "")] = result["error"]
    return accounts


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
