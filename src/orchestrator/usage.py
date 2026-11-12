"""Token usage and API-equivalent cost across every machine.

Local rows come from orchestrator.usage_scan in-process; ssh machines run the
same stdlib scanner over SSH (its cache lives on that machine). Rows are priced
here so a price-table change re-prices history immediately.
"""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from orchestrator import usage_scan
from orchestrator.machines import SSH_OPTIONS, _json, _run, herdr_machines
from orchestrator.pricing import PriceBook

SCAN_SOURCE = Path(usage_scan.__file__).read_text(encoding="utf-8")
TOKEN_FIELDS = usage_scan.TOKEN_FIELDS


def remote_rows(target, since):
    args = ["ssh", *SSH_OPTIONS, target, "python3", "-", "--since", since] if since else \
           ["ssh", *SSH_OPTIONS, target, "python3", "-"]
    ok, out, err = _run(args, timeout=300, stdin=SCAN_SOURCE)
    payload = _json(out) if ok else None
    if not payload:
        return [], err or "no output"
    return payload.get("rows", []), None


def collect(since=None, include_remote=True):
    machines = {"local": (usage_scan.scan(since=since), None)}
    if include_remote:
        remote, _ = herdr_machines()
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {m["label"]: pool.submit(remote_rows, m.get("target") or m["label"], since) for m in remote}
            machines.update({label: f.result() for label, f in futures.items()})
    book = PriceBook()
    rows, errors, unpriced = [], {}, {}
    for machine, (machine_rows, error) in machines.items():
        if error:
            errors[machine] = error
        for row in machine_rows:
            cost, source = book.cost(row)
            row = {**row, "machine": machine, "cost_usd": cost, "price_source": source}
            if cost is None:
                unpriced[row["model"]] = unpriced.get(row["model"], 0) + sum(row[f] for f in TOKEN_FIELDS)
            rows.append(row)
    return {"schema": "usage-v1-priced", "rows": rows, "errors": errors, "unpriced_models": unpriced}


def summarize(rows):
    """Daily totals and per-session totals for the dashboard."""
    days, sessions = {}, {}
    for row in rows:
        day = days.setdefault(row["day"], {f: 0 for f in TOKEN_FIELDS} | {"day": row["day"], "cost_usd": 0.0})
        key = (row["machine"], row["agent"], row["session"])
        sess = sessions.setdefault(key, {f: 0 for f in TOKEN_FIELDS} | {
            "machine": row["machine"], "agent": row["agent"], "session": row["session"], "project": row.get("project"),
            "models": set(), "first_ts": row["first_ts"], "last_ts": row["last_ts"], "cost_usd": 0.0,
            "unpriced_tokens": 0, "requests": 0})
        for field in TOKEN_FIELDS:
            day[field] += row[field]
            sess[field] += row[field]
        sess["requests"] += row.get("requests", 0)
        sess["models"].add(row["model"])
        sess["first_ts"] = min(sess["first_ts"], row["first_ts"])
        sess["last_ts"] = max(sess["last_ts"], row["last_ts"])
        if row["cost_usd"] is None:
            sess["unpriced_tokens"] += sum(row[f] for f in TOKEN_FIELDS)
        else:
            day["cost_usd"] += row["cost_usd"]
            sess["cost_usd"] += row["cost_usd"]
    session_list = sorted(({**s, "models": sorted(s["models"]), "cost_usd": round(s["cost_usd"], 4)}
                           for s in sessions.values()), key=lambda s: s["last_ts"], reverse=True)
    day_list = [{**d, "cost_usd": round(d["cost_usd"], 4)} for _, d in sorted(days.items())]
    return {"days": day_list, "sessions": session_list}
