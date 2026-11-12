"""Multiple accounts per agent: how to launch with one, and which one to use next.

An account is a separate login for the same CLI (a second Claude subscription, a free Z.ai
key, a work Codex plan). Each CLI has one switching mechanism, recorded in agents.json:

  env   point the CLI at another credential directory (CLAUDE_CONFIG_DIR, CODEX_HOME,
        XDG_DATA_HOME) or pass another API key from a named variable (SECRET_ENV)
  args  extra arguments (Hermes named profiles: `hermes -p <profile>`)

Log an account in once with its environment set, e.g.
`CLAUDE_CONFIG_DIR=~/.claude-work claude` then `/login`.
"""

import os

from orchestrator.config import accounts as load_accounts
from orchestrator.config import catalog
from orchestrator.state import STATE_DIR, read_json

LOW_QUOTA_PCT = 10   # skip an account with a window below this % remaining


def launch_spec(account, agent_meta=None):
    """Return (env_overrides, extra_args) that make the agent's CLI use this account."""
    meta = agent_meta or catalog()[account.agent]
    mechanism = meta.get("account")
    if not mechanism:
        return {}, []
    if mechanism["type"] == "env":
        if mechanism.get("value_is_secret"):
            if not account.secret_env:
                return {}, []
            value = os.environ.get(account.secret_env)
            return ({mechanism["var"]: value} if value else {}), []
        return ({mechanism["var"]: account.expanded_home} if account.home else {}), []
    if mechanism["type"] == "args" and account.home:
        return {}, [a.replace("{profile}", account.home) for a in mechanism["args"]]
    return {}, []


def _min_remaining(quota_accounts, account):
    """Lowest remaining % across this account's quota windows, or None if unknown."""
    lows = []
    for q in quota_accounts:
        if q.get("aao_account") == account.id or (q.get("aao_account") is None and q.get("provider") == account.agent
                                                  and account.home in (None, "")):
            lows += [w["remaining_pct"] for w in q.get("windows", []) if w.get("remaining_pct") is not None]
    return min(lows) if lows else None


def choose(agent, host="local", quotas=None, settings=None):
    """Pick the account to use for `agent` on `host`.

    Order: accounts with no window below LOW_QUOTA_PCT first; within those, free tier before
    paid before API (spend free tokens first, pay-per-token last), then PRIORITY, then the most
    quota left. Returns (account or None, reasons).
    """
    quotas = quotas if quotas is not None else (read_json(STATE_DIR / "quotas.json", {}) or {}).get("accounts", [])
    candidates = [a for a in load_accounts(settings) if a.agent == agent and host in a.hosts]
    if not candidates:
        return None, [f"no account configured for {agent} on {host}; the CLI's default login is used"]
    tier_rank = {"free": 0, "paid": 1, "api": 2}
    scored = []
    for a in candidates:
        remaining = _min_remaining(quotas, a)
        exhausted = remaining is not None and remaining < LOW_QUOTA_PCT
        scored.append(((exhausted, tier_rank.get(a.tier, 1), a.priority, -(remaining if remaining is not None else 50)), a, remaining))
    scored.sort(key=lambda item: item[0])
    best = scored[0]
    reasons = [f"{a.id}: tier {a.tier}, priority {a.priority}, "
               f"{'quota unknown' if r is None else f'{r:.0f}% left in tightest window'}" for _, a, r in scored]
    if best[0][0]:
        reasons.insert(0, "every account is below its quota threshold; using the least exhausted")
    return best[1], reasons
