import asyncio

import pytest

from ai_assistant.config import DEFAULT_GEMINI_MODELS, parse_models
from ai_assistant.llm.providers import ProviderError, Usage
from ai_assistant.llm.router import ProviderRouter

MSGS = [{"role": "user", "content": "hi"}]


def err(status, provider="gemini"):
    return ProviderError(provider, status, "boom")


class Fake:
    """Stand-in provider. ``error`` is raised before any token, or after
    ``fail_after`` tokens if that is set."""

    def __init__(self, name, model=None, tokens=("a", "b"), error=None, fail_after=None):
        self.name, self.model = name, model or f"{name}-model"
        self.tokens, self.error, self.fail_after = list(tokens), error, fail_after
        self.calls = 0

    async def stream(self, messages):
        self.calls += 1
        if self.error and self.fail_after is None:
            raise self.error
        for i, tok in enumerate(self.tokens):
            if self.error and i == self.fail_after:
                raise self.error
            yield tok


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def run(router, mode="auto"):
    async def go():
        return [e async for e in router.stream(MSGS, mode)]

    return asyncio.run(go())


def text(events):
    return "".join(e["text"] for e in events if e["type"] == "token")


def chain(*errors, tokens=("a", "b")):
    """Gemini models g1, g2, ... where errors[i] is the error for model i
    (None = healthy)."""
    return [Fake("gemini", f"g{i + 1}", tokens=tokens, error=e) for i, e in enumerate(errors)]


def make(gemini, local=None, clock=None, **kw):
    return ProviderRouter(gemini, local or Fake("local"), clock=clock or Clock(), **kw)


# ---- ordering and failover ------------------------------------------------

def test_auto_uses_first_gemini_model_when_healthy():
    gs = chain(None, None)
    local = Fake("local")
    events = run(make(gs, local))
    assert events[0]["model"] == "g1" and events[0]["fallback"] is False
    assert text(events) == "ab"
    assert gs[1].calls == 0 and local.calls == 0


def test_429_moves_to_next_gemini_model_not_local():
    gs = chain(err(429), None)
    local = Fake("local")
    router = make(gs, local, cooldown_seconds=300)
    events = run(router)
    assert events[0]["name"] == "gemini" and events[0]["model"] == "g2"
    assert events[0]["fallback"] is False  # still Gemini
    assert local.calls == 0
    state = {m["model"]: m for m in router.gemini_status()}
    assert state["g1"]["state"] == "rate_limited" and state["g1"]["retry_in"] == 300
    assert state["g2"]["state"] == "ready"


def test_models_are_tried_in_the_configured_order():
    gs = chain(err(429), err(500), err(429), None)
    events = run(make(gs))
    assert events[0]["model"] == "g4"
    assert [g.calls for g in gs] == [1, 1, 1, 1]


def test_all_gemini_models_exhausted_falls_back_to_local():
    gs = chain(err(429), err(429), err(429))
    local = Fake("local", tokens=("x", "y"))
    events = run(make(gs, local))
    assert events[0]["name"] == "local" and events[0]["fallback"] is True
    assert text(events) == "xy"


def test_404_model_is_skipped_and_blocked_for_a_long_time():
    clock = Clock()
    gs = chain(err(404), None)
    router = make(gs, clock=clock, cooldown_seconds=300, unavailable_seconds=3600)
    assert run(router)[0]["model"] == "g2"
    state = {m["model"]: m for m in router.gemini_status()}
    assert state["g1"]["state"] == "unavailable" and state["g1"]["retry_in"] == 3600
    clock.now += 600  # past the 429 cooldown, but a 404 stays blocked
    run(router)
    assert gs[0].calls == 1


def test_blocked_models_are_skipped_then_retried_after_cooldown():
    clock = Clock()
    gs = chain(err(429), None)
    router = make(gs, clock=clock, cooldown_seconds=300)
    run(router)
    assert gs[0].calls == 1
    clock.now += 100
    run(router)
    assert gs[0].calls == 1  # still cooling down, not called again
    clock.now += 250
    run(router)
    assert gs[0].calls == 2  # cooldown over, model tried again


def test_connection_failure_skips_remaining_gemini_models():
    gs = chain(err(0), None, None)
    local = Fake("local")
    router = make(gs, local)
    events = run(router)
    assert events[0]["name"] == "local" and events[0]["fallback"] is True
    assert gs[1].calls == 0 and gs[2].calls == 0
    # every Gemini model is blocked briefly, so the next request skips the wait
    assert all(m["state"] == "unreachable" and m["retry_in"] == 60 for m in router.gemini_status())


def test_5xx_tries_next_model_and_blocks_the_failing_one_briefly():
    gs = chain(err(503), None)
    router = make(gs, error_seconds=60)
    assert run(router)[0]["model"] == "g2"
    first = router.gemini_status()[0]
    assert first["state"] == "error" and first["retry_in"] == 60


def test_failing_model_is_not_retried_on_every_request():
    clock = Clock()
    gs = chain(err(503), None)
    router = make(gs, clock=clock, error_seconds=60)
    for _ in range(5):
        run(router)
    assert gs[0].calls == 1          # five requests, one attempt
    clock.now += 61
    run(router)
    assert gs[0].calls == 2          # retried once after the block expired


def test_unreachable_gemini_is_not_retried_on_every_request():
    clock = Clock()
    gs = chain(err(0), None)
    local = Fake("local")
    router = make(gs, local, clock=clock, error_seconds=60)
    for _ in range(4):
        run(router)
    assert gs[0].calls == 1 and gs[1].calls == 0 and local.calls == 4
    clock.now += 61
    run(router)
    assert gs[0].calls == 2


# ---- modes -----------------------------------------------------------------

def test_local_mode_never_calls_gemini():
    gs = chain(None)
    events = run(make(gs), "local")
    assert events[0]["name"] == "local" and events[0]["fallback"] is False
    assert gs[0].calls == 0


def test_gemini_mode_walks_the_chain_but_never_uses_local():
    gs = chain(err(429), None)
    local = Fake("local")
    events = run(make(gs, local), "gemini")
    assert events[0]["model"] == "g2"
    assert local.calls == 0


def test_gemini_mode_with_everything_exhausted_raises():
    gs = chain(err(429), err(429))
    local = Fake("local")
    with pytest.raises(ProviderError):
        run(make(gs, local), "gemini")
    assert local.calls == 0


def test_gemini_mode_tries_anyway_when_all_models_are_blocked():
    clock = Clock()
    gs = chain(err(429), err(429))
    router = make(gs, clock=clock)
    with pytest.raises(ProviderError):
        run(router, "gemini")
    calls = [g.calls for g in gs]
    for g in gs:
        g.error = None  # quota "comes back", but cooldown has not expired
    assert run(router, "gemini")[0]["model"] == "g1"  # manual override tries them
    assert [g.calls for g in gs] == [calls[0] + 1, calls[1]]


def test_unknown_mode_rejected():
    with pytest.raises(ValueError):
        run(make([]), "bogus")


# ---- errors that must not be hidden ------------------------------------------

def test_bad_key_is_shown_not_hidden_and_stops_the_chain():
    gs = chain(err(401), None)
    local = Fake("local")
    with pytest.raises(ProviderError) as info:
        run(make(gs, local))
    assert info.value.status == 401
    assert gs[1].calls == 0 and local.calls == 0


def test_mid_stream_failure_does_not_switch_models():
    g1 = Fake("gemini", "g1", tokens=("a", "b", "c"), fail_after=1, error=err(0))
    g2, local = Fake("gemini", "g2"), Fake("local")

    async def go():
        return [e async for e in make([g1, g2], local).stream(MSGS)]

    with pytest.raises(ProviderError):
        asyncio.run(go())
    assert g2.calls == 0 and local.calls == 0


def test_everything_failing_raises_the_local_error():
    gs = chain(err(429), err(429))
    local = Fake("local", error=err(0, "local"))
    with pytest.raises(ProviderError) as info:
        run(make(gs, local))
    assert info.value.provider == "local"


def test_local_404_is_shown_not_skipped():
    local = Fake("local", error=err(404, "local"))
    with pytest.raises(ProviderError) as info:
        run(make([], local))
    assert info.value.status == 404


def test_no_gemini_key_uses_local_in_auto_and_errors_in_gemini_mode():
    router = make([])
    events = run(router)
    assert events[0]["name"] == "local" and events[0]["fallback"] is False
    with pytest.raises(ProviderError):
        run(router, "gemini")


# ---- config ------------------------------------------------------------------

def test_default_model_order_is_as_requested():
    assert DEFAULT_GEMINI_MODELS == (
        "gemini-3.6-flash",
        "gemini-flash-latest",
        "gemini-4-argon",
        "gemini-3.8-flash",
        "gemini-3.5-flash-lite",
        "gemini-3.1-pro-preview",
    )


def test_parse_models_trims_and_dedupes_preserving_order():
    assert parse_models(" a, b ,,a,c ") == ("a", "b", "c")
    assert parse_models("") == ()


# ---- pinning one model ---------------------------------------------------------

def test_pinned_model_uses_only_that_model():
    gs = chain(None, None, None)
    local = Fake("local")
    events = run_pinned(make(gs, local), "g2")
    assert events[0]["model"] == "g2" and events[0]["fallback"] is False
    assert [g.calls for g in gs] == [0, 1, 0] and local.calls == 0


def test_pinned_model_failure_is_shown_not_hidden_by_other_models():
    gs = chain(None, err(429), None)
    local = Fake("local")
    with pytest.raises(ProviderError) as info:
        run_pinned(make(gs, local), "g2")
    assert info.value.status == 429
    assert [g.calls for g in gs] == [0, 1, 0] and local.calls == 0


def test_pinned_model_ignores_its_cooldown_so_you_can_retry_by_hand():
    clock = Clock()
    gs = chain(err(429))
    router = make(gs, clock=clock)
    with pytest.raises(ProviderError):
        run_pinned(router, "g1")
    gs[0].error = None
    assert run_pinned(router, "g1")[0]["model"] == "g1"


def test_pinned_unknown_model_is_rejected():
    gs = chain(None)
    with pytest.raises(ProviderError) as info:
        run_pinned(make(gs), "nope")
    assert "GEMINI_MODELS" in str(info.value) and gs[0].calls == 0


def test_model_is_ignored_outside_gemini_mode():
    gs = chain(None, None)
    events = run_pinned(make(gs), "g2", mode="auto")
    assert events[0]["model"] == "g1"


def run_pinned(router, model, mode="gemini"):
    async def go():
        return [e async for e in router.stream(MSGS, mode, model)]

    return asyncio.run(go())


def test_usage_is_forwarded_after_the_tokens():
    gemini = Fake("gemini", tokens=("a", "b", Usage(10, 2)))
    router = ProviderRouter([gemini], Fake("local"))

    async def go():
        return [e async for e in router.stream(MSGS)]

    events = asyncio.run(go())
    assert [e["type"] for e in events] == ["provider", "token", "token", "usage"]
    assert events[-1] == {"type": "usage", "name": "gemini", "prompt_tokens": 10, "completion_tokens": 2}
