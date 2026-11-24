import json

from orchestrator import usage, usage_scan


def _jsonl(path, events):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    return path


START = {"type": "session.start", "timestamp": "2026-11-22T06:37:00Z",
         "data": {"sessionId": "s-1", "context": {"cwd": "B:\\repo"}}}


def _record(ts, model, inp, read, write, out, reasoning=0, ttl=1800):
    return {"type": "session.usage_record", "timestamp": ts, "data": {"usage": {
        "model": model, "inputTokens": inp, "cacheReadTokens": read, "cacheWriteTokens": write,
        "outputTokens": out, "reasoningTokens": reasoning, "cacheTtlSeconds": ttl}}}


def _shutdown(ts, model, inp, read, write, out, premium, requests=1):
    return {"type": "session.shutdown", "timestamp": ts, "data": {"totalPremiumRequests": premium, "modelMetrics": {
        model: {"requests": {"count": requests}, "usage": {
            "inputTokens": inp, "cacheReadTokens": read, "cacheWriteTokens": write, "outputTokens": out}}}}}


def test_copilot_input_includes_cache_and_records_win_over_shutdown_totals(tmp_path):
    path = _jsonl(tmp_path / "s-1" / "events.jsonl", [
        START,
        _record("2026-11-22T06:37:55Z", "gpt-6-luna", 22444, 8704, 0, 148, reasoning=57),
        _record("2026-11-23T01:00:00Z", "claude-sonnet-5", 37187, 21424, 15761, 1017, ttl=3600),
        _shutdown("2026-11-23T01:01:00Z", "claude-sonnet-5", 37187, 21424, 15761, 1017, 1),
    ])
    rows = sorted(usage_scan.scan_copilot_file(str(path)), key=lambda r: r["day"])
    assert [(r["agent"], r["session"], r["project"], r["model"], r["day"]) for r in rows] == [
        ("copilot", "s-1", "B:\\repo", "gpt-6-luna", "2026-11-22"),
        ("copilot", "s-1", "B:\\repo", "claude-sonnet-5", "2026-11-23")]
    assert (rows[0]["input"], rows[0]["cache_read"], rows[0]["output"], rows[0]["reasoning"]) == (13740, 8704, 148, 57)
    # 37,187 = 2 uncached + 21,424 read + 15,761 written: the shutdown total is not counted again.
    assert (rows[1]["input"], rows[1]["cache_read"], rows[1]["cache_write_1h"], rows[1]["cache_write_5m"]) == (2, 21424, 15761, 0)
    assert sum(r["requests"] for r in rows) == 2


def test_copilot_shutdown_only_sessions_count_runs_and_running_totals_once(tmp_path):
    per_run = _jsonl(tmp_path / "old.jsonl", [
        _shutdown("2026-07-01T03:08:00Z", "claude-sonnet-5", 1000, 0, 0, 10, 1),
        _shutdown("2026-07-01T03:10:00Z", "claude-sonnet-5", 3000, 0, 0, 30, 1),   # new run, not a total
    ])
    (row,) = usage_scan.scan_copilot_file(str(per_run))
    assert row["session"] == "old" and (row["input"], row["output"], row["requests"]) == (4000, 40, 2)

    running = _jsonl(tmp_path / "r" / "events.jsonl", [
        _shutdown("2026-10-25T08:13:00Z", "gpt-6-luna", 700, 0, 0, 7, 2, requests=2),
        _shutdown("2026-10-25T09:00:00Z", "gpt-6-luna", 1500, 0, 0, 15, 7, requests=7),  # resumed: cumulative
    ])
    (row,) = usage_scan.scan_copilot_file(str(running))
    assert row["session"] == "r" and (row["input"], row["output"], row["requests"]) == (1500, 15, 7)


def _kiro(path, turns, model="claude-opus-5.5"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"session_id": "k-1", "cwd": "C:\\work", "created_at": "2026-10-18T21:44:42Z",
                                "session_state": {"conversation_metadata": {"user_turn_metadatas": turns}}}),
                    encoding="utf-8")
    return path


def _turn(ts, credits, requests, model="claude-opus-5.5", tokens=0):
    return {"end_timestamp": ts, "model": model, "total_request_count": requests, "input_token_count": tokens,
            "output_token_count": 0, "cache_read_input_token_count": 0, "cache_write_input_token_count": 0,
            "metering_usage": [{"value": c, "unit": "credit"} for c in credits]}


def test_kiro_turns_become_credit_rows_without_invented_tokens(tmp_path):
    path = _kiro(tmp_path / "k-1.json", [
        _turn("2026-10-18T21:51:08Z", [1.25, 0.5], 12),
        _turn("2026-10-18T22:10:00Z", [0.25], 3),
        _turn("2026-10-19T01:00:00Z", [2.0], 5, model="auto"),
        "not a turn",
    ])
    rows = sorted(usage_scan.scan_kiro_session(str(path)), key=lambda r: r["day"])
    assert [(r["model"], r["day"], r["requests"], r["credits"]) for r in rows] == [
        ("claude-opus-5.5", "2026-10-18", 15, 2.0), ("kiro/auto", "2026-10-19", 5, 2.0)]
    assert all(r[f] == 0 for r in rows for f in usage_scan.TOKEN_FIELDS)
    assert rows[0]["project"] == "C:\\work" and rows[0]["session"] == "k-1"
    assert usage_scan.scan_kiro_session(str(tmp_path / "missing.json")) == []


def test_sources_find_copilot_and_kiro_logs(tmp_path):
    a = _jsonl(tmp_path / ".copilot" / "session-state" / "x" / "events.jsonl", [START])
    b = _jsonl(tmp_path / ".copilot" / "session-state" / "y.jsonl", [START])
    c = _kiro(tmp_path / ".kiro" / "sessions" / "cli" / "k.json", [])
    found = usage_scan.sources(str(tmp_path))
    assert ("copilot", str(a)) in found and ("copilot", str(b)) in found and ("kiro", str(c)) in found


def test_credit_only_rows_are_free_and_never_unpriced(monkeypatch):
    row = {"agent": "kiro", "session": "k", "project": None, "model": "kiro/auto", "day": "2026-10-19",
           "first_ts": "2026-10-19T01:00:00Z", "last_ts": "2026-10-19T01:00:00Z", "requests": 5, "credits": 2.0,
           **{f: 0 for f in usage_scan.TOKEN_FIELDS}}
    monkeypatch.setattr(usage_scan, "scan", lambda since=None: [row])
    data = usage.collect(include_remote=False)
    assert data["unpriced_models"] == {} and data["rows"][0]["cost_usd"] == 0.0
    (session,) = usage.summarize(data["rows"])["sessions"]
    assert (session["credits"], session["requests"]) == (2.0, 5)
