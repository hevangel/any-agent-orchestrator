"""Claude Code on the web (claude.ai/code), read only: list sessions with where they run, read one
session, list projects and read one. Sends nothing, so it starts no work and uses no quota.
Starting or prompting a session (`aao code new|send`) is left to manual runs.
"""

import json
import shutil

import pytest

from orchestrator import web, web_code

pytestmark = pytest.mark.e2e


def test_web_code_read_only(web_target, evidence):
    if web_target != web_code.TARGET:
        pytest.skip("Code tab test runs for claude-code-web only")
    if not shutil.which("playwright-cli"):
        pytest.skip("playwright-cli not installed (npm install -g @playwright/cli)")
    steps = {}
    shot = evidence.screenshot("page.png", label="Page at end of test")
    try:
        listed = steps["sessions"] = web_code.sessions()
        if listed.get("loggedIn") is False:
            pytest.fail(f"not logged in ({listed.get('reason')}). Run: uv run aao web login claude")
        assert listed.get("ok"), listed
        sessions = [c for s in listed["sections"] for c in s["items"] if c.get("type") == "code"]
        assert sessions, "no sessions in the Code sidebar"
        assert all(c["runs_on"] in ("cloud", "machine") for c in sessions), sessions
        evidence.info(sessions=len(sessions), cloud=sum(c["runs_on"] == "cloud" for c in sessions))

        read = steps["read"] = web_code.session("read", sessions[0]["id"])
        assert read.get("ok") and read.get("id") == sessions[0]["id"], read
        assert read.get("turns", 0) >= 1 and read.get("reply"), read

        projects = steps["projects"] = web_code.session("projects")
        assert projects.get("ok"), projects
        if projects["projects"]:
            proj = steps["project"] = web_code.session("project-read", project=projects["projects"][0]["id"])
            assert proj.get("ok") and proj.get("name"), proj
            assert proj.get("groups") is not None and proj.get("messages") is not None
    finally:
        web.screenshot(web_code.TARGET, str(shot))
        evidence.text("steps.json", json.dumps(steps, indent=1), label="Every read and its result")
