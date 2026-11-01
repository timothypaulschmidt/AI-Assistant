"""The tool-calling loop.

One chat request becomes a short conversation between the model and the tools:

    ask the model  ->  it answers with text, or asks for tools to be run
    run the tools  ->  add their results to the conversation
    ask again      ->  repeat until it answers in text, or the round cap is hit

The model never runs anything itself. It only *asks*; this code decides to run
the request, through the MCP client. That is also why tool results are dangerous:
whatever text a tool returns is read by the model as part of its context.
"""
from __future__ import annotations

import json
from typing import AsyncIterator

from .llm.providers import Messages
from .mcp_client import McpTools

# How much of a tool result is shown in the browser. The model always gets the
# full (capped) result; this only keeps the page readable.
UI_PREVIEW_CHARS = 1500


def _preview(text: str) -> str:
    if len(text) <= UI_PREVIEW_CHARS:
        return text
    return text[:UI_PREVIEW_CHARS] + f"\n... [{len(text) - UI_PREVIEW_CHARS} more characters not shown]"


def _assistant_message(text: str, calls: list[dict]) -> dict:
    entries = []
    for c in calls:
        entry = {
            "id": c["id"],
            "type": "function",
            "function": {"name": c["name"], "arguments": c["arguments"]},
        }
        if c.get("extra"):
            entry["extra_content"] = c["extra"]  # must go back unchanged (Gemini)
        entries.append(entry)
    return {"role": "assistant", "content": text, "tool_calls": entries}


async def run_chat(
    router,
    messages: Messages,
    mode: str,
    model: str | None,
    tools: McpTools | None,
    max_rounds: int = 5,
) -> AsyncIterator[dict]:
    """Yield the events the browser shows: provider, token, usage, tool_call,
    tool_result and notice."""
    if tools is None:
        async for event in router.stream(messages, mode, model):
            yield event
        return

    messages = list(messages)  # we add to it; leave the caller's list alone
    offered = tools.openai_tools()

    for _ in range(max_rounds):
        text = ""
        calls: list[dict] = []
        async for event in router.stream(messages, mode, model, offered):
            if event["type"] == "token":
                text += event["text"]
            elif event["type"] == "tool_call":
                calls.append(event)
                try:
                    shown = json.loads(event["arguments"])
                except ValueError:
                    shown = event["arguments"]
                yield {"type": "tool_call", "id": event["id"], "name": event["name"], "arguments": shown}
                continue
            yield event
        if not calls:
            return

        messages.append(_assistant_message(text, calls))
        for call in calls:
            result = await tools.call(call["name"], call["arguments"])
            yield {
                "type": "tool_result",
                "id": call["id"],
                "name": call["name"],
                "content": _preview(result.text),
                "is_error": result.is_error,
            }
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": result.text})

    # Round cap reached while the model still wanted tools: make it answer with
    # what it has, without offering tools again.
    yield {"type": "notice", "message": f"Stopped after {max_rounds} rounds of tool calls."}
    async for event in router.stream(messages, mode, model):
        yield event
