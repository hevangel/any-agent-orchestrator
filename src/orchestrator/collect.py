"""Run collectors and write dashboard snapshots into state/."""

import json
import time
from datetime import datetime, timedelta, timezone

from orchestrator import machines, quotas, usage
from orchestrator.state import STATE_DIR, write_json

QUOTA_HISTORY = STATE_DIR / "quota_history.jsonl"


def _epoch(iso):
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def collect_machines():
    data = machines.collect_all()
    write_json(STATE_DIR / "machines.json", data)
    return data


def _load_history():
    try:
        lines = QUOTA_HISTORY.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def collect_quotas():
    data = quotas.collect()
    now = time.time()
    QUOTA_HISTORY.parent.mkdir(parents=True, exist_ok=True)
    with QUOTA_HISTORY.open("a", encoding="utf-8") as handle:
        for acc in data["accounts"]:
            for w in acc["windows"]:
                if w["used_pct"] is not None:
                    handle.write(json.dumps({"t": now, "provider": acc["provider"], "account": acc["account"],
                                             "window": w["name"], "scope": w["scope"], "resets_at": w["resets_at"],
                                             "used_pct": w["used_pct"]}) + "\n")
    history = _load_history()
    for acc in data["accounts"]:
        for w in acc["windows"]:
            points = [(h["t"], h["used_pct"]) for h in history
                      if (h["provider"], h["account"], h["window"], h.get("scope"), h["resets_at"])
                      == (acc["provider"], acc["account"], w["name"], w["scope"], w["resets_at"])]
            runout = quotas.project(points, None, w["name"], w["resets_at"], now)
            reset = _epoch(w["resets_at"])
            w["runout_seconds"] = runout
            w["runs_out_before_reset"] = bool(runout is not None and reset and now + runout < reset)
    write_json(STATE_DIR / "quotas.json", data)
    return data


def collect_usage(days=90):
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    data = usage.collect(since=since)
    summary = usage.summarize(data["rows"])
    cube = {}
    for row in data["rows"]:
        key = (row["day"], row["machine"], row["agent"], row["model"])
        cell = cube.setdefault(key, {"day": row["day"], "machine": row["machine"], "agent": row["agent"],
                                     "model": row["model"], "cost_usd": 0.0, "priced": True}
                               | {f: 0 for f in usage.TOKEN_FIELDS})
        for f in usage.TOKEN_FIELDS:
            cell[f] += row[f]
        if row["cost_usd"] is None:
            cell["priced"] = False
        else:
            cell["cost_usd"] = round(cell["cost_usd"] + row["cost_usd"], 6)
    out = {"schema": "usage-dashboard-v1", "since": since,
           "collected_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "by_day_machine_agent_model": sorted(cube.values(), key=lambda c: c["day"]),
           "sessions": summary["sessions"][:500], "unpriced_models": data["unpriced_models"],
           "errors": data["errors"]}
    write_json(STATE_DIR / "usage.json", out)
    return out


COLLECTORS = {"machines": collect_machines, "quotas": collect_quotas, "usage": collect_usage}
