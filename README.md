# Agentic JARVIS POC

Enterprise migration intelligence platform with real LLM-enabled agents, MCP-style tool orchestration, and local Ollama inference.

## What this project does

- Runs multi-agent workflows across Assessment, Migration, Testing, PMO, and Integrations.
- Uses an orchestrator agent to distribute specialist workloads and aggregate outputs.
- Uses local Ollama models for chat and embedding.
- Persists platform data in SQLite.

## Architecture at a glance

- Frontend: static Engine UI (HTML/CSS/JS).
- Backend: FastAPI with modular routers.
- Agent runtime:
	- Orchestrator + specialist agents in backend services.
	- MCP-style tool invocation for contextual retrieval.
	- Ollama chat model for reasoning and responses.
	- Ollama embedding model for semantic search/context.

## Prerequisites

- Python 3.11+ (3.13 works)
- Ollama installed and running
- Git

## 1) Install Ollama models

Run:

- ollama pull llama3.1:8b
- ollama pull nomic-embed-text:latest

Verify:

- ollama list

## 2) Configure environment

Use values from .env.example (or export env vars directly):

- OLLAMA_BASE_URL=http://127.0.0.1:11434
- OLLAMA_CHAT_MODEL=llama3.1:8b
- OLLAMA_EMBED_MODEL=nomic-embed-text:latest
- OLLAMA_CHAT_TIMEOUT_SEC=240
- OLLAMA_CHAT_NUM_PREDICT=450

Optional:

- JARVIS_DB_PATH=./jarvis.db

## 3) Install backend dependencies

From project root:

- python -m pip install -r backend/requirements.txt

## 4) Run backend

From project root:

- cd backend
- python -m uvicorn main:app --host 127.0.0.1 --port 8010

Health:

- http://127.0.0.1:8010/api/health

Agent status:

- http://127.0.0.1:8010/api/system/agents

## 5) Run frontend

From project root in a second terminal:

- python -m http.server 8080

Open:

- http://127.0.0.1:8080/Engine/

## Demo flow

1. Open Assessment and review predefined/demo apps.
2. Use chat assistant with persona context.
3. Trigger migration run for a demo app.
4. Run testing suites and inspect generated outcomes.
5. Open PMO reports and integration checks.

## API smoke checks

- GET /api/system/agents
- POST /api/jarvis/chat
- POST /api/migration/run
- POST /api/testing/suites/SMOKE/run
- POST /api/pmo/reports/generate
- POST /api/integrations/servicenow/test

## Operational notes

- Chat input is capped at 100 words and output is capped at 300 words.
- Runtime/cache artifacts should remain untracked (.gitignore).
- First request can be slower due to Ollama model warm-up.
- Increase OLLAMA_CHAT_TIMEOUT_SEC if needed on slower machines.
