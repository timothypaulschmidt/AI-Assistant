"""Talks to an MCP server: connects, does the handshake, lists its tools.

The tool-calling loop comes later. This module is the first step, so you can see
what the server offers before a model is ever involved.

`target` is normally the server's URL (Streamable HTTP, e.g. http://localhost:8000/mcp).
Tests pass an in-process MCPServer instead, which the same Client accepts.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from mcp import Client


class McpError(Exception):
    """The MCP server could not be reached or did not answer properly."""


@dataclass(frozen=True)
class ToolInfo:
    name: str
    description: str
    input_schema: dict[str, Any]


async def list_tools(target: Any, timeout: float = 5.0) -> list[ToolInfo]:
    """Connect, ask the server what tools it has, disconnect."""

    async def _go() -> list[ToolInfo]:
        async with Client(target) as client:
            result = await client.list_tools()
            return [
                ToolInfo(t.name, t.description or "", dict(t.input_schema or {}))
                for t in result.tools
            ]

    try:
        return await asyncio.wait_for(_go(), timeout)
    except asyncio.TimeoutError as exc:
        raise McpError(f"MCP server did not answer within {timeout:g}s") from exc
    except Exception as exc:  # connection refused, bad URL, protocol error...
        raise McpError(f"{type(exc).__name__}: {exc}") from exc
