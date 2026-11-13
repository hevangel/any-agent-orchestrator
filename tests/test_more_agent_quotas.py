import json
from datetime import datetime, timezone

import pytest

from orchestrator import drivers, quota_screens, quotas

NOW = datetime(2026, 11, 12, tzinfo=timezone.utc)
AGY_TOP = """Models & Quota
GEMINI MODELS
  Models within this group: Gemini Flash, Gemini Pro
  Weekly Limit Remaining
    [████] 99.44%
    Refreshes in 142h 1m
  Five Hour Limit Remaining
    [████] 100.00%
    Quota available
CLAUDE AND GPT MODELS
  Weekly Limit Remaining
    [████] 100.00%
    Quota available
  Five Hour Limit Remaining
    [████] 90.00%
    Refreshes in 2h 10m
"""
AGY_BOTTOM = """GEMINI 4 ARGON
  Gemini 4 Argon Weekly Limit
    [████] 81.25%
    Refreshes in 15h 30m
"""
KIRO = """Estimated Usage | resets on 2026-12-01 | KIRO PRO MAX
Credits (4,675.60 of 5,000 covered in plan)
[████] 93.5%
Overages
Disabled
"""


def test_kiro_credits_are_monthly_usage_not_context():
    (window,) = quota_screens.parse_screen("kiro", KIRO)
    assert window["name"] == "monthly"
    assert window["used_pct"] == pytest.approx(93.512)
    assert window["remaining_pct"] == 6.5
    assert window["used_credits"] == 4675.6 and window["limit_credits"] == 5000
    assert window["resets_at"] is None and window["reset_text"] == "resets on 2026-12-01"
    assert not quota_screens.parse_screen("kiro", "Context 93.5% used")
    assert not quota_screens.parse_screen("kiro", KIRO.replace("5,000", "0"))


def test_antigravity_groups_remaining_and_relative_reset():
    windows = quota_screens.parse_screen("antigravity", AGY_TOP + AGY_BOTTOM, NOW)
    assert len(windows) == 5
    assert [(w["scope"], w["name"], w["used_pct"]) for w in windows] == [
        ("Gemini", "weekly", 0.56), ("Gemini", "5-hour", 0),
        ("Claude/GPT", "weekly", 0), ("Claude/GPT", "5-hour", 10), ("Argon", "weekly", 18.75)]
    assert windows[0]["resets_at"] == "2026-11-17T22:01:00+00:00"
    assert windows[1]["resets_at"] is None
    assert not quota_screens.parse_screen("antigravity", "GEMINI MODELS\nContext remaining 25%")


def test_panel_reader_captures_all_pages_and_closes_only_its_workspace(monkeypatch):
    calls, page = [], [AGY_TOP]

    def herdr(machine, *args, **kwargs):
        calls.append(args)
        if args[:2] == ("workspace", "create"):
            return True, {"result": {"workspace": {"workspace_id": "own"}, "root_pane": {"pane_id": "own:p1"}}}, None
        if args[:2] == ("agent", "read"):
            return True, {"result": {"text": page[0]}}, None
        if args[:2] == ("agent", "send-keys"):
            page[0] = AGY_BOTTOM
        return True, {}, None

    monkeypatch.setattr(drivers, "_herdr", herdr)
    monkeypatch.setattr(drivers, "start_interactive", lambda *a, **kw: ("reader", None, "", []))
    monkeypatch.setattr(quota_screens.time, "sleep", lambda _: None)
    result = quota_screens.read_quota("antigravity")
    assert not result["error"] and result["accounts"][0]["provider"] == "agy"
    assert len(result["accounts"][0]["windows"]) == 5
    assert ("agent", "send-keys", "reader", *("down",) * 20) in calls
    assert calls[-1] == ("workspace", "close", "own")


def test_kiro_launches_chat_before_permission_arguments(monkeypatch):
    launches = []

    def herdr(machine, *args, **kwargs):
        if args[:2] == ("workspace", "create"):
            return True, {"result": {"workspace": {"workspace_id": "own"}, "root_pane": {"pane_id": "own:p1"}}}, None
        return True, {"result": {"text": KIRO}}, None

    def start(cli, machine, pane, args, **kwargs):
        launches.append(args)
        return "reader", None, "", []

    monkeypatch.setattr(drivers, "_herdr", herdr)
    monkeypatch.setattr(drivers, "start_interactive", start)
    monkeypatch.setattr(quota_screens.time, "sleep", lambda _: None)
    result = quota_screens.read_quota("kiro")
    assert launches[0][0] == "chat" and "--trust-workspace" in launches[0]
    assert result["accounts"][0]["plan"] == "KIRO PRO MAX"


@pytest.mark.parametrize("status", ["live", "cached"])
def test_windows_cursor_fallback_uses_cli_config_without_mutating_environment(monkeypatch, status):
    monkeypatch.setattr(quotas.platform, "system", lambda: "Windows")
    monkeypatch.setattr(quotas.shutil, "which", lambda _: "/collector")
    monkeypatch.setenv("APPDATA", "C:/native-config")
    captured = []

    def run(args, **kwargs):
        captured.append((args, kwargs["env"]))
        return True, json.dumps({"schema": "usage-v1", "provider": "cursor", "status": status,
                                 "windows": [{"name": "billing cycle", "used_pct": 12.5}]}), None

    monkeypatch.setattr(quotas, "_run", run)
    tool_env = {"PATH": "/bin"}
    old = {"provider": "cursor", "status": "unavailable", "windows": []}
    result = quotas.collect_cursor_windows([old], {}, tool_env)
    assert captured[0][1]["XDG_CONFIG_HOME"] == "C:/native-config"
    assert "XDG_CONFIG_HOME" not in tool_env
    assert result[0]["windows"][0]["remaining_pct"] == 87.5
    assert old not in result
    assert quotas.collect_cursor_windows(result, {}, tool_env) == result
    assert len(captured) == 1
    assert quotas.collect_cursor_windows([], {}, {"XDG_CONFIG_HOME": "/explicit"}) == []
    assert len(captured) == 1


def test_external_collectors_never_receive_sslkeylogfile(monkeypatch):
    monkeypatch.setenv("SSLKEYLOGFILE", "unsafe-pipe")
    monkeypatch.setattr(quotas, "collect_configured_accounts", lambda errors: [])
    monkeypatch.setattr(quotas, "collect_cursor_windows", lambda rows, errors, env: rows)
    monkeypatch.setattr(quotas, "collect_screen_accounts", lambda rows, errors: rows)
    monkeypatch.setattr(quotas, "from_codex_logs", lambda: [])
    captured = []

    def run(args, **kwargs):
        captured.append(kwargs["env"])
        return True, "[]", None

    monkeypatch.setattr(quotas, "_run", run)
    quotas.collect()
    assert len(captured) == 2 and all("SSLKEYLOGFILE" not in env for env in captured)
