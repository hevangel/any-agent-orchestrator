"""Every AI chat web app through playwright-cli, using the saved login profiles.

Log in once per site with `uv run aao web login <target>` (or `--all`); the profile is kept in
AAO_WEB_PROFILE_DIR so these tests never need your password. Each case saves a screenshot of the
page as evidence in tests/result/.
"""

import json
import shutil

import pytest

from orchestrator import drivers, web

pytestmark = pytest.mark.e2e


def test_web_interface(web_target, e2e_timeout, evidence):
    if not shutil.which("playwright-cli"):
        pytest.skip("playwright-cli not installed (npm install -g @playwright/cli)")
    shot = evidence.screenshot("page.png", label="Page at end of test")
    chat = web.TARGETS[web_target]["chat"]
    evidence.info(target=web_target, url=web.TARGETS[web_target]["url"], check="prompt" if chat else "login only")
    if chat:
        prompt, expect = drivers.check_prompt()
        evidence.info(expected=expect)
        result = web.ask(web_target, prompt, expect, timeout_s=e2e_timeout, screenshot_path=str(shot))
    else:
        # remote Codex / Claude Code: no prompt, it would start a cloud task
        result = web.status(web_target, screenshot_path=str(shot))
    evidence.text("result.json", json.dumps(result, indent=1), label="Driver result")
    if result.get("loggedIn") is False:
        pytest.fail(f"{web_target}: not logged in ({result.get('reason') or result.get('error')}). "
                    f"Run: uv run aao web login {web_target}")
    assert result.get("ok", result.get("loggedIn")), f"{web_target}: {result}"
