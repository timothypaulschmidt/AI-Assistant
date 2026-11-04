"""Settings, read from environment variables so the same image runs on
your Windows PC, in Docker, and in k3s without code changes."""
from __future__ import annotations

import os
from dataclasses import dataclass, replace

# Tried in this order; the local model is always last. Names your key can't
# use (404) are skipped automatically, so unknown models here are harmless.
DEFAULT_GEMINI_MODELS = (
    "gemini-3.6-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.5-flash",
    "gemini-robotics-er-2-preview",
)


def parse_models(raw: str) -> tuple[str, ...]:
    """Comma-separated list -> ordered tuple, without blanks or duplicates."""
    seen: list[str] = []
    for item in raw.split(","):
        name = item.strip()
        if name and name not in seen:
            seen.append(name)
    return tuple(seen)


ROUTES = ("direct", "faig_model", "faig_all")
ROUTE_LABELS = {
    "direct": "Direct",
    "faig_model": "Model via FortiAIGate",
    "faig_all": "Model + MCP via FortiAIGate",
}


@dataclass(frozen=True)
class Settings:
    # Primary provider: Google Gemini (AI Studio key, free tier)
    gemini_api_key: str
    gemini_models: tuple[str, ...]
    gemini_base_url: str
    # Fallback provider: Ollama running on your Linux box
    ollama_base_url: str
    ollama_model: str
    # After a Gemini model answers HTTP 429 (rate limit / quota), skip that
    # model for this long
    cooldown_seconds: float
    # After a Gemini model answers HTTP 404 (not found / retired), skip it
    # for this long
    unavailable_seconds: float
    # After a Gemini model fails with a 5xx or the connection fails, skip it
    # (connection failure: skip all Gemini models) for this long
    error_cooldown_seconds: float
    # Seconds to wait for a response (CPU-only local models can be slow)
    request_timeout: float
    system_prompt: str
    # Lets the browser send its own system prompt, for Local-only testing
    # (the "Prompt lab"). Off unless you turn it on.
    allow_prompt_override: bool
    # MCP server the assistant can use for tools (Streamable HTTP endpoint, for
    # example http://localhost:8000/mcp). Empty = MCP is off.
    mcp_server_url: str
    # Most model -> tool -> model rounds in one answer. Stops a confused (or
    # attacked) model from looping forever and burning your quota.
    mcp_max_rounds: int = 5
    # FortiAIGate: the same things reached through the gateway. Empty until FortiAIGate is
    # configured; a route that needs an empty one is refused.
    faig_gemini_base_url: str = ""
    faig_ollama_base_url: str = ""
    faig_mcp_server_url: str = ""
    # How the model is reached through FortiAIGate. The gateway publishes models under its own
    # names (provider slug + model, e.g. ollama-qwen3-8b) and wants its own API key.
    faig_ollama_model: str = ""
    faig_api_key: str = ""
    # False = accept the gateway's self-signed lab certificate. Only used on FortiAIGate routes.
    faig_verify_tls: bool = True
    # Set by for_route(): the "local" model is served by the gateway, not by Ollama itself.
    ollama_via_gateway: bool = False

    def route_problem(self, route: str) -> str:
        """Empty when the route can be used; otherwise what is missing."""
        if route not in ROUTES:
            return f"Unknown route {route!r}."
        if route in ("faig_model", "faig_all") and not self.faig_ollama_base_url:
            return "FortiAIGate is not configured for the model yet (FAIG_OLLAMA_BASE_URL is empty)."
        if route in ("faig_model", "faig_all") and not self.faig_api_key:
            return "FortiAIGate has no API key yet (FAIG_API_KEY is empty)."
        if route == "faig_all" and not self.faig_mcp_server_url:
            return "FortiAIGate is not configured for MCP yet (FAIG_MCP_SERVER_URL is empty)."
        return ""

    def for_route(self, route: str) -> "Settings":
        """The settings to use on a route: the FortiAIGate addresses swapped in.
        direct = as configured; faig_model = model through FortiAIGate, MCP direct;
        faig_all = model and MCP through FortiAIGate. A provider with no FortiAIGate address
        is switched off on a FortiAIGate route (Gemini) instead of going around the gateway."""
        if route == "direct":
            return self
        changes = {"ollama_base_url": self.faig_ollama_base_url, "ollama_via_gateway": True}
        if self.faig_ollama_model:
            changes["ollama_model"] = self.faig_ollama_model
        if self.faig_gemini_base_url:
            changes["gemini_base_url"] = self.faig_gemini_base_url
        else:
            changes["gemini_api_key"] = ""
        if route == "faig_all":
            changes["mcp_server_url"] = self.faig_mcp_server_url
        return replace(self, **changes)

    @property
    def mcp_enabled(self) -> bool:
        return bool(self.mcp_server_url)

    @property
    def gemini_chat_url(self) -> str:
        return self.gemini_base_url.rstrip("/") + "/chat/completions"

    @property
    def ollama_key(self) -> str:
        """Bearer key for the "local" model: the FortiAIGate key on a gateway route, none for plain Ollama."""
        return self.faig_api_key if self.ollama_via_gateway else ""

    @property
    def ollama_chat_url(self) -> str:
        return self.ollama_base_url.rstrip("/") + "/v1/chat/completions"


def load_settings() -> Settings:
    env = os.environ.get
    models = parse_models(env("GEMINI_MODELS", "")) or DEFAULT_GEMINI_MODELS
    return Settings(
        gemini_api_key=env("GEMINI_API_KEY", "").strip(),
        gemini_models=models,
        gemini_base_url=env(
            "GEMINI_BASE_URL",
            "https://generativelanguage.googleapis.com/v1beta/openai",
        ),
        ollama_base_url=env("OLLAMA_BASE_URL", "http://localhost:11434"),
        ollama_model=env("OLLAMA_MODEL", "llama3.2:3b"),
        cooldown_seconds=float(env("GEMINI_COOLDOWN_SECONDS", "300")),
        unavailable_seconds=float(env("GEMINI_UNAVAILABLE_SECONDS", "3600")),
        error_cooldown_seconds=float(env("GEMINI_ERROR_COOLDOWN_SECONDS", "60")),
        request_timeout=float(env("REQUEST_TIMEOUT", "180")),
        system_prompt=env("SYSTEM_PROMPT", "You are a helpful assistant."),
        allow_prompt_override=env("ALLOW_PROMPT_OVERRIDE", "").strip().lower()
        in ("1", "true", "yes", "on"),
        mcp_server_url=env("MCP_SERVER_URL", "").strip(),
        mcp_max_rounds=max(1, int(env("MCP_MAX_ROUNDS", "5"))),
        faig_gemini_base_url=env("FAIG_GEMINI_BASE_URL", "").strip(),
        faig_ollama_base_url=env("FAIG_OLLAMA_BASE_URL", "").strip(),
        faig_mcp_server_url=env("FAIG_MCP_SERVER_URL", "").strip(),
        faig_ollama_model=env("FAIG_OLLAMA_MODEL", "").strip(),
        faig_api_key=env("FAIG_API_KEY", "").strip(),
        faig_verify_tls=env("FAIG_VERIFY_TLS", "true").strip().lower()
        not in ("0", "false", "no", "off"),
    )
