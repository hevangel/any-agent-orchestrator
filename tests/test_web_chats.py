import json

import pytest

from orchestrator import cli, web_chats


def test_resolve_ids_and_urls_without_a_browser():
    chat = "E41373EF-1da1-49bb-8d9c-9157a12e7137"
    want = {"id": chat.lower(), "url": f"https://claude.ai/chat/{chat.lower()}"}
    assert web_chats.resolve("claude", chat) == want
    assert web_chats.resolve("claude", f"https://claude.ai/chat/{chat}?x=1") == want


def test_unstudied_target_is_refused():
    with pytest.raises(ValueError):
        web_chats.resolve("grok", "some title")


def test_script_payload_is_valid_json(monkeypatch):
    seen = {}
    monkeypatch.setattr(web_chats.web, "run_script", lambda target, script, timeout: seen.update(script=script) or {"ok": True})
    web_chats.run("claude", "move", chat='a "quoted" title', group="G", create=True)
    cfg = json.loads(seen["script"].split("const cfg = ", 1)[1].split(", r = cfg.recipe;", 1)[0])
    assert cfg["op"] == "move" and cfg["chat"] == 'a "quoted" title' and cfg["recipe"]["pin"] == "Pin"


def test_delete_group_needs_yes(capsys):
    assert cli.main(["chats", "claude", "delete-group", "G"]) == 1
    assert "--yes" in capsys.readouterr().out


def test_session_ids_resolve_without_a_browser():
    out = web_chats.resolve("claude-code-web", "https://claude.ai/code/session_01B3Vkm9gxDmezuT1LfBMCE7?x=1")
    assert out == {"id": "session_01B3Vkm9gxDmezuT1LfBMCE7", "url": "https://claude.ai/code/session_01B3Vkm9gxDmezuT1LfBMCE7"}


@pytest.mark.parametrize("item, want", [
    ({"detail": "Cloud · Merged · 2 pull requests", "env": "cloud"}, ("cloud", None, None)),
    ({"detail": "sidonia · Connected", "env": "bridge"}, ("machine", "sidonia", "Connected")),
    ({"detail": "Remote Control · Offline", "env": "bridge"}, ("machine", None, "Offline")),
    ({"detail": "Cloud"}, ("cloud", None, None)),
    ({}, (None, None, None)),
])
def test_where_a_session_runs(item, want):
    from orchestrator import web_code

    got = web_code.where(item)
    assert (got["runs_on"], got["machine"], got["connection"]) == want


def test_where_label():
    from orchestrator import web_code

    assert web_code.where_label({"runs_on": "machine", "machine": None, "connection": "Offline"}) == "unnamed machine (offline)"
    assert web_code.where_label({"runs_on": "cloud"}) == "cloud"


def test_section_names_are_aliases(monkeypatch):
    seen = {}
    monkeypatch.setattr(web_chats, "session", lambda target, op, **kw: seen.update(op=op, kw=kw) or {"ok": True, "sections": []})
    assert cli.main(["chats", "claude-code-web", "sections"]) == 0
    assert seen["op"] == "list"
    assert cli.main(["chats", "claude", "new-section", "X"]) == 0
    assert seen == {"op": "new-group", "kw": {"group": "X", "chat": None}}


def test_open_sessions_and_shared_login(monkeypatch):
    from orchestrator import web

    listing = ("### Browsers\n- aao-web-claude:\n  - status: open\n  - browser-type: chrome\n"
               "- aao-web-grok:\n  - status: closed\n")
    monkeypatch.setattr(web, "_run", lambda argv, timeout=0: (True, listing, ""))
    assert web.open_sessions() == {"aao-web-claude"}
    assert web.login_name("claude-code-web") == "claude"
    assert web.is_open("claude-code-web") and not web.is_open("grok")
