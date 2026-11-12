"""Deterministic price-table updater: parse official pricing pages, merge into data/api_prices.json.

    uv run aao prices update --dry-run     # show what would change
    uv run aao prices update               # write data/api_prices.json

Each provider serves its pricing page as Markdown (Accept: text/markdown), parsed
with a provider-specific parser below. Merge rules keep history intact:

  * same prices as the entry in force today  -> only `verified_at` moves to today
  * different prices                         -> a NEW entry with effective_from = the page's
                                                stated date, else today; old entries are never edited
  * a model seen for the first time          -> effective_from 2020-01-01 ("since first known")
  * future-dated prices on the page          -> an entry with that effective_from
  * entries for models no longer on the page -> left alone and reported

Bucket mapping: `cache_write_5m` is the provider's short-TTL cache write (Anthropic 5 min,
OpenAI 30 min), `cache_write_1h` the long-TTL write (Anthropic 1 h, OpenAI 12 h). Providers
with no cache-write fee (xAI, Z.ai, Gemini implicit caching) bill writes as normal input.
"""

import re
from datetime import date, datetime

from orchestrator.pricing import BUCKETS, PRICES_FILE, validate_table
from orchestrator.state import http_get_text, read_json, write_json

FIRST_KNOWN = "2020-01-01"
MONEY = re.compile(r"\$\s*([0-9]+(?:\.[0-9]+)?)")


def _money(cell):
    """'$1.25', '\\$1.4', '$4 / MTok<sup>3</sup>' -> 1.25; 'Free' -> 0.0; '-' / '' -> None."""
    text = (cell or "").replace("\\$", "$").strip()
    if text.lower().startswith("free"):
        return 0.0
    match = MONEY.search(text)
    return float(match.group(1)) if match else None


def _tables(md):
    """Yield (header_cells, rows) for each pipe table in Markdown."""
    lines = md.splitlines()
    i = 0
    while i < len(lines) - 1:
        line, sep = lines[i].strip(), lines[i + 1].strip()
        if line.startswith("|") and re.match(r"^\|\s*:?-", sep):
            header = [c.strip() for c in line.strip("|").split("|")]
            rows = []
            i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            yield header, rows
        else:
            i += 1


def _col(header, *needles):
    for idx, name in enumerate(header):
        low = name.lower()
        if all(n in low for n in needles):
            return idx
    return None


def _prices(inp, write_short, write_long, read, out):
    return {"input": inp, "cache_write_5m": write_short, "cache_write_1h": write_long,
            "cache_read": read, "output": out}


# ------------------------------------------------------------------ OpenAI

def parse_openai(md):
    start = md.find("### Standard pricing data")
    section = md[start:] if start >= 0 else md
    for header, rows in _tables(section):
        c_in, c_out = _col(header, "short", "input"), _col(header, "short", "output")
        c_cached = _col(header, "short", "cached")
        if c_in is None or c_out is None:
            continue
        c_w30, c_w12 = _col(header, "short", "30m"), _col(header, "short", "12h")
        c_lin, c_lout, c_lcached = _col(header, "long", "input"), _col(header, "long", "output"), _col(header, "long", "cached")
        found = []
        for row in rows:
            cell = lambda c: _money(row[c]) if c is not None and c < len(row) else None  # noqa: E731
            model = re.sub(r"\s*\(.*?\)\s*", "", row[0]).strip().lower()
            if not model or cell(c_in) is None:
                continue
            entry = {"model": model, "provider": "openai",
                     "prices": _prices(cell(c_in), cell(c_w30), cell(c_w12), cell(c_cached), cell(c_out))}
            if cell(c_lin) is not None:
                entry["long_context"] = {"above_input_tokens": 272000, "input": cell(c_lin), "cache_read": cell(c_lcached),
                                         "output": cell(c_lout), "applied": False}
            found.append(entry)
        return found  # first table of the Standard section only
    return []


# --------------------------------------------------------------- Anthropic

def anthropic_model_id(name):
    """'Claude Opus 5.5' -> 'claude-opus-5-5'."""
    base = re.sub(r"\[[^\]]*\]\([^)]*\)", "", name)          # markdown links first: they contain parentheses
    base = re.sub(r"\(.*?\)|<sup>.*?</sup>", "", base).lower()
    base = re.sub(r"[^a-z0-9. ]", "", base).strip()
    return re.sub(r"[ .]+", "-", base)


def parse_anthropic(md):
    for header, rows in _tables(md):
        if _col(header, "base input") is None:
            continue
        c_in, c_w5, c_w1 = _col(header, "base input"), _col(header, "5m"), _col(header, "1h")
        c_hit, c_out = _col(header, "cache hits"), _col(header, "output")
        found = []
        for row in rows:
            name = row[0]
            prices = _prices(_money(row[c_in]), _money(row[c_w5]), _money(row[c_w1]), _money(row[c_hit]), _money(row[c_out]))
            model = anthropic_model_id(name)
            if "over" in name.lower() and found and found[-1]["model"] == model:
                long_prompt = re.search(r"over ([0-9,]+)", name)
                found[-1]["long_context"] = {"above_input_tokens": int(long_prompt.group(1).replace(",", "")) if long_prompt else None,
                                             **{k: v for k, v in prices.items()}, "applied": False}
                continue
            entry = {"model": model, "provider": "anthropic", "prices": prices}
            if "retired" in name.lower():
                entry["notes"] = "retired on the Claude API"
            found.append(entry)
        return found
    return []


# --------------------------------------------------------------------- xAI

def parse_xai(md):
    found = {}
    for header, rows in _tables(md):
        c_in, c_cached, c_out = _col(header, "input"), _col(header, "cached"), _col(header, "output")
        if c_in is None or c_out is None or c_in == c_cached:
            continue
        for row in rows:
            model = re.sub(r"\s*\(.*?\)\s*", "", row[0]).strip().lower()
            inp, cached, out = _money(row[c_in]), _money(row[c_cached]) if c_cached is not None else None, _money(row[c_out])
            if inp is None or out is None:
                continue
            long_tier = "≥" in row[0] or ">=" in row[0]
            if long_tier and model in found:
                found[model]["long_context"] = {"above_input_tokens": 200000, "input": inp, "cache_read": cached,
                                                "output": out, "applied": False}
            elif model not in found:
                found[model] = {"model": model, "provider": "xai", "prices": _prices(inp, inp, inp, cached, out)}
    return list(found.values())


# --------------------------------------------------------------------- Z.ai

def parse_zai(md):
    found = {}
    for header, rows in _tables(md):
        c_in, c_cached, c_out = _col(header, "input"), _col(header, "cached input"), _col(header, "output")
        if c_in is None or c_out is None or c_cached == c_in:
            continue
        for row in rows:
            model = row[0].strip().lower()
            inp, out = _money(row[c_in]), _money(row[c_out])
            cached = _money(row[c_cached]) if c_cached is not None else None
            if inp is None or out is None or model in found:
                continue
            found[model] = {"model": model, "provider": "zai",
                            "prices": _prices(inp, inp, inp, cached if cached is not None else inp, out)}
    return list(found.values())


# ------------------------------------------------------------------- Gemini

_DATE = r"([A-Z][a-z]+ \d{1,2}, \d{4})"
# Priced per second, image or character rather than per text token: not comparable, skipped.
GEMINI_NON_TEXT = re.compile(r"tts|image|live|transcribe|banana|audio|lyria|embedding|omni|veo|imagen")


def _dated_values(text):
    """'$0.75 through December 31, 2026. $1.50 starting January 1, 2027.' -> [(None, 0.75), ('2027-01-01', 1.5)].

    Storage prices ('per hour') are ignored.
    """
    values = []
    for part in re.split(r"(?<=\.)\s+(?=\$)|\n", text):
        if "per hour" in part or "storage" in part.lower():
            continue
        money = _money(part)
        if money is None:
            continue
        start = re.search(r"starting " + _DATE, part)
        values.append((datetime.strptime(start.group(1), "%B %d, %Y").date().isoformat() if start else None, money))
    return values


def parse_gemini(md):
    found = []
    sections = re.split(r"\n## ", md)
    for section in sections:
        ids = [i for i in re.findall(r"\*\[`([a-z0-9.\-]+)`\]", section) if not GEMINI_NON_TEXT.search(i)]
        standard = section.split('{% tab title="Batch"')[0]
        if not ids or "Input price" not in standard:
            continue
        fields = {}
        for label, key in (("Input price", "input"), ("Output price", "output"), ("Context caching price", "cache_read")):
            match = re.search(r"\* " + label + r"[^\n]*\n\s*\* [^\n]*\n\s*\* (.*?)(?:\n\s*---|\n\s*\{%)", standard, re.S)
            if match:
                fields[key] = _dated_values(match.group(1))
        if not fields.get("input") or not fields.get("output"):
            continue
        dates = sorted({d for values in fields.values() for d, _ in values if d})
        for effective in [None, *dates]:
            def pick(key):
                # The first undated value is the text / short-prompt price (later ones are audio or
                # >200k-prompt tiers); a dated value replaces it from its start date.
                chosen = None
                for d, v in fields.get(key, []):
                    if d is None and chosen is None:
                        chosen = v
                    elif d is not None and effective and d <= effective:
                        chosen = v
                return chosen
            inp, out = pick("input"), pick("output")
            if inp is None or out is None:
                continue
            for model in dict.fromkeys(ids):
                entry = {"model": model, "provider": "google",
                         "prices": _prices(inp, inp, inp, pick("cache_read") if pick("cache_read") is not None else inp, out)}
                if effective:
                    entry["effective_from"] = effective
                found.append(entry)
    return found


SOURCES = {
    "anthropic": ("https://platform.claude.com/docs/en/about-claude/pricing.md", parse_anthropic),
    "openai": ("https://developers.openai.com/api/docs/pricing", parse_openai),
    "google": ("https://ai.google.dev/gemini-api/docs/pricing", parse_gemini),
    "xai": ("https://docs.x.ai/docs/models", parse_xai),
    "zai": ("https://docs.z.ai/guides/overview/pricing", parse_zai),
}


# -------------------------------------------------------------------- merge

def _same(a, b):
    return all((a.get(k) is None and b.get(k) is None) or
               (a.get(k) is not None and b.get(k) is not None and abs(a[k] - b[k]) < 1e-9) for k in BUCKETS)


def _matches(entry, model):
    return entry["model"] == model or model in entry.get("aliases", [])


def merge(table, parsed, source_url, parser, today=None):
    today = today or date.today().isoformat()
    changes = []
    models = table.setdefault("models", [])
    for item in parsed:
        model, effective = item["model"], item.get("effective_from")
        history = sorted((e for e in models if _matches(e, model)), key=lambda e: e["effective_from"])
        target_day = effective or today
        in_force = [e for e in history if e["effective_from"] <= target_day]
        current = in_force[-1] if in_force else None
        if current and _same(current["prices"], item["prices"]):
            if not effective or current["effective_from"] == effective:
                current["verified_at"] = today
                current["verified_by"] = f"aao prices update ({parser})"
                current["source_url"] = source_url
                for key in ("long_context", "notes"):
                    if key in item:
                        current[key] = item[key]
            continue
        if effective and any(e["effective_from"] == effective for e in history):
            continue  # already recorded; never rewrite an existing dated entry
        new = {"model": model, "provider": item["provider"],
               "effective_from": effective or (today if history else FIRST_KNOWN),
               "prices": item["prices"], "source_url": source_url, "verified_at": today,
               "verified_by": f"aao prices update ({parser})"}
        for key in ("long_context", "notes"):
            if key in item:
                new[key] = item[key]
        if history and history[-1].get("fast_multiplier") and not effective:
            new["fast_multiplier"] = history[-1]["fast_multiplier"]
        models.append(new)
        changes.append(("new model" if not history else "price change", model, new["effective_from"],
                        None if not current else current["prices"], item["prices"]))
    models.sort(key=lambda e: (e["provider"], e["model"], e["effective_from"]))
    return changes


def update(providers=None, dry_run=False, today=None):
    table = read_json(PRICES_FILE, {"schema": "api-prices-v1", "models": []})
    report = {"changes": [], "errors": {}, "parsed": {}}
    for provider, (url, parser) in SOURCES.items():
        if providers and provider not in providers:
            continue
        try:
            parsed = parser(http_get_text(url, accept="text/markdown"))
        except Exception as exc:  # network errors, layout changes
            report["errors"][provider] = f"{type(exc).__name__}: {exc}"
            continue
        if not parsed:
            report["errors"][provider] = "parser found no prices: the page layout probably changed"
            continue
        report["parsed"][provider] = len(parsed)
        report["changes"] += merge(table, parsed, url, f"{provider}-markdown", today)
    table["updated_at"] = today or date.today().isoformat()
    errors = validate_table(table)
    if errors:
        report["errors"]["validate"] = errors
    elif not dry_run:
        write_json(PRICES_FILE, table)
    return report
