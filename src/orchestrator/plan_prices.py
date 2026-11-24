"""Subscription plan prices (data/plan_prices.json) and matching them to quota accounts.

The table is written only by `aao prices plans update` (plan_update.py), which parses the official
pricing pages. Like data/api_prices.json, a changed price is a new entry with a later
`effective_from`; old entries are never edited, so history keeps the price it had.

Each entry is one plan of one vendor: {"vendor", "plan", "monthly", "currency", "effective_from",
"source_url", "verified_at", "verified_by"}. Quota providers map to vendors with VENDORS; Gemini,
Google Flow and Antigravity are all covered by one Google AI plan, so its price is attached to the
first of them only and the others are marked as included (no double counting).
"""

import re
from datetime import date

from orchestrator.state import DATA_DIR, read_json

PLANS_FILE = DATA_DIR / "plan_prices.json"
SCHEMA = "plan-prices-v1"

# quota provider -> vendor in plan_prices.json. Providers sharing a vendor share one subscription;
# the order here decides which of them carries its price.
VENDORS = {"claude": "claude", "codex": "chatgpt", "gemini": "google", "flow": "google", "agy": "google",
           "copilot": "copilot", "cursor": "cursor", "kiro": "kiro", "grok": "grok", "muse": "muse"}
VENDOR_NAMES = {"claude": "Claude", "chatgpt": "ChatGPT", "google": "Google AI", "copilot": "GitHub Copilot",
                "cursor": "Cursor", "kiro": "Kiro", "grok": "Grok", "muse": "Muse"}
# Words a quota screen puts around the plan name ("KIRO PRO MAX", "Muse Free", "Google AI Pro").
_NOISE = re.compile(r"\b(?:kiro|muse|claude|chatgpt|github|copilot|cursor|google ai|google|grok|plan|subscription)\b")


def normalize(name):
    """'KIRO PRO MAX' -> 'promax', 'Pro+' -> 'pro+', 'max-5x' -> 'max5x'."""
    return re.sub(r"[^a-z0-9+]", "", _NOISE.sub(" ", (name or "").lower()))


def load(path=None):
    return read_json(path or PLANS_FILE, {"schema": SCHEMA, "plans": []})


def in_force(table, vendor, plan, day=None):
    """The entry for this vendor's plan in force on `day` (default today), or None. An exact name wins;
    otherwise the longest plan name the reported one starts with ('max5x' -> Max)."""
    day = day or date.today().isoformat()
    wanted = normalize(plan)
    if not wanted:
        return None
    entries = [e for e in table.get("plans", []) if e.get("vendor") == vendor and e.get("effective_from", "") <= day]
    names = {normalize(e["plan"]) for e in entries}
    exact = wanted if wanted in names else None
    prefix = max((n for n in names if n and wanted.startswith(n)), key=len, default=None)
    chosen = exact or prefix
    if not chosen:
        return None
    # One history per currency (Google localizes); USD first, then the newest entry.
    return max((e for e in entries if normalize(e["plan"]) == chosen),
               key=lambda e: (e["currency"] == "USD", e["effective_from"]))


def attach(accounts, table=None, plans_by_agent=None, day=None):
    """Set `plan_price` on each quota account whose plan has an entry.

    The plan comes from the account row, else from the .env account PLAN of that agent
    (`plans_by_agent`), else from another row of the same vendor (Antigravity has no plan of its own
    and runs on the Google AI plan Gemini reports). A plan literally named Free costs 0 without an
    entry. The first account of a vendor carries the price; later ones get `included_in` instead."""
    table = table if table is not None else load()
    plans_by_agent = plans_by_agent or {}
    vendor_plan = {}
    for a in accounts:
        vendor = VENDORS.get(a["provider"])
        plan = a.get("plan") or plans_by_agent.get(a["provider"])
        if vendor and plan and vendor not in vendor_plan:
            vendor_plan[vendor] = plan
    carried = {}
    order = list(VENDORS)
    for a in sorted(accounts, key=lambda a: order.index(a["provider"]) if a["provider"] in order else len(order)):
        vendor = VENDORS.get(a["provider"])
        if not vendor:
            continue
        plan = a.get("plan") or plans_by_agent.get(a["provider"]) or vendor_plan.get(vendor)
        entry = in_force(table, vendor, plan, day)
        if entry:
            price = {"plan": entry["plan"], "vendor": vendor, "monthly": entry["monthly"], "currency": entry["currency"],
                     "source_url": entry["source_url"], "effective_from": entry["effective_from"]}
        elif normalize(plan) == "free":
            price = {"plan": "Free", "vendor": vendor, "monthly": 0.0, "currency": "USD", "source_url": None,
                     "effective_from": None}
        else:
            continue
        if price["monthly"] and vendor in carried and carried[vendor] != a["provider"]:
            price["included_in"] = carried[vendor]  # the same subscription, already valued on that card
        carried.setdefault(vendor, a["provider"])
        a["plan_price"] = price
    return accounts


def validate(table):
    errors = []
    if table.get("schema") != SCHEMA:
        errors.append(f"schema must be {SCHEMA}")
    seen = set()
    for i, entry in enumerate(table.get("plans", [])):
        where = f"plans[{i}] {entry.get('vendor')} {entry.get('plan')}"
        for key in ("vendor", "plan", "currency", "effective_from", "source_url", "verified_at"):
            if not entry.get(key):
                errors.append(f"{where}: missing {key}")
        if not isinstance(entry.get("monthly"), (int, float)) or entry["monthly"] < 0:
            errors.append(f"{where}: monthly must be a non-negative number")
        key = (entry.get("vendor"), normalize(entry.get("plan")), entry.get("currency"), entry.get("effective_from"))
        if key in seen:
            errors.append(f"{where}: duplicate vendor + plan + currency + effective_from")
        seen.add(key)
    return errors
