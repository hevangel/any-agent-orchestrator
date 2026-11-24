"""Subscription quota windows (5-hour, weekly, ...) for every signed-in agent account.

Sources, best first:
  Agent status screens   each CLI's own /usage or /status through herdr (agents.json quota_screen):
                         Claude, Codex, Cursor, Kiro, Antigravity
  Copilot entitlement    GitHub's copilot_internal/user document read with the gh token: Copilot
  quota-cli usage --json Grok and others without a quota screen; replaced by a readable screen
  Codex session logs     rate_limits recorded in ~/.codex/sessions when no Codex screen was read
  Web usage pages        the site's own usage panel in the saved browser profile: Muse (web_muse.quota)
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
    accounts = collect_copilot_accounts(accounts, errors, tool_env)
    accounts = collect_web_accounts(accounts, errors)
    if not any(a["provider"] == "codex" and not a.get("aao_account") and a.get("windows") for a in accounts):
        try:
            fallback = from_codex_logs()
            if fallback:
                accounts = [a for a in accounts if a["provider"] != "codex" or a.get("aao_account")] + fallback
        except (OSError, ValueError) as exc:
            errors["codex-logs"] = str(exc)
    accounts = [a for a in accounts if a["provider"] not in EXCLUDED]
    attach_plan_prices(accounts, errors)
    return {"schema": "quotas-v1", "accounts": accounts, "errors": errors,
            "collected_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}


def attach_plan_prices(accounts, errors=None, settings=None):
    """Each account's subscription price from data/plan_prices.json (written by
    `aao prices plans update`), matched by the plan the quota screen reports or, failing that, the
    .env account PLAN of that agent. The dashboard divides it into a weekly value to show what the
    unused share of each window is worth. Accounts whose plan has no entry show no value."""
    from orchestrator import plan_prices
    from orchestrator.config import accounts as load_accounts

    plans = {}
    try:
        for account in load_accounts(settings):
            if account.plan:
                plans.setdefault(provider_id(account.agent), account.plan)
    except (OSError, ValueError) as exc:
        if errors is not None:
            errors["plan-prices"] = f".env accounts: {exc}"
    table = plan_prices.load()
    problems = plan_prices.validate(table)
    if problems and errors is not None:
        errors["plan-prices"] = "data/plan_prices.json: " + "; ".join(problems[:3])
    return plan_prices.attach(accounts, table, plans)


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


COPILOT_USER = "https://api.github.com/copilot_internal/user"
COPILOT_QUOTAS = {"premium_interactions": "Premium requests", "chat": "Chat", "completions": "Completions"}
COPILOT_SKUS = {"free_limited_copilot": "Free", "copilot_pro": "Pro", "copilot_pro_plus": "Pro+",
                "copilot_for_business": "Business", "copilot_enterprise": "Enterprise"}


def from_copilot_user(data, now=None):
    """Monthly windows from GitHub's Copilot entitlement document (the one the IDEs and the
    Copilot CLI read). Unlimited quotas and quotas the plan does not include are skipped."""
    now = now or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    reset = data.get("quota_reset_date_utc") or (f"{data['quota_reset_date']}T00:00:00Z" if data.get("quota_reset_date") else None)
    if reset:
        reset = datetime.fromisoformat(reset.replace("Z", "+00:00")).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    windows = []
    snapshots = data.get("quota_snapshots") or {}
    for key, scope in COPILOT_QUOTAS.items():
        snap = snapshots.get(key)
        if not isinstance(snap, dict) or snap.get("unlimited") or not snap.get("entitlement"):
            continue
        remaining = snap.get("percent_remaining")
        if remaining is None:
            continue
        window = _window("monthly", 100 - float(remaining), reset, seconds=2592000, scope=scope)
        limit, left = snap.get("entitlement"), snap.get("remaining", snap.get("quota_remaining"))
        window.update(limit=limit, remaining=left, used=None if left is None else limit - left,
                      reset_text="resets " + (data.get("quota_reset_date") or "monthly"))
        windows.append(window)
    sku = data.get("access_type_sku")
    return {"provider": "copilot", "account": data.get("login"), "plan": COPILOT_SKUS.get(sku, sku or data.get("copilot_plan")),
            "status": "live" if windows else "unavailable",
            "error": None if windows else "no limited Copilot quota on this plan",
            "retrieved_at": now, "windows": windows, "balance": None, "source": "copilot-api"}


def _github_token(env=None):
    ok, out, _ = _run(["gh", "auth", "token"], timeout=20, env=env) if shutil.which("gh") else (False, "", "")
    token = out.strip() if ok else ""
    return token or os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")


def collect_copilot_accounts(accounts, errors, env=None):
    """GitHub Copilot monthly quotas, read with the signed-in gh token (AAO_QUOTA_COPILOT=0 disables).
    A read replaces quota-cli's Copilot row; a failure leaves it in place."""
    from orchestrator.config import Settings
    from orchestrator.state import http_get_json

    if Settings().get("AAO_QUOTA_COPILOT", "1") == "0":
        return accounts
    token = _github_token(env)
    if not token:
        return accounts
    try:
        data = http_get_json(COPILOT_USER, {"Authorization": f"token {token}", "Accept": "application/json",
                                            "X-GitHub-Api-Version": "2025-04-01"}, timeout=20)
        row = from_copilot_user(data)
    except Exception as exc:  # network, 401/404 (no Copilot), or a changed document
        errors["copilot-api"] = str(exc)
        return accounts
    if row["windows"]:
        accounts = [a for a in accounts if a["provider"] != "copilot" or a.get("aao_account")] + [row]
    elif not any(a["provider"] == "copilot" for a in accounts):
        accounts = accounts + [row]
    return accounts


WEB_READERS = {"muse": ("orchestrator.web_muse", "quota", "has_profile"),
               # Flow and Gemini share the Google login's browser, so they are read one after the other.
               "flow": ("orchestrator.web_quota_google", "flow_quota", "has_profile"),
               "gemini": ("orchestrator.web_quota_google", "gemini_quota", "has_profile")}


def collect_web_accounts(accounts, errors):
    """Web subscriptions read from the site's own usage page in the saved browser profile
    (AAO_QUOTA_WEB_TARGETS, default muse,flow,gemini; `none` disables). Targets never logged in are skipped."""
    import importlib

    from orchestrator.config import Settings

    for target in Settings().list("AAO_QUOTA_WEB_TARGETS", list(WEB_READERS)):
        if target == "none":
            break
        if target not in WEB_READERS:
            errors[f"{target}-web"] = "no web quota reader"
            continue
        module, read, signed_in = WEB_READERS[target]
        driver = importlib.import_module(module)
        if not getattr(driver, signed_in)():
            continue
        try:
            row = getattr(driver, read)()
        except Exception as exc:  # a broken page must not stop the other collectors
            row = {"provider": target, "error": str(exc), "windows": []}
        if row.get("windows"):
            accounts = [a for a in accounts if a["provider"] != row["provider"]] + [row]
        if row.get("error"):
            errors[f"{target}-web"] = row["error"]
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
