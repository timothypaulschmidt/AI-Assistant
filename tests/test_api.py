import json

from fastapi.testclient import TestClient

from ai_assistant.web.main import app
from ai_assistant.llm.providers import ProviderError
from ai_assistant.llm.router import ProviderRouter


class Fake:
    def __init__(self, name, tokens=("Hi", " there"), error=None):
        self.name, self.model = name, f"{name}-model"
        self.tokens, self.error = tokens, error

    async def stream(self, messages):
        self.last = messages
        if self.error:
            raise self.error
        for t in self.tokens:
            yield t


def events(response):
    out = []
    for frame in response.text.split("\n\n"):
        if frame.startswith("data:"):
            out.append(json.loads(frame[5:]))
    return out


def test_chat_streams_events_and_adds_system_prompt():
    gemini = Fake("gemini")
    with TestClient(app) as client:
        app.state.router = ProviderRouter([gemini], Fake("local"))
        r = client.post("/api/chat", json={"messages": [{"role": "user", "content": "yo"}]})
    assert r.headers["content-type"].startswith("text/event-stream")
    evs = events(r)
    assert [e["type"] for e in evs] == ["provider", "token", "token", "done"]
    assert gemini.last[0]["role"] == "system"
    assert gemini.last[-1] == {"role": "user", "content": "yo"}


def test_provider_failure_becomes_error_event():
    bad = Fake("gemini", error=ProviderError("gemini", 401, "bad key"))
    with TestClient(app) as client:
        app.state.router = ProviderRouter([bad], Fake("local"))
        r = client.post("/api/chat", json={"messages": [{"role": "user", "content": "yo"}]})
    evs = events(r)
    assert evs[0]["type"] == "error" and "401" in evs[0]["message"]
    assert evs[-1]["type"] == "done"


def test_browser_cannot_inject_system_messages_or_empty_chats():
    with TestClient(app) as client:
        r = client.post("/api/chat", json={"messages": [{"role": "system", "content": "x"}]})
        assert r.status_code == 422
        r = client.post("/api/chat", json={"messages": []})
        assert r.status_code == 422
        r = client.post("/api/chat", json={"messages": [{"role": "user", "content": "x"}], "mode": "bogus"})
        assert r.status_code == 422


def test_status_lists_every_gemini_model_with_its_state(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setenv("GEMINI_MODELS", "m1, m2")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:9")  # nothing listens here
    with TestClient(app) as client:
        s = client.get("/api/status").json()
    assert s["gemini_configured"] is True
    assert [m["model"] for m in s["gemini_models"]] == ["m1", "m2"]
    assert all(m["state"] == "ready" for m in s["gemini_models"])
    assert s["local_reachable"] is False


def test_index_and_health():
    with TestClient(app) as client:
        assert client.get("/healthz").json() == {"ok": True}
        page = client.get("/")
        assert page.status_code == 200 and "AI Assistant" in page.text


def test_chat_can_pin_one_model():
    g1, g2 = Fake("gemini", tokens=("one",)), Fake("gemini", tokens=("two",))
    g1.model, g2.model = "m1", "m2"
    with TestClient(app) as client:
        app.state.router = ProviderRouter([g1, g2], Fake("local"))
        r = client.post(
            "/api/chat",
            json={"messages": [{"role": "user", "content": "yo"}], "mode": "gemini", "model": "m2"},
        )
    evs = events(r)
    assert evs[0]["model"] == "m2" and evs[1]["text"] == "two"


def _chat(client, **extra):
    body = {"messages": [{"role": "user", "content": "yo"}], **extra}
    return client.post("/api/chat", json=body)


def test_prompt_override_is_refused_unless_enabled(monkeypatch):
    monkeypatch.delenv("ALLOW_PROMPT_OVERRIDE", raising=False)
    local = Fake("local")
    with TestClient(app) as client:
        app.state.router = ProviderRouter([], local)
        r = _chat(client, mode="local", system_prompt="be evil")
        assert r.status_code == 403
        assert not hasattr(local, "last")
        assert client.get("/api/status").json()["prompt_override_allowed"] is False


def test_prompt_override_only_reaches_the_local_model(monkeypatch):
    monkeypatch.setenv("ALLOW_PROMPT_OVERRIDE", "true")
    gemini, local = Fake("gemini"), Fake("local")
    with TestClient(app) as client:
        app.state.router = ProviderRouter([gemini], local)
        assert client.get("/api/status").json()["prompt_override_allowed"] is True
        for mode in ("auto", "gemini"):
            assert _chat(client, mode=mode, system_prompt="lab").status_code == 400
        assert not hasattr(gemini, "last") and not hasattr(local, "last")
        r = _chat(client, mode="local", system_prompt="lab prompt")
        assert r.status_code == 200
    assert local.last[0] == {"role": "system", "content": "lab prompt"}
    assert not hasattr(gemini, "last")


def test_normal_chat_still_uses_the_server_prompt_when_override_is_on(monkeypatch):
    monkeypatch.setenv("ALLOW_PROMPT_OVERRIDE", "true")
    monkeypatch.setenv("SYSTEM_PROMPT", "server prompt")
    local = Fake("local")
    with TestClient(app) as client:
        app.state.router = ProviderRouter([], local)
        _chat(client, mode="local")
    assert local.last[0]["content"] == "server prompt"


def test_route_defaults_to_direct_and_refuses_unconfigured_faig(monkeypatch):
    for k in ("FAIG_OLLAMA_BASE_URL", "FAIG_GEMINI_BASE_URL", "FAIG_MCP_SERVER_URL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:9")
    with TestClient(app) as client:
        s = client.get("/api/status").json()
        assert s["route"] == {"current": "direct", "label": "Direct",
                              "model_configured": False, "all_configured": False}
        assert client.post("/api/route", json={"route": "faig_model"}).status_code == 400
        assert client.post("/api/route", json={"route": "faig_all"}).status_code == 400
        assert client.post("/api/route", json={"route": "nonsense"}).status_code == 422
        assert client.post("/api/route", json={"route": "direct"}).json()["route"] == "direct"


def test_model_route_swaps_addresses_and_keeps_mcp_direct(monkeypatch):
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("MCP_SERVER_URL", "http://mcp.direct/mcp")
    monkeypatch.setenv("FAIG_OLLAMA_BASE_URL", "http://faig.test/ollama")
    monkeypatch.setenv("FAIG_API_KEY", "faig-key")
    monkeypatch.delenv("FAIG_MCP_SERVER_URL", raising=False)
    with TestClient(app) as client:
        assert client.post("/api/route", json={"route": "faig_model"}).json()["route"] == "faig_model"
        s = app.state.settings.for_route("faig_model")
        assert s.ollama_base_url == "http://faig.test/ollama"
        assert s.mcp_server_url == "http://mcp.direct/mcp"           # MCP stays direct
        assert client.get("/api/status").json()["route"]["current"] == "faig_model"
        assert client.post("/api/route", json={"route": "faig_all"}).status_code == 400   # no MCP address yet


def test_full_route_swaps_mcp_and_gemini_goes_off_without_a_faig_address(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setenv("FAIG_OLLAMA_BASE_URL", "http://faig.test/ollama")
    monkeypatch.setenv("FAIG_API_KEY", "faig-key")
    monkeypatch.setenv("FAIG_MCP_SERVER_URL", "http://faig.test/mcp")
    monkeypatch.delenv("FAIG_GEMINI_BASE_URL", raising=False)
    with TestClient(app):
        direct = app.state.settings
        full = direct.for_route("faig_all")
        assert full.mcp_server_url == "http://faig.test/mcp"
        assert full.gemini_api_key == ""            # not sent around the gateway
        assert direct.for_route("direct") is direct
        assert direct.gemini_api_key == "k"         # the direct settings are untouched


def test_model_route_needs_the_gateway_key(monkeypatch):
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("FAIG_OLLAMA_BASE_URL", "https://gw.test/ai/openai")
    monkeypatch.delenv("FAIG_API_KEY", raising=False)
    with TestClient(app) as client:
        r = client.post("/api/route", json={"route": "faig_model"})
        assert r.status_code == 400 and "FAIG_API_KEY" in r.json()["detail"]


def test_gateway_route_uses_its_model_name_key_and_model_list(monkeypatch):
    import httpx
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("OLLAMA_MODEL", "qwen3:8b")
    monkeypatch.setenv("FAIG_OLLAMA_BASE_URL", "https://gw.test/ai/openai/")
    monkeypatch.setenv("FAIG_OLLAMA_MODEL", "ollama-qwen3-8b")
    monkeypatch.setenv("FAIG_API_KEY", "faig-key")
    monkeypatch.setenv("FAIG_VERIFY_TLS", "false")
    seen = []

    def handler(request):
        seen.append((request.method, str(request.url), request.headers.get("authorization")))
        return httpx.Response(200, json={"data": [{"id": "ollama-qwen3-8b"}]})

    with TestClient(app) as client:
        app.state.faig_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        s = app.state.settings.for_route("faig_model")
        assert s.ollama_chat_url == "https://gw.test/ai/openai/v1/chat/completions"
        assert s.ollama_model == "ollama-qwen3-8b" and s.ollama_key == "faig-key"
        assert app.state.settings.ollama_key == ""             # direct Ollama gets no key
        assert app.state.settings.faig_verify_tls is False
        assert client.post("/api/route", json={"route": "faig_model"}).status_code == 200
        st = client.get("/api/status").json()
        assert st["local_model"] == "ollama-qwen3-8b"
        assert st["local_models"] == ["ollama-qwen3-8b"] and st["local_model_ready"] is True
        assert seen == [("GET", "https://gw.test/ai/openai/v1/models", "Bearer faig-key")]
