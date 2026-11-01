import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from mcp.server.mcpserver import MCPServer

import ai_assistant.web.main as web
from ai_assistant.agent import run_chat
from ai_assistant.llm.providers import OpenAICompatProvider, ToolCall
from ai_assistant.llm.router import ProviderRouter
from ai_assistant.mcp_client import clean_schema, load_tools


# ---- helpers ------------------------------------------------------------------

def sse(*chunks):
    body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
    return httpx.Response(200, content=body.encode(), headers={"content-type": "text/event-stream"})


def provider_for(response):
    seen = {}

    def handler(request):
        seen["payload"] = json.loads(request.content)
        return response

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OpenAICompatProvider("test", client, "http://x/chat/completions", "m"), seen


async def collect(agen):
    return [item async for item in agen]


def math_server() -> MCPServer:
    server = MCPServer(name="t")

    @server.tool()
    def add(a: int, b: int) -> int:
        """Add two numbers."""
        return a + b

    return server


# ---- provider: reading tool calls out of the stream ----------------------------

def test_tool_call_sent_whole_in_one_chunk_without_index():
    # The shape Gemini / Ollama use: one chunk, complete call, no "index".
    chunk = {"choices": [{"delta": {"tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "add", "arguments": '{"a": 1, "b": 2}'},
         "extra_content": {"google": {"thought_signature": "sig"}}}
    ]}}]}
    prov, seen = provider_for(sse(chunk))
    items = asyncio.run(collect(prov.stream([{"role": "user", "content": "hi"}], tools=[{"type": "function"}])))
    assert items == [ToolCall("c1", "add", '{"a": 1, "b": 2}', {"google": {"thought_signature": "sig"}})]
    assert seen["payload"]["tools"] == [{"type": "function"}]


def test_tool_call_arguments_streamed_in_pieces_and_two_calls():
    frag = lambda i, **f: {"choices": [{"delta": {"tool_calls": [{"index": i, **f}]}}]}
    prov, _ = provider_for(sse(
        frag(0, id="a", function={"name": "add", "arguments": '{"a":'}),
        frag(1, id="b", function={"name": "add", "arguments": '{"a": 9,'}),
        frag(0, function={"arguments": ' 1, "b": 2}'}),
        frag(1, function={"arguments": ' "b": 9}'}),
    ))
    items = asyncio.run(collect(prov.stream([], tools=[{}])))
    assert [(c.id, c.arguments) for c in items] == [("a", '{"a": 1, "b": 2}'), ("b", '{"a": 9, "b": 9}')]


def test_no_arguments_becomes_empty_object_and_text_still_streams():
    prov, seen = provider_for(sse(
        {"choices": [{"delta": {"content": "Hel"}}]},
        {"choices": [{"delta": {"content": "lo"}}]},
        {"choices": [{"delta": {"tool_calls": [{"id": "z", "function": {"name": "ping"}}]}}]},
    ))
    items = asyncio.run(collect(prov.stream([])))
    assert items[:2] == ["Hel", "lo"] and items[2] == ToolCall("z", "ping", "{}", None)
    assert "tools" not in seen["payload"]  # not offered unless asked


# ---- schema clean-up -----------------------------------------------------------

def test_clean_schema_drops_unsupported_keys_but_keeps_parameter_names():
    schema = {
        "type": "object", "title": "T", "additionalProperties": False,
        "properties": {
            "title": {"type": "string", "title": "Title"},
            "status": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None, "description": "d"},
            "limit": {"type": "integer", "default": 5},
        },
        "required": ["title"],
    }
    out = clean_schema(schema)
    assert out["properties"]["title"] == {"type": "string"}
    assert out["properties"]["status"] == {"type": "string", "description": "d"}
    assert out["properties"]["limit"] == {"type": "integer"}
    assert "additionalProperties" not in out and "title" not in out and out["required"] == ["title"]


def test_openai_tools_from_a_real_server():
    tools = asyncio.run(load_tools(math_server()))
    [t] = tools.openai_tools()
    assert t["type"] == "function" and t["function"]["name"] == "add"
    assert set(t["function"]["parameters"]["properties"]) == {"a", "b"}


# ---- the loop ------------------------------------------------------------------

class ScriptedRouter:
    """Stands in for ProviderRouter: each call to stream() plays the next script."""

    def __init__(self, *scripts):
        self.scripts, self.calls = list(scripts), []

    async def stream(self, messages, mode="auto", model=None, tools=None):
        self.calls.append({"messages": [dict(m) for m in messages], "tools": tools})
        for ev in self.scripts.pop(0):
            yield ev


def call_event(id, name, arguments, extra=None):
    return {"type": "tool_call", "id": id, "name": name, "arguments": arguments, "extra": extra}


def test_loop_runs_the_tool_and_feeds_the_result_back():
    tools = asyncio.run(load_tools(math_server()))
    router = ScriptedRouter(
        [{"type": "provider", "name": "g", "model": "m"}, call_event("c1", "add", '{"a": 2, "b": 3}', {"sig": 1})],
        [{"type": "token", "text": "The answer is 5."}],
    )
    events = asyncio.run(collect(run_chat(router, [{"role": "user", "content": "2+3?"}], "auto", None, tools)))
    kinds = [e["type"] for e in events]
    assert kinds == ["provider", "tool_call", "tool_result", "token"]
    assert events[1]["arguments"] == {"a": 2, "b": 3}
    assert events[2]["content"] == "5" and not events[2]["is_error"]
    assert "extra" not in events[1]  # provider-private data never reaches the browser

    second = router.calls[1]["messages"]
    assistant, tool = second[-2], second[-1]
    assert assistant["tool_calls"][0]["extra_content"] == {"sig": 1}
    assert assistant["tool_calls"][0]["function"]["name"] == "add"
    assert tool == {"role": "tool", "tool_call_id": "c1", "content": "5"}
    assert router.calls[0]["tools"]  # tools offered on every round


def test_bad_arguments_and_unknown_tools_become_error_results_not_crashes():
    tools = asyncio.run(load_tools(math_server()))
    router = ScriptedRouter(
        [call_event("c1", "add", "{not json"), call_event("c2", "drop_everything", "{}")],
        [{"type": "token", "text": "sorry"}],
    )
    events = asyncio.run(collect(run_chat(router, [], "auto", None, tools)))
    results = [e for e in events if e["type"] == "tool_result"]
    assert [r["is_error"] for r in results] == [True, True]
    assert "Invalid JSON" in results[0]["content"] and "Unknown tool" in results[1]["content"]


def test_round_cap_stops_the_loop_and_asks_for_a_final_answer_without_tools():
    tools = asyncio.run(load_tools(math_server()))
    again = [call_event("c", "add", '{"a": 1, "b": 1}')]
    router = ScriptedRouter(again, again, [{"type": "token", "text": "best guess"}])
    events = asyncio.run(collect(run_chat(router, [], "auto", None, tools, max_rounds=2)))
    assert any(e["type"] == "notice" for e in events)
    assert events[-1] == {"type": "token", "text": "best guess"}
    assert router.calls[-1]["tools"] is None and len(router.calls) == 3


def test_without_tools_the_loop_is_a_plain_pass_through():
    router = ScriptedRouter([{"type": "token", "text": "hi"}])
    events = asyncio.run(collect(run_chat(router, [], "auto", None, None)))
    assert events == [{"type": "token", "text": "hi"}] and router.calls[0]["tools"] is None


# ---- router --------------------------------------------------------------------

def test_router_turns_tool_calls_into_events_and_passes_tools_down():
    class P:
        name, model = "g", "m"

        async def stream(self, messages, tools=None):
            self.tools = tools
            yield ToolCall("c1", "add", "{}", None)

    p = P()
    events = asyncio.run(collect(ProviderRouter([p], P()).stream([], "gemini", None, [{"t": 1}])))
    assert [e["type"] for e in events] == ["provider", "tool_call"] and p.tools == [{"t": 1}]


# ---- the HTTP endpoint ----------------------------------------------------------

def parse(response):
    return [json.loads(f[5:]) for f in response.text.split("\n\n") if f.startswith("data:")]


def test_chat_refuses_tools_when_mcp_is_off(monkeypatch):
    monkeypatch.delenv("MCP_SERVER_URL", raising=False)
    with TestClient(web.app) as client:
        r = client.post("/api/chat", json={"messages": [{"role": "user", "content": "x"}], "use_tools": True})
    assert r.status_code == 400 and "MCP" in r.json()["detail"]


def test_chat_with_tools_end_to_end(monkeypatch):
    server = math_server()

    async def fake_load(url):
        return await load_tools(server)

    monkeypatch.setenv("MCP_SERVER_URL", "http://example.invalid/mcp")
    monkeypatch.setattr(web, "load_tools", fake_load)
    with TestClient(web.app) as client:
        web.app.state.router = ScriptedRouter(
            [call_event("c1", "add", '{"a": 20, "b": 22}')], [{"type": "token", "text": "42"}]
        )
        r = client.post("/api/chat", json={"messages": [{"role": "user", "content": "x"}], "use_tools": True})
    evs = parse(r)
    assert [e["type"] for e in evs] == ["tool_call", "tool_result", "token", "done"]
    assert evs[1]["content"] == "42"
