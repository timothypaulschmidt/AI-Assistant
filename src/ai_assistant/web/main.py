"""FastAPI backend: serves the chat page and streams model replies (SSE)."""
from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..config import Settings, load_settings
from ..llm.providers import OpenAICompatProvider, ProviderError
from ..llm.router import ProviderRouter

log = logging.getLogger("ai_assistant.web")
STATIC_DIR = Path(__file__).parent / "static"


class Message(BaseModel):
    # Only user/assistant roles come from the browser. The system prompt is
    # controlled by the server (SYSTEM_PROMPT env var).
    role: Literal["user", "assistant"]
    content: str = Field(max_length=50_000)


class ChatRequest(BaseModel):
    messages: list[Message] = Field(min_length=1, max_length=200)
    mode: Literal["auto", "gemini", "local"] = "auto"
    # With mode="gemini": pin the request to this one model from GEMINI_MODELS
    model: str | None = Field(default=None, max_length=100)


def build_router(settings: Settings, client: httpx.AsyncClient) -> ProviderRouter:
    gemini: list[OpenAICompatProvider] = []
    if settings.gemini_api_key:
        gemini = [
            OpenAICompatProvider(
                "gemini",
                client,
                settings.gemini_chat_url,
                model,
                settings.gemini_api_key,
            )
            for model in settings.gemini_models
        ]
    local = OpenAICompatProvider(
        "local", client, settings.ollama_chat_url, settings.ollama_model
    )
    return ProviderRouter(
        gemini,
        local,
        settings.cooldown_seconds,
        settings.unavailable_seconds,
        settings.error_cooldown_seconds,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = load_settings()
    # Short connect timeout so an unreachable provider fails over quickly;
    # long read timeout because CPU-only models can take a while to start.
    client = httpx.AsyncClient(
        timeout=httpx.Timeout(settings.request_timeout, connect=5.0)
    )
    app.state.settings = settings
    app.state.client = client
    app.state.router = build_router(settings, client)
    if not settings.gemini_api_key:
        log.warning("GEMINI_API_KEY not set: only the local model is available")
    yield
    await client.aclose()


app = FastAPI(title="AI Assistant", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/healthz")
async def healthz():
    return {"ok": True}


@app.get("/api/status")
async def status(request: Request):
    state = request.app.state
    settings: Settings = state.settings
    router: ProviderRouter = state.router

    local_reachable = False
    local_model_ready = False
    try:
        resp = await state.client.get(
            settings.ollama_base_url.rstrip("/") + "/api/tags",
            timeout=httpx.Timeout(3.0),
        )
        if resp.status_code == 200:
            local_reachable = True
            names = [m.get("name", "") for m in resp.json().get("models", [])]
            wanted = settings.ollama_model
            local_model_ready = any(
                n == wanted or n == f"{wanted}:latest" for n in names
            )
    except (httpx.HTTPError, ValueError):
        pass

    return {
        "gemini_configured": bool(router.gemini),
        "gemini_models": router.gemini_status(),
        "local_model": settings.ollama_model,
        "local_reachable": local_reachable,
        "local_model_ready": local_model_ready,
    }


@app.post("/api/chat")
async def chat(req: ChatRequest, request: Request):
    settings: Settings = request.app.state.settings
    router: ProviderRouter = request.app.state.router

    messages = [m.model_dump() for m in req.messages]
    if settings.system_prompt:
        messages.insert(0, {"role": "system", "content": settings.system_prompt})

    async def events():
        try:
            async for event in router.stream(messages, req.mode, req.model):
                yield sse(event)
        except ProviderError as exc:
            yield sse({"type": "error", "message": str(exc)})
        except Exception:
            log.exception("chat request failed")
            yield sse({"type": "error", "message": "Internal server error"})
        yield sse({"type": "done"})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
