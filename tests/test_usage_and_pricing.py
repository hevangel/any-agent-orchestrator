import json

from orchestrator import usage_scan
from orchestrator.pricing import PriceBook, validate_table
from orchestrator.state import DATA_DIR, read_json


def _write_lines(path, entries):
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")


def test_claude_dedupes_repeated_message_lines_and_splits_cache_ttl(tmp_path):
    usage = {"input_tokens": 2, "cache_creation_input_tokens": 300, "cache_read_input_tokens": 1000,
             "output_tokens": 50, "cache_creation": {"ephemeral_1h_input_tokens": 200, "ephemeral_5m_input_tokens": 100}}
    line = {"type": "assistant", "sessionId": "s1", "cwd": "/repo", "timestamp": "2026-11-10T01:00:00Z",
            "requestId": "r1", "message": {"id": "m1", "model": "claude-opus-5-5", "usage": usage}}
    path = tmp_path / "s1.jsonl"
    _write_lines(path, [line, line])  # one line per content block, same usage
    rows = usage_scan.scan_claude_file(str(path))
    assert len(rows) == 1
    row = rows[0]
    assert (row["input"], row["cache_write_5m"], row["cache_write_1h"], row["cache_read"], row["output"]) == (2, 100, 200, 1000, 50)
    assert row["requests"] == 1


def test_codex_counts_only_increases_of_cumulative_totals(tmp_path):
    def tc(ts, inp, cached, out):
        return {"timestamp": ts, "type": "event_msg", "payload": {"type": "token_count", "info": {"total_token_usage": {
            "input_tokens": inp, "cached_input_tokens": cached, "output_tokens": out, "total_tokens": inp + out}}}}
    entries = [
        {"type": "session_meta", "payload": {"id": "c1", "cwd": "/repo"}},
        {"type": "turn_context", "payload": {"model": "gpt-6.1-sol"}},
        tc("2026-11-10T01:00:00Z", 1000, 800, 10),
        tc("2026-11-10T01:00:01Z", 1000, 800, 10),  # re-emitted on a rate-limit update
        tc("2026-11-10T01:05:00Z", 3000, 2500, 30),
    ]
    path = tmp_path / "rollout-x.jsonl"
    _write_lines(path, entries)
    rows = usage_scan.scan_codex_file(str(path))
    assert len(rows) == 1
    row = rows[0]
    # input includes cached for OpenAI: non-cached = 3000 - 2500
    assert (row["input"], row["cache_read"], row["output"], row["requests"]) == (500, 2500, 30, 2)


def test_opencode_reasoning_billed_as_output():
    tokens = usage_scan.opencode_tokens({"input": 10, "output": 5, "reasoning": 7, "cache": {"read": 100, "write": 3}})
    assert tokens == {"input": 10, "cache_write_5m": 3, "cache_write_1h": 0, "cache_read": 100, "output": 12, "reasoning": 7}


def _book(models, litellm=None):
    return PriceBook(table={"schema": "api-prices-v1", "models": models}, litellm=litellm or {})


def _entry(model, eff, inp, out, **extra):
    return {"model": model, "provider": "x", "effective_from": eff, "source_url": "https://x", "verified_at": eff,
            "prices": {"input": inp, "cache_write_5m": inp * 1.25, "cache_write_1h": inp * 2, "cache_read": inp / 10, "output": out}, **extra}


def test_price_change_does_not_reprice_history():
    book = _book([_entry("m", "2020-01-01", 10, 50), _entry("m", "2026-07-30", 5, 25)])
    old = {"model": "m", "day": "2026-07-01", "input": 1_000_000, "output": 0}
    new = {"model": "m", "day": "2026-08-01", "input": 1_000_000, "output": 0}
    assert book.cost(old)[0] == 10
    assert book.cost(new)[0] == 5


def test_table_beats_litellm_and_fast_multiplier_applies():
    litellm = {"m": {"input_cost_per_token": 1e-6, "output_cost_per_token": 1e-6, "litellm_provider": "openai"}}
    book = _book([_entry("m", "2020-01-01", 4, 20, fast_multiplier=2.0)], litellm)
    assert book.cost({"model": "anthropic/m-20260101", "day": "2026-11-01", "output": 1_000_000})[0] == 20
    assert book.cost({"model": "m:fast", "day": "2026-11-01", "output": 1_000_000})[0] == 40


def test_litellm_prefers_first_party_over_resellers():
    litellm = {
        "azure/gpt-x": {"input_cost_per_token": 1e-6, "output_cost_per_token": 6e-6, "litellm_provider": "azure"},
        "gpt-x": {"input_cost_per_token": 2e-7, "output_cost_per_token": 1.2e-6, "litellm_provider": "openai",
                  "cache_read_input_token_cost": 2e-8},
        "zai/glm-y": {"input_cost_per_token": 1.5e-7, "output_cost_per_token": 5e-7, "litellm_provider": "zai"},
        "openrouter/glm-y": {"input_cost_per_token": 9e-7, "output_cost_per_token": 9e-7, "litellm_provider": "openrouter"},
    }
    book = _book([], litellm)
    assert round(book.lookup("gpt-x")["prices"]["input"], 4) == 0.2
    assert round(book.lookup("zai-coding-plan/glm-y")["prices"]["input"], 4) == 0.15


def test_free_routes_cost_zero_and_unknown_models_are_unpriced():
    book = _book([])
    assert book.cost({"model": "opencode/glm-5.3-flash-free", "day": "2026-11-01", "input": 5})[0] == 0
    assert book.cost({"model": "nobody/knows", "day": "2026-11-01", "input": 5}) == (None, None)


def test_committed_price_table_is_valid():
    assert validate_table(read_json(DATA_DIR / "api_prices.json")) == []
