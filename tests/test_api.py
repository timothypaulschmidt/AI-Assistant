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
