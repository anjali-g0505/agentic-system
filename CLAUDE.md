# CLAUDE.md

Operating instructions for building this application. Read this fully before writing any code. The user will supply only the problem statement, the components and features wanted, and the tools the agent should expose. Everything else is here.

---

## 0. OPERATING PROTOCOL — how to work with me

**This is a 60-minute timed assessment. I am being evaluated on the result and on my ability to justify every decision out loud.**

### Before any code

1. **State contradictions first.** If my instructions conflict with each other or with this file, list every conflict and the resolution you chose. One line each. Do not ask me to resolve them — decide, state it, move on.
2. **Then produce the phase plan in chat.** Not in a file. Phases, what each produces, minute budget. Short.
3. Then begin Phase 1 immediately. Do not wait for approval of the plan.

### During

- **Never ask my opinion on an architecture decision.** Decide. Pick the scalable, industry-standard option that fits the stack below. State what you chose and why in one line in the phase summary.
- **Stop at the end of every phase.** Give the summary (format below) and wait. I will say "go ahead."
- **No commentary between tool calls.** I am watching a clock.
- If something is genuinely blocking and cannot be decided — a missing file, an unreachable service — say so in one line and propose the workaround you are taking.

### Phase summary format — exactly this, every time

    PHASE N COMPLETE — <name>  [<elapsed> min]

    Built:
      - <file> — <one line>
    Decisions made:
      - <decision> — <one-line reason, panel-ready>
    Verified:
      - <command run> → <actual result>
    Next: Phase N+1 — <name>

The "Decisions made" lines are what I will say to the panel. Write them as justifications, not descriptions. "Hybrid RRF over dense+BM25 because dense mis-ranks exact circular numbers" — not "added hybrid retrieval."

### Non-negotiable

- **No fabricated output.** Never report a command you did not run or a test that did not pass. Paste real output.
- If unsure whether an import path or API is current, **say so** rather than guessing. A flagged gap costs a minute; wrong code costs twenty.
- Complete, runnable code. Real imports. No `...` placeholders, no "implement this yourself."

---

## 1. TIME BUDGET

**Code generation must be complete by minute 32. Hard stop at 35.** The remaining time is mine for deployment.

| Phase | Minutes | Produces |
|---|---|---|
| 0 — Contradictions + plan | 2 | Chat only, no files |
| 1 — Scaffold, config, schema | 5 | base.Dockerfile + requirements-base.txt (build `base_image:1.0` if missing), compose, Dockerfile, .env, init.sql, settings, /health |
| 2 — Ingestion | 6 | parse → chunk → embed → pgvector, run and verified |
| 3 — Retrieval + tools | 6 | hybrid retriever, tool functions, RBAC wrapper |
| 4 — Agent + API | 7 | LangGraph graph, FastAPI endpoints |
| 5 — Frontend | 6 | React UI, branded |
| 6 — Integrate + smoke test | 3 | End-to-end verified, start commands given |

**If behind schedule, cut in this order:** optional tools → UI polish → streaming → extra endpoints. **Never cut:** citations on answers, the confirmation gate on write actions, RBAC enforcement, the deployed-and-running requirement.

If a phase overruns its budget by more than 2 minutes, say so and compress the next one.

---

## 2. TECH STACK — fixed, do not substitute

| Layer | Choice | Notes |
|---|---|---|
| Orchestration | **LangGraph** — single agent, not multi-agent | |
| LLM | **Groq API** | `openai/gpt-oss-120b` generation · `openai/gpt-oss-20b` routing + grading |
| API | **FastAPI + Uvicorn** | |
| Frontend | **React** | Vite |
| Relational + vectors | **PostgreSQL + pgvector** | one instance, one service |
| Conversation state | **LangGraph PostgresSaver** checkpointer | same Postgres |
| Structured/tabular data | **DuckDB** | reads CSV/Parquet in place, no ingestion step |
| Lexical retrieval | **rank_bm25**, in process | |
| Embeddings | **fastembed** + `BAAI/bge-small-en-v1.5` (384d) | CPU, ONNX, no torch |
| PDF text | **PyMuPDF** (`pymupdf`, imports as `fitz`) | NOT pypdf |
| PDF tables | **pdfplumber** | tables only |
| Cache / limits | **Redis** | if required by the brief |
| Containers | **Docker + Docker Compose** | |

**No GPU. All generation is remote on Groq.** Do not add a local LLM, a second vector database, a reranker, or an ORM. If a library is not in the base image or `requirements.txt`, do not introduce it without saying so.

---

## 3. RAG PIPELINE — pre-decided, do not re-evaluate

These values come from a benchmark I already ran on NPCI circulars (see `../rag-benchmark/results/report.md`). **Do not re-run the benchmark. Do not evaluate retrieval quality against test cases.** Implement these values and verify the pipeline returns sensible chunks.

| Setting | Value |
|---|---|
| Embedding model | `BAAI/bge-small-en-v1.5` via fastembed, 384 dims |
| Vector column | `vector(384)` — dimension-typed, not bare `vector` |
| Normalisation | **Normalise embeddings to unit length explicitly at ingestion.** Do not assume fastembed does it. Dot product only equals cosine for unit vectors. |
| Chunk size / overlap | **1024 characters / 128 overlap** |
| Retrieval | **Hybrid: dense (pgvector cosine) + BM25, fused with RRF, k=60** |
| Candidates | 20 per arm before fusion |
| top_k returned | 5 |
| Index | HNSW, `vector_cosine_ops`, **built after bulk insert, not before** |
| Text extraction | PyMuPDF |
| Table extraction | pdfplumber, serialised to markdown, `content_type='table'` |

**Why 1024/128 and not smaller:** at 512/64 the benchmark's oracle check failed — evidence spans longer than the 64-character overlap were split across chunk boundaries and became unretrievable as a unit. **Overlap must be at least as long as the longest fact that needs to be retrievable in one piece.**

**Panel-ready justification, use it:** dense retrieval mis-ranks exact circular-number lookups because numerically adjacent identifiers embed almost identically; BM25 handles rare exact tokens; hybrid RRF uses rank rather than score, so no normalisation is needed between a bounded cosine and an unbounded BM25 score.

**Be straight about it if the panel pushes:** hybrid was a judgement call, not a measured win. On the primary metric (Recall@5) the three arms are within noise: hybrid 28/30, dense 27/30, BM25 26/30. BM25 alone is cheaper (1.0 ms vs 9.9 ms p50) and has the best Recall@1 (25 vs 23). Hybrid was chosen because the question set was written from the passages and so favours BM25; on the two questions phrased unlike the documents, BM25 found nothing in the top 5 and dense ranked first. Hybrid keeps BM25's identifier precision and covers that gap for about 9 ms on top of a multi-second LLM call. **If the panel wants a decision driven only by the measurements, BM25 is the defensible alternative.**

### Every chunk carries this metadata

`source_filename`, `page_number`, `product`, `circular_no`, `fiscal_year`, `content_type` (`text` | `table`), `chunk_index`

Chunks must never cross a page boundary, so `page_number` is exact.

### Ingestion is a separate job

Write a **new** ingestion script for whatever data the brief supplies. Two stages:

    ingest/parse.py   PDFs/source → chunks.jsonl   (slow, run once)
    ingest/embed.py   chunks.jsonl → pgvector      (fast, re-runnable)

**Ingestion must never run from FastAPI application startup.** In `docker-compose.yml` give ingestion services `profiles: ["tools"]` so `docker compose up` ignores them. Run deliberately with `docker compose run --rm ingest`.

Why it matters, for the panel: in startup, every container restart re-ingests; N replicas means N concurrent runs racing on the same tables; health checks fail during ingestion so the orchestrator restarts the app, which restarts ingestion, forever; and one malformed source file turns a data problem into an availability incident.

---

## 4. ARCHITECTURE INVARIANTS — enforce in code, never in a prompt

1. **Write actions pause for human confirmation.** Any tool with a side effect goes through a LangGraph `interrupt()` before executing. The agent prepares the action; a human approves it.

   **Verified versions (2026-11-12, inside `support-agent:latest`, Python 3.12.16):** `langgraph==1.2.20`, `langgraph-prebuilt==1.1.1`, `langgraph-checkpoint==4.3.1`, `langgraph-checkpoint-postgres==3.2.0`, `langchain-core==1.7.7`, `langchain-groq==1.1.3`. An `interrupt()` → `Command(resume="approve")` round trip was run against this install and works: the first `invoke` returns `__interrupt__`, `get_state(cfg).next` shows the paused node, and resuming finishes the node.

   **Use exactly these imports. Each one was run and resolves on the versions above:**

       from langgraph.types import interrupt, Command, RetryPolicy
       from langgraph.graph import StateGraph, START, END, MessagesState
       from langgraph.graph.message import add_messages
       from langgraph.prebuilt import ToolNode, tools_condition
       from langgraph.checkpoint.postgres import PostgresSaver
       from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
       from langgraph.checkpoint.memory import InMemorySaver        # tests only
       from langgraph.errors import GraphInterrupt
       from langchain_core.tools import tool, BaseTool, InjectedToolArg
       from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, ToolMessage, AnyMessage
       from langchain_core.runnables import RunnableConfig
       from langchain_groq import ChatGroq
       from fastembed import TextEmbedding
       from rank_bm25 import BM25Okapi
       import pymupdf                                               # `import fitz` also works
       import pdfplumber
       import duckdb
       from psycopg import AsyncConnection, Connection
       from psycopg_pool import AsyncConnectionPool, ConnectionPool
       from redis.asyncio import Redis
       from pydantic_settings import BaseSettings, SettingsConfigDict
       import jwt                                                    # PyJWT
       from fastapi import FastAPI, Depends, HTTPException
       from fastapi.middleware.cors import CORSMiddleware
       from fastapi.security import OAuth2PasswordBearer, HTTPBearer

   **Do not use these. They fail or are deprecated on this install:**
   - `from langchain.agents import create_agent` raises `ModuleNotFoundError`, because the `langchain` package is not installed and is not in the requirements list. Do not add it.
   - `from langgraph.prebuilt import create_react_agent` still imports but emits *"create_react_agent has been moved to `langchain.agents`… Deprecated in LangGraph V1.0"*.
   - **Build the agent by hand with `StateGraph` + `ToolNode` + `tools_condition`**, and compile with the Postgres checkpointer. This needs no extra package and gives you an explicit node in which to place the `interrupt()`.

   The package list that makes every import above work is in §5 (`requirements-base.txt`).
2. **The checkpointer is required for interrupts.** PostgresSaver gives durable pauses that survive a restart. Conversation memory and human-in-the-loop are the same component.
3. **Access control lives in a decorator at the tool boundary.** Never only in the system prompt. A prompt instruction is a suggestion; a decorator is a control.
4. **Identity comes from the authenticated session, never from LLM-supplied tool arguments.** Overwrite any user/customer id in the tool kwargs with the session value. The model never chooses whose data it reads.
5. **Never let the LLM do money or date arithmetic.** Amounts, turn-around times, compensation, eligibility windows — computed in Python from a rules table, cited to source. If the calculator did not return a number, the agent does not state one.
6. **Rules as data.** Thresholds, limits and timelines live in a CSV or table with a `source` column, not hardcoded in Python and not recalled by the model.
7. **Grounded answers only.** After retrieval, grade relevance with the 20b model. If nothing is relevant, say the corpus does not cover it. Never answer a domain question from parametric knowledge.
8. **Every answer carries citations** — source file, page, and the clause where available.
9. **Schema creation is declarative** — `init.sql` mounted at `/docker-entrypoint-initdb.d/`. The application never creates schema.
10. **Idempotency on every write.** Unique `idempotency_key`; a retry returns the original result rather than creating a duplicate.
11. **Append-only audit log** of every tool call: actor, role, tool, arguments, result summary, confirmed_by, timestamp.
12. **Dataset mounted read-only** (`:ro`). Postgres data on a **named volume**, never a bind mount — the postgres user has a fixed UID and bind-mounting the data directory causes ownership errors.
13. **Do not publish 5432 or 6379 to the host.** Services talk over the Docker network.
14. **The dataset never enters git.** `.gitignore` covers `data/`, `.env`, `*.duckdb`, `chunks.jsonl`.

---

## 5. DOCKER

**Always build the application `FROM base_image`.** A base image is pre-built on my EC2 instance with the full stack. **It does not exist on the development laptop:** on 2026-11-12, `docker images` showed no `base_image`, only `python:3.12-slim`, `pgvector/pgvector:0.8.0-pg16`, `redis:7.4-alpine`, `node:22-alpine` and old app images. **So the repo must be able to create `base_image` itself.** Do this in Phase 1, before any other build.

### Step 1: write `base.Dockerfile`

This is the **only** Dockerfile allowed to start from a public image.

    # base.Dockerfile: builds base_image. The only file allowed to start FROM a public image.
    FROM python:3.12-slim
    ENV PYTHONDONTWRITEBYTECODE=1 \
        PYTHONUNBUFFERED=1 \
        PIP_NO_CACHE_DIR=1 \
        PIP_DISABLE_PIP_VERSION_CHECK=1 \
        FASTEMBED_CACHE_PATH=/models
    COPY requirements-base.txt /tmp/requirements-base.txt
    RUN pip install -r /tmp/requirements-base.txt && pip check
    # Bake the embedding model in: no download at container start, no network dependency at runtime.
    RUN python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5')"
    ENV HF_HUB_OFFLINE=1

`FASTEMBED_CACHE_PATH` is read by fastembed when `cache_dir` is not passed (`fastembed/common/utils.py:82`, verified). Application code therefore calls `TextEmbedding(settings.EMBED_MODEL)` without `cache_dir`.

### Step 2: write `requirements-base.txt`, the exhaustive list

On 2026-11-12, `pip install --dry-run` on `python:3.12-slim` resolved this list with no conflicts. Every import in §4.1 is satisfied by it. **Do not add, remove or re-pin without saying so.**

    fastapi==0.162.4
    uvicorn[standard]==0.54.0
    pydantic==2.14.3
    pydantic-settings==2.16.2
    python-multipart==0.0.33        # required by FastAPI for form/OAuth2 login bodies
    PyJWT==2.15.1
    bcrypt==5.0.0                   # password hashing for demo users
    httpx==0.28.1                   # health checks and tests
    psycopg[binary,pool]==3.3.8
    psycopg-pool==3.3.3
    pgvector==0.5.1                 # psycopg adapter for the vector type
    redis==8.1.0
    duckdb==2.0.0
    fastembed==0.9.1
    onnxruntime==1.31.1
    numpy==2.5.4
    rank-bm25==0.2.2
    langchain-core==1.7.7
    langchain-groq==1.1.3
    langgraph==1.2.20
    langgraph-checkpoint-postgres==3.2.0
    pymupdf==1.26.5                 # same version the RAG benchmark measured
    pdfplumber==0.11.8              # same version the RAG benchmark measured
    pytest==9.2.0
    pytest-asyncio==1.4.1.post1

### Step 3: build it only if it is missing

    docker image inspect base_image:1.0 >/dev/null 2>&1 || docker build -f base.Dockerfile -t base_image:1.0 .

On EC2 the pre-built image is used as-is. If its tag differs from `base_image:1.0`, change `BASE_IMAGE` in `.env` (§6). Do not rename the image.

### Step 4: the application Dockerfile

    ARG BASE_IMAGE=base_image:1.0
    FROM ${BASE_IMAGE}
    WORKDIR /app
    COPY requirements.txt .
    RUN pip install -r requirements.txt && pip check
    COPY . .

In `docker-compose.yml` pass the tag through: `build: { context: ., args: { BASE_IMAGE: ${BASE_IMAGE} } }`.

**`requirements.txt` repeats the full pinned list from `requirements-base.txt`.** The EC2 base image's contents have not been verified, so this is the "if unsure, add it" rule applied to everything. On our own `base_image` every requirement is already satisfied and the step is a no-op. On an EC2 base that differs, it fills the gaps instead of failing at import time.

Other requirements:

- `.dockerignore` covering `node_modules/`, `data/`, `.git/`, `__pycache__/`, `*.pyc`, `.venv/` — build context size is a real time cost
- Postgres gets a `pg_isready` healthcheck; the app uses `depends_on: condition: service_healthy`. Without it the first `up` is a crash loop.
- Frontend built separately and served as static files by its own nginx container on host port 3000 (see "Ports" below). Do not build React inside the API container.
- Pin image tags. No `:latest`. Use `base_image:1.0`, `pgvector/pgvector:0.8.0-pg16` (the version the benchmark ran on), `redis:7.4-alpine`, and `node:22-alpine` for the frontend build stage. All four were present locally on 2026-11-12.

### Ports: fixed by the EC2 security group

The EC2 instance's security group allows inbound traffic on **exactly two ports, and only from my IP**:

| Port | Purpose |
|---|---|
| **22** | SSH |
| **3000** | The web UI, and the only application port a browser can reach |

Every other port is closed from outside, **including 8000**. The rules that follow:

- **Publish the frontend on host port 3000:** `ports: ["3000:80"]` for an nginx container.
- **The browser must never call the API on `:8000`.** Port 8000 is unreachable on EC2, so a frontend built with `http://<host>:8000` as its API URL works on the laptop and fails after deployment. Instead, the frontend's nginx proxies `/api/` to `http://api:8000/`, and the React code calls the relative path `/api/...`. That makes it one origin, so no CORS is needed and no extra port has to be opened.
- **The API may publish 8000 on the host** (`ports: ["8000:8000"]`), but only so that `curl localhost:8000/health` works from an SSH session on the instance. To use `/docs` from the laptop, tunnel it: `ssh -i <key> -L 8000:localhost:8000 ubuntu@<ip>`, then open `http://localhost:8000/docs`.
- **Never publish 5432 or 6379** (invariant 13). Databases stay on the Docker network.
- **Use the same ports on the laptop and on EC2:** 3000 for the UI, 8000 for the API. Do not make ports configurable per environment.

**Several projects share this instance, and only one runs at a time.** They all use 3000 and 8000, so:

- Give every project a unique top-level `name:` in `docker-compose.yml`. Containers, networks and volumes are then prefixed per project and never collide.
- Use `restart: unless-stopped` on long-running services, **never `restart: always`**. With `always`, a container you stopped comes back the next time the Docker daemon restarts, for example after a reboot. It then takes 3000 and 8000 from whichever project you meant to run.
- Switch projects with `docker compose down` in the old project's folder before `docker compose up -d` in the new one. `down` removes the containers, so nothing of the old project can restart on boot. Named volumes are kept.
- Before `up`, check that nothing holds the ports: `docker ps` should list no container publishing 3000 or 8000.

---

## 6. CONFIGURATION AND SECRETS — single source of truth

**The last build lost time to a Postgres password mismatch. This section exists to prevent that. There is exactly one place each value is defined.**

### The rule

One `.env` file at project root. `docker-compose.yml` references it via `env_file:`. The application reads it through one settings module. **No value is written in two places. No default is hardcoded in Python that could diverge from `.env`.**

Generate `.env` in Phase 1, before anything that needs it. Add `.env` to `.gitignore` in the same phase. Also write `.env.example` with the same keys and empty values — that one is committed.

### `.env` keys

    POSTGRES_USER=<<CONFIRM>>
    POSTGRES_PASSWORD=<<CONFIRM>>
    POSTGRES_DB=<<CONFIRM>>
    POSTGRES_HOST=postgres
    POSTGRES_PORT=5432
    DATABASE_URL=postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}

    REDIS_URL=redis://redis:6379/0

    JWT_SECRET=<<CONFIRM — or generate with: openssl rand -hex 32>>
    JWT_ALGORITHM=HS256
    JWT_EXPIRY_MINUTES=120

    GROQ_API_KEY=            # read from my shell environment, see below
    GROQ_MODEL_GENERATION=openai/gpt-oss-120b
    GROQ_MODEL_ROUTING=openai/gpt-oss-20b

    EMBED_MODEL=BAAI/bge-small-en-v1.5
    EMBED_DIM=384

    BASE_IMAGE=base_image:1.0     # change only if EC2's pre-built image has a different tag (§5)

**`GROQ_API_KEY` is never written into any file.** It comes from my shell environment, and compose passes it through:

    environment:
      - GROQ_API_KEY=${GROQ_API_KEY}

If it is unset at startup, fail loudly at import time with a clear message. Do not fall back to a placeholder and fail later inside a request.

### Settings module

One `config.py` using `pydantic-settings`, reading from the environment. Every module imports settings from there. **Nothing calls `os.getenv()` anywhere else.** That is what caused the password drift.

Validate at startup: if `DATABASE_URL` is unreachable or `GROQ_API_KEY` is empty, exit with a readable error rather than a stack trace twenty seconds into the first request.

### The Postgres gotcha that cost time last build

`POSTGRES_PASSWORD` in the compose environment is read **only when the data directory is empty**. If the named volume already exists from an earlier run with a different password, changing `.env` does nothing and connections keep failing with `password authentication failed`.

**Fix: `docker compose down -v` before the first real `up`.** Note this in the start commands.

The same applies to `init.sql` — scripts in `/docker-entrypoint-initdb.d/` run only on an empty data directory. Editing it later has no effect until `down -v`.

### Production note, for the panel

A `.env` on the box is a prototype shortcut: no rotation, no audit trail. Production is AWS Secrets Manager fetched via an IAM instance role, never written to disk. Say this before being asked.

---

## 7. TESTING POLICY — verify, do not evaluate

**I have already benchmarked retrieval. Do not build an evaluation harness, do not compute Recall/MRR/nDCG, do not write ground-truth question sets.**

**Use one quarter of the number of tests you would normally write.** Favour one end-to-end smoke test over five unit tests.

What to verify at each phase, with real output pasted into the summary:

| Phase | Verification |
|---|---|
| 1 | `docker compose ps` all healthy; `curl /health` returns 200 |
| 2 | Row count in the chunks table; one sample chunk printed with its metadata |
| 3 | One real query through the retriever; show the top 3 chunks and confirm they are on-topic |
| 4 | One request end to end producing an answer with citations; one write action hitting the interrupt |
| 5 | UI loads, sends a message, renders a response |
| 6 | Full path through the browser |

**"Verified" means you ran it and these were the results.** Not "this should work."

---

## 8. UI / UX

Clean, polished, professional. It should read as deliberately designed by a person — not as a template and not as generic AI output.

### Brand palette

| Colour | Hex | Role |
|---|---|---|
| Persian Blue | `#242D7A` | Primary. Dominant, paired with white. |
| Clementine | `#EF7102` | Vibrant accent — primary actions, highlights, key figures |
| Australia Green | `#018136` | Secondary accent — success states, status indicators |

Predominantly **Persian Blue and white**. Orange and green used strategically for contrast and emphasis, never dominant. No gradients across the whole page, no purple, no neon.

### Logo

The NPCI logo lives outside the repo on my laptop, at `C:\Users\viren\Downloads\images\npci_company_logo.png`. That is a real PNG, 662×302 RGBA, checked 2026-11-12. **Phase 1, first action: copy it into `frontend/public/npci-logo.png`.** `frontend/public/` does not exist yet, so create it first. From the project root:

    # PowerShell
    New-Item -ItemType Directory -Force frontend\public | Out-Null; Copy-Item "$env:USERPROFILE\Downloads\images\npci_company_logo.png" frontend\public\npci-logo.png
    # Git Bash / WSL
    mkdir -p frontend/public && cp "/c/Users/Anjali Gangotri/Downloads/images/npci_company_logo.png" frontend/public/npci-logo.png

Vite serves `public/` at the site root, so reference it as `/npci-logo.png` and from no other path. Never reference a `Downloads` path. It does not exist on the EC2 instance, and the image will 404 after deployment. If the build runs on EC2, the file gets there with the repo (it is not under `data/`, so it is not gitignored) or by `scp`.

If the file is not found at the expected source, say so and use a clean text wordmark placeholder. Do not stop for it.

Place it in the **top-right of the header**, consistently on every view.

### Design requirements

- One type scale, consistent through the app. System font stack or Inter. No more than two weights.
- Generous whitespace. Cards with subtle borders rather than heavy shadows.
- Status colours used semantically: green for resolved/success, orange for pending/attention, blue for informational, muted red for errors.
- Subtle transitions on hover and state change — 150ms, nothing bouncy.
- Visible loading states. The agent takes seconds; dead UI reads as broken.
- Citations rendered as distinct, clickable chips under an answer. **This is the feature that demonstrates grounding — make it visually obvious.**
- Any confirmation step is a **modal**, clearly stating what is about to happen and that it is not reversible. This is the centrepiece of the demo; give it real design attention.
- Responsive enough not to break on a projector or a shared screen.

---

## 9. CODE STANDARDS

- Type hints on function signatures. `Decimal` for money, never float.
- Pydantic models for API request and response bodies.
- One concern per module. Flat structure over deep nesting.
- `async` throughout FastAPI; async Postgres and Redis clients.
- Errors return structured JSON with a code, never a bare 500 with a stack trace.
- Log tool calls and their outcomes. Never log secrets, full VPAs, or account identifiers.
- Mask sensitive identifiers at the tool boundary before they reach the model, not in the prompt. If the model never receives the full value, it cannot leak it.
- Comments only where the *why* is non-obvious. No narration of what the line does.

---

## 10. AT THE END — do this unprompted

After the final phase, without being asked, output:

1. **Start commands**, copy-pasteable, in order, with what each does and what success looks like:

       docker image inspect base_image:1.0 >/dev/null 2>&1 || docker build -f base.Dockerfile -t base_image:1.0 .   # no-op if it already exists
       docker compose down -v          # required if the volume predates the current .env
       docker compose up -d postgres redis
       docker compose run --rm ingest
       docker compose up -d api frontend
       docker compose ps
       curl localhost:8000/health

2. **URLs** — frontend `http://<ip>:3000` (the only one reachable from a browser on EC2), plus API and docs on `localhost:8000` (on the laptop, or through the SSH tunnel in §5 "Ports").
3. **Demo path** — the exact sequence of clicks and inputs that shows the application working, including one write action through the confirmation gate.
4. **Known gaps** — anything cut for time, in one line each.
5. **Decisions summary** — every architecture decision made across all phases, one line of justification each, collected in one place. This is what I take into the panel discussion.

---

## 11. TRAPS FROM THE LAST BUILD — check these before reporting a phase complete

- `.env` password vs an existing Postgres volume → `down -v` first
- `init.sql` edited after the volume exists → does nothing until `down -v`
- App starting before Postgres is ready → healthcheck + `depends_on: service_healthy`
- CORS not configured → the browser blocks every call from React. Set `allow_origins` explicitly.
- Vite env vars need the `VITE_` prefix to reach the browser bundle
- `CREATE EXTENSION IF NOT EXISTS vector;` missing → every vector operation fails
- Embedding dimension ≠ column dimension → inserts rejected. Both are 384.
- Embeddings not normalised → cosine similarity silently wrong, ranking degrades, no error
- `interrupt()` without a checkpointer configured → does not pause
- Build context including `node_modules/` or `data/` → slow builds. `.dockerignore`.
- Logo referenced from an absolute laptop path → 404 after deployment
- Frontend calling `http://<host>:8000` directly → works on the laptop, fails on EC2 (only 22 and 3000 are open). Proxy `/api/` through the frontend's nginx.
- Another project's containers still running → `port is already allocated` on `up`. Run `docker compose down` in that project first; check with `docker ps`.