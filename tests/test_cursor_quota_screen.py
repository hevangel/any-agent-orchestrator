from datetime import datetime, timezone

import pytest

from orchestrator import quota_screens, quotas
from orchestrator.config import Settings

SCREEN = """Usage • Plus                                    Resets Nov 13
Monthly plan and on-demand usage
Category        Current             Usage
Included        13% used            █████░░░
  Auto          13% used            █████░░░
  API           0% used             ░░░░░░░░
On-Demand       $0                  ————————
No monthly limit
View in dashboard: cursor.com/dashboard?tab=usage
"""


def test_cursor_included_is_the_quota_and_breakdowns_are_not_duplicated():
    now = datetime(2026, 11, 11, 20, tzinfo=timezone.utc)
    (window,) = quota_screens.parse_screen("cursor", SCREEN, now)
    assert window["name"] == "billing cycle" and window["used_pct"] == 13
    assert window["remaining_pct"] == 87 and window["scope"] is None
    assert window["reset_text"] == "Resets Nov 13"
    # A date without a clock time: the machine's local midnight, flagged as an estimate.
    local = datetime(2026, 11, 13).astimezone()
    assert window["resets_at"] == local.astimezone(timezone.utc).isoformat() and window["reset_estimated"]


def test_compact_cursor_screen_and_zero_usage():
    windows = quota_screens.parse_screen("cursor", "Monthly plan and on-demand usage\nIncluded: 0% used\nAuto: 0% used\nAPI: 0% used\nOn-Demand: Disabled")
    assert len(windows) == 1 and windows[0]["used_pct"] == 0


def test_cursor_bounded_on_demand_allowance():
    included, demand = quota_screens.parse_screen("cursor", SCREEN.replace("On-Demand       $0", "On-Demand       $12.50 / $50"))
    assert demand["scope"] == "On-demand" and demand["used_pct"] == 25
    assert demand["used_usd"] == 12.5 and demand["limit_usd"] == 50


@pytest.mark.parametrize("screen", ["Loading usage data...", "Not logged in. Run /login first.",
                                  "Included 13% used", "Monthly plan and on-demand usage\nIncluded 101% used",
                                  "Personal spend for the current billing period\nCurrent cycle $20"])
def test_cursor_does_not_invent_a_quota(screen):
    assert quota_screens.parse_screen("cursor", screen) == []


@pytest.mark.parametrize("success", [True, False])
def test_quota_screen_replaces_quota_cli_with_fallback(monkeypatch, success):
    monkeypatch.setattr("orchestrator.config.Settings", lambda: Settings(env={"AAO_QUOTA_SCREEN_AGENTS": "cursor"}))
    monkeypatch.setattr("orchestrator.config.accounts", lambda: [])
    monkeypatch.setattr(quotas.shutil, "which", lambda _: "/agent")
    old = {"provider": "cursor", "status": "live", "source": "quota-cli", "windows": [{}]}
    native = {"provider": "cursor", "status": "live", "source": "cursor-screen", "windows": [{}]}
    calls = []

    def read(cli, account):
        calls.append(cli)
        return {"accounts": [native] if success else [], "error": None if success else "screen unavailable"}

    monkeypatch.setattr(quota_screens, "read_quota", read)
    errors = {}
    result = quotas.collect_screen_accounts([old], errors)
    assert calls == ["cursor"]
    assert result == ([native] if success else [old])
    assert bool(errors) != success
