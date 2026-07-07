# axel.rem — Multi-Agent Semantic Memory Engine

A lightweight, self-contained memory system for AI agents. Stores agent experiences as semantic facts, consolidates them nightly, and injects relevant context before each task.

## Architecture

```
write seed (extracted=FALSE)
        │
        ▼
  prompt-chill daemon          ← runs continuously
  LLM entity/fact extraction
  sentence-transformers embed
  extracted=TRUE + pgvector
        │
        ├──► Redis hot layer (48h, sorted by strength + recency)
        │
        ▼
  nightly dream cycle          ← runs at REM_DREAM_HOUR UTC
  cluster memories by entity
  LLM consolidation
  strength decay (×0.98/day)
  long-term promotion
        │
        ▼
  build_context_for_task()     ← called before each agent task
  Redis + pgvector combined
  entity dedup, strength-weighted
  [MEMORY CONTEXT] block injected into system prompt
```

**Storage layers:**
- **Redis** — hot memories, 48h TTL, sorted by `time + strength`
- **PostgreSQL + pgvector** — persistent store, 384-dim cosine search, time-weighted ranking
- **Long-term** — dream-promoted facts, higher strength, survive daily decay

## Requirements

- Python 3.11+
- PostgreSQL 14+ with [pgvector](https://github.com/pgvector/pgvector)
- Redis 5+
- An OpenAI-compatible LLM endpoint (Ollama, LiteLLM, OpenAI, Groq, ...)

## Setup

```bash
# 1. Install
pip install -r requirements.txt

# 2. Configure
cp .env.example .env
# edit .env — fill in DB, Redis, LLM settings

# 3. Initialize database
psql -U rem -d rem -f migrations/001_axel_rem_memory.sql

# 4. Run
python main.py           # all components
python main.py chill     # prompt-chill daemon only
python main.py dream     # dream scheduler only
```

## Writing memories

```python
from axel_rem.db import seed_write  # or use the CLI helper below
```

Or use the included CLI script:

```bash
# write a seed
python -m axel_rem.write "Redis sentinel értékek: -1=null, 0=false, 1=true" \
  --agent MYAGENT --entity "redis conventions" --strength 2.0

# search
python -m axel_rem.search "redis" --agent MYAGENT
python -m axel_rem.search --recent --agent MYAGENT
```

## Injecting context before a task

```python
from axel_rem.search import build_context_for_task

context = build_context_for_task(agent="MYAGENT", task_description="deploy nginx service")
system_prompt = context + your_system_prompt
```

## Configuration

All settings via environment variables (or `.env` file):

| Variable | Default | Description |
|---|---|---|
| `REM_DB_HOST` | `localhost` | PostgreSQL host |
| `REM_DB_NAME` | `rem` | Database name |
| `REM_DB_USER` | `rem` | DB user |
| `REM_DB_PASS` | | DB password |
| `REM_REDIS_HOST` | `localhost` | Redis host |
| `REM_LLM_URL` | `http://localhost:11434` | OpenAI-compatible LLM base URL |
| `REM_LLM_MODEL` | `llama3.1` | Model name |
| `REM_LLM_API_KEY` | | API key (leave empty for Ollama) |
| `REM_DREAM_HOUR` | `3` | UTC hour for nightly consolidation |
| `REM_AGENTS` | `AXEL` | Comma-separated agent names |

## Embedding model

Uses `sentence-transformers/all-MiniLM-L6-v2` (384 dimensions, ~80MB, CPU-friendly). Downloaded automatically on first run.

## Optional: task and message integration

`dream.py` can optionally process `axel_task` and `axel_message` tables if they exist in your DB. These are Axel Group specific — the core pipeline works without them.

## License

MIT
