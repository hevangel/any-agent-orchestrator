"""Codex on chatgpt.com, full cycle on a throwaway thread: start it (Cloud, Default environment),
make Codex ask a multiple-choice question, read the question as the master agent would, answer it,
wait for the turn and archive the thread. Spends a few small Codex turns; touches no other thread.
"""

import json
import shutil

import pytest

from orchestrator import web, web_codex

pytestmark = pytest.mark.e2e


def test_web_codex_cycle(web_target, evidence):
    if web_target != web_codex.TARGET:
        pytest.skip("Codex test runs for codex-cloud only")
    if not shutil.which("playwright-cli"):
        pytest.skip("playwright-cli not installed (npm install -g @playwright/cli)")
    steps = {}
    shot = evidence.screenshot("page.png", label="Thread at end of test")
    thread = None
    ok, _, err = web.open_target(web_codex.TARGET)
    assert ok, err
    try:
        run = lambda op, **kw: steps.setdefault(f"{len(steps):02} {op}", web_codex.run(op, **kw))  # noqa: E731

        listed = run("threads")
        if listed.get("loggedIn") is False:
            pytest.fail(f"not logged in ({listed.get('reason')}). Run: uv run aao web login chatgpt")
        assert listed.get("ok"), listed

        made = run("new", prompt="aao-e2e: reply with just the word READY. Do not run any commands or change any files.",
                   env="Default environment", workIn="Cloud", permission="Ask for approval", wait=True)
        assert made.get("ok") and made.get("id"), made
        thread = made["id"]
        evidence.info(thread=made["url"])
        assert "READY" in made.get("reply", ""), made

        asked = run("send", thread=thread, wait=True,
                    prompt="Ask me a multiple-choice question using your question tool (request user input) with the "
                           "options red, green and blue, asking which colour I like. Do not run commands or change files.")
        assert asked.get("ok") and asked.get("status") == "needs-attention", asked
        q = asked["attention"]["steps"][0]
        assert [o["label"] for o in q["options"]] == ["red", "green", "blue"], q
        assert not q["humanOnly"], q

        answered = run("answer", thread=thread, choice="green")
        assert answered.get("ok") and answered.get("sent") and not answered.get("next"), answered
        after = run("wait", thread=thread)
        assert after.get("ok") and after.get("status") == "done" and "green" in after.get("lastPrompt", ""), after
    finally:
        if thread:
            web.screenshot(web_codex.TARGET, str(shot))
            steps["archive"] = web_codex.run("archive", thread=thread)
        web.release(web_codex.TARGET)
        evidence.text("steps.json", json.dumps(steps, indent=1), label="Every operation and its result")
    assert steps["archive"].get("ok"), steps["archive"]
