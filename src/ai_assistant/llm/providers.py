"""LLM providers.

Gemini (via its OpenAI-compatible endpoint) and Ollama both speak the same
``/chat/completions`` streaming protocol, so a single class covers both. Only
the URL, model name and API key differ. A future provider that speaks this
protocol (vLLM, llama.cpp server, LM Studio, ...) is just another instance.
"""
from __future__ import annotations

import json
from typing import AsyncIterator, Protocol

import httpx

Messages = list[dict[str, str]]


class ProviderError(Exception):
    """A provider failed. ``status`` is the HTTP status, or 0 if the
    connection failed or timed out."""

    def __init__(self, provider: str, status: int, message: str):
        if status == 0:
            text = f"{provider} unreachable: {message}"
        else:
            text = f"{provider} returned HTTP {status}: {message}"
        super().__init__(text)
        self.provider = provider
        self.status = status

    @property
    def retryable(self) -> bool:
        """True if trying a different provider makes sense. Rate limits,
        outages and connection problems are retryable; bad requests and bad
        API keys are not (they should be fixed, not hidden by a fallback)."""
        return self.status == 0 or self.status == 429 or self.status >= 500


class Provider(Protocol):
    name: str
    model: str

    def stream(self, messages: Messages) -> AsyncIterator[str]: ...


class OpenAICompatProvider:
    def __init__(
        self,
        name: str,
        client: httpx.AsyncClient,
        chat_url: str,
        model: str,
        api_key: str = "",
    ):
        self.name = name
        self.client = client
        self.chat_url = chat_url
        self.model = model
        self.api_key = api_key

    async def stream(self, messages: Messages) -> AsyncIterator[str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        payload = {"model": self.model, "messages": messages, "stream": True}
        try:
            async with self.client.stream(
                "POST", self.chat_url, json=payload, headers=headers
            ) as resp:
                if resp.status_code != 200:
                    body = (await resp.aread()).decode("utf-8", "replace")
                    raise ProviderError(self.name, resp.status_code, body[:300].strip())
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        return
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    text = (choices[0].get("delta") or {}).get("content")
                    if text:
                        yield text
        except httpx.TransportError as exc:
            raise ProviderError(self.name, 0, f"{type(exc).__name__}: {exc}") from exc
