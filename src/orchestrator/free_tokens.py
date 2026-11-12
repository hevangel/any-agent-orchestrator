"""Validation for data/free_tokens.json (maintained daily by an agent)."""

import re

KINDS = {"free-tier", "daily", "weekly", "monthly", "one-time", "trial", "promo", "referral", "credits"}
STATUSES = {"active", "expired", "unverified", "ended"}
ACCESS = {"cli", "web", "api", "ide", "desktop"}
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def validate(doc):
    errors = []
    if doc.get("schema") != "free-tokens-v1":
        errors.append("schema must be free-tokens-v1")
    if doc.get("sources") and not doc.get("updated_at"):
        errors.append("updated_at is required once sources exist")
    ids = set()
    for i, src in enumerate(doc.get("sources") or []):
        where = f"sources[{i}] {src.get('id')}"
        for key in ("id", "provider", "offer", "kind", "amount", "access", "url", "verified_at", "status"):
            if src.get(key) in (None, "", []):
                errors.append(f"{where}: missing {key}")
        if src.get("id") in ids:
            errors.append(f"{where}: duplicate id")
        ids.add(src.get("id"))
        if src.get("kind") and src["kind"] not in KINDS:
            errors.append(f"{where}: kind must be one of {sorted(KINDS)}")
        if src.get("status") and src["status"] not in STATUSES:
            errors.append(f"{where}: status must be one of {sorted(STATUSES)}")
        if any(a not in ACCESS for a in src.get("access") or []):
            errors.append(f"{where}: access entries must be in {sorted(ACCESS)}")
        for key in ("verified_at", "expires"):
            if src.get(key) and not DATE.match(str(src[key])):
                errors.append(f"{where}: {key} must be YYYY-MM-DD")
        if src.get("url") and not str(src["url"]).startswith("https://"):
            errors.append(f"{where}: url must be https")
    return errors
