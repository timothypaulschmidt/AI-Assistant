import asyncio

import pytest
from fastapi.testclient import TestClient
from mcp.server.mcpserver import MCPServer

import ai_assistant.web.main as web
from ai_assistant.mcp_client import McpError, ToolInfo, list_tools


def make_server() -> MCPServer:
    server = MCPServer(name="test")

    @server.tool()
    def add(a: int, b: int) -> int:
        """Add two numbers."""
        return a + b

    return server


def test_list_tools_returns_name_description_and_schema():
    tools = asyncio.run(list_tools(make_server()))
    assert [t.name for t in tools] == ["add"]
    assert tools[0].description == "Add two numbers."
    assert set(tools[0].input_schema["properties"]) == {"a", "b"}


def test_unreachable_server_raises_mcp_error():
    with pytest.raises(McpError):
        asyncio.run(list_tools("http://127.0.0.1:9/mcp", timeout=2.0))


def test_status_reports_mcp_off_by_default(monkeypatch):
    monkeypatch.delenv("MCP_SERVER_URL", raising=False)
    with TestClient(web.app) as client:
        s = client.get("/api/status").json()
    assert s["mcp_configured"] is False and "mcp_tools" not in s


def test_status_lists_tools_when_mcp_is_on(monkeypatch):
    async def fake(url, timeout=5.0):
        return [ToolInfo("search_customers", "", {}), ToolInfo("get_ticket", "", {})]

    monkeypatch.setenv("MCP_SERVER_URL", "http://example.invalid/mcp")
    monkeypatch.setattr(web, "list_tools", fake)
    with TestClient(web.app) as client:
        s = client.get("/api/status").json()
    assert s["mcp_configured"] and s["mcp_reachable"]
    assert s["mcp_tools"] == ["search_customers", "get_ticket"]


def test_status_survives_an_unreachable_mcp_server(monkeypatch):
    async def boom(url, timeout=5.0):
        raise McpError("ConnectError: refused")

    monkeypatch.setenv("MCP_SERVER_URL", "http://example.invalid/mcp")
    monkeypatch.setattr(web, "list_tools", boom)
    with TestClient(web.app) as client:
        s = client.get("/api/status").json()
    assert s["mcp_reachable"] is False and "refused" in s["mcp_error"]
