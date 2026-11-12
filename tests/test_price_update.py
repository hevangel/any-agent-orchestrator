"""Parsers run against small synthetic pages in each provider's real layout; no network."""

from orchestrator import price_update as pu

OPENAI = """# Pricing
### Standard pricing data

| Model | Short context input | Short context output | Short context cached input | Short context 30m write | Short context 12h write | Long context input | Long context output | Long context cached input | Long context 30m write | Long context 12h write |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| gpt-9-sol | $2.00 | $10.00 | $0.10 | $2.50 | $5.00 | $4.00 | $15.00 | $0.20 | $5.00 | $10.00 |
| gpt-9 (<272K context length) | $5.00 | $30.00 | $0.50 | - | - | $10.00 | $45.00 | $1.00 | - | - |

### Batch pricing data

| Model | Short context input | Short context output | Short context cached input |
| --- | --- | --- | --- |
| gpt-9-sol | $1.00 | $5.00 | $0.05 |
"""

ANTHROPIC = """| Model | Base input tokens | 5m cache writes | 1h cache writes | Cache hits and refreshes | Output tokens |
| :---- | :---- | :---- | :---- | :---- | :---- |
| Claude Opus 9.5 | $4 / MTok | $5 / MTok | $8 / MTok | $0.20 / MTok | $20 / MTok |
| Claude Mythos 9 ([limited availability](https://example.com/a)) | $10 / MTok | $12.50 / MTok | $20 / MTok | $1 / MTok | $50 / MTok |
| Claude Haiku 9 (for prompts up to 100,000 tokens) | $0.10 / MTok | $0.125 / MTok | $0.20 / MTok | $0.01 / MTok | $0.50 / MTok |
| Claude Haiku 9 (for prompts over 100,000 tokens) | $0.50 / MTok | $0.625 / MTok | $1 / MTok | $0.05 / MTok | $2.50 / MTok |
"""

XAI = """| Model | Context | Input / 1M tokens | Cached input / 1M tokens | Output / 1M tokens |
| --- | --- | --- | --- | --- |
| grok-9 (< 200k prompt tokens) | 500k | $2.00 | $0.50 | $6.00 |
| grok-9 (≥ 200k prompt tokens) | 500k | $4.00 | $1.00 | $12.00 |
"""

ZAI = """| Model | Input | Cached Input | Cached Input Storage | Output |
| :- | :- | :- | :- | :- |
| GLM-9 | \\$1.4 | \\$0.26 | Limited-time Free | \\$4.4 |
| GLM-9-Flash | Free | Free | Free | Free |
"""

GEMINI = """
## Gemini 9 Flash  {% id="gemini-9-flash" %}
  *[`gemini-9-flash`](/docs/models/gemini-9-flash)*
{% tabs %}
  {% tab title="Standard" %}
  {% table %}
  * Input price
  * Free of charge
  * $0.75 through December 31, 2026.
  $1.50 starting January 1, 2027.
  ---
  * Output price (including thinking tokens)
  * Free of charge
  * $3.75 through December 31, 2026.
  $7.50 starting January 1, 2027.
  ---
  * Context caching price
  * Free of charge
  * $0.075 through December 31, 2026.
  $0.15 starting January 1, 2027.
  $0.50 / 1,000,000 tokens per hour (storage price) through December 31, 2026.
  ---
  {% /table %}
  {% /tab %}
  {% tab title="Batch" %}
  * Input price
  * Not available
  * $0.01
  ---
## Gemini 9 Pro  {% id="gemini-9-pro" %}
  *[`gemini-9-pro`](/docs/models/gemini-9-pro)*
  {% tab title="Standard" %}
  * Input price
  * Not available
  * $2.00, prompts <= 200k tokens
  $4.00, prompts > 200k tokens
  ---
  * Output price (including thinking tokens)
  * Not available
  * $12.00, prompts <= 200k tokens
  $18.00, prompts > 200k
  ---
## Gemini 9 Flash TTS  {% id="gemini-9-flash-tts" %}
  *[`gemini-9-flash-tts`](/docs/models/gemini-9-flash-tts)*
  * Input price
  * Free
  * $0.50 (text)
  ---
  * Output price
  * Free
  * $10.00 (audio)
  ---
"""


def by_model(rows):
    return {(r["model"], r.get("effective_from")): r for r in rows}


def test_openai_standard_table_only_with_ttl_writes():
    rows = by_model(pu.parse_openai(OPENAI))
    assert rows[("gpt-9-sol", None)]["prices"] == {"input": 2.0, "cache_write_5m": 2.5, "cache_write_1h": 5.0,
                                                   "cache_read": 0.1, "output": 10.0}
    assert rows[("gpt-9", None)]["prices"]["cache_write_5m"] is None
    assert rows[("gpt-9", None)]["long_context"]["input"] == 10.0
    assert len(rows) == 2  # Batch table ignored


def test_anthropic_ids_links_and_long_prompt_tier():
    rows = by_model(pu.parse_anthropic(ANTHROPIC))
    assert set(m for m, _ in rows) == {"claude-opus-9-5", "claude-mythos-9", "claude-haiku-9"}
    haiku = rows[("claude-haiku-9", None)]
    assert haiku["prices"]["input"] == 0.10
    assert haiku["long_context"]["above_input_tokens"] == 100000 and haiku["long_context"]["input"] == 0.50


def test_xai_short_tier_and_writes_billed_as_input():
    (row,) = pu.parse_xai(XAI)
    assert row["prices"] == {"input": 2.0, "cache_write_5m": 2.0, "cache_write_1h": 2.0, "cache_read": 0.5, "output": 6.0}
    assert row["long_context"]["output"] == 12.0


def test_zai_lowercases_ids_and_free_is_zero():
    rows = by_model(pu.parse_zai(ZAI))
    assert rows[("glm-9", None)]["prices"]["cache_read"] == 0.26
    assert rows[("glm-9-flash", None)]["prices"]["output"] == 0.0


def test_gemini_dated_prices_first_tier_and_non_text_skipped():
    rows = by_model(pu.parse_gemini(GEMINI))
    assert rows[("gemini-9-flash", None)]["prices"]["input"] == 0.75
    assert rows[("gemini-9-flash", None)]["prices"]["cache_read"] == 0.075
    assert rows[("gemini-9-flash", "2027-01-01")]["prices"]["output"] == 7.5
    assert rows[("gemini-9-pro", None)]["prices"]["input"] == 2.0  # <=200k tier, not the >200k one
    assert not any(m == "gemini-9-flash-tts" for m, _ in rows)


def _parsed(inp, eff=None):
    item = {"model": "m", "provider": "p", "prices": {"input": inp, "cache_write_5m": inp, "cache_write_1h": inp,
                                                      "cache_read": inp / 10, "output": inp * 5}}
    if eff:
        item["effective_from"] = eff
    return item


def test_merge_never_rewrites_history():
    table = {"schema": "api-prices-v1", "models": []}
    first = pu.merge(table, [_parsed(1.0)], "https://x", "t", today="2026-11-01")
    assert first[0][0] == "new model" and table["models"][0]["effective_from"] == "2020-01-01"

    assert pu.merge(table, [_parsed(1.0)], "https://x", "t", today="2026-11-02") == []
    assert table["models"][0]["verified_at"] == "2026-11-02"

    change = pu.merge(table, [_parsed(0.5)], "https://x", "t", today="2026-11-03")
    assert change[0][0] == "price change"
    assert [(e["effective_from"], e["prices"]["input"]) for e in table["models"]] == [("2020-01-01", 1.0), ("2026-11-03", 0.5)]


def test_merge_records_future_dated_price_once():
    table = {"schema": "api-prices-v1", "models": []}
    pu.merge(table, [_parsed(1.0), _parsed(2.0, "2027-01-01")], "https://x", "t", today="2026-11-01")
    pu.merge(table, [_parsed(1.0), _parsed(2.0, "2027-01-01")], "https://x", "t", today="2026-11-02")
    assert [(e["effective_from"], e["prices"]["input"]) for e in table["models"]] == [("2020-01-01", 1.0), ("2027-01-01", 2.0)]
