"""Plan prices: synthetic official pricing pages for each parser, the merge rules and quota matching."""

from orchestrator import plan_prices, plan_update, quotas

CLAUDE = """<html><script>var x = "$999";</script><nav><a>Free</a><a>Pricing</a></nav>
<h3>Free</h3><p>Try Claude</p><span>$0</span><p>Free for everyone</p>
<h3>Pro</h3><p>For everyday work</p><span>$17</span><p>Per month with annual subscription discount (</p>
<span>$200</span><p>billed up front).</p><span>$20</span><p>if billed monthly.</p>
<h3>Max</h3><p>For all-day work</p><span>$100</span><p>Per month</p>
<h3>Ultra</h3><p>For your longest tasks</p><span>$200</span><p>Per month</p>
<h3>Standard seat</h3><span>$25</span></html>"""

COPILOT = """<p>Pro</p><p>plan</p><p>$15 per month</p><h2>Pricing plans</h2>
<h3>Free</h3><b>$0</b><a>Get started</a><h3>Pro</h3><b>$10</b><i>per month</i>
<h3>Pro+</h3><b>$39</b><i>per month</i><h3>Max</h3><b>$100</b><i>per month</i>"""

CURSOR = """<button>Individual</button><button>Teams</button>
<h3>Free</h3><b>$0</b><i>/month</i><h3>Plus</h3><b>$8</b><i>/month</i>
<h3>Premium</h3><p>Most popular</p><b>$30</b><i>/month</i><h3>Super</h3><b>$100</b><i>/month</i>
<h3>Ultra</h3><b>$200</b><i>/month</i><h3>Teams</h3><b>$30</b><i>+/user/month</i>"""

KIRO = """<h3>KIRO FREE</h3><b>$0</b><i>per month</i><h3>KIRO PRO</h3><b>$20</b><i>per user / month</i>
<h3>KIRO PRO+</h3><b>$40</b><h3>KIRO PRO MAX</h3><b>$100</b><h3>KIRO POWER</h3><b>$200</b>
<h3>Teams and enterprises</h3><b>$20</b>"""

GOOGLE = """<div>Basic (100 GB) CA$2.79/mo</div><div>Google AI Plus (2 TB)</div><div>CA$13.99/mo</div>
<div>Google AI Pro (5 TB) CA$26.99/mo</div><div>Google AI Pro (5 TB) CA$269.99/yr</div>"""

CHATGPT_PLUS = "<p>Subscription Details</p><p>Price: $20/month (billed monthly).</p>"
CHATGPT_PRO = """<table><tr><th>Plan</th><th>Monthly price (USD)</th></tr><tr><td>Pro 100</td><td>$100</td></tr>
<tr><td>Pro 200</td><td>$200</td></tr><tr><td>Pro 500</td><td>$500</td></tr></table><p>Pro 200 ($200/month)</p>"""

GROK = r'{\"supergrok_subscriptions\":{\"subscriptions\":[{\"id\":\"grok.pro.monthly.30\"},{\"id\":\"supergrok.pro.monthly.300\"}]}}'


def _plans(found):
    return [(p["plan"], p["monthly"], p["currency"]) for p in found]


def test_claude_takes_the_month_to_month_price_and_skips_scripts_and_teams():
    assert _plans(plan_update.parse_claude(CLAUDE)) == [
        ("Free", 0, "USD"), ("Pro", 20, "USD"), ("Max", 100, "USD"), ("Ultra", 200, "USD")]


def test_copilot_cursor_and_kiro_cards():
    assert _plans(plan_update.parse_copilot(COPILOT)) == [
        ("Free", 0, "USD"), ("Pro", 10, "USD"), ("Pro+", 39, "USD"), ("Max", 100, "USD")]
    assert _plans(plan_update.parse_cursor(CURSOR)) == [
        ("Free", 0, "USD"), ("Plus", 8, "USD"), ("Premium", 30, "USD"), ("Super", 100, "USD"), ("Ultra", 200, "USD")]
    assert _plans(plan_update.parse_kiro(KIRO)) == [
        ("Free", 0, "USD"), ("Pro", 20, "USD"), ("Pro+", 40, "USD"), ("Pro Max", 100, "USD"), ("Power", 200, "USD")]


def test_google_keeps_the_local_currency_and_the_monthly_price():
    assert _plans(plan_update.parse_google(GOOGLE)) == [("Plus", 13.99, "CAD"), ("Pro", 26.99, "CAD")]
    assert _plans(plan_update.parse_google(GOOGLE.replace("CA$", "US$"))) == [("Plus", 13.99, "USD"), ("Pro", 26.99, "USD")]


def test_chatgpt_help_articles_and_grok_catalog():
    assert _plans(plan_update.parse_chatgpt_plus(CHATGPT_PLUS)) == [("Plus", 20, "USD")]
    assert _plans(plan_update.parse_chatgpt_pro(CHATGPT_PRO)) == [("Pro 100", 100, "USD"), ("Pro 200", 200, "USD"), ("Pro 500", 500, "USD")]
    assert _plans(plan_update.parse_grok(GROK)) == [("SuperGrok", 30, "USD"), ("SuperGrok Heavy", 300, "USD")]


def test_parsers_find_nothing_on_an_unrelated_page():
    for parser in (plan_update.parse_claude, plan_update.parse_copilot, plan_update.parse_cursor, plan_update.parse_kiro,
                   plan_update.parse_google, plan_update.parse_chatgpt_plus, plan_update.parse_chatgpt_pro, plan_update.parse_grok):
        assert parser("<h1>Page not found</h1>") == []


def test_merge_never_edits_history_and_keeps_currencies_apart():
    table = {"schema": plan_prices.SCHEMA, "plans": []}
    url = "https://example.test/pricing"
    first = plan_update.merge(table, "claude", [{"plan": "Pro", "monthly": 20.0, "currency": "USD"}], url, "t", "2026-01-01")
    assert first[0][0] == "new plan" and table["plans"][0]["effective_from"] == "2020-01-01"
    assert plan_update.merge(table, "claude", [{"plan": "Pro", "monthly": 20.0, "currency": "USD"}], url, "t", "2026-02-01") == []
    assert table["plans"][0]["verified_at"] == "2026-02-01"
    change = plan_update.merge(table, "claude", [{"plan": "Pro", "monthly": 25.0, "currency": "USD"}], url, "t", "2026-03-01")
    assert change[0][0] == "price change"
    assert [(e["monthly"], e["effective_from"]) for e in table["plans"]] == [(20.0, "2020-01-01"), (25.0, "2026-03-01")]
    assert plan_prices.in_force(table, "claude", "pro", "2026-02-15")["monthly"] == 20.0
    assert plan_prices.in_force(table, "claude", "pro", "2026-03-02")["monthly"] == 25.0
    plan_update.merge(table, "google", [{"plan": "Pro", "monthly": 26.99, "currency": "CAD"}], url, "t", "2026-03-01")
    plan_update.merge(table, "google", [{"plan": "Pro", "monthly": 19.99, "currency": "USD"}], url, "t", "2026-03-02")
    assert [e["effective_from"] for e in table["plans"] if e["vendor"] == "google"] == ["2020-01-01", "2020-01-01"]
    assert plan_prices.in_force(table, "google", "PRO", "2026-03-03")["currency"] == "USD"
    assert plan_prices.validate(table) == []


def test_update_reports_layout_changes_without_writing(monkeypatch, tmp_path):
    monkeypatch.setattr(plan_update, "PLANS_FILE", tmp_path / "plans.json")
    pages = {"https://claude.com/pricing": CLAUDE}
    report = plan_update.update(vendors=["claude", "grok"], dry_run=True, today="2026-11-24",
                                fetch=lambda url, accept: pages.get(url, "<h1>moved</h1>"))
    assert report["parsed"] == {"claude-claude": 4} and "grok-grok" in report["errors"]
    assert not (tmp_path / "plans.json").exists()


TABLE = {"schema": plan_prices.SCHEMA, "plans": [
    {"vendor": "kiro", "plan": "Pro", "monthly": 20.0, "currency": "USD", "effective_from": "2020-01-01", "source_url": "k", "verified_at": "d"},
    {"vendor": "kiro", "plan": "Pro Max", "monthly": 100.0, "currency": "USD", "effective_from": "2020-01-01", "source_url": "k", "verified_at": "d"},
    {"vendor": "claude", "plan": "Max", "monthly": 100.0, "currency": "USD", "effective_from": "2020-01-01", "source_url": "c", "verified_at": "d"},
    {"vendor": "google", "plan": "Pro", "monthly": 26.99, "currency": "CAD", "effective_from": "2020-01-01", "source_url": "g", "verified_at": "d"},
]}


def test_attach_matches_reported_plans_and_counts_a_shared_plan_once():
    rows = [{"provider": "agy"}, {"provider": "kiro", "plan": "KIRO PRO MAX"}, {"provider": "claude"},
            {"provider": "flow", "plan": "PRO"}, {"provider": "gemini", "plan": "PRO"}, {"provider": "muse", "plan": "Muse Free"},
            {"provider": "cursor", "plan": "Plus"}]
    plan_prices.attach(rows, TABLE, {"claude": "max-5x"}, day="2026-11-24")
    by = {r["provider"]: r.get("plan_price") for r in rows}
    assert by["kiro"]["plan"] == "Pro Max" and by["kiro"]["monthly"] == 100
    assert by["claude"]["plan"] == "Max"  # .env PLAN max-5x -> the longest plan name it starts with
    assert by["gemini"]["monthly"] == 26.99 and "included_in" not in by["gemini"]
    assert by["flow"]["included_in"] == "gemini" and by["agy"]["included_in"] == "gemini" and by["agy"]["plan"] == "Pro"
    assert by["muse"]["monthly"] == 0 and by["muse"]["source_url"] is None
    assert by["cursor"] is None  # no entry: the dashboard says the price is unknown


def test_collect_attaches_plan_prices_from_the_json(monkeypatch):
    monkeypatch.setattr(quotas, "_run", lambda args, **kw: (True, "[]", None))
    monkeypatch.setattr(quotas, "from_quota_cli", lambda records: [{"provider": "kiro", "plan": "KIRO PRO", "windows": []}])
    for name in ("collect_screen_accounts", "collect_web_accounts"):
        monkeypatch.setattr(quotas, name, lambda rows, errors: rows)
    monkeypatch.setattr(quotas, "collect_copilot_accounts", lambda rows, errors, env=None: rows)
    monkeypatch.setattr(quotas, "from_codex_logs", lambda: [])
    monkeypatch.setattr(plan_prices, "load", lambda path=None: TABLE)
    monkeypatch.setattr("orchestrator.config.accounts", lambda settings=None: [])
    (kiro,) = quotas.collect()["accounts"]
    assert kiro["plan_price"]["monthly"] == 20.0 and "plan_usd_month" not in kiro
