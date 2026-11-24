from datetime import datetime, timedelta, timezone

import pytest

from orchestrator import quota_screens, quotas

NOW = datetime(2026, 11, 24, 3, 20, tzinfo=timezone.utc)  # 2026-11-23 19:20 at UTC-8
PST = timezone(timedelta(hours=-8))


@pytest.mark.parametrize("label, expected, estimated", [
    ("12:20am (America/Los_Angeles)", "2026-11-24T08:20:00+00:00", False),   # tomorrow, zone from the label
    ("Nov 28, 8pm (America/Los_Angeles)", "2026-11-29T04:00:00+00:00", False),
    ("12:38 AM on 24 Nov", "2026-11-24T08:38:00+00:00", False),             # Codex, machine zone
    ("9:13 PM on 29 Nov", "2026-11-30T05:13:00+00:00", False),
    ("16:00", "2026-11-25T00:00:00+00:00", False),  # already past today: tomorrow
    ("8pm", "2026-11-24T04:00:00+00:00", False),                            # later today
    ("on 2026-12-01", "2026-12-01T08:00:00+00:00", True),                    # Kiro: date only
    ("Dec 5", "2026-12-05T08:00:00+00:00", True),                            # Cursor: date only
    ("Jan 3", "2027-01-03T08:00:00+00:00", True),                            # no year: next occurrence
    ("Nov 23", "2026-11-23T08:00:00+00:00", True),                           # earlier today is still today
])
def test_clock_reset_labels_become_timestamps(label, expected, estimated):
    assert quota_screens.clock_reset(label, NOW, PST) == (expected, estimated)


@pytest.mark.parametrize("label", ["soon", "13", "25:00", "13pm", "Feb 30", "4pm (Mars/Olympus_Mons)", "next month"])
def test_unreadable_reset_labels_stay_verbatim(label):
    assert quota_screens.clock_reset(label, NOW, PST) == (None, False)


def test_reset_keeps_raw_text_and_zone_parenthesis():
    stamp, raw, estimated = quota_screens._reset("Resets 4pm (America/Los_Angeles)", NOW)
    assert stamp == "2026-11-25T00:00:00+00:00" and raw == "Resets 4pm (America/Los_Angeles)" and not estimated


USER = {
    "login": "octo", "access_type_sku": "free_limited_copilot", "copilot_plan": "individual",
    "quota_reset_date": "2026-12-01", "quota_reset_date_utc": "2026-12-01T00:00:00.000Z",
    "quota_snapshots": {
        "chat": {"percent_remaining": 13.5, "remaining": 27, "entitlement": 200, "unlimited": False},
        "completions": {"percent_remaining": 94.0, "remaining": 1880, "entitlement": 2000, "unlimited": False},
        "premium_interactions": {"percent_remaining": 0.0, "remaining": 0, "entitlement": 0, "unlimited": False},
    },
}


def test_copilot_monthly_windows_from_entitlement_document():
    row = quotas.from_copilot_user(USER, now="2026-11-24T03:20:00Z")
    assert (row["provider"], row["account"], row["plan"], row["status"]) == ("copilot", "octo", "Free", "live")
    assert [(w["scope"], w["name"], w["used_pct"]) for w in row["windows"]] == [("Chat", "monthly", 86.5), ("Completions", "monthly", 6.0)]
    chat = row["windows"][0]
    assert chat["resets_at"] == "2026-12-01T00:00:00Z" and (chat["used"], chat["remaining"], chat["limit"]) == (173, 27, 200)


def test_copilot_unlimited_plan_reports_no_invented_quota():
    user = dict(USER, access_type_sku="copilot_pro", quota_snapshots={
        "chat": {"percent_remaining": 100, "entitlement": 0, "unlimited": True},
        "premium_interactions": {"percent_remaining": 40, "remaining": 120, "entitlement": 300, "unlimited": False}})
    row = quotas.from_copilot_user(user)
    assert row["plan"] == "Pro" and [(w["scope"], w["used_pct"]) for w in row["windows"]] == [("Premium requests", 60)]
    empty = quotas.from_copilot_user(dict(USER, quota_snapshots={}))
    assert empty["windows"] == [] and empty["status"] == "unavailable"


def test_copilot_collector_replaces_quota_cli_row_and_keeps_it_on_failure(monkeypatch):
    import orchestrator.state as state

    monkeypatch.delenv("AAO_QUOTA_COPILOT", raising=False)
    monkeypatch.setattr(quotas, "_github_token", lambda env=None: "t")
    seen = {}

    def fake_get(url, headers=None, timeout=30):
        seen.update(url=url, auth=headers["Authorization"])
        return USER

    monkeypatch.setattr(state, "http_get_json", fake_get)
    cli_row = {"provider": "copilot", "account": None, "windows": [{"name": "mo"}], "source": "quota-cli"}
    errors = {}
    rows = quotas.collect_copilot_accounts([cli_row], errors)
    assert [r["source"] for r in rows] == ["copilot-api"] and not errors
    assert seen == {"url": quotas.COPILOT_USER, "auth": "token t"}

    def broken(*_a, **_k):
        raise OSError("HTTP 404")

    monkeypatch.setattr(state, "http_get_json", broken)
    rows = quotas.collect_copilot_accounts([cli_row], errors)
    assert rows == [cli_row] and errors["copilot-api"] == "HTTP 404"


def test_copilot_collector_opt_out_and_no_token(monkeypatch):
    monkeypatch.setattr(quotas, "_github_token", lambda env=None: None)
    assert quotas.collect_copilot_accounts([], {}) == []
    monkeypatch.setenv("AAO_QUOTA_COPILOT", "0")
    monkeypatch.setattr(quotas, "_github_token", lambda env=None: pytest.fail("token read while disabled"))
    assert quotas.collect_copilot_accounts([], {}) == []


def test_plan_prices_from_settings_attach_per_provider_and_account():
    from orchestrator.config import Settings

    errors = {}
    prices = quotas.plan_prices(Settings(env={"AAO_QUOTA_PLAN_USD": "claude=20, Antigravity=$19.99,copilot=0,claude:work=100,kiro=lots"}), errors)
    assert prices == {"claude": 20.0, "agy": 19.99, "copilot": 0.0, "claude:work": 100.0}
    assert "kiro=lots" in errors["plan-prices"]
    assert quotas.plan_prices(Settings(env={})) == {}
