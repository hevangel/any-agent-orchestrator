import json
import shutil
import subprocess

import pytest

from orchestrator import cli, web, web_autoclaw


@pytest.mark.parametrize("args", [
    ["ask", "autoclaw"],
    ["ask", "autoclaw", "--prompt", "Hello"],
    ["read", "autoclaw"],
    ["ask", "autoclaw", "--chat", "Daily task", "--prompt", "Hello", "--expect", "["],
])
def test_invalid_autoclaw_requests_do_not_open_chrome(monkeypatch, args):
    monkeypatch.setattr(web, "open_target", lambda *args, **kwargs: pytest.fail("opened Chrome"))
    assert cli.main(["web", *args]) == 1


def test_ask_autoclaw_returns_full_reply_and_uses_the_exact_title(monkeypatch, capsys):
    reply = "Hello " + "a" * 510 + " done"
    monkeypatch.setattr(web, "open_target", lambda *args, **kwargs: (True, "", ""))
    monkeypatch.setattr(web, "release", lambda *args, **kwargs: None)

    def run_script(target, script, timeout_s):
        assert target == "autoclaw"
        assert timeout_s >= 180
        config = json.loads(script.split("const cfg = ", 1)[1].split(";\n", 1)[0])
        assert config["action"] == "ask"
        assert config["chat"] == "Daily task"
        assert config["prompt"] == "Hello"
        return {"ok": True, "reply": reply}

    monkeypatch.setattr(web, "run_script", run_script)
    assert cli.main(["web", "ask", "autoclaw", "--chat", "Daily task", "--prompt", "Hello",
                     "--expect", "done$", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["reply"] == reply


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_autoclaw_script_is_valid_javascript():
    done = subprocess.run(["node", "--check"], encoding="utf-8", capture_output=True,
                          input="const run = " + web_autoclaw.SCRIPT.replace("__CFG__", "{}"))
    assert done.returncode == 0, done.stderr
