"""FastAPI backend: serves the chat page and streams model replies (SSE)."""
from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..config import ROUTE_LABELS, Settings, load_settings
from ..llm.providers import OpenAICompatProvider, ProviderError
from ..llm.router import ProviderRouter
from ..agent import run_chat
from ..mcp_client import McpError, list_tools, load_tools

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
    # Prompt lab: replaces SYSTEM_PROMPT for this request. Only accepted when
    # ALLOW_PROMPT_OVERRIDE is on, and only with mode="local" (never sent to Gemini).
    system_prompt: str | None = Field(default=None, max_length=8_000)
    # With mode "local" (or an auto fallback): use this Ollama model instead of
    # OLLAMA_MODEL. Must be one Ollama has pulled; checked against /api/tags.
    local_model: str | None = Field(default=None, max_length=100)
    # Let the model use the MCP server's tools for this request (needs MCP_SERVER_URL).
    use_tools: bool = False


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
    app.state.router = build_router(settings, client)   # the direct route
    app.state.route = "direct"
    app.state.faig_routers = {}                          # built the first time a FortiAIGate route is used
    if not settings.gemini_api_key:
        log.warning("GEMINI_API_KEY not set: only the local model is available")
    yield
    await client.aclose()


app = FastAPI(title="AI Assistant", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def active(state) -> tuple[Settings, ProviderRouter]:
    """The settings and provider router for the current traffic route (direct, or through FortiAIGate)."""
    route = state.route
    if route == "direct":
        return state.settings, state.router
    settings = state.settings.for_route(route)
    router = state.faig_routers.get(route)
    if router is None:
        router = state.faig_routers[route] = build_router(settings, state.client)
    return settings, router


class RouteRequest(BaseModel):
    route: Literal["direct", "faig_model", "faig_all"]


def sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/healthz")
async def healthz():
    return {"ok": True}


async def ollama_models(state, settings: Settings) -> list[str] | None:
    """Names of the models Ollama has pulled, or None if Ollama can't be reached."""
    try:
        resp = await state.client.get(
            settings.ollama_base_url.rstrip("/") + "/api/tags",
            timeout=httpx.Timeout(3.0),
        )
        if resp.status_code == 200:
            return [m.get("name", "") for m in resp.json().get("models", [])]
    except (httpx.HTTPError, ValueError):
        pass
    return None


@app.get("/api/status")
async def status(request: Request):
    state = request.app.state
    settings, router = active(state)

    names = await ollama_models(state, settings)
    local_reachable = names is not None
    wanted = settings.ollama_model
    local_model_ready = any(n == wanted or n == f"{wanted}:latest" for n in names or [])

    mcp = {"mcp_configured": settings.mcp_enabled}
    if settings.mcp_enabled:
        try:
            tools = await list_tools(settings.mcp_server_url, timeout=3.0)
            mcp.update(mcp_reachable=True, mcp_tools=[t.name for t in tools])
        except McpError as exc:
            mcp.update(mcp_reachable=False, mcp_tools=[], mcp_error=str(exc))

    return {
        **mcp,
        "route": {
            "current": state.route,
            "label": ROUTE_LABELS[state.route],
            "model_configured": state.settings.route_problem("faig_model") == "",
            "all_configured": state.settings.route_problem("faig_all") == "",
        },
        "gemini_configured": bool(router.gemini),
        "gemini_models": router.gemini_status(),
        "local_model": settings.ollama_model,
        "local_models": sorted(n for n in (names or []) if n),
        "local_reachable": local_reachable,
        "local_model_ready": local_model_ready,
        "prompt_override_allowed": settings.allow_prompt_override,
    }


@app.post("/api/route")
async def set_route(body: RouteRequest, request: Request):
    """Pick where this app's traffic goes. Called by the Scripted Pipeline menu, not by the page."""
    problem = request.app.state.settings.route_problem(body.route)
    if problem:
        raise HTTPException(400, problem)
    request.app.state.route = body.route
    return {"route": body.route, "label": ROUTE_LABELS[body.route]}


@app.post("/api/chat")
async def chat(req: ChatRequest, request: Request):
    settings, router = active(request.app.state)

    system_prompt = settings.system_prompt
    if req.system_prompt is not None:
        if not settings.allow_prompt_override:
            return JSONResponse(
                {"detail": "Custom system prompts are disabled (ALLOW_PROMPT_OVERRIDE)."},
                status_code=403,
            )
        if req.mode != "local":
            return JSONResponse(
                {"detail": "Custom system prompts are only allowed with mode 'local'."},
                status_code=400,
            )
        system_prompt = req.system_prompt

    if req.local_model and req.local_model != settings.ollama_model:
        available = await ollama_models(request.app.state, settings)
        if available is None:
            return JSONResponse({"detail": "Ollama is unreachable."}, status_code=400)
        if req.local_model not in available:
            return JSONResponse(
                {"detail": f"{req.local_model} is not pulled in Ollama."}, status_code=400
            )
        router = router.with_local(
            OpenAICompatProvider(
                "local", request.app.state.client, settings.ollama_chat_url, req.local_model
            )
        )

    if req.use_tools and not settings.mcp_enabled:
        return JSONResponse(
            {"detail": "MCP is not configured (set MCP_SERVER_URL)."}, status_code=400
        )

    messages = [m.model_dump() for m in req.messages]
    if system_prompt:
        messages.insert(0, {"role": "system", "content": system_prompt})

    async def events():
        try:
            tools = None
            if req.use_tools:
                try:
                    tools = await load_tools(settings.mcp_server_url)
                except McpError as exc:
                    yield sse({"type": "error", "message": f"MCP server unavailable: {exc}"})
                    yield sse({"type": "done"})
                    return
            async for event in run_chat(
                router, messages, req.mode, req.model, tools, settings.mcp_max_rounds
            ):
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
