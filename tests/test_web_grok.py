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


def _payload(script):
    return json.loads(script.split("const cfg = ")[1].split(";\n", 1)[0])


@pytest.fixture
def grok_page(monkeypatch):
    calls = []

    def run_script(target, script, timeout):
        calls.append(_payload(script))
        return calls[-1].pop("_result", {"ok": True})

    monkeypatch.setattr(web_grok.web, "open_target", lambda target, headed=None: (True, "", ""))
    monkeypatch.setattr(web_grok.web, "run_script", run_script)
    monkeypatch.setattr(web_grok.web, "release", lambda target: None)
    return calls


def test_image_mentions_strip_at_and_keep_uploads(grok_page, tmp_path):
    upload = tmp_path / "a.png"
    upload.write_bytes(b"png")
    assert cli.main(["grok", "image", "a fox", "--ref", "@Fox", "--ref", "Lena", "--upload", str(upload),
                     "--count", "2", "--out", str(tmp_path)]) == 0
    payload = grok_page[0]
    assert payload["mentions"] == ["Fox", "Lena"]
    assert payload["references"] == [upload.as_posix()]
    assert payload["menus"] == [["Image Count", "2"]]


def test_reference_cli_list_new_and_guarded_delete(grok_page, tmp_path, monkeypatch, capsys):
    image = tmp_path / "hero.png"
    image.write_bytes(b"png")
    monkeypatch.setattr(web_grok, "list_references", lambda category=None: {
        "ok": True, "references": [{"name": "Hero", "sample": False}, {"name": "Fox", "sample": True}]})
    assert cli.main(["grok", "reference", "list"]) == 0
    assert capsys.readouterr().out.splitlines() == ["@Hero", "@Fox  (sample)"]
    assert cli.main(["grok", "reference", "new", "Hero", "--image", str(image), "--category", "Prop"]) == 0
    assert grok_page[-1]["action"] == "create"
    assert grok_page[-1]["images"] == [image.as_posix()] and grok_page[-1]["category"] == "Prop"
    assert cli.main(["grok", "reference", "delete", "@Hero"]) == 1
    assert len(grok_page) == 1
    assert cli.main(["grok", "reference", "delete", "@Hero", "--yes"]) == 0
    assert grok_page[-1] == {"action": "delete", "name": "Hero", "url": web_grok.IMAGINE_URL,
                             "loginUrl": web_grok.web.LOGIN_URL}


def test_draw_sends_sketch_and_validated_strokes(grok_page, tmp_path):
    sketch = tmp_path / "sketch.png"
    sketch.write_bytes(b"\x89PNG")
    strokes = tmp_path / "strokes.json"
    strokes.write_text(json.dumps([{"points": [[0.1, 0.2], [0.5, 0.5]], "color": "#EF4444", "size": 8}]))
    assert cli.main(["grok", "draw", "a boat", "--sketch", str(sketch), "--strokes", str(strokes),
                     "--style", "Painting", "--aspect", "21:9", "--brush", "6", "--out", str(tmp_path)]) == 0
    payload = grok_page[0]
    assert payload["url"].endswith("/imagine/draw")
    assert payload["sketch"].startswith("data:image/png;base64,")
    assert payload["strokes"] == [{"points": [[0.1, 0.2], [0.5, 0.5]], "color": "#ef4444", "size": 8}]
    assert (payload["style"], payload["aspect"], payload["brush"]) == ("Painting", "21:9", 6)


@pytest.mark.parametrize("options", [
    {"strokes": [{"points": [[1.5, 0]]}]}, {"strokes": [{"points": [[0, 0]], "color": "red"}]},
    {"style": "Anime"}, {"aspect_ratio": "7:3"}, {"brush": 0},
])
def test_draw_validates_before_opening_browser(monkeypatch, options):
    monkeypatch.setattr(web_grok.web, "open_target", lambda *args, **kwargs: pytest.fail("browser opened"))
    with pytest.raises(ValueError):
        web_grok.draw("prompt", **options)


def test_canvas_accepts_ids_and_urls_only(grok_page, tmp_path):
    agent = "3542c068-00ff-4461-9e5d-14aa522b4fed"
    assert web_grok.canvas_url(agent) == f"https://grok.com/imagine/agent/{agent}"
    url = f"https://grok.com/imagine/agent/{agent}?conversation={agent}"
    assert web_grok.canvas_url(url) == url
    for bad in ("https://evil.example/imagine/agent/" + agent, "not-a-canvas"):
        with pytest.raises(ValueError):
            web_grok.canvas_url(bad)
    with pytest.raises(ValueError):
        web_grok.canvas(files=[__file__])
    assert cli.main(["grok", "canvas", "add a teapot", "--canvas", agent, "--out", str(tmp_path)]) == 0
    assert grok_page[0]["canvas"] == f"https://grok.com/imagine/agent/{agent}"
    assert grok_page[0]["kind"] == "media" and grok_page[0]["prompt"] == "add a teapot"
    assert cli.main(["grok", "canvas", "--out", str(tmp_path)]) == 0
    assert grok_page[1]["canvas"] is None and grok_page[1]["prompt"] is None


def test_video_frames_loop_voice_and_picks_reach_the_page(grok_page, tmp_path):
    first, middle, last = (tmp_path / f"{n}.png" for n in ("first", "middle", "last"))
    for frame in (first, middle, last):
        frame.write_bytes(b"png")
    assert cli.main(["grok", "video", "pan", "--image", str(first), "--middle-frame", str(middle),
                     "--last-frame", str(last), "--voice", "Eve", "--no-audio", "--res", "1080p",
                     "--duration", "15s", "--pick", "upload:sketch.png", "--pick", "generation:2",
                     "--dry-run", "--out", str(tmp_path)]) == 0
    payload = grok_page[0]
    assert (payload["image"], payload["middleFrame"], payload["lastFrame"]) == (
        first.as_posix(), middle.as_posix(), last.as_posix())
    assert payload["voice"] == "Eve" and payload["audio"] is False and payload["dryRun"] is True
    assert payload["radios"] == ["1080p", "15s"]
    assert payload["picks"] == [{"source": "upload", "name": "sketch.png"}, {"source": "generation", "index": 2}]
    assert cli.main(["grok", "video", "sway", "--image", str(first), "--loop", "--audio", "--out", str(tmp_path)]) == 0
    assert grok_page[1]["loop"] is True and grok_page[1]["audio"] is True


@pytest.mark.parametrize("options", [
    {"last_frame": "x.png"}, {"loop": True}, {"picks": ["generation:newest"]}, {"picks": ["library:1"]},
    {"voice": " "},
])
def test_video_options_validate_before_opening_browser(monkeypatch, options):
    monkeypatch.setattr(web_grok.web, "open_target", lambda *args, **kwargs: pytest.fail("browser opened"))
    with pytest.raises(ValueError):
        web_grok.generate_video("prompt", **options)


def test_reference_edit_and_library_cli(grok_page, tmp_path):
    image = tmp_path / "new.png"
    image.write_bytes(b"png")
    assert cli.main(["grok", "reference", "edit", "@Hero", "--title", "Heroine", "--description", "",
                     "--image", str(image), "--replace-media"]) == 0
    edit = grok_page[0]
    assert (edit["action"], edit["name"], edit["title"], edit["description"]) == ("edit", "Hero", "Heroine", "")
    assert edit["images"] == [image.as_posix()] and edit["replaceMedia"] is True and edit["category"] is None
    with pytest.raises(ValueError):
        web_grok.edit_reference("Hero")
    with pytest.raises(ValueError):
        web_grok.edit_reference("Hero", replace_media=True)
    assert cli.main(["grok", "library", "voices"]) == 0
    assert grok_page[1]["source"] == "voices"


def test_template_run_pairs_and_slugs(grok_page, tmp_path):
    photo = tmp_path / "photo.png"
    photo.write_bytes(b"png")
    assert cli.main(["grok", "template", "run", "BG Removal & Change", "--image", str(photo),
                     "--image", f"Your photos={photo}", "--choose", "Background=Custom",
                     "--text", "New background=a forest = misty", "--dry-run", "--out", str(tmp_path)]) == 0
    payload = grok_page[0]
    assert payload["query"] == "BG Removal & Change" and payload["slug"] == "imagine-bg-removal-change"
    assert payload["images"] == [[None, photo.as_posix()], ["Your photos", photo.as_posix()]]
    assert payload["choices"] == [["Background", "Custom"]]
    assert payload["texts"] == [["New background", "a forest = misty"]]
    assert web_grok._template_slug("imagine-photo-edit") == "imagine-photo-edit"
    with pytest.raises(ValueError):
        web_grok.run_template("photo-edit", choices=["Cozy Warm"])


def test_canvas_board_tools_and_list(grok_page, tmp_path):
    board = tmp_path / "board.png"
    board.write_bytes(b"png")
    assert cli.main(["grok", "canvas", "--note", "hello", "--board-image", str(board), "--out", str(tmp_path)]) == 0
    assert grok_page[0]["notes"] == ["hello"] and grok_page[0]["boardImages"] == [board.as_posix()]
    assert cli.main(["grok", "canvas", "--list"]) == 0
    assert grok_page[1]["action"] == "list"
    with pytest.raises(ValueError):
        web_grok.canvas(notes=[" "])