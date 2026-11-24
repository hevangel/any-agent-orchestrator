"""Muse usage quota: parsing the Settings server action and dialog text, and the quota collector hook."""

import json

from orchestrator import quotas, web_muse
from orchestrator.config import Settings

SUBSCRIPTION = {
    "subscriptionType": "FREE",
    "tier": {"tierId": "hatch_free", "name": "Muse Free", "tierCode": "HATCH_FREE", "isPaid": False, "rank": 0},
    "usage": {"state": "METERED", "percentUsed": 58, "resetsAt": 1795621485, "quotaStatus": "SUFFICIENT"},
    "statusSubtitle": "Weekly limit resets on Nov 25", "usageRowLabel": "Free plan", "usageRowValueLabel": "58% used",
    "topupBalance": 430020520, "topupTotal": 440000000, "topupRowLabel": "Additional tokens",
    "topupRowValueLabel": "2% used (10B tokens left)", "entryPointCtaLabel": "Upgrade",
}
BODY = '0:{"a":"$@1","q":"","i":false}\n1:' + json.dumps({"success": True, "subscription": SUBSCRIPTION}) + "\n"
DIALOG = ("Settings\nGeneral\nBrowser\nLog out\nGeneral\nMeta Account\nPassword, security, personal details\nUsage\n"
          "Free plan\nWeekly limit resets on Nov 25\n58% used\nAdditional tokens\nNever expires\n"
          "2% used (10B tokens left)\nUpgrade\nLanguage\nAppearance\n")


def test_server_action_response_becomes_weekly_and_topup_windows():
    row = web_muse.quota_account({"ok": True, "body": BODY, "text": ""})
    assert (row["provider"], row["status"], row["plan"], row["source"]) == ("muse", "live", "Muse Free", "muse-web")
    weekly, tokens = row["windows"]
    assert weekly["name"] == "weekly" and weekly["seconds"] == 604800
    assert (weekly["used_pct"], weekly["remaining_pct"]) == (58.0, 42.0)
    assert weekly["resets_at"] == "2026-11-25T15:44:45Z"
    assert tokens["name"] == "additional tokens" and tokens["resets_at"] is None
    assert (tokens["used"], tokens["remaining"], tokens["limit"]) == (9979480, 430020520, 440000000)
    assert tokens["used_pct"] == 2.3 and tokens["reset_text"] == "never expires"
    assert row["balance"] == [{"amount": 430020520, "total": 440000000, "currency": "top-up units",
                               "label": "10B tokens left"}]
    assert row["error"] is None


def test_exhausted_quota_status_is_reported():
    sub = {**SUBSCRIPTION, "usage": {**SUBSCRIPTION["usage"], "percentUsed": 100, "quotaStatus": "EXCEEDED"}}
    row = web_muse.quota_account({"ok": True, "body": "1:" + json.dumps({"subscription": sub})})
    assert row["windows"][0]["used_pct"] == 100.0 and row["error"] == "Muse quota status: EXCEEDED"


def test_dialog_text_is_the_fallback_when_no_action_response_was_caught():
    row = web_muse.quota_account({"ok": True, "body": "", "text": DIALOG})
    assert row["status"] == "live" and row["source"] == "muse-web-page" and row["plan"] == "Free plan"
    assert [(w["name"], w["used_pct"], w["reset_text"]) for w in row["windows"]] == [
        ("weekly", 58.0, "Weekly limit resets on Nov 25"), ("additional tokens", 2.0, "never expires")]
    assert row["balance"][0]["label"] == "2% used (10B tokens left)"


def test_failed_or_empty_reads_never_guess_values():
    signed_out = web_muse.quota_account({"ok": False, "loggedIn": False, "reason": "Muse is not signed in"})
    assert signed_out["status"] == "unavailable" and signed_out["windows"] == []
    assert signed_out["error"] == "Muse is not signed in"
    empty = web_muse.quota_account({"ok": True, "body": "0:{}", "text": "Settings\nGeneral"})
    assert empty["status"] == "unavailable" and empty["error"] == "Muse Settings showed no Usage section"


def test_quota_opens_a_headless_browser_and_closes_only_what_it_opened(monkeypatch):
    calls = []
    monkeypatch.setattr(web_muse.web, "is_open", lambda target: False)
    monkeypatch.setattr(web_muse.web, "open_target", lambda target, headed: calls.append(("open", headed)) or (True, "", ""))
    monkeypatch.setattr(web_muse.web, "run_script", lambda target, script, timeout: {"ok": True, "body": BODY})
    monkeypatch.setattr(web_muse.web, "close_target", lambda target: calls.append(("close", target)))
    assert web_muse.quota()["status"] == "live"
    assert calls == [("open", False), ("close", "muse")]
    calls.clear()
    monkeypatch.setattr(web_muse.web, "is_open", lambda target: True)
    web_muse.quota()
    assert calls == []


def test_has_profile_does_not_create_the_profile(tmp_path):
    settings = Settings(env={"AAO_WEB_PROFILE_DIR": str(tmp_path)})
    assert not web_muse.has_profile(settings) and not (tmp_path / "muse").exists()
    (tmp_path / "muse").mkdir()
    assert web_muse.has_profile(settings)


def test_collector_adds_muse_and_skips_targets_never_logged_in(monkeypatch):
    monkeypatch.setattr("orchestrator.web_quota_google.has_profile", lambda: False)  # Flow/Gemini: no browser
    live = web_muse.quota_account({"ok": True, "body": BODY})
    monkeypatch.setattr("orchestrator.config.Settings", lambda: Settings(env={}))
    monkeypatch.setattr(web_muse, "has_profile", lambda: True)
    monkeypatch.setattr(web_muse, "quota", lambda: live)
    errors = {}
    rows = quotas.collect_web_accounts([{"provider": "codex", "windows": []}], errors)
    assert [r["provider"] for r in rows] == ["codex", "muse"] and errors == {}

    monkeypatch.setattr(web_muse, "has_profile", lambda: False)
    monkeypatch.setattr(web_muse, "quota", lambda: (_ for _ in ()).throw(AssertionError("must not open Muse")))
    assert quotas.collect_web_accounts([], {}) == []


def test_collector_reports_failures_and_can_be_disabled(monkeypatch):
    monkeypatch.setattr("orchestrator.web_quota_google.has_profile", lambda: False)  # Flow/Gemini: no browser
    monkeypatch.setattr(web_muse, "has_profile", lambda: True)
    monkeypatch.setattr(web_muse, "quota", lambda: web_muse.quota_account({"ok": False, "reason": "signed out"}))
    monkeypatch.setattr("orchestrator.config.Settings", lambda: Settings(env={}))
    errors = {}
    assert quotas.collect_web_accounts([], errors) == [] and errors == {"muse-web": "signed out"}
    monkeypatch.setattr("orchestrator.config.Settings", lambda: Settings(env={"AAO_QUOTA_WEB_TARGETS": "none"}))
    monkeypatch.setattr(web_muse, "quota", lambda: (_ for _ in ()).throw(AssertionError("disabled")))
    assert quotas.collect_web_accounts([], {}) == []
