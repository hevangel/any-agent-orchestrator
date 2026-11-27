"""Unit tests for Muse web CLI features (image references, --file, --aspect, --voice TTS, library, chats)."""

import json

import pytest

from orchestrator import cli, web, web_muse


def test_muse_image_accepts_up_to_nine_references_and_aspect(tmp_path, monkeypatch, capsys):
    refs = [tmp_path / f"ref_{i}.png" for i in range(9)]
    for path in refs:
        path.write_bytes(b"\x89PNG\r\n\x1a\nimg")

    def generate_image(prompt, directory, **kwargs):
        assert prompt == "Combine these"
        assert kwargs["aspect_ratio"] == "16:9"
        assert kwargs["references"] == refs
        return {"ok": True, "path": str(directory / "out.png")}

    monkeypatch.setattr(web_muse, "generate_image", generate_image)
    flags = [arg for p in refs for arg in ("--reference", str(p))]
    rc = cli.main(["web", "image", "muse", "--prompt", "Combine these", "--aspect", "16:9",
                   "--out", str(tmp_path), "--json", *flags])
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["ok"]


def test_muse_rejects_ten_image_references(tmp_path, monkeypatch, capsys):
    refs = [tmp_path / f"ref_{i}.png" for i in range(10)]
    for path in refs:
        path.write_bytes(b"\x89PNG\r\n\x1a\nimg")
    monkeypatch.setattr(web, "open_target", lambda *a, **k: pytest.fail("browser opened"))  # noqa: F821
    flags = [arg for p in refs for arg in ("--reference", str(p))]
    rc = cli.main(["web", "image", "muse", "--prompt", "Too many", "--out", str(tmp_path), "--json", *flags])
    assert rc == 1
    assert "up to 9 image references" in json.loads(capsys.readouterr().out)["error"]


def test_muse_video_accepts_video_and_audio_files(tmp_path, monkeypatch, capsys):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"ftypmp42")
    voice = tmp_path / "voice.mp3"
    voice.write_bytes(b"ID3audio")

    def generate(prompt, directory, **kwargs):
        assert kwargs["files"] == [clip, voice]
        assert kwargs["aspect_ratio"] == "9:16"
        return {"ok": True, "path": str(directory / "out.mp4")}

    monkeypatch.setattr("orchestrator.web_muse_video.generate", generate)
    rc = cli.main(["web", "video", "muse", "--prompt", "Swap speaker", "--aspect", "9:16",
                   "--file", str(clip), "--file", str(voice), "--out", str(tmp_path), "--json"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["ok"]


def test_muse_file_rejects_unsupported_extension(tmp_path, capsys):
    bad = tmp_path / "script.exe"
    bad.write_bytes(b"MZ")
    rc = cli.main(["web", "video", "muse", "--prompt", "Bad file", "--file", str(bad),
                   "--out", str(tmp_path), "--json"])
    assert rc == 1
    assert "PNG, JPEG, WebP, MP4, MOV, WebM, MP3, WAV, M4A, or OGG" in json.loads(capsys.readouterr().out)["error"]


def test_muse_audio_voice_uses_tts_without_ffmpeg(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(web, "open_target", lambda target, headed=True: (True, "", ""))
    monkeypatch.setattr(web, "release", lambda target: None)
    monkeypatch.setattr(web_muse.shutil, "which", lambda _: None)

    def run_script(target, script, timeout):
        assert target == "muse"
        cfg = json.loads(script.split("const cfg = ")[1].split(";\n", 1)[0])
        assert cfg["kind"] == "audio" and cfg["extension"] == ".mp3"
        assert "Use the Aria voice." in cfg["prompt"]
        return {"ok": True, "path": str(tmp_path / "tts.mp3")}

    monkeypatch.setattr(web, "run_script", run_script)
    rc = cli.main(["web", "audio", "muse", "--prompt", "Hello world", "--voice", "Aria",
                   "--out", str(tmp_path), "--json"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["files"] == [str(tmp_path / "tts.mp3")]


def test_muse_library_and_download_cli(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(web_muse, "list_library", lambda **kw: {
        "ok": True, "media": [{"index": 1, "kind": "video", "title": "clip.mp4"}], "documents": [],
    })
    assert cli.main(["web", "library", "muse", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["media"][0]["title"] == "clip.mp4"

    monkeypatch.setattr(web_muse, "download_library", lambda out, **kw: {
        "ok": True, "files": [str(tmp_path / "clip.mp4")],
    })
    assert cli.main(["web", "download", "muse", "--item", "1", "--out", str(tmp_path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["files"] == [str(tmp_path / "clip.mp4")]


def test_muse_chats_list_search_read(monkeypatch, capsys):
    monkeypatch.setattr(web_muse, "list_chats", lambda: {
        "ok": True, "chats": [{"index": 1, "title": "Tell a joke", "subtitle": "2m ago"}],
    })
    assert cli.main(["chats", "muse", "--json", "list"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed["sections"][0]["items"][0]["title"] == "Tell a joke"

    monkeypatch.setattr(web_muse, "search_chats", lambda q: {
        "ok": True, "chats": [{"index": 1, "title": "Tell a joke", "subtitle": ""}],
    })
    assert cli.main(["chats", "muse", "--json", "search", "joke"]) == 0
    assert json.loads(capsys.readouterr().out)["results"][0]["title"] == "Tell a joke"

    monkeypatch.setattr(web_muse, "read_chat", lambda c: {
        "ok": True, "title": "Tell a joke", "url": "https://muse.ai/",
        "messages": [{"role": "user", "text": "Tell a joke"}, {"role": "assistant", "text": "Why did..."}],
    })
    assert cli.main(["chats", "muse", "--json", "read", "Tell a joke"]) == 0
    read = json.loads(capsys.readouterr().out)
    assert read["lastPrompt"] == "Tell a joke" and read["reply"] == "Why did..."
