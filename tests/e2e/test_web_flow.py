"""Google Flow, read only plus one free download: list projects, read a project's media, read the agent
options and save a media at its original size. Creates, generates and deletes nothing, so it spends
no credits (`aao flow new` / `delete` were checked by hand).
"""

import json
import shutil

import pytest

from orchestrator import web, web_flow

pytestmark = pytest.mark.e2e


def test_web_flow_read_only(web_target, evidence, tmp_path):
    if web_target != web_flow.TARGET:
        pytest.skip("Flow test runs for flow only")
    if not shutil.which("playwright-cli"):
        pytest.skip("playwright-cli not installed (npm install -g @playwright/cli)")
    steps = {}
    shot = evidence.screenshot("page.png", label="Page at end of test")
    try:
        listed = steps["projects"] = web_flow.session("projects")
        if listed.get("loggedIn") is False:
            pytest.fail(f"not logged in ({listed.get('reason')}). Run: uv run aao web login gemini")
        assert listed.get("ok"), listed
        projects = listed["projects"]
        assert projects, "no Flow projects in this account"
        assert all(p["id"] and p["title"] for p in projects), projects
        evidence.info(projects=len(projects))

        options = steps["options"] = web_flow.session("options", project=projects[0]["id"])
        assert options.get("ok"), options
        assert {r["label"] for r in options["confirm"]} == {"Always", "Never"}, options["confirm"]
        assert [len(g) for g in options["toggles"]] == [5, 4, 2, 4], options["toggles"]
        assert len(options["dropdowns"]) == 2 and all(d["choices"] for d in options["dropdowns"]), options["dropdowns"]

        with_media = None
        for p in projects:
            read = steps["read-" + p["id"][:8]] = web_flow.session("read", project=p["id"])
            assert read.get("ok") and read.get("id") == p["id"], read
            if read["media"]:
                with_media = read
                break
        if not with_media:
            pytest.skip("no project with media to read or download")
        first = with_media["media"][0]
        assert first["type"] in ("video", "image") and first["index"] == 1, first

        saved = steps["download"] = web_flow.download(with_media["id"], 1, to=str(tmp_path), quality="original")
        assert saved.get("ok"), saved
        files = list(tmp_path.iterdir())
        assert files and files[0].stat().st_size > 10_000, saved
        evidence.info(downloaded=files[0].name, bytes=files[0].stat().st_size)
    finally:
        web.screenshot(web_flow.TARGET, str(shot))
        evidence.text("steps.json", json.dumps(steps, indent=1), label="Every step and its result")
