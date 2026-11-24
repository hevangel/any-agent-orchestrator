import json

import pytest

from orchestrator import (
    cli, web, web_chatgpt_image, web_flow, web_flow_music, web_gemini_image, web_gemini_video, web_grok, web_muse,
    web_muse_video,
)


@pytest.mark.parametrize(("target", "kind", "module", "function"), [
    ("flow", "image", web_flow, "generate"),
    ("flow", "video", web_flow, "generate"),
    ("grok", "image", web_grok, "generate_image"),
    ("grok", "video", web_grok, "generate_video"),
    ("gemini", "image", web_gemini_image, "generate"),
    ("chatgpt", "image", web_chatgpt_image, "generate"),
    ("muse", "image", web_muse, "generate_image"),
    ("gemini", "video", web_gemini_video, "generate"),
    ("muse", "video", web_muse_video, "generate"),
    ("grok", "audio", web_grok, "generate_audio"),
    ("muse", "audio", web_muse, "generate_audio"),
    ("flow-music", "audio", web_flow_music, "generate"),
])
def test_web_generation_routes_and_reports_downloads(target, kind, module, function, tmp_path, monkeypatch, capsys):
    def generate(*args, **kwargs):
        assert kwargs["timeout_s"] == 19
        if target == "flow":
            assert args[:2] == (kind, "A paper plane")
        else:
            assert args[0] == "A paper plane"
        folder = kwargs["download_dir"] if target == "grok" and kind != "audio" else args[-1]
        assert folder.parent == tmp_path
        assert folder.is_dir() and folder.name.startswith(f"{target}-{kind}-")
        filenames = {"image": "media.png", "video": "media.mp4", "audio": "media.wav" if target == "grok" else "media.mp3"}
        result = str(folder / filenames[kind])
        return {"ok": True, "files": [result]} if "download_dir" in kwargs else {"ok": True, "path": result}

    monkeypatch.setattr(module, function, generate)
    assert cli.main(["web", kind, target, "--prompt", "A paper plane", "--out", str(tmp_path),
                     "--timeout", "19", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] and (result["target"], result["kind"]) == (target, kind)
    assert len(result["files"]) == 1


@pytest.mark.parametrize(("kind", "target", "module", "function", "count"), [
    ("video", "flow", web_flow, "generate", 3),
    ("video", "gemini", web_gemini_video, "generate", 1),
    ("video", "grok", web_grok, "generate_video", 1),
    ("video", "muse", web_muse_video, "generate", 4),
    ("audio", "muse", web_muse, "generate_audio", 4),
])
def test_web_media_passes_references_in_order(kind, target, module, function, count, tmp_path, monkeypatch, capsys):
    references = [tmp_path / f"paper boat {i}.PNG" for i in range(count)]
    for path in references:
        path.write_bytes(b"\x89PNG\r\n\x1a\nreference")

    def generate(*args, **options):
        if target == "grok":
            assert options["image"] == references[0]
        else:
            assert options["references"] == references
        return {"ok": True, "path": "result"}

    monkeypatch.setattr(module, function, generate)
    flags = [arg for path in references for arg in ("--reference", str(path))]
    if target == "grok":
        flags[0] = "--image"
    assert cli.main(["web", kind, target, "--prompt", "A quiet lake", "--out", str(tmp_path),
                     "--json", *flags]) == 0
    assert json.loads(capsys.readouterr().out)["files"] == ["result"]


def test_flow_image_passes_settings_and_reports_all_downloads(tmp_path, monkeypatch, capsys):
    reference = tmp_path / "reference.png"
    reference.write_bytes(b"\x89PNG\r\n\x1a\nreference")

    def generate(kind, prompt, directory, **settings):
        assert (kind, prompt) == ("image", "Paint this in blue")
        assert settings == {
            "project": "Library", "model": "Nano Banana 2.1", "aspect_ratio": "4:3",
            "quality": "High", "count": 2, "references": [reference], "timeout_s": 720,
        }
        return {"ok": True, "files": [str(directory / f"{index}.png") for index in (1, 2)]}

    monkeypatch.setattr(web_flow, "generate", generate)
    assert cli.main(["web", "image", "flow", "--prompt", "Paint this in blue", "--json", "--out", str(tmp_path),
                     "--project", "Library", "--model", "Nano Banana 2.1", "--aspect", "4:3", "--quality", "High",
                     "--count", "2", "--reference", str(reference)]) == 0
    assert len(json.loads(capsys.readouterr().out)["files"]) == 2


def test_flow_music_passes_song_brief_to_browser(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "open_target", lambda target: (True, "", ""))
    monkeypatch.setattr(web, "release", lambda target: None)

    def run_script(target, script, timeout):
        assert target == "flow-music"
        config = json.loads(script.split("const cfg = ")[1].split(";\n", 1)[0])
        assert config["song"] == 2
        assert "Create 2 song versions" in config["prompt"]
        assert "Musical style: folk" in config["prompt"]
        assert "Target duration: 30 seconds" in config["prompt"]
        assert "Use these lyrics verbatim:\nLeaves float by" in config["prompt"]
        return {"ok": True, "path": "song.mp3"}

    monkeypatch.setattr(web, "run_script", run_script)
    assert cli.main(["web", "audio", "flow-music", "--prompt", "Write a refrain", "--out", str(tmp_path),
                     "--lyrics", "Leaves float by", "--style", "folk", "--length", "30", "--song", "2"]) == 0


@pytest.mark.parametrize(("kind", "flag_values", "message"), [
    ("video", ["missing.png"], "Reference image file not found"),
    ("video", ["invalid.png"], "does not match its declared format"),
    ("video", ["unsupported.gif"], "must be PNG, JPEG, or WebP"),
    ("video", ["oversized.png"], "exceeds 8 MiB"),
    ("video", ["missing.png", "another.png"], "accepts one image reference"),
    ("image", ["missing.png"], "applies to web video"),
    ("audio", ["missing.png"], "applies to web video"),
])
def test_web_rejects_invalid_video_references(kind, flag_values, message, tmp_path, monkeypatch, capsys):
    (tmp_path / "invalid.png").write_text("not an image")
    (tmp_path / "unsupported.gif").write_bytes(b"GIF89a")
    with (tmp_path / "oversized.png").open("wb") as file:
        file.write(b"\x89PNG\r\n\x1a\n")
        file.truncate(8 * 1024 * 1024 + 1)
    monkeypatch.setattr(web, "open_target", lambda *args, **options: pytest.fail("browser opened"))
    flags = [arg for value in flag_values for arg in ("--reference", value)]
    output = tmp_path / "downloads"
    monkeypatch.chdir(tmp_path)
    assert cli.main(["web", kind, "grok", "--prompt", "Animate the boat", "--out", str(output), "--json", *flags]) == 1
    assert message in json.loads(capsys.readouterr().out)["error"]
    assert not output.exists()


@pytest.mark.parametrize("args", [["image", "claude", "--prompt", "hello"], ["video", "flow"],
                                  ["audio", "flow", "--prompt", "hello"],
                                  ["video", "muse", "--prompt", "hello", "--all"],
                                  ["video", "grok", "--prompt", "hello", "--file", "ref.png"]])
def test_web_media_rejects_invalid_requests_before_creating_output(args, tmp_path, capsys):
    assert cli.main(["web", *args, "--out", str(tmp_path), "--json"]) == 1
    assert not json.loads(capsys.readouterr().out)["ok"]
    assert not list(tmp_path.iterdir())


def test_web_ask_custom_prompt_has_no_implicit_smoke_expectation(monkeypatch):
    def ask(target, prompt, expect, **options):
        assert (target, prompt, expect, options["timeout_s"]) == ("gemini", "hello", None, 120)
        return {"ok": True, "reply": "hi"}

    monkeypatch.setattr(web, "ask", ask)
    assert cli.main(["web", "ask", "gemini", "--prompt", "hello"]) == 0
