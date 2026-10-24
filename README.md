# AI Assistant

A browser chat assistant backed by Google Gemini (free AI Studio key) that fails over
to a local Ollama model when Gemini is rate limited or unreachable.

```
Browser  --HTTP + SSE-->  FastAPI backend  --> Gemini  (primary)
                          (provider router)--> Ollama  (fallback / manual)
```

## Quick Start
Get started quickly! See the [Initial Deployment Guide](docs/INITIAL_DEPLOYMENT.md).

## Documentation
- 📋 [Initial Deployment Checklist](docs/INITIAL_DEPLOYMENT.md) - Complete setup guide
- 🔄 [Update Cycle](docs/UPDATE_CYCLE.md) - How to update and redeploy

## Run it on Windows (no Docker)

1. Get a free key: https://aistudio.google.com/app/apikey
2. In PowerShell, from this folder:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
pip install -e .

$env:GEMINI_API_KEY = "paste-your-key-here"
# Only needed once Ollama exists somewhere (VM IP shown as an example):
$env:OLLAMA_BASE_URL = "http://192.168.1.50:11434"

uvicorn ai_assistant.web.main:app --reload --port 8000
```

Same thing in `cmd.exe` (note `set` instead of `$env:`, and no quotes):

```bat
.venv\Scripts\activate.bat
pip install -r requirements-dev.txt
pip install -e .
set GEMINI_API_KEY=paste-your-key-here
set OLLAMA_BASE_URL=http://192.168.1.50:11434
uvicorn ai_assistant.web.main:app --reload --port 8000
```

3. Open http://localhost:8000. The status line at the top shows whether Gemini
   and the local model are ready. Without Ollama running, "Gemini only" works
   and the local status shows unreachable.

`pip install -e .` makes the `ai_assistant` package importable from the `src/` folder
(an editable install: code changes take effect immediately). It is a one-time step per
virtual environment. Run the tests with `pytest`.

## Run it in Docker

```powershell
docker build -t ai-assistant:dev .
docker run --rm -p 8000:8000 `
  -e GEMINI_API_KEY=your-key `
  -e OLLAMA_BASE_URL=http://host.docker.internal:11434 `
  ai-assistant:dev
```

## Push to Docker Hub
1. Tag: `docker tag ai-assistant:dev timothypaulschmidt/ai-assistant:dev`
2. Push: `docker push timothypaulschmidt/ai-assistant:dev`

## Deploy to k3s

Kubernetes manifests for the assistant and a CPU-only Ollama are in `deploy/k8s`.
Step-by-step instructions (build and push the image, create the Secret, apply,
pull a model, update and roll back) are in [deploy/README.md](deploy/README.md).

## Configuration (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `GEMINI_API_KEY` | (empty) | Without it, only the local model is used |
| `GEMINI_MODELS` | `gemini-3.6-flash,gemini-flash-latest,gemini-4-argon,gemini-3.8-flash,gemini-3.5-flash-lite,gemini-3.1-pro-preview` | Comma-separated, tried in this order, then the local model |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Where Ollama listens (no `/v1`) |
| `OLLAMA_MODEL` | `llama3.2:3b` | Must be pulled: `ollama pull llama3.2:3b` |
| `GEMINI_COOLDOWN_SECONDS` | `300` | Skip a Gemini model this long after it returns 429 |
| `GEMINI_UNAVAILABLE_SECONDS` | `3600` | Skip a Gemini model this long after it returns 404 |
| `GEMINI_ERROR_COOLDOWN_SECONDS` | `60` | Skip a model after a 5xx, or all Gemini models after a connection failure |
| `REQUEST_TIMEOUT` | `180` | Read timeout in seconds (CPU models are slow) |
| `SYSTEM_PROMPT` | `You are a helpful assistant.` | Prepended to every chat |

## How failover works

Order: each Gemini model in `GEMINI_MODELS`, then the local model last.

- **Auto**: the first model that answers wins. A failing model is skipped for a
  while, so a broken one is never retried on every message:

  | Error from Gemini | Model is skipped for | Setting |
  |---|---|---|
  | 429 quota used up | 5 minutes | `GEMINI_COOLDOWN_SECONDS` |
  | 404 model unknown or retired for your key | 1 hour | `GEMINI_UNAVAILABLE_SECONDS` |
  | 5xx server error | 1 minute | `GEMINI_ERROR_COOLDOWN_SECONDS` |
  | Connection failure | 1 minute, all Gemini models | `GEMINI_ERROR_COOLDOWN_SECONDS` |

  When a block expires, the next message tries that model once; if it fails
  again it is blocked again. Only models that are actually being tried cost
  time, and the local model answers if every Gemini model is blocked.
- **Dropdown**: Auto, Gemini only (walks the model list, never uses local),
  one specific Gemini model (that model only, errors shown, no skipping), or
  Local only. Picking a specific model ignores its cooldown, so you can retry by hand.
- Bad API keys and bad requests (other 4xx) are shown as errors, not hidden by
  a fallback, so a mistyped key doesn't silently turn into local-only.
- Failover happens only before the first token. If a provider dies
  mid-answer, the error is shown rather than splicing two models together.
- Each reply is labelled with the model that answered ("(fallback)" for local).
  Hover the Gemini status at the top of the page for each model's state.

## API (for other clients, such as a phone app)

The chat page is just one client of this HTTP API. Anything that can make an
HTTP request and read a streaming response can use it.

`POST /api/chat` with JSON:

```json
{
  "messages": [{"role": "user", "content": "Hello"}],
  "mode": "auto",
  "model": null
}
```

- `messages`: the whole conversation so far, oldest first. Roles are `user` and
  `assistant` only; the server adds the system prompt.
- `mode`: `auto`, `gemini` or `local`.
- `model`: with `mode` set to `gemini`, pins the request to one model from
  `GEMINI_MODELS`. Otherwise `null`.

The reply is a stream of Server-Sent Events (`text/event-stream`). Each event is
a line `data: {json}` followed by a blank line:

| `type` | Fields | Meaning |
|---|---|---|
| `provider` | `name`, `model`, `fallback` | Which model is answering; sent once, first |
| `token` | `text` | A piece of the reply; append them in order |
| `error` | `message` | Something failed; show it |
| `done` | | The stream is finished (always last) |

Other endpoints: `GET /api/status` (model availability) and `GET /healthz`.

There is no login yet. That is fine on your home network. Before reaching it
from outside (a phone away from home), put it behind a VPN such as Tailscale or
add an access token, and use HTTPS.

## Layout

```
src/ai_assistant/
  config.py            settings from env vars (shared)
  llm/
    providers.py       one OpenAI-compatible streaming client (Gemini and Ollama)
    router.py          model order, failover, cooldowns
  web/
    main.py            FastAPI: /, /api/chat (SSE), /api/status, /healthz
    static/index.html  the chat page
tests/                 router, provider and API tests
deploy/k8s/            Kubernetes manifests (see deploy/README.md)
```

`llm/` is shared code: the chat page uses it now, and the future agent, RAG and
MCP pieces will too. They become sibling subpackages (`ai_assistant/agent`,
`ai_assistant/rag`, `ai_assistant/mcp`) and call the same router.
