"""Subscription quota windows (5-hour, weekly, ...) for every signed-in agent account.

Sources, best first; a provider is taken from the first source that reports it:
  clanker-usage --json   usage-v1 documents (stable schema), 10 providers incl. Z.ai and Muse
  quota-cli usage --json Copilot, Grok, Kiro and others clanker does not cover
  Agent status screens  Claude /usage and Codex /status through herdr
  Codex session logs     rate_limits recorded in ~/.codex/sessions (no network call)
"""

import glob
import json
import os
import platform
import shutil
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
    # Windows SSL monitors can point this variable at a named pipe, crashing
    # OpenSSL in external Python collectors just as they do in our own HTTP client.
    tool_env = {k: v for k, v in os.environ.items() if k.upper() != "SSLKEYLOGFILE"}
    ok, out, err = _run(["clanker-usage", "--json", "--max-age", "300"], timeout=90, env=tool_env)
    if out.strip():
        accounts += from_clanker(_json(out))
    if not ok:
        errors["clanker-usage"] = err
    ok, out, err = _run(["quota-cli", "usage", "--json"], timeout=90, env=tool_env)
    if out.strip():
        have = {a["provider"] for a in accounts}
        accounts += [a for a in from_quota_cli(_json(out)) if a["provider"] not in have]
    if not ok:
        errors["quota-cli"] = err
    accounts += collect_configured_accounts(errors)
    accounts = collect_cursor_windows(accounts, errors, tool_env)
    accounts = collect_screen_accounts(accounts, errors)
    if not any(a["provider"] == "codex" and not a.get("aao_account") and a.get("windows") for a in accounts):
        try:
            fallback = from_codex_logs()
            if fallback:
                accounts = [a for a in accounts if a["provider"] != "codex" or a.get("aao_account")] + fallback
        except (OSError, ValueError) as exc:
            errors["codex-logs"] = str(exc)
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
        env.pop("SSLKEYLOGFILE", None)
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


def collect_screen_accounts(accounts, errors):
    """Fill missing quota windows using installed agents' built-in status screens."""
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
            if not known[cli]["quota_screen"].get("prefer") and any(
                a.get("windows") and a.get("status") in ("live", "cached") for a in existing
            ):
                continue
            result = read_quota(cli, account)
            if result["accounts"]:
                accounts = [a for a in accounts if a not in existing] + result["accounts"]
            if result["error"]:
                errors[f"{cli}-screen" + (f":{aid}" if aid else "")] = result["error"]
    return accounts


def collect_cursor_windows(accounts, errors, tool_env):
    """Use Cursor CLI's native Windows config directory, not the IDE's token store.

    clanker-usage defaults to ~/.config/cursor even on Windows. Cursor CLI writes
    %APPDATA%/Cursor/auth.json there; pass XDG_CONFIG_HOME for this one provider.
    """
    existing = [a for a in accounts if a["provider"] == "cursor"]
    if any(a.get("windows") and a.get("status") in ("live", "cached") for a in existing):
        return accounts
    if platform.system() != "Windows" or not os.environ.get("APPDATA") or not shutil.which("clanker-usage"):
        return accounts
    if tool_env.get("XDG_CONFIG_HOME"):
        return accounts
    env = {**tool_env, "XDG_CONFIG_HOME": os.environ["APPDATA"]}
    ok, text, error = _run(["clanker-usage", "cursor", "json", "--max-age", "300"], timeout=90, env=env)
    doc = _json(text)
    rows = from_clanker([doc]) if isinstance(doc, dict) else []
    if rows:
        accounts = [a for a in accounts if a not in existing] + rows
    if not ok:
        errors["cursor-usage"] = error or (rows[0].get("error") if rows else "Cursor quota read failed")
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
