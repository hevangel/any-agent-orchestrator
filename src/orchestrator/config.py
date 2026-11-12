"""User configuration from `.env` (gitignored) and the process environment.

The process environment wins over `.env`. Names are case-insensitive after the prefix, so
`AAO_ACCOUNT_claude_work_PLAN` and `AAO_ACCOUNT_CLAUDE_WORK_PLAN` are the same key.
See `.env.example` for every setting.
"""

import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from orchestrator.state import ROOT

MODES = ("native", "docker", "herdr", "herdr-agent", "ssh", "herdr-remote")
TIERS = ("paid", "free", "api")


def load_dotenv(path=None):
    values = {}
    try:
        lines = Path(path or ROOT / ".env").read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.split(" #", 1)[0].strip().strip('"').strip("'")
        values[key.strip()] = value
    return values


class Settings:
    def __init__(self, env=None, dotenv=None):
        merged = dict(load_dotenv(dotenv) if env is None else {})
        merged.update(os.environ if env is None else env)
        self._raw = {k.upper(): v for k, v in merged.items()}

    def get(self, key, default=None):
        value = self._raw.get(key.upper())
        return default if value in (None, "") else value

    def list(self, key, default=()):
        value = self.get(key)
        return [v.strip() for v in value.split(",") if v.strip()] if value else list(default)

    def int(self, key, default):
        try:
            return int(self.get(key, default))
        except (TypeError, ValueError):
            return default


@dataclass
class Host:
    label: str
    ssh: str | None
    herdr: str | None
    clis: list
    os: str = "linux"


@dataclass
class Account:
    id: str
    agent: str
    plan: str = ""
    tier: str = "paid"
    home: str | None = None          # credential dir (CLAUDE_CONFIG_DIR, CODEX_HOME, ...) or Hermes profile name
    secret_env: str | None = None    # for API-key accounts: name of the env var that holds the key
    hosts: list = field(default_factory=lambda: ["local"])
    priority: int = 100
    monthly_usd: float | None = None

    @property
    def expanded_home(self):
        return os.path.expanduser(self.home) if self.home else None


def catalog():
    return {a["id"]: a for a in json.loads((ROOT / "agents.json").read_text(encoding="utf-8"))["agents"]}


def hosts(settings=None):
    s = settings or Settings()
    found = []
    for label in s.list("AAO_HOSTS"):
        key = f"AAO_HOST_{label}_"
        found.append(Host(label=label, ssh=s.get(key + "SSH", label), herdr=s.get(key + "HERDR"),
                          clis=s.list(key + "CLIS"), os=s.get(key + "OS", "linux")))
    return found


def local_clis(settings=None):
    s = settings or Settings()
    configured = s.list("AAO_LOCAL_CLIS")
    if configured:
        return configured
    return [aid for aid, a in catalog().items() if a.get("headless") and shutil.which(a["binary"])]


def accounts(settings=None):
    s = settings or Settings()
    found = []
    for aid in s.list("AAO_ACCOUNTS"):
        key = f"AAO_ACCOUNT_{aid}_"
        agent = s.get(key + "AGENT")
        if not agent:
            continue
        monthly = s.get(key + "MONTHLY_USD")
        found.append(Account(id=aid, agent=agent, plan=s.get(key + "PLAN", ""), tier=s.get(key + "TIER", "paid"),
                             home=s.get(key + "HOME"), secret_env=s.get(key + "SECRET_ENV"),
                             hosts=s.list(key + "HOSTS", ["local"]), priority=s.int(key + "PRIORITY", 100),
                             monthly_usd=float(monthly) if monthly else None))
    return found


def validate(settings=None):
    """Return a list of human-readable config problems."""
    s = settings or Settings()
    problems = []
    known = catalog()
    host_labels = {"local"} | {h.label for h in hosts(s)}
    for h in hosts(s):
        for cli in h.clis:
            if cli not in known:
                problems.append(f"host {h.label}: unknown cli {cli!r}")
    for a in accounts(s):
        if a.agent not in known:
            problems.append(f"account {a.id}: unknown agent {a.agent!r}")
            continue
        if a.tier not in TIERS:
            problems.append(f"account {a.id}: TIER must be one of {TIERS}")
        mechanism = known[a.agent].get("account")
        if mechanism is None and (a.home or a.secret_env):
            problems.append(f"account {a.id}: {a.agent} has no known way to switch accounts; only its default login is usable")
        if mechanism and mechanism.get("value_is_secret") and a.home:
            problems.append(f"account {a.id}: {a.agent} accounts use an API key; set SECRET_ENV to the name of the "
                            "variable holding it, never the key itself")
        for host in a.hosts:
            if host not in host_labels:
                problems.append(f"account {a.id}: unknown host {host!r}")
    for mode in s.list("AAO_E2E_MODES"):
        if mode not in MODES:
            problems.append(f"AAO_E2E_MODES: unknown mode {mode!r} (use {', '.join(MODES)})")
    return problems
