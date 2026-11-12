"""Public API price lookup and cost calculation.

Order of precedence for a model:
  1. data/api_prices.json   committed, verified entries with effective dates
  2. state/litellm_prices.json   snapshot of LiteLLM's public price list (`aao prices sync`)
  3. unpriced                    cost is null and the model is reported as unpriced

Prices are per million tokens. An entry applies from its `effective_from` date
until a later entry for the same model, so price changes never re-price history.
"""

import fnmatch
import json
import re
from pathlib import Path

from orchestrator.state import DATA_DIR, STATE_DIR, http_get_json, write_json

PRICES_FILE = DATA_DIR / "api_prices.json"
LITELLM_FILE = STATE_DIR / "litellm_prices.json"
LITELLM_URL = "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"
BUCKETS = ("input", "cache_write_5m", "cache_write_1h", "cache_read", "output")
FIRST_PARTY = ("openai", "anthropic", "gemini", "xai", "zai", "deepseek", "mistral", "moonshot", "dashscope", "minimax")
FREE_MARKERS = (":free", "-free")


def normalize_model(model):
    """Drop provider prefixes, `:fast` and date suffixes: `anthropic/claude-x-20250101` -> `claude-x`."""
    name = (model or "").split(":")[0].strip().lower()
    name = name.rsplit("/", 1)[-1]
    name = re.sub(r"[-@]\d{8}$", "", name)
    return name


class PriceBook:
    def __init__(self, table=None, litellm=None):
        self.table = table if table is not None else _load(PRICES_FILE, {"models": []})
        self.litellm = litellm if litellm is not None else _load(LITELLM_FILE, {})
        self._litellm_index = {}
        for key, value in self.litellm.items():
            if isinstance(value, dict) and value.get("input_cost_per_token") is not None:
                self._litellm_index.setdefault(normalize_model(key), []).append((key, value))

    # ------------------------------------------------------------- lookup
    def _table_entry(self, model, day):
        name = normalize_model(model)
        best = None
        for entry in self.table.get("models", []):
            patterns = [entry["model"]] + entry.get("aliases", [])
            if not any(fnmatch.fnmatch(name, p.lower()) for p in patterns):
                continue
            if day and entry.get("effective_from") and entry["effective_from"] > day:
                continue
            if best is None or (entry.get("effective_from") or "") > (best.get("effective_from") or ""):
                best = entry
        return best

    def _litellm_raw(self, model):
        """Pick the first-party price: exact bare key, then the row's own provider, then a lab's own API.

        LiteLLM lists the same model under resellers (azure, bedrock, openrouter...) at different prices.
        """
        name = normalize_model(model)
        candidates = self._litellm_index.get(name)
        if not candidates:
            return None
        hint = (model or "").split("/")[0].lower() if "/" in (model or "") else None
        for key, value in candidates:
            if key.lower() == name:
                return value
        for wanted in ([hint] if hint else []) + list(FIRST_PARTY):
            for key, value in candidates:
                provider = str(value.get("litellm_provider") or "").lower()
                if provider == wanted or (wanted and provider.startswith(wanted)):
                    return value
        return candidates[0][1]

    def _litellm_entry(self, model):
        raw = self._litellm_raw(model)
        if not raw:
            return None
        per_m = lambda key: raw[key] * 1e6 if raw.get(key) is not None else None  # noqa: E731
        def first(*values):
            return next((v for v in values if v is not None), None)

        input_price = per_m("input_cost_per_token")
        write_5m = first(per_m("cache_creation_input_token_cost"), input_price)
        return {
            "model": normalize_model(model),
            "source": "litellm",
            "prices": {
                "input": input_price,
                "cache_write_5m": write_5m,
                "cache_write_1h": first(per_m("cache_creation_input_token_cost_above_1hr"), write_5m),
                "cache_read": first(per_m("cache_read_input_token_cost"), input_price),
                "output": per_m("output_cost_per_token"),
            },
        }

    def lookup(self, model, day=None):
        if any(marker in (model or "").lower() for marker in FREE_MARKERS):
            # Free-tier routes (e.g. `opencode/glm-5.3-flash-free`, `:free`) have no public API fee.
            return {"model": model, "source": "free", "prices": {b: 0.0 for b in BUCKETS}}
        entry = self._table_entry(model, day)
        if entry:
            return {**entry, "source": entry.get("source_kind", "table")}
        return self._litellm_entry(model)

    # --------------------------------------------------------------- cost
    def cost(self, row):
        """API-equivalent USD for a usage row, or None when the model is unpriced."""
        entry = self.lookup(row.get("model"), row.get("day"))
        if not entry:
            return None, None
        prices = dict(entry["prices"])
        if (row.get("model") or "").endswith(":fast") and entry.get("fast_multiplier"):
            prices = {k: (v * entry["fast_multiplier"] if v is not None else None) for k, v in prices.items()}
        total = 0.0
        for bucket in BUCKETS:
            tokens = row.get(bucket) or 0
            if not tokens:
                continue
            price = prices.get(bucket)
            if price is None:
                price = prices.get("input") if bucket.startswith("cache") else None
            if price is None:
                return None, entry.get("source")
            total += tokens * price / 1e6
        return round(total, 6), entry.get("source")


def _load(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def sync_litellm(url=LITELLM_URL):
    """Download LiteLLM's price list (data, not code) into state/."""
    data = http_get_json(url)
    write_json(LITELLM_FILE, data)
    return len(data)


def validate_table(table):
    errors = []
    if table.get("schema") != "api-prices-v1":
        errors.append("schema must be api-prices-v1")
    seen = set()
    for i, entry in enumerate(table.get("models", [])):
        where = f"models[{i}] {entry.get('model')}"
        for key in ("model", "provider", "effective_from", "prices", "source_url", "verified_at"):
            if not entry.get(key):
                errors.append(f"{where}: missing {key}")
        prices = entry.get("prices") or {}
        for bucket in ("input", "output"):
            if not isinstance(prices.get(bucket), (int, float)):
                errors.append(f"{where}: prices.{bucket} must be a number")
        for bucket in BUCKETS:
            value = prices.get(bucket)
            if value is not None and (not isinstance(value, (int, float)) or value < 0):
                errors.append(f"{where}: prices.{bucket} must be a non-negative number or null")
        key = (entry.get("model"), entry.get("effective_from"))
        if key in seen:
            errors.append(f"{where}: duplicate model + effective_from")
        seen.add(key)
    return errors
