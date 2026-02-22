import json
import os
import platform

from fastapi import APIRouter, HTTPException

from database import DB_PATH, db, rows2list
from services.agentic_orchestrator import orchestrate_workload, run_specialist_agent
from services.embeddings import cosine_similarity, embed_text
from services.llm_client import ollama_chat_model, ollama_embed_model, ollama_list_models

router = APIRouter()

MAX_CHAT_INPUT_WORDS = 100
MAX_CHAT_OUTPUT_WORDS = 300


@router.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "jarvis-backend",
        "python": platform.python_version(),
        "db_path": str(DB_PATH),
    }


async def _search_vector_context(query: str, limit: int = 6) -> list[dict]:
    q = (query or "").strip()
    if not q:
        return []

    q_vec = embed_text(q)
    async with db() as conn:
        rows = await conn.execute_fetchall(
            "SELECT app_id, file_path, chunk_text, embedding_json FROM code_chunks"
        )
    items = rows2list(rows)

    scored = []
    for item in items:
        try:
            emb = json.loads(item.get("embedding_json") or "[]")
            sim = cosine_similarity(q_vec, emb)
            scored.append(
                {
                    "app_id": item.get("app_id"),
                    "file_path": item.get("file_path"),
                    "chunk_text": (item.get("chunk_text") or "")[:800],
                    "score": sim,
                }
            )
        except Exception:
            continue

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[: max(1, min(limit, 10))]


def _count_words(text: str) -> int:
    return len([w for w in str(text or "").strip().split() if w])


def _trim_words(text: str, max_words: int) -> str:
    words = [w for w in str(text or "").strip().split() if w]
    if len(words) <= max_words:
        return " ".join(words)
    return " ".join(words[:max_words])


@router.post("/jarvis/chat")
async def jarvis_chat(payload: dict):
    message = (payload or {}).get("message", "")
    persona = (payload or {}).get("persona", "assessment")
    ui_context = (payload or {}).get("context", "")
    history = (payload or {}).get("history", [])[-8:]

    if not str(message).strip():
        raise HTTPException(status_code=400, detail="message is required")

    if _count_words(str(message)) > MAX_CHAT_INPUT_WORDS:
        raise HTTPException(
            status_code=400,
            detail=f"Chat input exceeds {MAX_CHAT_INPUT_WORDS} words. Please shorten your message.",
        )

    vector_hits = await _search_vector_context(str(message), limit=6)
    agent_name = {
        "assessment": "assessment",
        "migration": "migration",
        "testing": "testing",
        "pmo": "pmo",
        "integration": "integration",
    }.get(str(persona or "").lower(), "assessment")

    try:
        orchestration = await orchestrate_workload(
            objective=str(message),
            tasks=[
                {
                    "agent": agent_name,
                    "objective": str(message),
                    "context": {
                        "persona": persona,
                        "ui_context": ui_context,
                        "history": history,
                        "vector_hits": vector_hits,
                    },
                    "mcp_calls": [
                        {"tool": "semantic_context_search", "args": {"query": str(message), "limit": 6}},
                        {"tool": "get_platform_kpis", "args": {}},
                    ],
                    "max_words": 240,
                }
            ],
            shared_context={"channel": "jarvis-chat", "persona": persona, "ui_context": ui_context},
        )
        response_text = str(orchestration.get("summary") or "").strip()
        if not response_text:
            fallback = await run_specialist_agent(
                agent=agent_name,
                objective=str(message),
                context={"persona": persona, "ui_context": ui_context, "vector_hits": vector_hits},
                mcp_calls=[{"tool": "semantic_context_search", "args": {"query": str(message), "limit": 6}}],
                max_words=240,
            )
            response_text = str(fallback.get("reply") or "").strip()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"agentic-ollama request failed: {e}")

    if not response_text:
        response_text = "I couldn't generate a response from the configured LLM provider. Please retry."

    response_text = _trim_words(response_text, MAX_CHAT_OUTPUT_WORDS)

    return {
        "reply": response_text,
        "persona": persona,
        "context_hits": vector_hits,
        "provider": "ollama",
        "limits": {
            "max_input_words": MAX_CHAT_INPUT_WORDS,
            "max_output_words": MAX_CHAT_OUTPUT_WORDS,
        },
    }


@router.get("/system/kpis")
async def kpis():
    return {
        "apps": 0,
        "migrated": 0,
        "at_risk": 0,
        "open_issues": 0,
    }


@router.get("/system/agents")
async def agents():
    models = []
    try:
        models = ollama_list_models()
    except Exception:
        models = []

    return {
        "orchestrator": {
            "status": "ready",
            "type": "llm-orchestrator",
            "provider": "ollama",
            "model": ollama_chat_model(),
            "mcp_enabled": True,
        },
        "agents": {
            "assessment": {"status": "ready", "provider": "ollama", "mcp_enabled": True},
            "migration": {"status": "ready", "provider": "ollama", "mcp_enabled": True},
            "testing": {"status": "ready", "provider": "ollama", "mcp_enabled": True},
            "pmo": {"status": "ready", "provider": "ollama", "mcp_enabled": True},
            "integration": {"status": "ready", "provider": "ollama", "mcp_enabled": True},
        },
        "mcp": {
            "enabled": True,
            "tool_count": 8,
        },
        "available_models": models,
    }


@router.get("/system/settings")
async def get_settings():
    return {
        "api_base": "http://localhost:8000/api",
        "embedding_model": ollama_embed_model(),
    }


@router.post("/system/settings")
async def save_settings(settings: dict):
    return {"saved": True, "settings": settings}
