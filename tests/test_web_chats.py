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


def test_chatgpt_ids_resolve_from_project_urls():
    chat = "6af5514c-4acc-83e8-95f3-bb41ffce89c4"
    want = {"id": chat, "url": f"https://chatgpt.com/c/{chat}"}
    assert web_chats.resolve("chatgpt", f"https://chatgpt.com/g/g-p-6af5537d6ecc8191a4adfe0883017599-ai/c/{chat}") == want
    assert web_chats.resolve("chatgpt", chat.upper()) == want


def test_chatgpt_uses_its_own_script(monkeypatch):
    seen = {}
    monkeypatch.setattr(web_chats.web, "run_script", lambda target, script, timeout: seen.update(script=script) or {"ok": True})
    web_chats.run("chatgpt", "move", chat="Calculate Sum", group="P", create=True)
    cfg = json.loads(seen["script"].split("const cfg = ", 1)[1].split(", r = cfg.recipe;", 1)[0])
    assert cfg["recipe"]["engine"] == "chatgpt" and cfg["group"] == "P"
    assert "projectChats" in seen["script"] and "__CFG__" not in seen["script"]


def test_project_names_are_aliases(monkeypatch, capsys):
    seen = {}
    monkeypatch.setattr(web_chats, "session", lambda target, op, **kw: seen.update(op=op, kw=kw) or {"ok": True, "sections": []})
    assert cli.main(["chats", "chatgpt", "projects"]) == 0 and seen["op"] == "list"
    assert cli.main(["chats", "chatgpt", "list", "--project", "AI"]) == 0 and seen["kw"] == {"group": "AI"}
    assert cli.main(["chats", "chatgpt", "unproject", "X"]) == 0 and seen["op"] == "ungroup"
    assert cli.main(["chats", "chatgpt", "delete-project", "P"]) == 1
    assert "deletes a project's chats" in capsys.readouterr().out


def test_codex_cli_passes_choice_and_thread(monkeypatch):
    from orchestrator import web_codex

    seen = {}
    monkeypatch.setattr(web_codex, "session", lambda op, **kw: seen.update(op=op, kw=kw) or {"ok": True, "pressed": "Next", "step": 1, "title": "T"})
    assert cli.main(["codex", "answer", "[cloud] x", "--choice", "Next", "--step", "2"]) == 0
    assert seen["op"] == "answer" and seen["kw"]["thread"] == "[cloud] x" and seen["kw"]["choice"] == "Next" and seen["kw"]["step"] == 2
    assert web_codex.thread_id("https://chatgpt.com/local/01A17C27-82a8-7164-bc68-0412c71f103c") == "01a17c27-82a8-7164-bc68-0412c71f103c"


def test_codex_question_report_marks_secrets(capsys):
    q = {"step": 2, "steps": 3, "title": "Configure secrets", "description": None, "items": [], "options": [],
         "fields": [{"name": "DROPBOX_CLIENT_SECRET", "kind": "secret", "filled": False, "scope": "Environment"}],
         "actions": [{"label": "Skip", "enabled": True}, {"label": "Next", "enabled": False}], "humanOnly": True}
    cli._print_question(q)
    out = capsys.readouterr().out
    assert "HUMAN: secrets needed" in out and "secret DROPBOX_CLIENT_SECRET: EMPTY" in out and "Next (disabled)" in out


def test_codex_shares_the_chatgpt_login():
    from orchestrator import web

    assert web.login_name("codex-cloud") == "chatgpt"


def test_codex_new_and_answer_arguments(monkeypatch):
    from orchestrator import web_codex

    seen = {}
    monkeypatch.setattr(web_codex, "session", lambda op, **kw: seen.update(op=op, kw=kw) or {"ok": True})
    assert cli.main(["codex", "new", "--prompt", "p", "--env", "ai_engineer", "--work-in", "Cloud",
                     "--permission", "Approve for me", "--effort", "Medium", "--wait"]) == 0
    assert seen["op"] == "new" and seen["kw"]["env"] == "ai_engineer" and seen["kw"]["workIn"] == "Cloud"
    assert seen["kw"]["permission"] == "Approve for me" and seen["kw"]["effort"] == "Medium" and seen["kw"]["wait"]
    assert cli.main(["codex", "answer", "T", "--text", "use green"]) == 0
    assert seen["kw"]["text"] == "use green" and seen["kw"]["choice"] is None
    with pytest.raises(SystemExit):  # --choice and --text are exclusive
        cli.main(["codex", "answer", "T", "--choice", "red", "--text", "x"])
