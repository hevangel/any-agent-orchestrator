"""Record every test run into tests/result/ for tests/index.html.

    tests/result/runs/<run-id>/summary.json        one document per run
    tests/result/runs/<run-id>/evidence/...         transcripts, screenshots
    tests/result/index.js                           window.AAO_TEST_RUNS = [...] (newest first)

index.js is a script, not JSON, so tests/index.html works when opened straight from disk
(browsers block fetch() on file://). Text evidence is also inlined in the summary for the same
reason. Set AAO_TEST_RESULTS=0 to turn recording off. tests/result/ is gitignored: transcripts
and screenshots can contain account details.
"""

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

RESULT_DIR = Path(__file__).parent / "result"
KEEP_RUNS = 50
INLINE_TEXT_LIMIT = 20000


def _enabled():
    return os.environ.get("AAO_TEST_RESULTS", "1") != "0"


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Evidence:
    """Per-test collector: `evidence.text(...)`, `evidence.path(...)` for files a tool writes, `evidence.info(...)`."""

    def __init__(self, run_dir, nodeid):
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", nodeid.split("::", 1)[-1])[:120]
        self.dir = run_dir / "evidence" / safe
        self.items = []
        self.data = {}

    def path(self, name, kind="file", label=None):
        self.dir.mkdir(parents=True, exist_ok=True)
        target = self.dir / name
        self.items.append({"name": label or name, "kind": kind, "path": target})
        return target

    def text(self, name, content, label=None):
        target = self.path(name, "text", label)
        target.write_text(content, encoding="utf-8")
        self.items[-1]["content"] = content[-INLINE_TEXT_LIMIT:]

    def screenshot(self, name="screenshot.png", label=None):
        return self.path(name, "image", label)

    def info(self, **fields):
        self.data.update({k: v for k, v in fields.items() if v is not None})


def pytest_configure(config):
    if not _enabled() or hasattr(config, "workerinput"):
        return
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    config._aao = {"run_id": run_id, "dir": RESULT_DIR / "runs" / run_id, "started": _now(),
                   "t0": time.monotonic(), "tests": {}, "evidence": {}}


@pytest.fixture
def evidence(request):
    state = getattr(request.config, "_aao", None)
    run_dir = state["dir"] if state else RESULT_DIR / "runs" / "_disabled"
    collector = Evidence(run_dir, request.node.nodeid)
    if state:
        state["evidence"][request.node.nodeid] = collector
    return collector


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    state = getattr(item.config, "_aao", None)
    if not state:
        return
    entry = state["tests"].setdefault(item.nodeid, {
        "nodeid": item.nodeid, "file": item.nodeid.split("::")[0], "name": item.name,
        "suite": "e2e" if item.get_closest_marker("e2e") else "unit",
        "params": {k: _plain(v) for k, v in (getattr(item, "callspec", None).params.items()
                                              if getattr(item, "callspec", None) else [])},
        "outcome": "passed", "duration": 0.0, "message": None})
    entry["duration"] = round(entry["duration"] + report.duration, 2)
    if report.failed:
        entry["outcome"] = "failed" if report.when == "call" else "error"
        entry["message"] = str(report.longrepr)[-6000:]
    elif report.skipped and entry["outcome"] == "passed":
        entry["outcome"] = "skipped"
        entry["message"] = report.longrepr[2] if isinstance(report.longrepr, tuple) else str(report.longrepr)


def _plain(value):
    """Test parameters as JSON: dataclasses (Account, Host) become their id/label."""
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    for attr in ("id", "label"):
        if hasattr(value, attr) and not isinstance(value, (str, int, float)):
            return getattr(value, attr)
    return value if isinstance(value, (str, int, float, bool, type(None))) else str(value)


def _git_commit():
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                              cwd=Path(__file__).parent, timeout=10).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def pytest_sessionfinish(session, exitstatus):
    state = getattr(session.config, "_aao", None)
    if not state or not state["tests"]:
        return
    run_dir = state["dir"]
    run_dir.mkdir(parents=True, exist_ok=True)
    tests = []
    for nodeid, entry in state["tests"].items():
        collector = state["evidence"].get(nodeid)
        if collector:
            entry["info"] = collector.data
            entry["evidence"] = [{"name": e["name"], "kind": e["kind"],
                                  "path": e["path"].relative_to(RESULT_DIR).as_posix(),
                                  **({"content": e["content"]} if "content" in e else {})}
                                 for e in collector.items if Path(e["path"]).exists()]
        tests.append(entry)
    totals = {}
    for t in tests:
        totals[t["outcome"]] = totals.get(t["outcome"], 0) + 1
    summary = {
        "schema": "aao-test-run-v1", "run_id": state["run_id"], "started": state["started"], "finished": _now(),
        "duration": round(time.monotonic() - state["t0"], 1), "exit_status": int(exitstatus), "totals": totals,
        "environment": {"platform": platform.platform(), "python": sys.version.split()[0], "commit": _git_commit(),
                        "args": session.config.invocation_params.args and list(session.config.invocation_params.args),
                        "permission_mode": os.environ.get("AAO_PERMISSION_MODE")},
        "tests": tests,
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    _rebuild_index()


def _rebuild_index():
    runs_dir = RESULT_DIR / "runs"
    runs = sorted((p for p in runs_dir.iterdir() if (p / "summary.json").exists()), reverse=True)
    for old in runs[KEEP_RUNS:]:
        shutil.rmtree(old, ignore_errors=True)
    summaries = []
    for path in runs[:KEEP_RUNS]:
        try:
            summaries.append(json.loads((path / "summary.json").read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    (RESULT_DIR / "index.js").write_text("window.AAO_TEST_RUNS = " + json.dumps(summaries) + ";\n", encoding="utf-8")
