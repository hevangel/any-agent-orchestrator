"""Chat-list control (list, search, pin, groups) on each studied web target.

The test starts its own chat (one prompt), then pins/unpins it, creates a throwaway group
`aao-e2e-<n>` (a section on Claude, a project on ChatGPT), moves the chat in and out, and deletes
the group again. Nothing else in the account is touched. The chat itself stays in Recents.
"""

import json
import random
import shutil

import pytest

from orchestrator import drivers, web, web_chats

pytestmark = pytest.mark.e2e


def _section_of(sections, chat_id):
    return next((s for s in sections if any(c["id"] == chat_id for c in s["items"])), {})


def _groups_of(sections, chat_id):
    """Groups holding the chat. ChatGPT also lists a project's chat under Recents, and shows a
    project's chats only when it is expanded, so membership is read with list --group."""
    return [s["name"] for s in sections if s["kind"] == "group" and any(c["id"] == chat_id for c in s["items"])]


def test_web_chat_list(web_target, e2e_timeout, evidence):
    if web_target not in web_chats.RECIPES:
        pytest.skip(f"chat-list control not studied for {web_target}")
    if not shutil.which("playwright-cli"):
        pytest.skip("playwright-cli not installed (npm install -g @playwright/cli)")
    steps = {}

    prompt, expect = drivers.check_prompt()
    made = web.ask(web_target, prompt, expect, timeout_s=e2e_timeout)
    steps["ask"] = made
    if made.get("loggedIn") is False:
        pytest.fail(f"{web_target}: not logged in. Run: uv run aao web login {web_target}")
    assert made.get("ok"), made
    chat_id = web_chats.resolve(web_target, made["url"])["id"]
    group = f"aao-e2e-{random.randint(1000, 9999)}"
    evidence.info(target=web_target, chat=made["url"], group=group)

    shot = evidence.screenshot("sidebar.png", label="Sidebar at end of test")
    ok, _, err = web.open_target(web_target)
    assert ok, err
    try:
        run = lambda op, **kw: steps.setdefault(f"{len(steps):02} {op}", web_chats.run(web_target, op, **kw))  # noqa: E731

        listed = run("list")
        kinds = {s["kind"] for s in listed["sections"]}
        assert {"pinned", "recents"} <= kinds, listed
        # search an older chat: a chat made seconds ago is often not in the search index yet
        older = next((c for s in listed["sections"] for c in s["items"] if c["id"] != chat_id and c["type"] == "chat"), None)
        if older:
            found = run("search", query=older["title"])
            assert older["id"] in [r["id"] for r in found["results"]], found

        assert run("pin", chat=chat_id)["ok"]
        assert _section_of(run("list")["sections"], chat_id).get("kind") == "pinned"
        assert run("unpin", chat=chat_id)["ok"]

        assert run("new-group", group=group)["ok"]
        assert run("move", chat=chat_id, group=group)["ok"]
        assert _groups_of(run("list", group=group)["sections"], chat_id) == [group]
        assert run("ungroup", chat=chat_id)["ok"]
        assert _groups_of(run("list", group=group)["sections"], chat_id) == []
        assert run("delete-group", group=group)["ok"]
        assert group not in [s["name"] for s in run("list")["sections"]]
    finally:
        web_chats.run(web_target, "delete-group", group=group)  # no-op when already deleted
        web.screenshot(web_target, str(shot))
        web.release(web_target)
        evidence.text("steps.json", json.dumps(steps, indent=1), label="Every operation and its result")
