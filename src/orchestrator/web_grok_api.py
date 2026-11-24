"""Optional loopback HTTP and stdio MCP interfaces for the Grok web driver."""

from asyncio import to_thread
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from orchestrator import web_grok as grok


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: str = Field(min_length=1)
    chat_ref: str | None = None
    timeout_s: float = Field(default=120, gt=0, allow_inf_nan=False)


class ImageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: str = Field(min_length=1)
    quality: grok.Quality | None = None
    aspect_ratio: grok.Aspect | None = None
    count: grok.Count | None = None
    mentions: list[str] = []
    download_dir: str = grok.DEFAULT_OUT
    timeout_s: float = Field(default=300, gt=0, allow_inf_nan=False)


class VideoRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: str = Field(min_length=1)
    resolution: grok.Resolution = "480p"
    duration: grok.Duration = "6s"
    aspect_ratio: grok.Aspect | None = None
    mentions: list[str] = []
    download_dir: str = grok.DEFAULT_OUT
    timeout_s: float = Field(default=600, gt=0, allow_inf_nan=False)


def _http(operation, request):
    try:
        result = operation(**request.model_dump())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(502, str(exc)) from exc
    if not result.get("ok"):
        return JSONResponse(status_code=503 if result.get("loggedIn") is False else 502, content=result)
    return result


def create_app():
    app = FastAPI(title="AAO Grok")
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1"])

    @app.middleware("http")
    async def require_local_client(request: Request, call_next):
        # Web pages on other origins must not be able to spend the local account's quota.
        if request.method == "POST":
            origin = request.headers.get("origin")
            same_origin = f"http://{request.headers.get('host')}"
            if origin and origin != same_origin:
                return JSONResponse({"detail": "cross-origin requests are disabled"}, status_code=403)
            if request.headers.get("content-type", "").split(";", 1)[0].lower() != "application/json":
                return JSONResponse({"detail": "expected application/json"}, status_code=415)
        return await call_next(request)

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.post("/chat/messages")
    def chat(request: ChatRequest):
        return _http(grok.chat, request)

    @app.post("/imagine/images")
    def image(request: ImageRequest):
        return _http(grok.generate_image, request)

    @app.post("/imagine/videos")
    def video(request: VideoRequest):
        return _http(grok.generate_video, request)

    return app


def serve(port: int = 8000):
    import uvicorn

    if not 1 <= port <= 65535:
        raise ValueError("port must be between 1 and 65535")
    uvicorn.run(create_app(), host="127.0.0.1", port=port)


def create_mcp():
    from mcp.server.fastmcp import FastMCP

    server = FastMCP("AAO Grok")

    @server.tool()
    async def grok_chat(prompt: str, chat_ref: str | None = None, timeout_s: float = 120) -> dict[str, Any]:
        """Send a text prompt to Grok and return the reply, reasoning header, and URL."""
        return await to_thread(grok.chat, prompt, chat_ref=chat_ref, timeout_s=timeout_s)

    @server.tool()
    async def grok_generate_image(prompt: str, quality: grok.Quality | None = None,
                                  aspect_ratio: grok.Aspect | None = None, count: grok.Count | None = None,
                                  mentions: list[str] | None = None,
                                  download_dir: str = grok.DEFAULT_OUT, timeout_s: float = 300) -> dict[str, Any]:
        """Generate images with the signed-in Grok account; mentions are saved @references. Returns file paths."""
        return await to_thread(grok.generate_image, prompt, quality=quality, aspect_ratio=aspect_ratio,
                               count=count, mentions=mentions or (), download_dir=download_dir, timeout_s=timeout_s)

    @server.tool()
    async def grok_generate_video(prompt: str, resolution: grok.Resolution = "480p", duration: grok.Duration = "6s",
                                  aspect_ratio: grok.Aspect | None = None, mentions: list[str] | None = None,
                                  download_dir: str = grok.DEFAULT_OUT, timeout_s: float = 600) -> dict[str, Any]:
        """Generate a video with the signed-in Grok account; mentions are saved @references. Returns file paths."""
        return await to_thread(grok.generate_video, prompt, resolution=resolution, duration=duration,
                               aspect_ratio=aspect_ratio, mentions=mentions or (), download_dir=download_dir,
                               timeout_s=timeout_s)

    return server


def mcp():
    create_mcp().run(transport="stdio")
