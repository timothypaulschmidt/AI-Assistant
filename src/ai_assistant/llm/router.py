"""The provider router: picks which LLM answers, and fails over.

It is plain Python inside the backend container, not a separate service.

Gemini is an ordered list of models (GEMINI_MODELS). The local model is always
last. Each Gemini model has its own free-tier quota, so one running out does
not mean the others have.

Modes
-----
auto    Gemini models in order, then the local model.
gemini  Gemini models only, in order. Errors are shown to the user.
local   Local model only. Gemini is never called.

What happens when a Gemini model fails before it has produced any text:

  429 rate limited      skip that model for ``cooldown_seconds`` (default 5 min)
  404 model not found   skip that model for ``unavailable_seconds`` (default
                        1 hour: a typo or retired name should not be retried
                        per message)
  5xx                   skip that model for ``error_seconds`` (default 1 min)
  connection failure    Gemini itself is unreachable: skip ALL Gemini models
                        for ``error_seconds`` and go straight to local

A skipped model costs nothing while it is blocked. When the block expires, the
next request tries it once; if it fails again it is blocked again. So a broken
model is retried at most once per block period, never once per request.
  other 4xx (bad key,   shown as an error, never hidden by a fallback, so a
  bad request)          mistyped key doesn't silently become local-only

Failover can only happen *before* the first token is sent to the browser. If a
provider dies mid-answer, the error is surfaced rather than splicing two
different models' text together.
"""
from __future__ import annotations

import logging
import time
from typing import AsyncIterator, Callable

from .providers import Messages, Provider, ProviderError

log = logging.getLogger("ai_assistant.llm.router")

MODES = ("auto", "gemini", "local")


class ProviderRouter:
    def __init__(
        self,
        gemini: list[Provider],
        local: Provider,
        cooldown_seconds: float = 300.0,
        unavailable_seconds: float = 3600.0,
        error_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.gemini = list(gemini)
        self.local = local
        self.cooldown_seconds = cooldown_seconds
        self.unavailable_seconds = unavailable_seconds
        self.error_seconds = error_seconds
        self._clock = clock
        # model name -> (blocked until, reason)
        self._blocked: dict[str, tuple[float, str]] = {}

    # ---- per-model state -------------------------------------------------

    def _remaining(self, provider: Provider) -> float:
        until, _ = self._blocked.get(provider.model, (0.0, ""))
        return max(0.0, until - self._clock())

    def _block(self, provider: Provider, seconds: float, reason: str) -> None:
        self._blocked[provider.model] = (self._clock() + seconds, reason)
        log.warning("gemini model %s skipped for %ds (%s)", provider.model, seconds, reason)

    def gemini_status(self) -> list[dict]:
        out = []
        for p in self.gemini:
            remaining = self._remaining(p)
            if remaining > 0:
                state = self._blocked[p.model][1]
            else:
                state = "ready"
            out.append({"model": p.model, "state": state, "retry_in": round(remaining)})
        return out

    # ---- routing ---------------------------------------------------------

    def _candidates(self, mode: str, model: str | None = None) -> list[Provider]:
        if mode == "local":
            return [self.local]
        available = [p for p in self.gemini if self._remaining(p) <= 0]
        if mode == "gemini":
            if not self.gemini:
                raise ProviderError("gemini", 400, "GEMINI_API_KEY is not set")
            if model:
                # Pinned to one model: no skipping, no fallback to others.
                pinned = [p for p in self.gemini if p.model == model]
                if not pinned:
                    raise ProviderError("gemini", 400, f"{model} is not in GEMINI_MODELS")
                return pinned
            # Manual override: if everything is cooling down, try anyway.
            return available or list(self.gemini)
        return available + [self.local]

    async def stream(
        self, messages: Messages, mode: str = "auto", model: str | None = None
    ) -> AsyncIterator[dict]:
        """Yields {"type": "provider", ...} once, then {"type": "token", ...}.

        ``model`` (only with mode="gemini") pins the request to that one Gemini
        model. Errors are shown instead of falling back, so you can tell what
        a specific model does.
        """
        if mode not in MODES:
            raise ValueError(f"unknown mode: {mode}")
        queue = self._candidates(mode, model)

        while queue:
            provider = queue.pop(0)
            is_local = provider is self.local
            started = False
            announce = {
                "type": "provider",
                "name": provider.name,
                "model": provider.model,
                # True when auto mode had to use the local model
                "fallback": mode == "auto" and is_local and bool(self.gemini),
            }
            try:
                async for text in provider.stream(messages):
                    if not started:
                        started = True
                        yield announce
                    yield {"type": "token", "text": text}
                if not started:  # provider answered with an empty stream
                    yield announce
                return
            except ProviderError as exc:
                if started:
                    raise  # mid-answer failure: never switch models silently
                skippable = exc.retryable
                if not is_local:
                    if exc.status == 429:
                        self._block(provider, self.cooldown_seconds, "rate_limited")
                    elif exc.status == 404:
                        self._block(provider, self.unavailable_seconds, "unavailable")
                        skippable = True
                    elif exc.status == 0:
                        # Network problem: no other Gemini model will do better,
                        # so block them all, not just this one.
                        for p in self.gemini:
                            self._block(p, self.error_seconds, "unreachable")
                        queue = [p for p in queue if p is self.local]
                    elif exc.status >= 500:
                        self._block(provider, self.error_seconds, "error")
                if not queue or not skippable:
                    raise
                log.info("%s failed (%s); trying next provider", provider.model, exc)
