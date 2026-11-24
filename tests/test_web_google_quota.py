from datetime import datetime, timezone

from orchestrator import quotas, web_quota_google as g

NOW = datetime(2026, 11, 24, 18, 0, tzinfo=timezone.utc)

FLOW_MENU = """close
Someone
someone@example.com
Switch account
movie_filter_auto
1,910 Google Flow credits
Resets on 9 Dec at 16:08
Upgrade
Manage subscription
"""

GEMINI_USAGE = """Gemini
New chat
Some chat title
Usage limits
PRO
Your plan's limits determine how much you can use Gemini over time.
Updated just now
You've reached your usage limit
CA$139.99/month
Upgrade
Get extra usage
Current usage
info
100% used
Resets at 14:34
Weekly limit
Resets on 25 Nov at 16:34
8% used
Usage settings
Extra usage
Off
arrow_forward_ios
"""

ONE_ACTIVITY = """AI credits activity
AI credits let you keep using AI models and features when you hit a plan usage limit.
Learn more about how AI credits work
AI credits
1,250
add
Add
"""


def _local(*args):
    return datetime(*args).astimezone().astimezone(timezone.utc).isoformat()


def test_reset_text_is_local_time_and_rolls_forward():
    now = datetime(2026, 11, 24, 12, 0).astimezone()
    assert g.parse_reset("Resets on 9 Dec at 16:08", now) == _local(2026, 12, 9, 16, 8)
    assert g.parse_reset("Resets on Dec 9 at 4:08 PM", now) == _local(2026, 12, 9, 16, 8)
    assert g.parse_reset("Resets on 3 Jan at 09:00", now) == _local(2027, 1, 3, 9, 0)
    assert g.parse_reset("Resets at 14:34", now) == _local(2026, 11, 24, 14, 34)
    assert g.parse_reset("Resets at 11:00", now) == _local(2026, 11, 25, 11, 0)
    assert g.parse_reset("no reset here", now) is None


def test_flow_menu_credits_are_a_monthly_balance_without_a_percentage():
    row = g.flow_account({"ok": True, "badge": "PRO", "text": FLOW_MENU}, NOW)
    assert row["status"] == "live" and row["plan"] == "PRO" and row["provider"] == "flow"
    (w,) = row["windows"]
    assert w["name"] == "monthly credits" and w["seconds"] == 2592000
    assert w["used_pct"] is None and w["remaining"] == 1910 and w["reset_text"] == "Resets on 9 Dec at 16:08"
    assert w["resets_at"].startswith("2026-12-")
    assert row["balance"] == [{"amount": 1910, "currency": "Google Flow credits", "label": "1,910 Google Flow credits"}]


def test_flow_errors_are_reported_not_raised():
    assert g.flow_account({"ok": False, "reason": "Flow is not signed in"})["error"] == "Flow is not signed in"
    row = g.flow_account({"ok": True, "badge": "", "text": "Switch account\nUpgrade"})
    assert row["status"] == "unavailable" and "no Google Flow credits" in row["error"]


def test_gemini_usage_page_has_current_and_weekly_windows_and_credits():
    row = g.gemini_account({"ok": True, "text": GEMINI_USAGE, "credits": ONE_ACTIVITY}, NOW)
    assert row["status"] == "live" and row["plan"] == "PRO" and row["error"] is None
    current, weekly = row["windows"]
    assert (current["name"], current["used_pct"], current["reset_text"]) == ("current", 100.0, "Resets at 14:34")
    assert (weekly["name"], weekly["seconds"], weekly["used_pct"], weekly["remaining_pct"]) == ("weekly", 604800, 8.0, 92.0)
    assert weekly["reset_text"] == "Resets on 25 Nov at 16:34" and weekly["resets_at"].startswith("2026-11-2")
    assert [b["label"] for b in row["balance"]] == ["Extra usage off", "1,250 Google AI credits"]


def test_gemini_without_limits_or_credits_page():
    assert g.gemini_account({"ok": True, "text": "Gemini\nNew chat", "credits": ""})["error"]
    row = g.gemini_account({"ok": True, "text": GEMINI_USAGE.replace("Extra usage\nOff\n", ""), "credits": ""}, NOW)
    assert row["balance"] is None and len(row["windows"]) == 2


def test_collector_reads_every_signed_in_web_target(monkeypatch):
    from orchestrator import web_muse

    calls = []
    monkeypatch.setattr(web_muse, "has_profile", lambda: True)
    monkeypatch.setattr(web_muse, "quota", lambda: calls.append("muse") or {"provider": "muse", "windows": [{}], "error": None})
    monkeypatch.setattr(g, "has_profile", lambda: True)
    monkeypatch.setattr(g, "flow_quota", lambda: calls.append("flow") or {"provider": "flow", "windows": [{}], "error": None})
    monkeypatch.setattr(g, "gemini_quota", lambda: calls.append("gemini") or {"provider": "gemini", "windows": [], "error": "boom"})
    from orchestrator.config import Settings

    monkeypatch.setattr(Settings, "list", lambda self, key, default=None: default)  # ignore the real .env
    errors = {}
    rows = quotas.collect_web_accounts([], errors)
    assert calls == ["muse", "flow", "gemini"]
    assert [r["provider"] for r in rows] == ["muse", "flow"] and errors == {"gemini-web": "boom"}
