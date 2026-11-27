import json

from orchestrator import cli, web, web_gemini_library


def test_web_library_gemini_lists_items_and_filters(monkeypatch, capsys):
    monkeypatch.setattr(web, "open_target", lambda target: (True, "", ""))
    monkeypatch.setattr(web, "release", lambda target: None)

    def run_script(target, script, timeout):
        assert target == "gemini"
        cfg = json.loads(script.split("const cfg = ")[1].split(";\n", 1)[0])
        assert cfg["libraryUrl"] == "https://gemini.google.com/library"
        return {
            "ok": True,
            "url": "https://gemini.google.com/library",
            "documents": [
                {"index": 1, "kind": "document", "title": "Doc One", "date": "27 Oct, 0:40",
                 "id": "abc#123", "url": "https://gemini.google.com/app/abc#123"},
                {"index": 2, "kind": "document", "title": "Doc Two", "date": "28 Oct, 1:00",
                 "id": "def#456", "url": "https://gemini.google.com/app/def#456"},
            ],
            "media": [
                {"index": 1, "kind": "image", "title": "Untitled", "thumbnail": "https://example.com/1.jpg"},
                {"index": 2, "kind": "video", "title": "Clip", "thumbnail": ""},
            ],
        }

    monkeypatch.setattr(web, "run_script", run_script)

    assert cli.main(["web", "library", "gemini", "--type", "media", "--limit", "1", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["documents"] == []
    assert [m["index"] for m in payload["media"]] == [1]

    assert cli.main(["web", "library", "gemini"]) == 0
    text_out = capsys.readouterr().out
    assert "Documents (2):" in text_out
    assert "Doc One" in text_out
    assert "Media (2):" in text_out
    assert "[video] Clip" in text_out


def test_web_download_gemini_passes_items_and_existing_files(tmp_path, monkeypatch, capsys):
    (tmp_path / "Gemini_Generated_Image.jpeg").write_bytes(b"existing")
    monkeypatch.setattr(web, "open_target", lambda target: (True, "", ""))
    monkeypatch.setattr(web, "release", lambda target: None)

    def run_script(target, script, timeout):
        assert target == "gemini"
        assert timeout == 45
        cfg = json.loads(script.split("const cfg = ")[1].split(";\n", 1)[0])
        assert cfg["items"] == ["1", "Sunset"]
        assert cfg["downloadAll"] is False
        assert cfg["existingFiles"] == ["Gemini_Generated_Image.jpeg"]
        out_file = tmp_path / "Gemini_Generated_Image-2.jpeg"
        return {
            "ok": True,
            "url": "https://gemini.google.com/library",
            "files": [str(out_file)],
            "items": [{"index": 1, "title": "Untitled", "path": str(out_file)}],
        }

    monkeypatch.setattr(web, "run_script", run_script)
    assert cli.main(["web", "download", "gemini", "--item", "1", "--item", "Sunset",
                     "--out", str(tmp_path), "--timeout", "45", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["files"] == [str(tmp_path / "Gemini_Generated_Image-2.jpeg")]


def test_web_download_gemini_all_and_validation(tmp_path, monkeypatch, capsys):
    captured = {}

    def fake_download(directory, **kwargs):
        captured["directory"] = directory
        captured["kwargs"] = kwargs
        return {"ok": True, "target": "gemini", "files": [str(tmp_path / "a.jpeg")]}

    monkeypatch.setattr(web_gemini_library, "download_library", fake_download)
    assert cli.main(["web", "download", "gemini", "--all", "--limit", "3", "--out", str(tmp_path)]) == 0
    assert captured["kwargs"] == {"items": (), "download_all": True, "limit": 3}
    assert str(tmp_path / "a.jpeg") in capsys.readouterr().out

    assert cli.main(["web", "library", "grok"]) == 1
    assert "Use aao web library gemini" in capsys.readouterr().err

    assert cli.main(["web", "library", "gemini", "--all"]) == 1
    assert "Use aao web library gemini" in capsys.readouterr().err

    monkeypatch.undo()
    assert cli.main(["web", "download", "gemini", "--json"]) == 1
    err_payload = json.loads(capsys.readouterr().out)
    assert err_payload["ok"] is False
    assert "--item" in err_payload["reason"]
