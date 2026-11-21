import base64
import os
from pathlib import Path
from time import monotonic, sleep

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from orchestrator import media, web_flow_music


HEADERS = {"X-AAO-Studio": "1"}
CRAFT_TOKEN = "aao-test-token-" + "x" * 32
CRAFT_HEADERS = {"Authorization": f"Bearer {CRAFT_TOKEN}"}


def test_craft_api_requires_its_own_bearer_and_native_request(tmp_path):
    url = media.CRAFT_PREFIX + "/health"
    with TestClient(media.create_app(tmp_path), base_url="http://localhost") as client:
        assert client.get(url, headers=CRAFT_HEADERS).status_code == 404
    with TestClient(media.create_app(tmp_path, craft_token=CRAFT_TOKEN), base_url="http://localhost") as client:
        assert client.get(url, headers=HEADERS).status_code == 401
        assert client.get(url, headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert client.get(url, headers={**CRAFT_HEADERS, "Origin": "null"}).status_code == 403
        probe = client.get(url, headers=CRAFT_HEADERS)
        assert probe.json() == {"ok": True}
        assert probe.headers["Cache-Control"] == "no-store"
        assert client.get("/providers", headers=CRAFT_HEADERS).status_code == 403
        assert client.get("/providers", headers=HEADERS).status_code == 200
        request = {"provider": "grok", "kind": "audio", "prompt": "Audio"}
        for invalid in ({"kind": "mesh"}, {"kind": "video", "provider": "chatgpt"},
                        {"provider": "codex"}, {"references": []}):
            assert client.post(media.CRAFT_PREFIX + "/jobs", headers=CRAFT_HEADERS,
                               json={**request, **invalid}).status_code == 422


@pytest.mark.parametrize(("provider", "filename", "data"), [
    ("flow-music", "song.mp3", b"ID3 a song"),
    ("muse", "soundtrack.mp3", b"ID3 a soundtrack"),
    ("grok", "sound.wav", b"RIFF0000WAVE a sound"),
    ("muse", "wrong.wav", b"RIFF0000WAVE wrong format"),
    ("grok", "oversized.wav", b"RIFF0000WAVE" + b"x" * 100),
])
def test_craft_audio_jobs_return_bounded_inline_results(provider, filename, data, tmp_path, monkeypatch):
    monkeypatch.setattr(media.shutil, "which", lambda _: "test-binary")
    monkeypatch.setattr(media, "CRAFT_MAX_BYTES", 64)

    def generate(request, directory):
        assert request.provider == provider and request.prompt == "Test audio"
        path = directory / filename
        path.write_bytes(data)
        return [path]

    monkeypatch.setattr(media, "generate", generate)
    request = {"provider": provider, "kind": "audio", "prompt": "Test audio"}
    with TestClient(media.create_app(tmp_path, craft_token=CRAFT_TOKEN), base_url="http://localhost") as client:
        response = client.post(media.CRAFT_PREFIX + "/jobs", headers=CRAFT_HEADERS, json=request)
        assert response.status_code == 202
        job_url = f'/jobs/{response.json()["id"]}'
        deadline = monotonic() + 3
        while (result := client.get(media.CRAFT_PREFIX + job_url, headers=CRAFT_HEADERS).json())["state"] == "pending" and monotonic() < deadline:
            sleep(0.01)
        assert client.get(job_url, headers=HEADERS).status_code == 404
        assert client.get(job_url + "/files/0", headers=HEADERS).status_code == 404
        if filename in {"wrong.wav", "oversized.wav"}:
            assert result["state"] == "failed" and "result" not in result
        else:
            assert result == {"state": "succeeded", "result": {"data": base64.b64encode(data).decode()}}
    with TestClient(media.create_app(tmp_path, craft_token=CRAFT_TOKEN), base_url="http://localhost") as restarted:
        assert restarted.get(job_url, headers=HEADERS).status_code == 404
        assert restarted.get(media.CRAFT_PREFIX + job_url, headers=CRAFT_HEADERS).json() == result


@pytest.mark.parametrize(("kind", "provider", "filename", "suffix", "demuxer"), [
    ("image", "chatgpt", "image.png", ".png", None),
    ("image", "grok", "image.jpg", ".png", "jpeg_pipe"),
    ("image", "gemini", "image.webp", ".png", "webp_pipe"),
    ("video", "flow", "clip.mp4", ".mp4", None),
    ("video", "muse", "clip.webm", ".mp4", "matroska"),
])
def test_craft_visual_jobs_use_first_output_and_fixed_format(
    kind, provider, filename, suffix, demuxer, tmp_path, monkeypatch
):
    def generate(request, directory):
        assert (request.kind, request.provider, request.prompt) == (kind, provider, "Test visual")
        first = directory / filename
        first.write_bytes(b"first output")
        second = directory / f"second{suffix}"
        second.write_bytes(b"second output")
        return [first, second]

    def convert(args, *, timeout=30):
        assert demuxer is not None
        if args[-1] == "-encoders":
            assert kind == "video"
            return True, " V....D libopenh264  OpenH264 H.264", None
        assert args[args.index("-f") + 1] == demuxer
        assert Path(args[args.index("-i") + 1]).name == filename
        if kind == "video":
            assert args[args.index("-c:v") + 1] == "libopenh264"
            assert "0:a:0?" in args
        output = Path(args[-1])
        assert output.suffix == suffix
        output.write_bytes(b"converted first output")
        return True, "", None

    monkeypatch.setattr(media, "generate", generate)
    monkeypatch.setattr(media.shutil, "which", lambda _: "ffmpeg")
    monkeypatch.setattr(media, "_run", convert)
    request = {"provider": provider, "kind": kind, "prompt": "Test visual"}
    with TestClient(media.create_app(tmp_path, craft_token=CRAFT_TOKEN), base_url="http://localhost") as client:
        response = client.post(media.CRAFT_PREFIX + "/jobs", headers=CRAFT_HEADERS, json=request)
        assert response.status_code == 202
        url = f'{media.CRAFT_PREFIX}/jobs/{response.json()["id"]}'
        deadline = monotonic() + 3
        while (result := client.get(url, headers=CRAFT_HEADERS).json())["state"] == "pending" and monotonic() < deadline:
            sleep(0.01)
        data = b"converted first output" if demuxer else b"first output"
        assert result == {"state": "succeeded", "result": {"data": base64.b64encode(data).decode()}}
    with TestClient(media.create_app(tmp_path, craft_token=CRAFT_TOKEN), base_url="http://localhost") as restarted:
        assert restarted.get(url, headers=CRAFT_HEADERS).json() == result


@pytest.mark.parametrize(("provider", "yolo_flags"), [
    ("codex", ["--dangerously-bypass-approvals-and-sandbox"]),
    ("antigravity", ["--dangerously-skip-permissions"]),
    ("cursor", ["--yolo", "--trust"]),
])
def test_image_cli_uses_yolo_without_interactive_or_sandbox_flags(provider, yolo_flags, tmp_path, monkeypatch):
    monkeypatch.setenv("AAO_PERMISSION_MODE", "auto")
    if provider == "cursor" and os.name == "nt":
        (tmp_path / "cursor-agent.ps1").touch()
        monkeypatch.setattr(media.shutil, "which", lambda binary: str(tmp_path / f"{binary}.cmd"))

    def run(args, *, timeout, cwd, stdin):
        assert all(flag in args for flag in yolo_flags)
        assert not {"--sandbox", "--mode", "--auto-review", "--approve-for-me"}.intersection(args)
        assert cwd == tmp_path
        if provider == "codex":
            assert args[:3] == ["codex", "exec", yolo_flags[0]] and args[-1] == "-"
            assert stdin.endswith("A paper boat")
        else:
            assert stdin is None and args[-1].endswith("A paper boat")
        if provider == "cursor" and os.name == "nt":
            assert args[:4] == ["powershell.exe", "-NoProfile", "-File", str(tmp_path / "cursor-agent.ps1")]
        (cwd / "result.png").write_bytes(b"test image")
        return True, "", None

    monkeypatch.setattr(media, "_run", run)
    assert media.cli_image(provider, "A paper boat", tmp_path) == [tmp_path / "result.png"]


def test_studio_job_downloads_only_its_outputs(tmp_path, monkeypatch):
    outside = tmp_path / "unrelated.mp4"
    outside.write_bytes(b"unrelated")

    def generate(job, directory):
        assert job.provider == "grok"
        clip = directory / "output.mp4"
        clip.write_bytes(b"test video")
        return [outside, clip]

    monkeypatch.setattr(media, "generate", generate)
    directory = tmp_path / "jobs"
    with TestClient(media.create_app(directory), headers=HEADERS, base_url="http://localhost") as client:
        accepted = client.post("/jobs", json={"provider": "grok", "kind": "video", "prompt": "A test video"})
        assert accepted.status_code == 202
        url = f'/jobs/{accepted.json()["id"]}'
        deadline = monotonic() + 3
        while (result := client.get(url).json())["state"] == "pending" and monotonic() < deadline:
            sleep(0.01)
        assert result == {"state": "succeeded", "files": ["output.mp4"]}
        assert client.get(f"{url}/files/0").content == b"test video"
        assert client.get(f"{url}/files/1").status_code == 404
    with TestClient(media.create_app(directory), headers=HEADERS, base_url="http://localhost") as restarted:
        assert restarted.get(url).json() == result
        assert restarted.get(f"{url}/files/0").content == b"test video"
    with TestClient(media.create_app(directory, craft_token=CRAFT_TOKEN), base_url="http://localhost") as bridge:
        assert bridge.get(media.CRAFT_PREFIX + url, headers=CRAFT_HEADERS).status_code == 404


def test_invalid_provider_media_kind_and_unmarked_client_cannot_submit(tmp_path):
    with TestClient(media.create_app(tmp_path), base_url="http://localhost") as client:
        request = {"provider": "codex", "kind": "video", "prompt": "A test video"}
        assert client.post("/jobs", json=request).status_code == 403
        assert client.post("/jobs", json=request, headers=HEADERS).status_code == 422
        assert client.post("/jobs", json=request, headers={**HEADERS, "Origin": "http://localhost:8888"}).status_code == 403
        assert client.get("/providers", headers={**HEADERS, "Origin": "http://tauri.localhost"}).status_code == 200


@pytest.mark.parametrize(("provider", "driver", "function"), [
    ("flow-music", web_flow_music, "generate"),
    ("muse", media.web_muse, "generate_audio"),
    ("grok", media.web_grok, "generate_audio"),
])
def test_audio_job_returns_downloadable_audio_after_restart(provider, driver, function, tmp_path, monkeypatch):
    audio = b"ID3 test song"

    def generate(prompt, directory):
        assert prompt == "A gentle instrumental"
        path = directory / "gentle.mp3"
        path.write_bytes(audio)
        return {"ok": True, "path": str(path)}

    monkeypatch.setattr(driver, function, generate)
    monkeypatch.setattr(media.shutil, "which", lambda _: "test-binary")
    request = {"provider": provider, "kind": "audio", "prompt": "A gentle instrumental"}
    with TestClient(media.create_app(tmp_path), headers=HEADERS, base_url="http://localhost") as client:
        providers = [row for row in client.get("/providers").json() if row["kind"] == "audio"]
        assert next(row for row in providers if row["id"] == provider)["available"]
        assert client.post("/jobs", json={**request, "aspect_ratio": "16:9"}).status_code == 422
        response = client.post("/jobs", json=request)
        assert response.status_code == 202
        job_url = f'/jobs/{response.json()["id"]}'
        deadline = monotonic() + 3
        while (result := client.get(job_url).json())["state"] == "pending" and monotonic() < deadline:
            sleep(0.01)
        assert result == {"state": "succeeded", "files": ["gentle.mp3"]}
    with TestClient(media.create_app(tmp_path), headers=HEADERS, base_url="http://localhost") as restarted:
        download = restarted.get(f"{job_url}/files/0")
        assert download.headers["Content-Type"] == "audio/mpeg"
        assert download.content == audio


@pytest.mark.parametrize(("provider", "kind"), [
    ("flow", "image"), ("flow", "video"), ("gemini", "image"), ("gemini", "video"),
    ("chatgpt", "image"), ("muse", "video"),
])
def test_media_routes_use_their_web_driver(provider, kind, tmp_path, monkeypatch):
    gemini = media.web_gemini_image if kind == "image" else media.web_gemini_video
    driver = {"flow": media.web_flow, "gemini": gemini, "chatgpt": media.web_chatgpt_image,
              "muse": media.web_muse_video}[provider]
    result = tmp_path / ("result.png" if kind == "image" else "result.mp4")
    calls = []

    def generate(*args):
        calls.append(args)
        return {"ok": True, "path": str(result)}

    monkeypatch.setattr(driver, "generate", generate)
    request = media.GenerateRequest(provider=provider, kind=kind, prompt="A test prompt")
    assert media.generate(request, tmp_path) == [result]
    assert calls == ([(kind, request.prompt, tmp_path)] if provider == "flow" else [(request.prompt, tmp_path)])


@pytest.mark.parametrize("provider", ["flow", "gemini", "chatgpt", "muse", "grok", "cursor"])
def test_image_editor_sends_ratio_and_local_reference(provider, tmp_path, monkeypatch):
    source = b"\x89PNG\r\n\x1a\nfixture"
    encoded = "data:image/png;base64," + base64.b64encode(source).decode("ascii")
    result = tmp_path / "result.png"

    def generate(*args, aspect_ratio, references, download_dir=None):
        assert aspect_ratio == "16:9"
        assert (download_dir or args[-1]) == tmp_path
        assert [path.relative_to(tmp_path).as_posix() for path in references] == ["references/reference-1.png"]
        assert references[0].read_bytes() == source
        if provider == "cursor":
            return [result]
        if provider == "grok":
            return {"ok": True, "files": [str(result)]}
        return {"ok": True, "path": str(result)}

    drivers = {"flow": (media.web_flow, "generate"), "gemini": (media.web_gemini_image, "generate"),
               "chatgpt": (media.web_chatgpt_image, "generate"),
               "muse": (media.web_muse, "generate_image"), "grok": (media.web_grok, "generate_image"),
               "cursor": (media, "cli_image")}
    driver, function = drivers[provider]
    monkeypatch.setattr(driver, function, generate)
    request = media.GenerateRequest(provider=provider, kind="image", prompt="Change the artwork",
                                    aspect_ratio="16:9", references=[encoded])
    assert media.generate(request, tmp_path) == [result]


@pytest.mark.parametrize(("provider", "limit"), [("flow", 3), ("gemini", 1), ("muse", 4), ("grok", 1)])
def test_video_job_sends_attached_images_to_provider(provider, limit, tmp_path, monkeypatch):
    source = b"\x89PNG\r\n\x1a\nvideo reference"
    encoded = "data:image/png;base64," + base64.b64encode(source).decode("ascii")

    def generate(*args, references=(), image=None, download_dir=None):
        directory = download_dir or args[-1]
        actual_references = [image] if image else references
        assert len(actual_references) == limit
        assert [path.read_bytes() for path in actual_references] == [source] * limit
        result = directory / "output.mp4"
        result.write_bytes(b"test video")
        return {"ok": True, "path": str(result)}

    drivers = {"flow": (media.web_flow, "generate"), "gemini": (media.web_gemini_video, "generate"),
               "muse": (media.web_muse_video, "generate"), "grok": (media.web_grok, "generate_video")}
    driver, function = drivers[provider]
    monkeypatch.setattr(driver, function, generate)
    with TestClient(media.create_app(tmp_path), headers=HEADERS, base_url="http://localhost") as client:
        catalog = client.get("/providers").json()
        entry = next(row for row in catalog if row["id"] == provider and row["kind"] == "video")
        assert entry["image_reference_limit"] == limit
        request = {"provider": provider, "kind": "video", "prompt": "Animate the reference"}
        assert client.post("/jobs", json={**request, "references": [encoded] * (limit + 1)}).status_code == 422
        accepted = client.post("/jobs", json={**request, "references": [encoded] * limit})
        assert accepted.status_code == 202
        job_url = f'/jobs/{accepted.json()["id"]}'
        deadline = monotonic() + 3
        while (result := client.get(job_url).json())["state"] == "pending" and monotonic() < deadline:
            sleep(0.01)
        assert result == {"state": "succeeded", "files": ["output.mp4"]}
