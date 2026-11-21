"""Loopback media jobs for the ArtCraft generation picker."""

import base64
import hmac
import os
import re
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock
from typing import Annotated, Literal, get_args
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from orchestrator import (
    web_chatgpt_image, web_flow, web_flow_music, web_gemini_image, web_gemini_video, web_grok, web_muse, web_muse_video,
)
from orchestrator.config import catalog
from orchestrator.drivers import permission_args
from orchestrator.machines import _run
from orchestrator.media_references import IMAGE_FILES, VIDEO_REFERENCE_LIMITS, validate_image_reference
from orchestrator.state import STATE_DIR, read_json, write_json

PORT = 8771
CRAFT_PREFIX = "/craft-ai/v1"
CRAFT_TOKEN_ENV = "AAO_CRAFT_AI_TOKEN"
CRAFT_MAX_BYTES = 23 * 1024 * 1024  # Leave room for base64 and JSON within the adapter's 32 MiB limit.
CRAFT_AUDIO_FORMATS = {"flow-music": ".mp3", "muse": ".mp3", "grok": ".wav"}
CRAFT_FORMATS = {
    "audio": CRAFT_AUDIO_FORMATS,
    "image": dict.fromkeys(("chatgpt", "flow", "gemini", "grok", "muse"), ".png"),
    "video": dict.fromkeys(VIDEO_REFERENCE_LIMITS, ".mp4"),
}
DESKTOP_ORIGINS = {None, "null", "tauri://localhost", "http://tauri.localhost", "https://tauri.localhost"}
VIDEO_FILES = {".mp4": "video/mp4", ".webm": "video/webm"}
AUDIO_FILES = {".mp3": "audio/mpeg", ".wav": "audio/wav"}
MEDIA_FILES = {"image": IMAGE_FILES, "video": VIDEO_FILES, "audio": AUDIO_FILES}
FILE_TYPES = IMAGE_FILES | VIDEO_FILES | AUDIO_FILES
CLI_OPTIONS = {
    "codex": ["exec", "--ephemeral", "--skip-git-repo-check", "-"],
    "antigravity": ["--print"],
    "cursor": ["--print", "--output-format", "text"],
}


def providers():
    rows = []
    for cli in CLI_OPTIONS:
        agent = catalog()[cli]
        available = bool(shutil.which(agent["binary"]))
        rows.append({"id": cli, "label": f'{agent["name"]} (via AAO)', "kind": "image",
                     "available": available, "detail": "" if available else "CLI is missing from PATH"})
    for kind in ("image", "video"):
        rows.append({"id": "flow", "label": "Google Flow (via AAO)", "kind": kind,
                     "available": True, "detail": "Uses the signed-in Flow account"})
    for kind in ("image", "video"):
        rows.append({"id": "gemini", "label": "Gemini (via AAO)", "kind": kind,
                     "available": True, "detail": "Uses the signed-in Gemini account"})
    for kind in ("image", "video"):
        rows.append({"id": "muse", "label": "Muse (via AAO)", "kind": kind, "available": True,
                     "detail": "Uses the signed-in Muse account"})
        rows.append({"id": "grok", "label": "Grok Imagine (via AAO)", "kind": kind, "available": True,
                     "detail": "Uses the signed-in Grok account"})
    rows.append({"id": "chatgpt", "label": "ChatGPT Web (via AAO)", "kind": "image", "available": True,
                 "detail": "Uses the signed-in ChatGPT account"})
    rows.append({"id": "flow-music", "label": "Google Flow Music (via AAO)", "kind": "audio", "available": True,
                 "detail": "Uses the signed-in Google Flow Music account"})
    ffmpeg_available = bool(shutil.which("ffmpeg"))
    rows.append({"id": "muse", "label": "Muse Soundtrack (via AAO)", "kind": "audio", "available": ffmpeg_available,
                 "detail": "Exports the soundtrack of a short Muse video as MP3" if ffmpeg_available
                           else "Requires FFmpeg on PATH"})
    rows.append({"id": "grok", "label": "Grok Audio (via AAO)", "kind": "audio", "available": True,
                 "detail": "Generates standalone WAV audio in the signed-in Grok chat"})
    for row in rows:
        if row["kind"] == "video":
            row["image_reference_limit"] = VIDEO_REFERENCE_LIMITS[row["id"]]
    return rows


class GenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    provider: str
    kind: str
    prompt: str = Field(min_length=1, max_length=10000)
    aspect_ratio: Literal["1:1", "16:9", "9:16", "4:3", "3:4"] | None = None
    references: list[Annotated[str, Field(max_length=11_200_000)]] = Field(default_factory=list, max_length=4)


class CraftMediaRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    provider: str
    kind: Literal["audio", "image", "video"]
    prompt: str = Field(min_length=1, max_length=10000)


def cli_image(cli, prompt, directory, *, aspect_ratio=None, references=()):
    binary = catalog()[cli]["binary"]
    task = ("Use your built-in image generation tool to make the requested raster image. "
            "Save it inside the current workspace as result.png. If the tool saves to another folder, "
            "copy its generated raster file into this workspace. The request describes image content. "
            "Do not use other generation tools, API keys, or code to draw the image.\n\n"
            f"Image description:\n{prompt}")
    if aspect_ratio:
        task += f"\nUse a {aspect_ratio} output aspect ratio."
    if references:
        names = ", ".join(path.relative_to(directory).as_posix() for path in references)
        task += f"\nEdit the first image and use the others as references. Workspace image files: {names}."
    options = CLI_OPTIONS[cli]
    subcommand_end = 1 if cli == "codex" else 0
    flags = [*options[:subcommand_end], *permission_args(cli, "yolo"), *options[subcommand_end:]]
    args, stdin = [binary, *flags], None
    if cli == "codex":
        stdin = task
    elif os.name == "nt" and cli == "cursor":
        # Pass the prompt through PowerShell's -File argument parser, bypassing the .cmd shim.
        executable = shutil.which(binary)
        if executable is None:
            raise RuntimeError("Cursor Agent CLI is missing from PATH")
        binary_path = Path(executable)
        script = binary_path.with_name("cursor-agent.ps1")
        if not script.is_file():
            raise RuntimeError("Cursor's PowerShell launcher was not found")
        args = ["powershell.exe", "-NoProfile", "-File", str(script), *flags, task]
    else:
        args.append(task)
    ok, _, error = _run(args, timeout=360, cwd=directory, stdin=stdin)
    if not ok:
        raise RuntimeError(error or "CLI generation failed")
    return sorted(path for path in directory.rglob("*") if path.suffix.lower() in IMAGE_FILES
                  and not path.is_relative_to(directory / "references"))


def save_references(values, directory):
    if not values:
        return []
    folder = directory / "references"
    folder.mkdir()
    files = []
    formats = {"data:image/png;base64": ".png", "data:image/jpeg;base64": ".jpg", "data:image/webp;base64": ".webp"}
    for index, value in enumerate(values):
        header, _, encoded = value.partition(",")
        if header not in formats:
            raise ValueError("Image references must be PNG, JPEG, or WebP")
        suffix = formats[header]
        data = base64.b64decode(encoded, validate=True)
        validate_image_reference(IMAGE_FILES[suffix], data[:12], len(data))
        path = folder / f"reference-{index + 1}{suffix}"
        path.write_bytes(data)
        files.append(path)
    return files


def generate(request, directory):
    if request.kind == "audio":
        driver = {"flow-music": web_flow_music.generate, "muse": web_muse.generate_audio,
                  "grok": web_grok.generate_audio}[request.provider]
        return generated_paths(driver(request.prompt, directory))

    options = {}
    if request.aspect_ratio:
        options["aspect_ratio"] = request.aspect_ratio
    if request.references:
        options["references"] = save_references(request.references, directory)
    if request.provider == "flow":
        result = web_flow.generate(request.kind, request.prompt, directory, **options)
    elif request.provider == "gemini" and request.kind == "image":
        result = web_gemini_image.generate(request.prompt, directory, **options)
    elif request.provider == "chatgpt" and request.kind == "image":
        result = web_chatgpt_image.generate(request.prompt, directory, **options)
    elif request.provider == "muse" and request.kind == "image":
        result = web_muse.generate_image(request.prompt, directory, **options)
    elif request.provider in {"gemini", "muse"}:
        driver = web_gemini_video if request.provider == "gemini" else web_muse_video
        result = driver.generate(request.prompt, directory, **options)
    elif request.provider == "grok":
        if request.kind == "image":
            prompt = request.prompt
            ratio = options.get("aspect_ratio")
            if ratio and ratio not in get_args(web_grok.Aspect):
                prompt += f"\n\nUse a {ratio} aspect ratio for the output image."
                options.pop("aspect_ratio")
            result = web_grok.generate_image(prompt, download_dir=directory, **options)
        else:
            references = options.get("references", [])
            result = web_grok.generate_video(request.prompt, download_dir=directory,
                                             image=references[0] if references else None)
    else:
        return cli_image(request.provider, request.prompt, directory, **options)
    return generated_paths(result)


def generated_paths(result):
    if not result.get("ok"):
        raise RuntimeError(result.get("reason") or result.get("error") or "Check the AAO browser login")
    if result.get("path"):
        return [Path(result["path"])]
    return [Path(path) for path in result.get("files", [])]


def craft_output(files, kind, suffix, directory):
    source = files[0]
    if kind == "audio" and (len(files) != 1 or source.suffix.lower() != suffix):
        raise RuntimeError(f"The provider must return one {suffix} file for Craft AI")
    if source.suffix.lower() != suffix:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise RuntimeError(f"FFmpeg is required to convert the result to {suffix}")
        if kind == "image":
            options = ["-frames:v", "1", "-c:v", "png"]
        else:
            ok, encoders, error = _run([ffmpeg, "-hide_banner", "-encoders"])
            if not ok:
                raise RuntimeError(error or "Could not list FFmpeg encoders")
            if re.search(r"^\s*V\S*\s+libx264\s", encoders, re.MULTILINE):
                video_codec = ["-c:v", "libx264"]
            elif re.search(r"^\s*V\S*\s+libopenh264\s", encoders, re.MULTILINE):
                video_codec = ["-c:v", "libopenh264", "-b:v", "2M"]
            else:
                raise RuntimeError("WebM conversion requires FFmpeg with libx264 or libopenh264")
            options = [
                "-map", "0:v:0", "-map", "0:a:0?", *video_codec, "-pix_fmt", "yuv420p",
                "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2", "-c:a", "aac", "-movflags", "+faststart",
            ]
        output = directory / "craft-output" / f"result{suffix}"
        output.parent.mkdir()
        demuxer = {".jpg": "jpeg_pipe", ".jpeg": "jpeg_pipe", ".webp": "webp_pipe",
                   ".webm": "matroska"}[source.suffix.lower()]
        ok, _, error = _run([ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error",
                             "-protocol_whitelist", "file", "-f", demuxer, "-i", str(source),
                             *options, str(output)], timeout=180)
        if not ok:
            raise RuntimeError(error or f"Could not convert the result to {suffix}")
        source = output
    if not 0 < source.stat().st_size <= CRAFT_MAX_BYTES:
        raise RuntimeError("The file is empty or exceeds the Craft AI inline result limit")
    return source


def create_app(root=None, *, craft_token=None):
    root = Path(root or STATE_DIR / "media").resolve()
    if craft_token is not None and not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", craft_token):
        raise ValueError(f"{CRAFT_TOKEN_ENV} must be a URL-safe token of 32–128 characters")
    app = FastAPI(title="AAO Media")
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1"])
    jobs = {}
    lock = Lock()
    workers = ThreadPoolExecutor(max_workers=1, thread_name_prefix="aao-media")

    def finished_job(job_id):
        directory = root / str(job_id)
        saved = read_json(directory / "job.json")
        if not isinstance(saved, dict) or saved.get("state") not in {"succeeded", "failed"}:
            return None
        paths = [(directory / name).resolve() for name in saved.get("files", [])]
        if any(not path.is_relative_to(directory) or not path.is_file()
               or path.suffix.lower() not in FILE_TYPES for path in paths):
            return {**saved, "state": "failed", "error": "The saved media files are unavailable", "files": []}
        return {**saved, "files": paths}

    def get_job(job_id, *, craft=False):
        with lock:
            job = jobs.get(job_id) or finished_job(job_id)
        if job is None or ("craft_format" in job) != craft:
            raise HTTPException(404, "AAO media job not found")
        return job

    @app.middleware("http")
    async def local_client(request: Request, call_next):
        path = request.scope["path"]
        if path == CRAFT_PREFIX or path.startswith(CRAFT_PREFIX + "/"):
            if craft_token is None:
                return Response(status_code=404)
            if "origin" in request.headers:
                return Response(status_code=403)
            scheme, _, token = request.headers.get("authorization", "").partition(" ")
            if scheme.lower() != "bearer" or not hmac.compare_digest(token.encode(), craft_token.encode()):
                return Response(status_code=401, headers={"WWW-Authenticate": "Bearer"})
            response = await call_next(request)
            response.headers["Cache-Control"] = "no-store"
            return response
        # Browsers cannot send the custom header cross-origin without a permitted preflight.
        if request.headers.get("x-aao-studio") != "1" or request.headers.get("origin") not in DESKTOP_ORIGINS:
            return Response(status_code=403)
        return await call_next(request)

    def finish(job_id, request, craft_format):
        directory = root / str(job_id)
        try:
            directory.mkdir(parents=True, exist_ok=False)
            files = []
            allowed = MEDIA_FILES[request.kind]
            for candidate in generate(request, directory):
                path = candidate.resolve()
                if path.is_relative_to(directory) and path.suffix.lower() in allowed and path.is_file() and path.stat().st_size:
                    files.append(path)
            if not files:
                raise RuntimeError("The provider finished without a media file in the job directory")
            if craft_format:
                files = [craft_output(files, request.kind, craft_format, directory)]
            outcome = {"state": "succeeded", "files": files}
        except Exception as exc:
            outcome = {"state": "failed", "error": str(exc)}
        if craft_format:
            outcome["craft_format"] = craft_format
        with lock:
            jobs[job_id] = outcome
            if directory.is_dir():
                try:
                    write_json(directory / "job.json", {**outcome, "files": [
                        path.relative_to(directory).as_posix() for path in outcome.get("files", [])]})
                except OSError:
                    # The current session can still serve the generated files.
                    pass

    @app.get("/providers")
    def get_providers():
        return providers()

    def enqueue(request, *, craft_format=None):
        provider = next((row for row in providers()
                         if row["id"] == request.provider and row["kind"] == request.kind), None)
        if provider is None or not provider["available"]:
            raise HTTPException(422, "This AAO provider is unavailable for the requested media")
        if request.kind == "audio" and (request.aspect_ratio or request.references):
            raise HTTPException(422, "AAO audio generation accepts a text prompt in ArtCraft")
        if request.kind == "video":
            if request.aspect_ratio:
                raise HTTPException(422, "Aspect ratio on this endpoint applies to image jobs")
            limit = provider["image_reference_limit"]
            if len(request.references) > limit:
                raise HTTPException(422, f'This provider accepts up to {limit} image reference(s) for video')
        job_id = uuid4()
        with lock:
            if sum(job["state"] == "pending" for job in jobs.values()) >= 8:
                raise HTTPException(429, "The AAO media queue is full")
            jobs[job_id] = {"state": "pending"}
            if craft_format:
                jobs[job_id]["craft_format"] = craft_format
        workers.submit(finish, job_id, request, craft_format)
        return {"id": job_id}

    @app.post("/jobs", status_code=202)
    def submit(request: GenerateRequest):
        return enqueue(request)

    @app.get("/jobs/{job_id}")
    def status(job_id: UUID):
        job = get_job(job_id)
        return {**job, "files": [path.name for path in job.get("files", [])]}

    @app.get("/jobs/{job_id}/files/{index}")
    def file(job_id: UUID, index: int):
        files = get_job(job_id).get("files", [])
        if not 0 <= index < len(files):
            raise HTTPException(404, "AAO media file not found")
        path = files[index]
        return FileResponse(path, media_type=FILE_TYPES[path.suffix.lower()])

    @app.get(CRAFT_PREFIX + "/health")
    def craft_health():
        return {"ok": True}

    @app.post(CRAFT_PREFIX + "/jobs", status_code=202)
    def craft_submit(request: CraftMediaRequest):
        suffix = CRAFT_FORMATS[request.kind].get(request.provider)
        if not suffix:
            raise HTTPException(422, "This provider does not support the requested media through Craft AI")
        return enqueue(GenerateRequest(**request.model_dump()), craft_format=suffix)

    @app.get(CRAFT_PREFIX + "/jobs/{job_id}")
    def craft_status(job_id: UUID):
        job = get_job(job_id, craft=True)
        if job["state"] != "succeeded":
            return {key: job[key] for key in ("state", "error") if key in job}
        try:
            with job["files"][0].open("rb") as source:
                data = source.read(CRAFT_MAX_BYTES + 1)
        except OSError:
            return {"state": "failed", "error": "The saved media file is unavailable"}
        if not data or len(data) > CRAFT_MAX_BYTES:
            return {"state": "failed", "error": "The file is empty or exceeds the Craft AI inline result limit"}
        return {"state": "succeeded", "result": {"data": base64.b64encode(data).decode("ascii")}}

    return app


def serve():
    import uvicorn

    uvicorn.run(create_app(craft_token=os.environ.get(CRAFT_TOKEN_ENV)), host="127.0.0.1", port=PORT)
