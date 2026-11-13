from datetime import datetime, timezone

import pytest

from orchestrator import drivers, quota_screens, quotas
from orchestrator.config import Account, Settings

NOW = datetime(2026, 11, 11, 20, tzinfo=timezone.utc)
CLAUDE = """
Current session
████░░ 23% used
Resets 4pm (America/Vancouver)
Current week (all models)
██░░░░ 12.5% used
Resets 2026-11-18T20:00:00Z
Current week (Sonnet only)
░░░░░░ 0% used
Resets in 3d 2h
"""
CODEX = """
Context window: 95% left (1K used / 200K)
5h limit: [████░░] 80% left (resets 16:00)
Weekly limit: [██░░░░] 90.5% remaining (resets in 2d 3h)
"""


def test_claude_windows_scopes_and_reset_times():
    session, weekly, sonnet = quota_screens.parse_screen("claude", CLAUDE, NOW)
    assert (session["name"], session["used_pct"], session["remaining_pct"]) == ("5-hour", 23, 77)
    assert session["seconds"] == 18000 and session["resets_at"] is None
    assert session["reset_text"] == "Resets 4pm (America/Vancouver)"
    assert weekly["scope"] is None and weekly["used_pct"] == 12.5
    assert weekly["resets_at"] == "2026-11-18T20:00:00+00:00"
    assert sonnet["scope"] == "Sonnet only" and sonnet["used_pct"] == 0
    assert sonnet["resets_at"] == "2026-11-14T22:00:00+00:00"


def test_codex_remaining_is_not_used_and_context_is_ignored():
    windows = quota_screens.parse_screen("codex", "\x1b[32m" + CODEX + "\x1b[0m", NOW)
    assert [(w["name"], w["used_pct"]) for w in windows] == [("5-hour", 20), ("weekly", 9.5)]
    assert windows[0]["reset_text"] == "resets 16:00"
    assert windows[1]["resets_at"] == "2026-11-13T23:00:00+00:00"


@pytest.mark.parametrize("screen", ["Please log in", "/usage is only available for subscription plans",
                                  "Current session\nLoading...", "Current session\n101% used",
                                  "Current session\n-5% used",
                                  "Context window: 15% used", "Current week\n1000 tokens"])
def test_no_quota_is_invented(screen):
    assert quota_screens.parse_screen("claude", screen, NOW) == []


def fake_reader(monkeypatch, screen=CLAUDE, fail=None):
    calls = []

    def herdr(machine, *args, **kwargs):
        calls.append((machine, args))
        if args[:2] == ("workspace", "create"):
            return True, {"result": {"workspace": {"workspace_id": "own"}, "root_pane": {"pane_id": "own:p1"}}}, None
        if fail == args[:2]:
            return False, None, "test failure"
        if args[:2] == ("agent", "read"):
            return True, {"result": {"text": screen}}, None
        return True, {}, None

    monkeypatch.setattr(drivers, "_herdr", herdr)
    monkeypatch.setattr(drivers, "start_interactive", lambda *a, **kw: ("quota-reader", None, "", []))
    monkeypatch.setattr(quota_screens.time, "sleep", lambda _: None)
    return calls


def test_reader_uses_only_catalog_command_and_cleans_up_own_workspace(monkeypatch):
    calls = fake_reader(monkeypatch)
    result = quota_screens.read_quota("claude")
    assert result["error"] is None and result["accounts"][0]["source"] == "claude-screen"
    assert [args for _, args in calls if args[:2] == ("agent", "prompt")] == [("agent", "prompt", "quota-reader", "/usage")]
    assert calls[-1] == (None, ("workspace", "close", "own"))
    assert not any(args[:2] == ("agent", "list") for _, args in calls)


@pytest.mark.parametrize("failure", [("agent", "prompt"), ("agent", "read"), ("workspace", "close")])
def test_cleanup_on_failure_and_cleanup_error_is_visible(monkeypatch, failure):
    calls = fake_reader(monkeypatch, fail=failure)
    result = quota_screens.read_quota("claude")
    assert "test failure" in result["error"]
    assert calls[-1][1] == ("workspace", "close", "own")


def test_startup_login_is_not_answered(monkeypatch):
    calls = fake_reader(monkeypatch)
    monkeypatch.setattr(drivers, "start_interactive", lambda *a, **kw: (None, "login required", "Login", []))
    result = quota_screens.read_quota("claude")
    assert result["error"] == "login required" and not result["accounts"]
    assert not any(args[:2] == ("agent", "prompt") for _, args in calls)
    assert calls[-1][1] == ("workspace", "close", "own")


def test_timeout_is_not_success_and_closes_workspace(monkeypatch):
    calls = fake_reader(monkeypatch, screen="No quotas for API authentication")
    result = quota_screens.read_quota("claude", timeout=0)
    assert not result["accounts"] and "no readable quota windows" in result["error"]
    assert calls[-1][1] == ("workspace", "close", "own")


def test_custom_account_and_remote_machine(monkeypatch):
    calls = fake_reader(monkeypatch)
    account = Account("work", "claude", home="/home/me/.claude-work", plan="team")
    result = quota_screens.read_quota("claude", account, machine="devbox", cwd="/home/me/repo")
    assert all(machine == "devbox" for machine, _ in calls)
    assert "CLAUDE_CONFIG_DIR=/home/me/.claude-work" in calls[0][1]
    assert "/home/me/repo" in calls[0][1]
    assert result["accounts"][0]["aao_account"] == "work"
    assert result["accounts"][0]["account"] == "work"


def test_unsupported_agent_never_launches(monkeypatch):
    calls = fake_reader(monkeypatch)
    assert "no supported quota_screen" in quota_screens.read_quota("unknown")["error"]
    assert not calls


@pytest.mark.parametrize("status", ["live", "cached"])
def test_collector_fills_missing_accounts_without_replacing_live_sources(monkeypatch, status):
    monkeypatch.setattr("orchestrator.config.Settings", lambda: Settings(env={"AAO_QUOTA_SCREEN_AGENTS": "claude,codex"}))
    monkeypatch.setattr("orchestrator.config.accounts", lambda: [Account("work", "claude", home="/custom")])
    monkeypatch.setattr(quotas.shutil, "which", lambda _: "/bin/agent")
    calls = []

    def read(cli, account):
        calls.append((cli, account.id if account else None))
        return {"accounts": [{"provider": cli, "aao_account": account.id if account else None, "windows": [{}]}], "error": None}

    monkeypatch.setattr(quota_screens, "read_quota", read)
    live = {"provider": "codex", "windows": [{}], "status": status}
    failed = {"provider": "claude", "windows": [], "status": "unavailable"}
    errors = {}
    result = quotas.collect_screen_accounts([live, failed], errors)
    assert calls == [("claude", None), ("claude", "work")]
    assert live in result and failed not in result and not errors


def test_collector_screen_opt_out(monkeypatch):
    monkeypatch.setattr("orchestrator.config.Settings", lambda: Settings(env={"AAO_QUOTA_SCREEN_AGENTS": "none"}))
    monkeypatch.setattr(quota_screens, "read_quota", lambda *a: pytest.fail("must not launch"))
    assert quotas.collect_screen_accounts([], {}) == []
