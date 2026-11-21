import asyncio
import json

import pytest

from orchestrator import cli, web_grok


def test_web_ask_grok_custom_prompt_returns_full_reply(monkeypatch, capsys):
    def ask(target, prompt, expect, **kwargs):
        assert (target, prompt, expect) == ("grok", "custom question", None)
        return {"ok": True, "reply": "x" * 520 + "end of reply", "thinking": "Thought for 9s"}

    monkeypatch.setattr(web_grok.web, "ask", ask)
    assert cli.main(["web", "ask", "grok", "--prompt", "custom question"]) == 0
    result = capsys.readouterr()
    assert "end of reply" in result.out
    assert result.err == "Thought for 9s\n"
    assert "Thought for" not in result.out


def test_grok_web_ask_preserves_default_smoke_test(monkeypatch):
    from orchestrator import drivers

    monkeypatch.setattr(drivers, "check_prompt", lambda: ("smoke test", "^AAO42$"))

    def ask(target, prompt, expect, **kwargs):
        assert (target, prompt, expect) == ("grok", "smoke test", "^AAO42$")
        return {"ok": True}

    monkeypatch.setattr(web_grok.web, "ask", ask)
    assert cli.main(["web", "ask", "grok"]) == 0


def test_imagine_video_cli_passes_options_and_releases_browser(monkeypatch, tmp_path, capsys):
    image = tmp_path / "reference.png"
    image.write_bytes(b"reference data")
    out_dir = tmp_path / "saved media"
    calls = []

    def run_script(target, script, timeout):
        assert target == "grok"
        assert timeout > 85
        options = json.loads(script.split("const cfg = ")[1].split(";\n", 1)[0])
        assert options["prompt"] == 'Animate "this"'
        assert options["radios"] == ["720p", "10s"]
        assert options["menus"] == [["Aspect Ratio", "16:9"]]
        assert options["image"] == image.as_posix()
        assert options["out"] == out_dir.as_posix()
        assert options["timeoutMs"] == 85000
        assert out_dir.is_dir()
        return {"ok": True, "files": ["saved.mp4"]}

    def open_target(target, headed):
        calls.append((target, headed))
        return True, "", ""

    monkeypatch.setattr(web_grok.web, "open_target", open_target)
    monkeypatch.setattr(web_grok.web, "run_script", run_script)
    monkeypatch.setattr(web_grok.web, "release", lambda target: calls.append(target))
    assert cli.main(["grok", "--json", "video", 'Animate "this"', "--image", str(image), "--res", "720p",
                     "--duration", "10s", "--aspect", "16:9", "--out", str(out_dir), "--timeout", "85"]) == 0
    assert json.loads(capsys.readouterr().out)["files"] == ["saved.mp4"]
    assert calls == [("grok", True), "grok"]


def test_grok_audio_downloads_the_requested_chat_attachment(monkeypatch, tmp_path):
    turn = {}
    released = []

    def ask(target, prompt, *, timeout_s):
        assert target == "grok" and timeout_s == 21
        assert "Generate audio only" in prompt
        assert prompt.endswith("piano")
        turn["prompt"] = prompt
        return {"ok": True}

    def run(target, script, timeout):
        assert target == "grok"
        payload = json.loads(script.split("const cfg = ")[1].split(";\n", 1)[0])
        assert payload["filename"] in turn["prompt"]
        assert payload["path"] == (tmp_path / "Grok audio.wav").as_posix()
        return {"ok": True, "path": payload["path"]}

    monkeypatch.setattr(web_grok.web, "open_target", lambda target: (True, "", ""))
    monkeypatch.setattr(web_grok.web, "_run_page", ask)
    monkeypatch.setattr(web_grok.web, "run_script", run)
    monkeypatch.setattr(web_grok.web, "release", released.append)
    assert web_grok.generate_audio("piano", tmp_path, timeout_s=21)["ok"]
    assert released == ["grok"]


@pytest.mark.parametrize("options", [{"aspect_ratio": "5:7"}, {"count": "5"}, {"timeout_s": float("nan")}])
def test_imagine_validates_before_opening_browser(monkeypatch, options):
    monkeypatch.setattr(web_grok.web, "open_target", lambda *args, **kwargs: pytest.fail("browser opened"))
    with pytest.raises(ValueError):
        web_grok.generate_image("prompt", **options)


def test_grok_attach_requires_cdp_and_uses_named_session(monkeypatch, tmp_path):
    config = tmp_path / "browser.json"
    config.write_text('{"browser":{"cdpEndpoint":"http://127.0.0.1:9222"}}', encoding="utf-8")
    monkeypatch.setattr(web_grok.web, "is_open", lambda target: False)

    def run(*args):
        assert args == ("grok", "open", "--config", str(config), "https://grok.com/")
        return False, "", "Endpoint error with credentials"

    monkeypatch.setattr(web_grok.web, "_pw", run)
    assert web_grok.attach(config) == {"ok": False, "error": "could not attach to the configured CDP browser"}
    config.write_text('{}', encoding="utf-8")
    with pytest.raises(ValueError, match="cdpEndpoint"):
        web_grok.attach(config)


def test_grok_http_validation_and_dispatch(monkeypatch):
    pytest.importorskip("fastapi")
    from httpx import ASGITransport, AsyncClient

    from orchestrator import web_grok_api

    monkeypatch.setattr(web_grok, "generate_image", lambda **kw: {"ok": True, "options": kw, "files": ["a.png"]})
    monkeypatch.setattr(web_grok, "chat", lambda **kw: {"ok": False, "loggedIn": False})
    async def check_routes():
        transport = ASGITransport(app=web_grok_api.create_app())
        async with AsyncClient(transport=transport, base_url="http://localhost:8000") as client:
            assert (await client.get("/health")).json() == {"ok": True}
            image = {"prompt": "a teapot", "aspect_ratio": "1:1", "count": "4"}
            response = await client.post("/imagine/images", json=image)
            assert response.status_code == 200
            assert response.json()["options"]["count"] == "4"
            assert (await client.post("/imagine/images", json={**image, "count": "7"})).status_code == 422
            assert (await client.post("/imagine/images", json=image,
                                      headers={"Origin": "https://example.com"})).status_code == 403
            assert (await client.post("/chat/messages", json={"prompt": "hello"})).status_code == 503

    asyncio.run(check_routes())


def test_grok_mcp_tool_schemas_and_dispatch(monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("mcp")
    from orchestrator import web_grok_api

    monkeypatch.setattr(web_grok, "chat", lambda prompt, **kw: {"ok": True, "response": prompt})

    async def check_tools():
        server = web_grok_api.create_mcp()
        assert {tool.name for tool in await server.list_tools()} == {
            "grok_chat", "grok_generate_image", "grok_generate_video",
        }
        _, result = await server.call_tool("grok_chat", {"prompt": "hello"})
        assert result == {"ok": True, "response": "hello"}

    asyncio.run(check_tools())
