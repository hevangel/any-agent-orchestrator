import shutil
import subprocess

import pytest

from orchestrator import web, web_flow


def test_flow_shares_the_gemini_login():
    assert web.login_name("flow") == "gemini"
    assert web.TARGETS["flow"]["chat"] is False


def test_download_qualities_name_what_costs_credits():
    assert web_flow.QUALITIES["original"]["free"] and web_flow.QUALITIES["gif"]["free"]
    assert not web_flow.QUALITIES["1080p"]["free"] and not web_flow.QUALITIES["4k"]["free"]
    with pytest.raises(ValueError):
        web_flow.download("p", 1, quality="8k")


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_flow_script_is_valid_javascript(tmp_path):
    src = tmp_path / "flow.js"
    src.write_text("const f = " + web_flow.SCRIPT.replace("__CFG__", "{}"), encoding="utf-8")
    done = subprocess.run(["node", "--check", str(src)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
