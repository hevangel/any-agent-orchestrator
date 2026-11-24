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


MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_MONTH = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
_CLOCK = r"(\d{1,2})(?::(\d{2}))?\s*([ap]\.?m\.?)?"


def local_tz():
    """Timezone of the machine whose CLI printed the screen (collection is local)."""
    return datetime.now().astimezone().tzinfo


def _zone(name):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:  # unknown zone or no tz database: leave the time unparsed
        return None


def _hour(hour, minute, meridiem):
    hour, minute = int(hour), int(minute or 0)
    if meridiem:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if meridiem[0].lower() == "p" else 0)
    return (hour, minute) if hour < 24 and minute < 60 else None


def clock_reset(value, now, tz=None):
    """Turn a screen's local reset label into (UTC ISO timestamp, estimated).

    Handles "4pm", "16:00", "12:38 AM on 24 Nov", "Nov 28, 8pm", "Dec 5" and
    "2026-12-01", with an optional trailing "(Area/City)" zone; otherwise the zone
    is this machine's. The next occurrence after `now` is chosen when the year or
    day is missing. A label without a clock time ("Dec 5") is taken as local
    midnight and marked estimated. Anything else returns (None, False).
    """
    value = value.strip().rstrip(".")
    zone = re.search(r"\(([A-Za-z_]+(?:/[A-Za-z_+-]+)+|UTC)\)\s*$", value)
    if zone:
        tz = timezone.utc if zone[1] == "UTC" else _zone(zone[1])
        if tz is None:
            return None, False
        value = value[:zone.start()].strip()
    tz = tz or local_tz()
    local_now = now.astimezone(tz)
    value = re.sub(r"^(?:on|at)\s+", "", value, flags=re.I).strip(" ,")
    year = month = day = clock = None
    if m := re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", value):
        year, month, day = map(int, m.groups())
    elif m := re.fullmatch(_CLOCK + r"(?:\s*,?\s*(?:on\s+)?(\d{1,2})\s+" + _MONTH + r"|\s*,?\s*(?:on\s+)?" + _MONTH + r"\s+(\d{1,2}))?(?:,?\s+(\d{4}))?", value, re.I):
        clock = m.group(1, 2, 3)
        if m[4]:
            day, month = int(m[4]), MONTHS[m[5][:3].lower()]
        elif m[6]:
            month, day = MONTHS[m[6][:3].lower()], int(m[7])
        year = int(m[8]) if m[8] else None
        if not m[2] and not m[3]:  # a bare number is not a clock time
            return None, False
    elif m := re.fullmatch(_MONTH + r"\s+(\d{1,2})(?:(?:,\s*|\s+)(\d{4}))?(?:\s*,?\s*(?:at\s+)?" + _CLOCK + r")?", value, re.I):
        month, day = MONTHS[m[1][:3].lower()], int(m[2])
        year = int(m[3]) if m[3] else None
        if m[4] and (m[5] or m[6]):
            clock = m.group(4, 5, 6)
    elif m := re.fullmatch(r"(\d{1,2})\s+" + _MONTH + r"(?:\s+(\d{4}))?", value, re.I):
        day, month = int(m[1]), MONTHS[m[2][:3].lower()]
        year = int(m[3]) if m[3] else None
    else:
        return None, False
    hour, minute = (0, 0)
    if clock:
        parsed = _hour(*clock)
        if parsed is None:
            return None, False
        hour, minute = parsed
    try:
        if month is None:  # clock only: today or tomorrow
            stamp = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if stamp <= local_now:
                stamp = (stamp.replace(tzinfo=None) + timedelta(days=1)).replace(tzinfo=tz)
        else:
            stamp = datetime(year or local_now.year, month, day, hour, minute, tzinfo=tz)
            if year is None and stamp < local_now - timedelta(days=1):
                stamp = stamp.replace(year=stamp.year + 1)
    except ValueError:
        return None, False
    return stamp.astimezone(timezone.utc).isoformat(), clock is None


def _reset(text, now):
    """Return (resets_at, raw label, estimated). Relative durations, ISO stamps and
    local clock/date labels become timestamps; anything else stays verbatim only."""
    match = re.search(r"\b(?:resets?|refreshes)\s+(?:at\s+)?(.+)", text, re.I)
    if not match:
        return None, None, False
    raw = match[0].strip(" │┃║")
    if raw.count(")") > raw.count("("):
        raw = raw[:-1]
    value = match[1].strip(" │┃║")
    if value.count(")") > value.count("("):
        value = value[:value.rindex(")")]
    relative = re.fullmatch(r"in\s+((?:\d+\s*[dhms]\s*)+)\)?", value, re.I)
    if relative:
        seconds = sum(int(n) * {"d": 86400, "h": 3600, "m": 60, "s": 1}[unit.lower()]
                      for n, unit in re.findall(r"(\d+)\s*([dhms])", relative[1], re.I))
        return (now + timedelta(seconds=seconds)).isoformat(), raw, False
    try:
        stamp = datetime.fromisoformat(value.rstrip(")").replace("Z", "+00:00"))
        if stamp.tzinfo is not None:
            return stamp.astimezone(timezone.utc).isoformat(), raw, False
    except ValueError:
        pass
    stamp, estimated = clock_reset(value, now)
    return stamp, raw, estimated


def _set_reset(window, reset):
    stamp, raw, estimated = reset
    if stamp:
        window["resets_at"] = stamp
    if raw:
        window["reset_text"] = raw
    if stamp and estimated:
        window["reset_estimated"] = True
    return window


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
        window = _set_reset(_window(name, used, None, seconds, scope), _reset(block[pct.end():], now))
        if not any((w["name"], w["scope"]) == (name, scope) for w in windows):
            windows.append(window)
    return windows


def parse_cursor(screen, now=None):
    """Cursor /usage: Included is the plan quota; Auto/API are its breakdown."""
    now = now or datetime.now(timezone.utc)
    text = ANSI.sub("", screen).replace("\r", "")
    if "Monthly plan and on-demand usage" not in text:
        return []
    included = re.search(r"^\s*Included\s*:?\s*(\d+(?:\.\d+)?)%\s+used\b", text, re.M | re.I)
    if not included or float(included[1]) > 100:
        return []
    reset = re.search(r"\bResets [A-Za-z]+ \d{1,2}\b", text)
    window = _window("billing cycle", float(included[1]), None)
    if reset:
        _set_reset(window, _reset(reset[0], now))
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
                _set_reset(extra, _reset(reset[0], now))
            windows.append(extra)
    return windows


def parse_kiro(screen, now=None):
    now = now or datetime.now(timezone.utc)
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
    window.update(used_credits=used, limit_credits=total)
    _set_reset(window, _reset(heading[1].strip(), now))
    return [window]


def parse_antigravity(screen, now=None):
    now = now or datetime.now(timezone.utc)
    text = ANSI.sub("", screen).replace("\r", "")
    heading = r"GEMINI MODELS|CLAUDE AND GPT MODELS|GEMINI \d+(?:\.\d+)? ARGON"
    groups = [(m.start(), m.end(), m[1]) for m in re.finditer(rf"^\s*({heading})\s*$", text, re.M)]
    # A scrolled page can start below a group's heading. Its "Models within this group:" line still
    # names the group, so a models line above the first heading starts that group.
    first = groups[0][0] if groups else len(text)
    orphan = re.search(r"^\s*Models within this group:\s*([^\n]+)", text[:first], re.M)
    if orphan:
        models = orphan[1]
        name = ("CLAUDE AND GPT MODELS" if re.search(r"\b(Claude|GPT)", models)
                else "GEMINI MODELS" if "Gemini" in models else None)
        if name:
            groups.insert(0, (orphan.start(), orphan.end(), name))
    windows = []
    for i, (_, end, name) in enumerate(groups):
        body = text[end:groups[i + 1][0] if i + 1 < len(groups) else len(text)]
        scope = "Gemini" if name == "GEMINI MODELS" else "Claude/GPT" if name == "CLAUDE AND GPT MODELS" else "Argon"
        pattern = r"(Weekly|Five Hour) Limit" + (r"(?: Remaining)?" if scope == "Argon" else " Remaining")
        labels = list(re.finditer(pattern, body, re.I))
        for j, label in enumerate(labels):
            block = body[label.end():labels[j + 1].start() if j + 1 < len(labels) else len(body)]
            block = "\n".join(block.splitlines()[:4])
            pct = re.search(r"(?<![\d.+-])(\d+(?:\.\d+)?)\s*%", block)
            if not pct or float(pct[1]) > 100:
                continue
            window = _set_reset(_window("weekly" if label[1].lower() == "weekly" else "5-hour",
                                        round(100 - float(pct[1]), 6), None, scope=scope),
                                _reset(block[pct.end():], now))
            windows.append(window)
    return windows


UNSENT_READS = 6  # ~3 s of screens with no quota before checking for an unsent command
MAX_RESUBMITS = 2
STUCK_READS = 4  # unchanged unreadable reads of a later page before keeping the pages already read


def command_unsent(screen, command):
    """True when `command` is still typed in the input with the slash-command menu open below it:
    an input line holding only the command, and a menu line with the command and its description."""
    text = ANSI.sub("", screen or "").replace("\r", "")
    cmd = re.escape(command)
    typed = re.search(rf"^[ \t>❯→›│]*{cmd}[ \t│]*$", text, re.M)
    menu = re.search(rf"^[ \t>❯→›│]*{cmd}[ \t]{{2,}}\S", text, re.M)
    return bool(typed and menu)


def read_quota(cli, account=None, *, machine=None, cwd=None, timeout=60):
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
        idle_reads = resubmits = same_reads = 0
        last_screen = None
        while time.monotonic() < deadline:
            ok, data, error = drivers._herdr(machine, "agent", "read", name, "--source", "visible",
                                            "--lines", "160", timeout=min(10, timeout))
            if not ok:
                result["error"] = error or "quota screen read failed"
                return result
            screen = drivers._text(data)
            result["screen"] = screen
            windows = parse_screen(parser, screen, now)
            # A later page that stays unchanged and unreadable ends the read with the pages already
            # read, rather than timing out and dropping them.
            same_reads = same_reads + 1 if merged and not windows and screen == last_screen else 0
            last_screen = screen
            stuck = same_reads >= STUCK_READS
            if (windows and windows == previous) or stuck:
                if windows:
                    captured.append(screen)
                    merged.update({(w["name"], w["scope"]): w for w in windows})
                page = None if stuck else next(pages, None)
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
            idle_reads = 0 if windows else idle_reads + 1
            if idle_reads >= UNSENT_READS and resubmits < MAX_RESUBMITS and command_unsent(screen, command):
                # The slash-command menu swallowed the Enter (seen with Cursor): the command still sits in
                # the input with its menu open. Press Enter once more; this only runs the same command.
                ok, _, error = drivers._herdr(machine, "agent", "send-keys", name, "enter")
                if not ok:
                    result["error"] = error or "quota command resubmission failed"
                    return result
                resubmits, idle_reads = resubmits + 1, 0
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
