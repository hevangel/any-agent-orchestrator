"""Token usage scanner for local agent session logs.

Standard library only, so it can be piped to a remote machine:
`ssh host python3 - --since 2026-10-01 < usage_scan.py`. Prints a JSON list of
rows, one per (agent, session, model, day), with token counts split the way API
pricing is: non-cached input, 5-minute and 1-hour cache writes, cache reads and
output. Prices are applied by the caller (orchestrator.pricing), so a price
table change re-prices history without rescanning.

Sources:
  claude    ~/.claude/projects/*/*.jsonl          per-message usage (deduplicated by message id)
  codex     ~/.codex/sessions/**/rollout-*.jsonl  cumulative token_count events (deltas)
  opencode  ~/.local/share/opencode/opencode.db   message.data.tokens
  kilo      ~/.local/share/kilo/kilo.db           same schema as opencode

Each file's rows are cached by (mtime, size) in ~/.cache/aao/usage_scan.json so
repeated scans only re-read changed files.
"""

import glob
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

SCHEMA = "usage-rows-v1"
CACHE_VERSION = 2
TOKEN_FIELDS = ("input", "cache_write_5m", "cache_write_1h", "cache_read", "output", "reasoning")


def _home():
    return os.path.expanduser("~")


def _day(ts):
    return ts[:10] if isinstance(ts, str) and len(ts) >= 10 else None


def _epoch_ms_to_iso(ms):
    try:
        return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError, OverflowError, OSError):
        return None


class _Rows:
    """Accumulates usage into rows keyed by (agent, session, model, day)."""

    def __init__(self):
        self.rows = {}

    def add(self, agent, session, project, model, ts, tokens):
        day = _day(ts)
        if day is None:
            return
        key = (agent, session, model or "unknown", day)
        row = self.rows.get(key)
        if row is None:
            row = {"agent": agent, "session": session, "project": project, "model": model or "unknown",
                   "day": day, "first_ts": ts, "last_ts": ts, "requests": 0}
            row.update({field: 0 for field in TOKEN_FIELDS})
            self.rows[key] = row
        row["requests"] += 1
        for field in TOKEN_FIELDS:
            row[field] += int(tokens.get(field) or 0)
        if project and not row["project"]:
            row["project"] = project
        row["first_ts"] = min(row["first_ts"], ts)
        row["last_ts"] = max(row["last_ts"], ts)

    def values(self):
        return list(self.rows.values())


# ---------------------------------------------------------------- Claude Code

def claude_tokens(usage):
    """Map an Anthropic `usage` object to priced token buckets.

    `input_tokens` already excludes cached tokens. The 5m/1h split comes from
    `cache_creation`; without it every write is treated as 5-minute.
    """
    creation = usage.get("cache_creation") or {}
    write_total = int(usage.get("cache_creation_input_tokens") or 0)
    write_1h = int(creation.get("ephemeral_1h_input_tokens") or 0)
    write_5m = int(creation.get("ephemeral_5m_input_tokens") or 0)
    if write_1h + write_5m == 0:
        write_5m = write_total
    details = usage.get("output_tokens_details") or {}
    return {
        "input": int(usage.get("input_tokens") or 0),
        "cache_write_5m": write_5m,
        "cache_write_1h": write_1h,
        "cache_read": int(usage.get("cache_read_input_tokens") or 0),
        "output": int(usage.get("output_tokens") or 0),
        "reasoning": int(details.get("thinking_tokens") or 0),
    }


def scan_claude_file(path):
    rows = _Rows()
    seen = set()
    session = os.path.splitext(os.path.basename(path))[0]
    with open(path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if '"usage"' not in line:
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            message = entry.get("message") or {}
            usage = message.get("usage")
            if entry.get("type") != "assistant" or not isinstance(usage, dict):
                continue
            # Claude Code writes one line per content block, each repeating the message's usage.
            key = (message.get("id"), entry.get("requestId"))
            if key != (None, None):
                if key in seen:
                    continue
                seen.add(key)
            model = message.get("model")
            if model == "<synthetic>":
                continue
            agent = "claude"
            if usage.get("speed") == "fast":
                model = f"{model}:fast"
            rows.add(agent, entry.get("sessionId") or session, entry.get("cwd"), model,
                     entry.get("timestamp"), claude_tokens(usage))
    return rows.values()


# ---------------------------------------------------------------------- Codex

def codex_tokens(delta):
    """OpenAI semantics: `input_tokens` includes cached input; output includes reasoning."""
    cached = int(delta.get("cached_input_tokens") or 0)
    return {
        "input": max(int(delta.get("input_tokens") or 0) - cached, 0),
        "cache_write_5m": int(delta.get("cache_write_input_tokens") or 0),
        "cache_write_1h": 0,
        "cache_read": cached,
        "output": int(delta.get("output_tokens") or 0),
        "reasoning": int(delta.get("reasoning_output_tokens") or 0),
    }


_CODEX_KEYS = ("input_tokens", "cached_input_tokens", "cache_write_input_tokens", "output_tokens",
               "reasoning_output_tokens", "total_tokens")


def scan_codex_file(path):
    rows = _Rows()
    session = project = None
    model = None
    previous = {key: 0 for key in _CODEX_KEYS}
    with open(path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            payload = entry.get("payload") or {}
            kind = entry.get("type")
            if kind == "session_meta":
                session = payload.get("id") or session
                project = payload.get("cwd") or project
            elif kind == "turn_context":
                model = payload.get("model") or model
            elif kind == "event_msg" and payload.get("type") == "token_count":
                total = (payload.get("info") or {}).get("total_token_usage")
                if not isinstance(total, dict):
                    continue
                # Totals are cumulative and the same total is re-emitted on rate-limit updates:
                # count only the increase since the last event.
                if int(total.get("total_tokens") or 0) <= previous["total_tokens"]:
                    continue
                delta = {key: max(int(total.get(key) or 0) - previous[key], 0) for key in _CODEX_KEYS}
                previous = {key: int(total.get(key) or 0) for key in _CODEX_KEYS}
                rows.add("codex", session or os.path.basename(path), project, model,
                         entry.get("timestamp"), codex_tokens(delta))
    return rows.values()


# ----------------------------------------------------------- OpenCode / Kilo

def opencode_tokens(tokens):
    cache = tokens.get("cache") or {}
    reasoning = int(tokens.get("reasoning") or 0)
    return {
        "input": int(tokens.get("input") or 0),
        "cache_write_5m": int(cache.get("write") or 0),
        "cache_write_1h": 0,
        "cache_read": int(cache.get("read") or 0),
        # Reasoning is reported separately from output and billed at the output rate.
        "output": int(tokens.get("output") or 0) + reasoning,
        "reasoning": reasoning,
    }


def scan_opencode_db(path, agent):
    rows = _Rows()
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        cursor = con.execute(
            "select m.session_id, m.time_created, m.data, s.directory from message m "
            "left join session s on s.id = m.session_id")
    except sqlite3.Error:
        return []
    for session, created, data, directory in cursor:
        try:
            message = json.loads(data)
        except (TypeError, ValueError):
            continue
        tokens = message.get("tokens")
        if message.get("role") != "assistant" or not isinstance(tokens, dict):
            continue
        model = message.get("modelID")
        provider = message.get("providerID")
        if provider and model and "/" not in model:
            model = f"{provider}/{model}"
        ts = _epoch_ms_to_iso((message.get("time") or {}).get("created") or created)
        rows.add(agent, session or message.get("sessionID"), directory, model, ts, opencode_tokens(tokens))
    con.close()
    return rows.values()


# ----------------------------------------------------------------- scanning

def sources(home=None):
    home = home or _home()
    found = []
    for path in glob.glob(os.path.join(home, ".claude", "projects", "*", "*.jsonl")):
        found.append(("claude", path))
    for path in glob.glob(os.path.join(home, ".codex", "sessions", "**", "rollout-*.jsonl"), recursive=True):
        found.append(("codex", path))
    for agent, rel in (("opencode", (".local", "share", "opencode", "opencode.db")),
                       ("kilo", (".local", "share", "kilo", "kilo.db"))):
        path = os.path.join(home, *rel)
        if os.path.exists(path):
            found.append((agent, path))
    return found


def _scan_one(agent, path):
    if agent == "claude":
        return scan_claude_file(path)
    if agent == "codex":
        return scan_codex_file(path)
    return scan_opencode_db(path, agent)


def _cache_path():
    return os.path.join(_home(), ".cache", "aao", "usage_scan.json")


def _load_cache():
    try:
        with open(_cache_path(), encoding="utf-8") as handle:
            cache = json.load(handle)
        if cache.get("version") == CACHE_VERSION:
            return cache
    except (OSError, ValueError):
        pass
    return {"version": CACHE_VERSION, "files": {}}


def _save_cache(cache):
    path = _cache_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(cache, handle)
    os.replace(tmp, path)


def scan(since=None, home=None, use_cache=True):
    cache = _load_cache() if use_cache else {"version": CACHE_VERSION, "files": {}}
    files = {}
    rows = []
    for agent, path in sources(home):
        try:
            stat = os.stat(path)
        except OSError:
            continue
        # SQLite databases change through their -wal file; include it in the fingerprint.
        wal = path + "-wal"
        wal_stamp = os.stat(wal).st_mtime if os.path.exists(wal) else 0
        stamp = [stat.st_mtime, stat.st_size, wal_stamp]
        entry = cache["files"].get(path)
        if entry and entry.get("stamp") == stamp:
            file_rows = entry["rows"]
        else:
            file_rows = _scan_one(agent, path)
        files[path] = {"stamp": stamp, "rows": file_rows}
        rows.extend(file_rows)
    if use_cache:
        cache["files"] = files
        try:
            _save_cache(cache)
        except OSError:
            pass
    if since:
        rows = [row for row in rows if row["day"] >= since]
    return rows


def main(argv):
    since = None
    if "--since" in argv:
        since = argv[argv.index("--since") + 1]
    print(json.dumps({"schema": SCHEMA, "rows": scan(since=since, use_cache="--no-cache" not in argv)}))


if __name__ == "__main__":
    main(sys.argv[1:])
