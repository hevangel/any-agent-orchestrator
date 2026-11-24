"""Deterministic plan-price updater: parse official pricing pages into data/plan_prices.json. No LLM.

    uv run aao prices plans update --dry-run     # show what would change
    uv run aao prices plans update               # write data/plan_prices.json

Each vendor's page is fetched with state.http_get_text and read by a vendor-specific parser below,
which returns [{"plan", "monthly", "currency"}] for the monthly price of each individual plan (annual
discounts and team seats are left out). Merge rules match aao prices update:

  * same price as the entry in force today  -> only `verified_at` moves to today
  * a different price                       -> a NEW entry with effective_from = today; old entries
                                               are never edited, so history is not re-priced
  * a plan seen for the first time          -> effective_from 2020-01-01 ("since first known")
  * plans no longer on the page             -> left alone

Prices are recorded in the currency the page shows. Google localizes by the caller's country and
ignores ?gl=, so its plans can be in CA$ or another currency. Each currency is its own history (a
run from another country adds a USD entry instead of a 'price change'); lookups prefer USD, and the
dashboard prints the entry's currency.
Muse has no public pricing page (muse.ai/pricing answers 401), so it has no parser; a plan named
Free still costs 0 without an entry (plan_prices.attach).
"""

import html
import re
from datetime import date

from orchestrator.plan_prices import PLANS_FILE, SCHEMA, normalize, validate
from orchestrator.state import http_get_text, read_json, write_json

FIRST_KNOWN = "2020-01-01"
_DOLLARS = re.compile(r"^(US\$|CA\$|A\$|\$)\s?(\d[\d,]*(?:\.\d+)?)$")
CURRENCIES = {"US$": "USD", "$": "USD", "CA$": "CAD", "A$": "AUD"}


def text_lines(page):
    """Visible text of an HTML page, one element per line (scripts and styles dropped)."""
    body = re.sub(r"<script\b.*?</script>|<style\b.*?</style>|<!--.*?-->", " ", page, flags=re.S | re.I)
    text = html.unescape(re.sub(r"<[^>]+>", "\n", body))
    return [line.strip() for line in text.splitlines() if line.strip()]


def _money(line):
    match = _DOLLARS.match(line.replace(" ", " ").strip())
    return (float(match.group(2).replace(",", "")), CURRENCIES[match.group(1)]) if match else None


def plan_cards(lines, names, start=None, window=8, monthly_hint=None):
    """Plan cards in page order: each name heading followed (within `window` lines) by a '$N' line.

    `start` is a line the plan section begins after; `monthly_hint` a line that, right after a
    second '$N' in the same card, marks it as the month-to-month price (Claude Pro shows the annual
    per-month price first)."""
    i = lines.index(start) + 1 if start in lines else 0
    found = []
    for name in names:
        while i < len(lines):
            if lines[i] == name:
                card = lines[i + 1:i + 1 + window]
                prices = [(k, _money(x)) for k, x in enumerate(card) if _money(x)]
                if prices:
                    k, (amount, currency) = prices[0]
                    if monthly_hint:
                        for k2, money in prices[1:]:
                            if k2 + 1 < len(card) and card[k2 + 1].lower().startswith(monthly_hint):
                                amount, currency = money
                    found.append({"plan": name, "monthly": amount, "currency": currency})
                    i += 1
                    break
            i += 1
    return found


# ----------------------------------------------------------------- parsers

def parse_claude(page):
    """claude.com/pricing: Free, Pro ('$17 … $20 if billed monthly'), Max, Ultra."""
    return plan_cards(text_lines(page), ["Free", "Pro", "Max", "Ultra"], monthly_hint="if billed monthly")


def parse_copilot(page):
    """github.com/features/copilot/plans: the 'Pricing plans' cards Free, Pro, Pro+, Max."""
    return plan_cards(text_lines(page), ["Free", "Pro", "Pro+", "Max"], start="Pricing plans", window=3)


def parse_cursor(page):
    """cursor.com/pricing: the Individual cards Free, Plus, Premium, Super, Ultra ('$N /month')."""
    return plan_cards(text_lines(page), ["Free", "Plus", "Premium", "Super", "Ultra"], start="Individual", window=4)


def parse_kiro(page):
    """kiro.dev/pricing: KIRO FREE, KIRO PRO, KIRO PRO+, KIRO PRO MAX, KIRO POWER ('$N per user / month')."""
    names = ["KIRO FREE", "KIRO PRO", "KIRO PRO+", "KIRO PRO MAX", "KIRO POWER"]
    return [{**card, "plan": card["plan"].title().replace("Kiro ", "")}
            for card in plan_cards(text_lines(page), names, window=3)]


_GOOGLE = re.compile(r"Google AI (Plus|Pro|Ultra)\s*\([^)]*\)\s*(US\$|CA\$|A\$|\$)\s?([\d,]+(?:\.\d+)?)\s*/\s*mo")


def parse_google(page):
    """one.google.com/about/plans: 'Google AI Pro (5 TB) CA$26.99/mo' (in the caller's local currency)."""
    text = " ".join(text_lines(page))
    found = {}
    for plan, symbol, amount in _GOOGLE.findall(text):
        found.setdefault(plan, {"plan": plan, "monthly": float(amount.replace(",", "")), "currency": CURRENCIES[symbol]})
    return list(found.values())


def parse_chatgpt_plus(page):
    """help.openai.com 'What is ChatGPT Plus?': 'Price: $20/month (billed monthly)'."""
    match = re.search(r"Price:\s*\$(\d+(?:\.\d+)?)\s*/\s*month", " ".join(text_lines(page)))
    return [{"plan": "Plus", "monthly": float(match.group(1)), "currency": "USD"}] if match else []


def parse_chatgpt_pro(page):
    """help.openai.com 'What is ChatGPT Pro?': the monthly plans table 'Pro 100 $100', 'Pro 200 $200', ..."""
    text = " ".join(text_lines(page))
    found = {}
    for tier, amount in re.findall(r"\bPro (\d{2,4}) \$(\d+(?:\.\d+)?)\b", text):
        found.setdefault(tier, {"plan": f"Pro {tier}", "monthly": float(amount), "currency": "USD"})
    return list(found.values())


def parse_grok(page):
    """grok.com/plans: the page's subscription catalog names its monthly products with their USD price,
    'grok.pro.monthly.30' (SuperGrok) and 'supergrok.pro.monthly.300' (SuperGrok Heavy)."""
    found = {}
    for heavy, amount in re.findall(r"\b(super)?grok\.pro\.monthly\.(\d+)\b", page):
        plan = "SuperGrok Heavy" if heavy else "SuperGrok"
        found.setdefault(plan, {"plan": plan, "monthly": float(amount), "currency": "USD"})
    return list(found.values())


SOURCES = {
    "claude": [("https://claude.com/pricing", parse_claude)],
    "chatgpt": [("https://help.openai.com/en/articles/6950777-what-is-chatgpt-plus", parse_chatgpt_plus),
                ("https://help.openai.com/en/articles/9793128-what-is-chatgpt-pro", parse_chatgpt_pro)],
    "google": [("https://one.google.com/about/plans?hl=en-US", parse_google)],
    "copilot": [("https://github.com/features/copilot/plans", parse_copilot)],
    "cursor": [("https://cursor.com/pricing", parse_cursor)],
    "kiro": [("https://kiro.dev/pricing/", parse_kiro)],
    "grok": [("https://grok.com/plans", parse_grok)],
}


# -------------------------------------------------------------------- merge

def merge(table, vendor, parsed, source_url, parser, today=None):
    today = today or date.today().isoformat()
    plans = table.setdefault("plans", [])
    changes = []
    for item in parsed:
        history = sorted((e for e in plans if e["vendor"] == vendor and normalize(e["plan"]) == normalize(item["plan"])
                          and e["currency"] == item["currency"]),
                         key=lambda e: e["effective_from"])
        current = ([e for e in history if e["effective_from"] <= today] or [None])[-1]
        if current and abs(current["monthly"] - item["monthly"]) < 1e-9 and current["currency"] == item["currency"]:
            current.update(verified_at=today, verified_by=f"aao prices plans update ({parser})", source_url=source_url)
            continue
        if any(e["effective_from"] == today for e in history):
            continue  # already recorded a change today; never rewrite an entry
        new = {"vendor": vendor, "plan": item["plan"], "monthly": item["monthly"], "currency": item["currency"],
               "effective_from": today if history else FIRST_KNOWN, "source_url": source_url,
               "verified_at": today, "verified_by": f"aao prices plans update ({parser})"}
        plans.append(new)
        changes.append(("new plan" if not history else "price change", vendor, item["plan"], new["effective_from"],
                        None if not current else f"{current['monthly']:g} {current['currency']}",
                        f"{item['monthly']:g} {item['currency']}"))
    plans.sort(key=lambda e: (e["vendor"], e["monthly"], e["plan"], e["effective_from"]))
    return changes


def update(vendors=None, dry_run=False, today=None, fetch=http_get_text):
    table = read_json(PLANS_FILE, None) or {
        "schema": SCHEMA, "notes": "Monthly subscription prices of individual plans, from the official pricing pages "
        "(aao prices plans update; never edited by hand). A changed price is a new entry with a later effective_from.",
        "plans": []}
    report = {"changes": [], "errors": {}, "parsed": {}}
    for vendor, sources in SOURCES.items():
        if vendors and vendor not in vendors:
            continue
        for url, parser in sources:
            name = f"{vendor}-{parser.__name__.removeprefix('parse_')}"
            try:
                parsed = parser(fetch(url, accept="text/html"))
            except Exception as exc:  # network errors, layout changes
                report["errors"][name] = f"{type(exc).__name__}: {exc}"
                continue
            if not parsed:
                report["errors"][name] = "parser found no plans: the page layout probably changed"
                continue
            report["parsed"][name] = len(parsed)
            report["changes"] += merge(table, vendor, parsed, url, name, today)
    table["updated_at"] = today or date.today().isoformat()
    errors = validate(table)
    if errors:
        report["errors"]["validate"] = errors
    elif not dry_run:
        write_json(PLANS_FILE, table)
    return report
