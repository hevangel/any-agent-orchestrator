"""Read built-in quota commands in private herdr workspaces, without model prompts.

Commands and parser names belong in agents.json. Only the workspace created by this
reader is controlled or closed; existing agent sessions are never used.
"""

import re
import time
import uuid
from datetime import datetime, timedelta, timezone

from orchestrator import drivers
from orchestrator.accounts import launch_spec
from orchestrator.config import Settings, catalog
from orchestrator.quotas import _window, provider_id
from orchestrator.state import ROOT

ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07]*(?:\x07|\x1b\\)")
PERCENT = re.compile(r"(?<![\d.+-])(\d+(?:\.\d+)?)\s*%\s*(used|left|remaining)\b", re.I)
HEADINGS = {
    "claude": re.compile(r"current\s+(session|week)(?:\s*\(([^)]+)\))?", re.I),
    "codex": re.compile(r"\b(\d+(?:\.\d+)?\s*h|weekly|monthly)\s+limit\s*:", re.I),
}


def _reset(text, now):
    """Keep ambiguous local clock/date strings verbatim rather than guess a timezone."""
    match = re.search(r"\b(?:resets?|refreshes)\s+(?:at\s+)?(.+)", text, re.I)
    if not match:
        return None, None
    raw = match[0].strip(" │┃║")
    if raw.count(")") > raw.count("("):
        raw = raw[:-1]
    value = match[1].strip(" │┃║")
    relative = re.fullmatch(r"in\s+((?:\d+\s*[dhms]\s*)+)\)?", value, re.I)
    if relative:
        seconds = sum(int(n) * {"d": 86400, "h": 3600, "m": 60, "s": 1}[unit.lower()]
                      for n, unit in re.findall(r"(\d+)\s*([dhms])", relative[1], re.I))
        return (now + timedelta(seconds=seconds)).isoformat(), raw
    try:
        stamp = datetime.fromisoformat(value.rstrip(")").replace("Z", "+00:00"))
        if stamp.tzinfo is not None:
            return stamp.astimezone(timezone.utc).isoformat(), raw
    except ValueError:
        pass
    return None, raw


def parse_screen(parser, screen, now=None):
    """Parse only labelled subscription windows, never context/token usage percentages."""
    if parser == "antigravity":
        return parse_antigravity(screen, now)
    if parser == "kiro":
        return parse_kiro(screen, now)
    if parser == "cursor":
        return parse_cursor(screen, now)
    if parser not in HEADINGS:
        raise ValueError(f"unsupported quota screen parser: {parser}")
    now = now or datetime.now(timezone.utc)
    text = ANSI.sub("", screen).replace("\r", "")
    headings = list(HEADINGS[parser].finditer(text))
    windows = []
    for i, heading in enumerate(headings):
        block = text[heading.end():headings[i + 1].start() if i + 1 < len(headings) else len(text)]
        # A window's percentage belongs directly below/beside its label. Don't search
        # arbitrary later transcript text if the screen is still loading or unavailable.
        block = "\n".join(block.splitlines()[:4])
        pct = PERCENT.search(block)
        if not pct or float(pct[1]) > 100:
            continue
        used = float(pct[1]) if pct[2].lower() == "used" else 100 - float(pct[1])
        label = heading[1].lower().replace(" ", "")
        name = {"session": "5-hour", "week": "weekly", "5h": "5-hour"}.get(label, label)
        seconds = float(label[:-1]) * 3600 if label.endswith("h") else None
        scope = heading[2] if parser == "claude" else None
        if scope and scope.lower() == "all models":
            scope = None
        reset_at, reset_text = _reset(block[pct.end():], now)
        window = _window(name, used, reset_at, seconds, scope)
        if reset_text:
            window["reset_text"] = reset_text
        if not any((w["name"], w["scope"]) == (name, scope) for w in windows):
            windows.append(window)
    return windows


def parse_cursor(screen, now=None):
    """Cursor /usage: Included is the plan quota; Auto/API are its breakdown."""
    text = ANSI.sub("", screen).replace("\r", "")
    if "Monthly plan and on-demand usage" not in text:
        return []
    included = re.search(r"^\s*Included\s*:?\s*(\d+(?:\.\d+)?)%\s+used\b", text, re.M | re.I)
    if not included or float(included[1]) > 100:
        return []
    reset = re.search(r"\bResets [A-Za-z]+ \d{1,2}\b", text)
    window = _window("billing cycle", float(included[1]), None)
    if reset:
        window["reset_text"] = reset[0]
    windows = [window]
    demand = re.search(r"^\s*On-Demand\s*:?\s*\$([\d,.]+)\s*/\s*\$([\d,.]+)", text, re.M | re.I)
    if demand:
        try:
            used, limit = (float(v.replace(",", "")) for v in demand.groups())
        except ValueError:
            return windows
        if limit > 0:
            extra = _window("billing cycle", 100 * used / limit, None, scope="On-demand")
            extra.update(used_usd=used, limit_usd=limit)
            if reset:
                extra["reset_text"] = reset[0]
            windows.append(extra)
    return windows


def parse_kiro(screen, now=None):
    text = ANSI.sub("", screen)
    heading = re.search(r"Estimated Usage\s*\|\s*(resets on [^|\n]+)\|", text, re.I)
    credits = re.search(r"Credits\s*\(([\d,.]+)\s+of\s+([\d,.]+)\s+covered in plan\)", text, re.I)
    if not heading or not credits:
        return []
    try:
        used, total = (float(v.replace(",", "")) for v in credits.groups())
    except ValueError:
        return []
    if used < 0 or total <= 0:
        return []
    window = _window("monthly", 100 * used / total, None, seconds=2592000)
    window.update(reset_text=heading[1].strip(), used_credits=used, limit_credits=total)
    return [window]


def parse_antigravity(screen, now=None):
    now = now or datetime.now(timezone.utc)
    text = ANSI.sub("", screen).replace("\r", "")
    groups = list(re.finditer(r"^\s*(GEMINI MODELS|CLAUDE AND GPT MODELS|GEMINI \d+(?:\.\d+)? ARGON)\s*$", text, re.M))
    windows = []
    for i, group in enumerate(groups):
        body = text[group.end():groups[i + 1].start() if i + 1 < len(groups) else len(text)]
        scope = "Gemini" if group[1] == "GEMINI MODELS" else "Claude/GPT" if group[1] == "CLAUDE AND GPT MODELS" else "Argon"
        pattern = r"(Weekly|Five Hour) Limit" + (r"(?: Remaining)?" if scope == "Argon" else " Remaining")
        labels = list(re.finditer(pattern, body, re.I))
        for j, label in enumerate(labels):
            block = body[label.end():labels[j + 1].start() if j + 1 < len(labels) else len(body)]
            block = "\n".join(block.splitlines()[:4])
            pct = re.search(r"(?<![\d.+-])(\d+(?:\.\d+)?)\s*%", block)
            if not pct or float(pct[1]) > 100:
                continue
            reset_at, reset_text = _reset(block[pct.end():], now)
            window = _window("weekly" if label[1].lower() == "weekly" else "5-hour",
                             round(100 - float(pct[1]), 6), reset_at, scope=scope)
            if reset_text:
                window["reset_text"] = reset_text
            windows.append(window)
    return windows


def read_quota(cli, account=None, *, machine=None, cwd=None, timeout=30):
    """Return {accounts, error, screen}. Supports saved herdr machines and account homes.

    Startup handling uses the same literal startup_forms as the CLI driver. A login
    screen is never answered. The only submitted text is the catalog's slash command.
    """
    meta = catalog().get(cli, {})
    spec = meta.get("quota_screen") or {}
    command, parser = spec.get("command", ""), spec.get("parser")
    result = {"accounts": [], "error": None, "screen": ""}
    if not meta.get("herdr_kind") or parser not in {*HEADINGS, "antigravity", "kiro", "cursor"} or not re.fullmatch(r"/[a-z-]+", command):
        result["error"] = f"{cli}: no supported quota_screen in agents.json"
        return result
    if account and account.agent != cli:
        result["error"] = "quota account does not match the requested agent"
        return result
    env, args = launch_spec(account, meta) if account else ({}, [])
    create = ["workspace", "create", "--no-focus", "--label", f"aao-quota-{uuid.uuid4().hex[:8]}"]
    if cwd or not machine:
        create += ["--cwd", str(cwd or ROOT)]
    for key, value in env.items():
        create += ["--env", f"{key}={value}"]
    ok, data, error = drivers._herdr(machine, *create)
    if not ok:
        result["error"] = error or "could not create quota workspace"
        return result
    created = (data or {}).get("result") or {}
    workspace = (created.get("workspace") or {}).get("workspace_id")
    try:
        pane = created["root_pane"]["pane_id"]
        if not workspace:
            raise ValueError("herdr did not return a workspace ID")
        name, error, screen, _ = drivers.start_interactive(
            cli, machine, pane, args + spec.get("start_args", []) + drivers.permission_args(cli),
            trust_folders=Settings().get("AAO_TRUST_FOLDERS", "1") != "0")
        result["screen"] = screen
        if error:
            if not screen:
                _, data, _ = drivers._herdr(machine, "pane", "read", pane, "--source", "visible", "--lines", "160")
                result["screen"] = drivers._text(data)
            result["error"] = error
            return result
        # Slash commands don't trigger an LLM working state. Do not use agent
        # prompt --wait, whose activity gate would incorrectly report them stalled.
        ok, _, error = drivers._herdr(machine, "agent", "prompt", name, command)
        if not ok:
            result["error"] = error or "quota command submission failed"
            return result
        deadline = time.monotonic() + timeout
        now = datetime.now(timezone.utc)
        previous = None
        pages = iter(spec.get("read_pages", []))
        captured = []
        merged = {}
        while time.monotonic() < deadline:
            ok, data, error = drivers._herdr(machine, "agent", "read", name, "--source", "visible",
                                            "--lines", "160", timeout=min(10, timeout))
            if not ok:
                result["error"] = error or "quota screen read failed"
                return result
            screen = drivers._text(data)
            result["screen"] = screen
            windows = parse_screen(parser, screen, now)
            if windows and windows == previous:
                captured.append(screen)
                merged.update({(w["name"], w["scope"]): w for w in windows})
                page = next(pages, None)
                if page:
                    keys = page["keys"] * page.get("repeat", 1)
                    ok, _, error = drivers._herdr(machine, "agent", "send-keys", name, *keys)
                    if not ok:
                        result["error"] = error or "quota panel scrolling failed"
                        return result
                    previous = None
                    time.sleep(0.5)
                    continue
                result["screen"] = "\n\n--- next quota page ---\n\n".join(captured)
                row = {"provider": provider_id(cli), "account": account.id if account else None,
                       "plan": account.plan if account else None, "status": "live", "error": None,
                       "retrieved_at": now.isoformat(), "windows": list(merged.values()), "balance": None,
                       "source": f"{cli}-screen"}
                if parser == "kiro" and not row["plan"]:
                    plan = re.search(r"Estimated Usage\s*\|[^|\n]+\|\s*([^\n│]+)", screen)
                    row["plan"] = plan[1].strip() if plan else None
                if parser == "cursor" and not row["plan"]:
                    plan = re.search(r"^\s*Usage\s*[•·]\s*([^\n]+)", screen, re.M)
                    row["plan"] = re.split(r"\s{2,}Resets\b", plan[1])[0].strip() if plan else None
                if account:
                    row["aao_account"] = account.id
                result["accounts"] = [row]
                return result
            previous = windows
            time.sleep(0.5)
        result["error"] = f"{cli} {command}: no readable quota windows within {timeout}s (login, plan, or screen format may be unsupported)"
    except (KeyError, TypeError, ValueError) as exc:
        result["error"] = f"invalid quota screen response: {exc}"
    finally:
        if workspace:
            ok, _, error = drivers._herdr(machine, "workspace", "close", workspace)
            if not ok:
                result["error"] = f"{result['error'] or ''} quota workspace cleanup failed: {error}".strip()
    return result
