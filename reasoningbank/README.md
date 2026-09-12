# ReasoningBank-Lite

A Dockerized HTTP sidecar that gives an existing AI agent system reusable
**reasoning memory**: it distills generalizable strategies from past task
successes and failures, retrieves the most relevant ones before each new
task, and injects them into the agent's context.

Based on the [ReasoningBank](https://github.com/google-research/reasoning-bank)
paper, deliberately simplified for low-effort PoC integration into an existing
docker-compose-deployed agent stack.

## What it does

- **Before a task** (`POST /v1/before_task`): embeds the task, cosine-searches
  the memory bank, returns a ready-to-inject context string.
- **After a task** (`POST /v1/after_task`): runs a merged judge+reflection LLM
  call, applies the generalizability gate, dedups, and stores at most one
  memory.
- Learns from **both successes and failures**.
- Stores **abstract reasoning strategies**, not raw trajectories.
- **Fail-open**: any error degrades to empty context / no storage — the host
  agent is never blocked.

## Quick start

```bash
# 1. Put your keys in .env (see .env.example)
cp .env.example .env  # then edit

# 2. Add the service to your existing docker-compose.yml (see below), then:
docker compose up -d reasoningbank

# 3. Check it's healthy
curl http://reasoningbank:8000/v1/health
```

## Add to your existing docker-compose

```yaml
services:
  reasoningbank:
    build: ./reasoningbank          # or: image: reasoningbank-lite:latest
    container_name: reasoningbank
    env_file: .env
    volumes:
      - reasoningbank_data:/data
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/v1/health').getcode()"]
      interval: 30s
      timeout: 5s
      retries: 3
    restart: unless-stopped

volumes:
  reasoningbank_data:
```

Your agent reaches the service at `http://reasoningbank:8000` over the shared
compose network — no host port needed.

## Use from your agent

```python
import httpx

RB_BASE = "http://reasoningbank:8000"

async def before_task(client: httpx.AsyncClient, task: str) -> str:
    try:
        r = await client.post(f"{RB_BASE}/v1/before_task",
                               json={"task": task}, timeout=5.0)
        r.raise_for_status()
        return r.json().get("context", "")
    except Exception:
        return ""  # fail-open

async def after_task(client, task, trace, result, success=None):
    try:
        r = await client.post(f"{RB_BASE}/v1/after_task",
                              json={"task": task, "trace": trace,
                                    "result": result, "success": success},
                              timeout=30.0)
        r.raise_for_status()
        return r.json()
    except Exception:
        return {"stored": False}  # fail-open

# In your agent loop:
async with httpx.AsyncClient() as client:
    context = await before_task(client, task)
    result = await agent.run(task, context={"reasoning_memory": context})
    await after_task(client, task, result.trace, result, result.success)
```

## API

| Method | Path | Purpose |
|---|---|---|
| POST | `/v1/before_task` | Retrieve memories, return injectable context string |
| POST | `/v1/after_task` | Reflect + gate + dedup + store (0-or-1 memory) |
| GET | `/v1/health` | Liveness (always 200) |

## Configuration

All via environment variables (see `.env`):

| Var | Default | Purpose |
|---|---|---|
| `RB_EMBEDDING_API_BASE` | — | OpenAI-compatible embedding endpoint |
| `RB_EMBEDDING_API_KEY` | — | Embedding API key |
| `RB_EMBEDDING_MODEL` | — | Embedding model name |
| `RB_LLM_API_BASE` | — | LLM chat-completions endpoint |
| `RB_LLM_API_KEY` | — | LLM API key |
| `RB_LLM_MODEL` | — | LLM model name |
| `RB_LLM_REASONING` | — | Reasoning effort (e.g. `high`) if the model supports it |
| `RB_RETRIEVAL_TOP_K` | `3` | Memories to retrieve |
| `RB_SIMILARITY_THRESHOLD` | `0.70` | Min cosine similarity to include a memory |
| `RB_DEDUP_THRESHOLD` | `0.95` | Skip storing if nearest existing memory is this similar |
| `RB_REFLECTION_TEMPERATURE` | `0.0` | LLM sampling temperature for reflection |
| `RB_DATA_DIR` | `/data` | SQLite database location (put on a named volume) |
| `RB_LOG_LEVEL` | `INFO` | Logging level |

## Security

**Never commit `.env`.** The `.env` file contains live API keys. Add it to
`.gitignore` and rotate any key that was shared in plaintext.

## Project structure

```
reasoningbank/
├── reasoning_bank/
│   ├── api.py          # FastAPI routes (3 endpoints)
│   ├── bank.py         # orchestration (before_task / after_task flows)
│   ├── reflector.py    # merged judge + extractor LLM call
│   ├── retriever.py    # embed + cosine search
│   ├── embedder.py     # external embedding API client
│   ├── store.py        # SQLite + NumPy vector store
│   ├── formatter.py    # memories → injectable context string
│   ├── models.py       # pydantic schemas
│   └── config.py       # env-var settings
├── Dockerfile
├── requirements.txt
├── .env                # gitignored — your keys
└── reasoningbank_lite_development_spec.md
```
