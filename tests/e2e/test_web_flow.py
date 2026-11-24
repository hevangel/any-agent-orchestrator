"""Google Flow, read only plus one free download: list projects, read a project's media, read the
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
        assert set(options["modes"]) == {"image", "video"}
        for kind, expected in (("image", {"Low", "Medium", "High"}), ("video", {"360p", "720p"})):
            settings = options["modes"][kind]
            groups = [{t["label"] for t in group} for group in settings["toggles"]]
            assert {"Image", "Video"} in groups, groups
            assert expected in groups, groups
            assert settings["dropdowns"][0]["choices"], settings["dropdowns"]

        downloadable = None
        for p in projects:
            read = steps["read-" + p["id"][:8]] = web_flow.session("read", project=p["id"])
            assert read.get("ok") and read.get("id") == p["id"], read
            assert not read["media"] or read["media"][0]["index"] == 1
            downloadable = next((media for media in read["media"] if media["type"] in ("image", "video")), None)
            if downloadable:
                break
        if not downloadable:
            pytest.skip("no project with an image or video to download")

        saved = steps["download"] = web_flow.download(read["id"], downloadable["index"], to=str(tmp_path), quality="original")
        assert saved.get("ok"), saved
        files = list(tmp_path.iterdir())
        assert files and files[0].stat().st_size > 10_000, saved
        evidence.info(downloaded=files[0].name, bytes=files[0].stat().st_size)
    finally:
        web.screenshot(web_flow.TARGET, str(shot))
        evidence.text("steps.json", json.dumps(steps, indent=1), label="Every step and its result")
