# ComplyNexus — Agentic RAG for AI Regulatory Compliance

ComplyNexus answers natural-language questions about **AI regulation and
governance** across jurisdictions, grounded in a corpus of country-specific
regulatory documents and international standards (ISO/IEC 42001, 23894, 23053,
OECD, World Bank). Every answer cites its sources (document + page).

It is an **Agentic RAG** system: an LLM agent (Azure GPT-4.1) decides which
retrieval tools to call — possibly multiple times — to gather evidence before
synthesizing a cited answer.

---

## Architecture

```
Browser ──► NGINX (reverse proxy) ──┬──► Frontend (React SPA)
                                     └──► Backend (FastAPI, Agentic RAG)
                                              │   │
                                              │   └─ enqueue ─► Redis ─► Celery worker
                                              │                            │ (ingestion job)
                                Azure OpenAI ◄┤► ChromaDB (vector store) ◄──┘
                                (GPT-4.1, ada-002)
```

| Layer        | Tech                                                       |
|--------------|------------------------------------------------------------|
| Frontend     | React 18 + Vite + TypeScript (chat UI, jurisdiction filter)|
| Backend      | FastAPI tool-calling agent                                 |
| LLM / Embed  | Azure OpenAI GPT-4.1 + text-embedding-ada-002              |
| Vector store | ChromaDB (persistent)                                      |
| Job queue    | Celery worker + Redis (async ingestion)                    |
| Edge         | Nginx reverse proxy                                        |
| Orchestration| Docker Compose                                             |

---

## Getting started

### Prerequisites

- Windows 10/11 with Docker Desktop installed and running. Enable the WSL 2
  backend if Docker Desktop offers that option.
- Docker Compose v2, available through `docker compose`. Check it in
  PowerShell:
  ```powershell
  docker --version
  docker compose version
  ```
- Node.js 20 or newer and npm (needed only to run/build the frontend locally).
- Python and pip (needed only for local backend, worker, or ingestion
  development; the Docker backend uses Python 3.11).
- An Azure OpenAI resource with a chat deployment and an embeddings deployment.
  The endpoint and API key must be valid, and the embedding deployment must be
  compatible with the configured embedding model.
- Network access from Docker containers to Azure OpenAI.
- Ports **80** (web application) and **8000** (ChromaDB) available on the host.
- Enough disk space for Docker images, the source corpus, ChromaDB data, and
  generated ingestion files.

### Configure environment

Run all Docker commands from the repository root (the directory containing
`docker-compose.yml`).

Create `.env` without replacing an existing file:
```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

Edit `.env` and provide values for:

| Variable | Purpose |
| --- | --- |
| `AZURE_OPENAI_ENDPOINT` | Azure OpenAI resource endpoint URL |
| `AZURE_OPENAI_API_KEY` | API key for that Azure resource |
| `AZURE_OPENAI_API_VERSION` | Azure OpenAI API version |
| `AZURE_OPENAI_CHAT_DEPLOYMENT` | Name of the deployed chat model |
| `AZURE_OPENAI_EMBEDDING_DEPLOYMENT` | Name of the deployed embedding model |
| `JWT_SECRET_KEY` | Long, unique secret used to sign login tokens |

The `.env.example` file contains local defaults for the remaining settings.
Compose overrides the ChromaDB host and Redis URLs with the service names
`chromadb` and `redis`; do not set those Docker URLs to `localhost`.
Set `ADMIN_PASSWORD`, `PRIVS_PASSWORD`, and `USER_PASSWORD` to configure the
role login credentials. If omitted, the development defaults are
`admin123`, `privs123`, and `user123`; change them before using the app beyond
local development. Keep `.env` private and never commit API keys or other
secrets.

### Build and start with Docker Compose

Build the application images and start the stack in the background:
```powershell
docker compose up --build -d
```

Check that the services are running and healthy:
```powershell
docker compose ps
```

| Service | Purpose | Host access |
| --- | --- | --- |
| `nginx` | Serves the web UI and proxies API requests | `http://localhost` |
| `backend` | FastAPI endpoints and chat agent | Through Nginx |
| `frontend` | React single-page application | Through Nginx |
| `chromadb` | Persistent vector database | `localhost:8000` |
| `redis` | Celery broker and job-result store | Internal to Compose |
| `worker` | Runs ingestion and document reprocessing jobs | Internal to Compose |

Compose waits for Redis and ChromaDB healthchecks before starting the backend
and worker. Open `http://localhost`; check the API at
`http://localhost/api/health`.
The interactive API documentation is available at `http://localhost/docs`.
Sign in through the application with one of the configured role accounts.

### Initial embeddings and ingestion

When the backend starts with Azure credentials configured, it queues a
background check. If the ChromaDB collection is empty, the worker indexes
available data from `data/ingest/` (including `chunks/chunks.jsonl`). If
embeddings already exist, startup indexing is skipped. Uploaded PDFs are saved
under the host's `document/` directory; the API can write there and the worker
can read the files for processing.

Follow application and indexing logs:
```powershell
docker compose logs -f backend worker chromadb
```

Manually run or resume ingestion from the available corpus:
```powershell
docker compose run --rm ingest
```

To deliberately delete and rebuild the ChromaDB collection:
```powershell
docker compose run --rm ingest --reset
```
This removes existing embeddings in that collection before indexing. Do not use
`docker compose down -v` as a routine reset; it may delete persisted data.

### Stop and restart

Stop the containers while preserving ChromaDB data:
```powershell
docker compose down
```

Start the already-built stack again:
```powershell
docker compose up -d
```

Rebuild after changing application code or dependencies:
```powershell
docker compose up --build -d
```

### Troubleshooting

- **`dependency chromadb failed to start` / ChromaDB is unhealthy:** inspect
  its startup log and check that port 8000 is free and `chroma_data/` is
  writable:
  ```powershell
  docker compose logs --tail 200 chromadb
  ```
- **Backend or worker keeps restarting:** inspect logs and confirm `.env`
  exists and its Azure endpoint, key, and deployment names are correct:
  ```powershell
  docker compose logs --tail 200 backend worker
  ```
- **Web page does not load:** check that Nginx, frontend, and backend are
  running and that port 80 is available:
  ```powershell
  docker compose ps
  docker compose logs --tail 100 nginx frontend backend
  ```
- **No embeddings appear:** check the worker logs, Azure connectivity, and
  that `data/ingest/chunks/chunks.jsonl` exists and is not empty:
  ```powershell
  docker compose logs --tail 200 worker
  ```
- **A port is already in use:** stop the local process using port 80 or 8000.
  Stop a local ChromaDB server before starting Compose.

ChromaDB data persists in the repository's `chroma_data/` directory. Do not
run the local ChromaDB server and Docker Compose at the same time: both use port
8000 and the same database files.

---

## Local development (without Docker)

**Backend**
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r backend/requirements.txt
# Create .env from .env.example if it has not been configured yet.
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
# Load environment variables from the root .env:
Get-Content .env | ForEach-Object { if ($_ -and $_ -notlike '#*') { $name, $value = $_ -split '=', 2; Set-Item "env:$name" $value } } 
# Run this from the repository root.
uvicorn app.main:app --reload --port 8080 --app-dir backend
```

Run the frontend in a second PowerShell terminal:
```powershell
cd frontend
npm install
npm run dev                         # http://localhost:5173, proxies /api → :8080
```

**Supporting services for local development**

The local backend defaults expect ChromaDB and Redis at `localhost`. Start just
those Compose services (not the full Docker application) in another terminal:
```powershell
docker compose up -d chromadb redis
docker compose ps
```
The Compose-backed ChromaDB persists in `chroma_data/`, matching the local
ChromaDB command below. Do not start another ChromaDB process on port 8000.

**Celery worker** (for asynchronous jobs; requires the services above)
```powershell
.\.venv\Scripts\Activate.ps1
# From the repository root, load environment variables from the root .env:
Get-Content .env | ForEach-Object { if ($_ -and $_ -notlike '#*') { $name, $value = $_ -split '=', 2; Set-Item "env:$name" $value } } 
$env:PYTHONPATH = "backend"
celery -A app.celery_app worker --loglevel=info --concurrency=1 -P solo 
```
Run the backend, frontend, and worker in separate terminals. Stop each with
`Ctrl+C`; stop the supporting containers when finished:
```powershell
docker compose stop redis chromadb
```

**Ingestion only (offline stages, no Azure/Chroma needed)**
```powershell
pip install -r requirements.txt
python ingestion\run_pipeline.py --skip-index
```
**Chroma Server**
```powershell
chroma run --path .\chroma_data --host localhost --port 8000
```
Do not run the local ChromaDB server and Docker Compose simultaneously: both
use port 8000 and the same `chroma_data/` database directory.

---

## API

| Method | Path                 | Description                                  |
|--------|----------------------|----------------------------------------------|
| GET    | `/api/health`        | Health + whether Azure is configured         |
| POST   | `/api/auth/login`    | Authenticate and receive a bearer token      |
| GET    | `/api/auth/me`       | Get the current authenticated user           |
| GET    | `/api/jurisdictions` | Available jurisdictions with document counts |
| GET    | `/api/documents`     | List documents and their indexing status     |
| POST   | `/api/documents/upload` | Upload a PDF and queue its indexing       |
| DELETE | `/api/documents/file` | Delete a PDF and its indexed chunks          |
| POST   | `/api/chat`          | `{question, history[], jurisdiction?}` → cited answer |
| POST   | `/api/ingest`        | `{reset?, skip_index?}` → enqueue ingestion job |
| GET    | `/api/ingest/{id}`   | Poll ingestion job state / progress / result |

Protected endpoints require the bearer token returned by login. Available
actions depend on the user's role and privileges.

---

## Repository layout
```
ingestion/   offline pipeline: parser → cleaner → chunker → embedder → index
backend/     FastAPI Agentic RAG service (app/), Dockerfile
frontend/    React + Vite SPA, Dockerfile (build → nginx static)
nginx/       edge reverse proxy config
document/    source corpus (PDFs)
docker-compose.yml
```

---

## How the agent works
The agent (`backend/app/agent.py`) runs a tool-calling loop on GPT-4.1 with
three tools:
- `list_jurisdictions` — enumerate what's in the knowledge base.
- `retrieve_regulations(query, jurisdiction?, k)` — semantic search over ChromaDB.
- `compare_jurisdictions(query, jurisdictions[])` — multi-jurisdiction retrieval.

The model may call tools several times (multi-hop), accumulating passages into a
citation pool, then produces a grounded answer. If the corpus lacks the answer,
it says so rather than fabricating.
