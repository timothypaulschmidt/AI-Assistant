import asyncio
import json

import httpx
import pytest

from ai_assistant.llm.providers import OpenAICompatProvider, ProviderError

MSGS = [{"role": "user", "content": "hi"}]


def sse_body(*texts):
    lines = []
    for t in texts:
        lines.append("data: " + json.dumps({"choices": [{"delta": {"content": t}}]}))
        lines.append("")
    # role-only chunk, empty choices, and a keep-alive comment must be ignored
    lines.insert(0, "data: " + json.dumps({"choices": [{"delta": {"role": "assistant"}}]}))
    lines.insert(1, "")
    lines.append("data: " + json.dumps({"choices": []}))
    lines.append("")
    lines.append(": keep-alive")
    lines.append("")
    lines.append("data: [DONE]")
    lines.append("")
    return "\n".join(lines).encode()


def provider(handler, api_key="secret"):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OpenAICompatProvider("gemini", client, "http://x/chat/completions", "m1", api_key)


def collect(p):
    async def go():
        return [t async for t in p.stream(MSGS)]

    return asyncio.run(go())


def test_streams_text_and_sends_expected_request():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, content=sse_body("Hel", "lo"))

    assert collect(provider(handler)) == ["Hel", "lo"]
    assert seen["auth"] == "Bearer secret"
    assert seen["body"] == {
        "model": "m1",
        "messages": MSGS,
        "stream": True,
        "stream_options": {"include_usage": True},
    }


def test_no_auth_header_when_no_key():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, content=sse_body("ok"))

    collect(provider(handler, api_key=""))
    assert seen["auth"] is None


def test_429_becomes_retryable_provider_error():
    p = provider(lambda r: httpx.Response(429, text="quota exhausted"))
    with pytest.raises(ProviderError) as info:
        collect(p)
    assert info.value.status == 429 and info.value.retryable
    assert "quota exhausted" in str(info.value)


def test_bad_key_is_not_retryable():
    p = provider(lambda r: httpx.Response(401, text="bad key"))
    with pytest.raises(ProviderError) as info:
        collect(p)
    assert not info.value.retryable


def test_connection_failure_is_status_zero_and_retryable():
    def handler(request):
        raise httpx.ConnectError("refused")

    with pytest.raises(ProviderError) as info:
        collect(provider(handler))
    assert info.value.status == 0 and info.value.retryable


def test_usage_chunk_is_reported_even_with_empty_choices():
    from ai_assistant.llm.providers import Usage

    body = sse_body("Hi") .replace(
        b"data: [DONE]",
        ("data: " + json.dumps({"choices": [], "usage": {"prompt_tokens": 7, "completion_tokens": 3}}) + "\n\ndata: [DONE]").encode(),
    )
    items = collect(provider(lambda r: httpx.Response(200, content=body)))
    assert items == ["Hi", Usage(7, 3)]


def test_server_rejecting_stream_options_is_retried_once_without_it():
    bodies = []

    def handler(request):
        payload = json.loads(request.content)
        bodies.append(payload)
        if "stream_options" in payload:
            return httpx.Response(400, text="Unknown field stream_options")
        return httpx.Response(200, content=sse_body("ok"))

    p = provider(handler)
    assert collect(p) == ["ok"]
    assert collect(p) == ["ok"]          # remembered: no second failed attempt
    assert [("stream_options" in b) for b in bodies] == [True, False, False]


def test_other_400_errors_are_not_retried():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(400, text="Please pass a valid API key")

    with pytest.raises(ProviderError):
        collect(provider(handler))
    assert len(calls) == 1
