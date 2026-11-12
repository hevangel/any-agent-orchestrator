"""Machines the orchestrator can run agents on, in three classes.

local  this computer: resources via the probe in-process, agents via `herdr agent list`.
ssh    machines saved in herdr (`herdr machine list --json`): resources by piping the
       stdlib probe over SSH, agents via `herdr --machine <label> agent list`.
cloud  agents hosted by an AI lab (Codex cloud, Copilot coding agent, Claude Code cloud
       sessions, Cursor cloud agents). No CPU/RAM to measure: we track their tasks.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

from orchestrator import probe

PROBE_SOURCE = Path(probe.__file__).read_text(encoding="utf-8")
SSH_OPTIONS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=8"]


def _run(args, timeout=30, stdin=None, env=None, cwd=None):
    """Run a command; return (ok, stdout, error)."""
    exe = shutil.which(args[0], path=(env or os.environ).get("PATH"))
    if exe is None:
        return False, "", f"{args[0]} not installed"
    try:
        # The resolved path matters on Windows, where npm installs agents as .cmd shims.
        done = subprocess.run([exe, *args[1:]], input=stdin, capture_output=True, text=True, timeout=timeout,
                              encoding="utf-8", errors="replace", env=env, cwd=cwd,
                              stdin=None if stdin is not None else subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return False, "", f"timed out after {timeout}s"
    except OSError as exc:
        return False, "", str(exc)
    if done.returncode != 0:
        return False, done.stdout, (done.stderr or done.stdout).strip()[-500:]
    return True, done.stdout, None


def _json(text):
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------- herdr agents

def summarize_herdr_agents(payload):
    agents = ((payload or {}).get("result") or {}).get("agents") or []
    counts = {}
    rows = []
    for agent in agents:
        status = agent.get("agent_status") or "unknown"
        counts[status] = counts.get(status, 0) + 1
        rows.append({
            "agent": agent.get("agent"),
            "status": status,
            "cwd": agent.get("cwd"),
            "title": agent.get("terminal_title_stripped") or agent.get("terminal_title"),
            "pane_id": agent.get("pane_id"),
        })
    return {"counts": counts, "agents": rows}


def herdr_agents(machine_label=None):
    args = ["herdr"] + (["--machine", machine_label] if machine_label else []) + ["agent", "list"]
    ok, out, err = _run(args, timeout=20)
    if not ok:
        return {"error": err, "counts": {}, "agents": []}
    return summarize_herdr_agents(_json(out))


def herdr_machines():
    ok, out, err = _run(["herdr", "machine", "list", "--json"], timeout=15)
    if not ok:
        return [], err
    return [m for m in (_json(out) or []) if m.get("enabled", True)], None


# ------------------------------------------------------------- local / ssh

def collect_local():
    return {"id": "local", "label": os.environ.get("AAO_LOCAL_LABEL") or probe.socket.gethostname(),
            "class": "local", "status": "online", "host": probe.collect(), "herdr": herdr_agents()}


def collect_ssh(machine):
    label, target = machine.get("label"), machine.get("target") or machine.get("label")
    result = {"id": machine.get("id") or label, "label": label, "class": "ssh", "target": target}
    host = None
    error = None
    for python in ("python3", "python"):
        ok, out, err = _run(["ssh", *SSH_OPTIONS, target, python, "-"], timeout=40, stdin=PROBE_SOURCE)
        if ok and _json(out):
            host = _json(out)
            break
        error = err
    result["status"] = "online" if host else "unreachable"
    result["host"] = host
    result["error"] = None if host else error
    result["herdr"] = herdr_agents(label) if host else {"counts": {}, "agents": []}
    return result


# -------------------------------------------------------------------- cloud

def _task(provider, id_, title, state, url=None, repo=None, updated=None, pr=None):
    return {"provider": provider, "id": id_, "title": title, "state": (state or "unknown").lower(),
            "url": url, "repo": repo, "updated_at": updated, "pr": pr}


def normalize_copilot(items):
    return [_task("copilot", t.get("id"), t.get("name") or t.get("pullRequestTitle"), t.get("state"),
                  t.get("pullRequestUrl"), (t.get("repository") or {}).get("nameWithOwner")
                  if isinstance(t.get("repository"), dict) else t.get("repository"),
                  t.get("updatedAt"), t.get("pullRequestNumber")) for t in items or []]


def normalize_codex(payload):
    items = payload.get("tasks") if isinstance(payload, dict) else payload
    tasks = []
    for t in items or []:
        status = t.get("status")
        if isinstance(status, dict):
            status = status.get("state") or status.get("type")
        env = t.get("environment") or t.get("env")
        tasks.append(_task("codex", t.get("id"), t.get("title"), status, t.get("url"),
                           env.get("label") if isinstance(env, dict) else env,
                           t.get("updated_at") or t.get("updatedAt")))
    return tasks


def normalize_cursor(payload):
    return [_task("cursor", t.get("id"), t.get("name") or t.get("summary"), t.get("status"),
                  (t.get("target") or {}).get("url"), (t.get("source") or {}).get("repository"),
                  t.get("createdAt"), (t.get("target") or {}).get("prUrl"))
            for t in (payload or {}).get("agents") or []]


def cloud_copilot(limit=30):
    ok, out, err = _run(["gh", "agent-task", "list", "--limit", str(limit), "--json",
                         "id,name,state,repository,pullRequestNumber,pullRequestUrl,pullRequestTitle,updatedAt"])
    return (normalize_copilot(_json(out)), None) if ok else ([], err)


def cloud_codex(limit=20):
    ok, out, err = _run(["codex", "cloud", "list", "--json", "--limit", str(limit)], timeout=40)
    return (normalize_codex(_json(out)), None) if ok else ([], err)


def cloud_cursor(limit=30):
    """Cursor cloud agents via its REST API; needs CURSOR_API_KEY (Cursor dashboard > Integrations)."""
    key = os.environ.get("CURSOR_API_KEY")
    if not key:
        return [], "CURSOR_API_KEY not set"
    import base64

    from orchestrator.state import http_get_json

    try:
        payload = http_get_json(f"https://api.cursor.com/v0/agents?limit={limit}", timeout=20, headers={
            "Authorization": "Basic " + base64.b64encode(f"{key}:".encode()).decode()})
        return normalize_cursor(payload), None
    except Exception as exc:  # network, auth and JSON errors all mean "unavailable"
        return [], str(exc)[:300]


CLOUD_PROVIDERS = {
    # id: (label, collector or None, how to dispatch)
    "codex": ("Codex cloud", cloud_codex, "codex cloud exec --env <ENV_ID> '<task>'"),
    "copilot": ("Copilot coding agent", cloud_copilot, "gh agent-task create -R OWNER/REPO '<task>'"),
    "cursor": ("Cursor cloud agents", cloud_cursor, "POST https://api.cursor.com/v0/agents (CURSOR_API_KEY)"),
    "claude": ("Claude Code cloud", None, "claude --cloud '<task>'  (list/attach: claude.ai/code via Claude in Chrome)"),
}


def collect_cloud(enabled=None):
    machines = []
    for provider, (label, collector, dispatch) in CLOUD_PROVIDERS.items():
        if enabled is not None and provider not in enabled:
            continue
        entry = {"id": f"cloud:{provider}", "label": label, "class": "cloud", "provider": provider,
                 "dispatch": dispatch, "tasks": [], "error": None}
        if collector is None:
            entry["status"] = "untracked"
            entry["error"] = "no CLI listing; tracked through the web interface"
        else:
            tasks, error = collector()
            entry["tasks"], entry["error"] = tasks, error
            entry["status"] = "online" if error is None else "unavailable"
        counts = {}
        for task in entry["tasks"]:
            counts[task["state"]] = counts.get(task["state"], 0) + 1
        entry["counts"] = counts
        machines.append(entry)
    return machines


def collect_all(include_cloud=True, cloud_providers=None):
    from concurrent.futures import ThreadPoolExecutor

    remote, error = herdr_machines()
    with ThreadPoolExecutor(max_workers=8) as pool:
        local = pool.submit(collect_local)
        ssh = [pool.submit(collect_ssh, m) for m in remote]
        cloud = pool.submit(collect_cloud, cloud_providers) if include_cloud else None
        machines = [local.result()] + [f.result() for f in ssh] + (cloud.result() if cloud else [])
    return {"schema": "machines-v1", "machines": machines, "herdr_error": error}
