import json
from typing import Any

from database import db
from services.embeddings import cosine_similarity, embed_text


MCP_TOOLS = {
    "get_application_context",
    "get_wave_context",
    "get_latest_job_context",
    "get_testing_context",
    "get_pmo_context",
    "get_integration_context",
    "semantic_context_search",
    "get_platform_kpis",
}


async def _get_application_context(args: dict[str, Any]) -> dict:
    app_id = (args.get("app_id") or "").strip()
    if not app_id:
        return {"app": None}
    async with db() as conn:
        row = await conn.execute_fetchone(
            "SELECT id, name, pattern_id, risk_score, language, framework, dependencies_json, findings_json FROM applications WHERE id=?",
            (app_id,),
        )
    if not row:
        return {"app": None}
    return {
        "app": {
            "id": row[0],
            "name": row[1],
            "pattern_id": row[2],
            "risk_score": row[3],
            "language": row[4],
            "framework": row[5],
            "dependencies": json.loads(row[6] or "[]"),
            "findings": json.loads(row[7] or "[]"),
        }
    }


async def _get_wave_context(args: dict[str, Any]) -> dict:
    wave_id = (args.get("wave_id") or "").strip()
    async with db() as conn:
        if wave_id:
            row = await conn.execute_fetchone(
                "SELECT id,name,status,apps_json,progress,started_at,completed_at FROM migration_waves WHERE id=?",
                (wave_id,),
            )
            if not row:
                return {"wave": None}
            return {
                "wave": {
                    "id": row[0],
                    "name": row[1],
                    "status": row[2],
                    "apps": json.loads(row[3] or "[]"),
                    "progress": row[4],
                    "started_at": row[5],
                    "completed_at": row[6],
                }
            }

        rows = await conn.execute_fetchall("SELECT id,name,status,progress FROM migration_waves ORDER BY id")
        return {
            "waves": [
                {"id": r[0], "name": r[1], "status": r[2], "progress": r[3]}
                for r in rows
            ]
        }


async def _get_latest_job_context(args: dict[str, Any]) -> dict:
    app_id = (args.get("app_id") or "").strip()
    if not app_id:
        return {"job": None}
    async with db() as conn:
        row = await conn.execute_fetchone(
            "SELECT id,status,pattern_id,progress,created_at,completed_at,diff_json FROM migration_jobs WHERE app_id=? ORDER BY created_at DESC LIMIT 1",
            (app_id,),
        )
    if not row:
        return {"job": None}
    return {
        "job": {
            "id": row[0],
            "status": row[1],
            "pattern_id": row[2],
            "progress": row[3],
            "created_at": row[4],
            "completed_at": row[5],
            "diff": json.loads(row[6] or "{}"),
        }
    }


async def _get_testing_context(args: dict[str, Any]) -> dict:
    app_id = (args.get("app_id") or "").strip()
    if not app_id:
        return {"runs": []}

    async with db() as conn:
        rows = await conn.execute_fetchall(
            "SELECT id, status, pattern_id, created_at, completed_at FROM migration_jobs WHERE app_id=? ORDER BY created_at DESC LIMIT 5",
            (app_id,),
        )
    return {
        "runs": [
            {
                "job_id": r[0],
                "status": r[1],
                "pattern_id": r[2],
                "created_at": r[3],
                "completed_at": r[4],
            }
            for r in rows
        ]
    }


async def _get_pmo_context(args: dict[str, Any]) -> dict:
    async with db() as conn:
        apps = await conn.execute_fetchone("SELECT COUNT(*) FROM applications")
        migrated = await conn.execute_fetchone("SELECT COUNT(*) FROM migration_jobs WHERE status IN ('approved','complete')")
        risks = await conn.execute_fetchall("SELECT id,title,rating,status,owner FROM pmo_risks ORDER BY id LIMIT 25")
        budget = await conn.execute_fetchall("SELECT wave,planned,actual,gcp_monthly FROM pmo_budget ORDER BY id")

    return {
        "total_apps": int((apps or [0])[0]),
        "migrated_apps": int((migrated or [0])[0]),
        "risks": [
            {"id": r[0], "title": r[1], "rating": r[2], "status": r[3], "owner": r[4]}
            for r in risks
        ],
        "budget": [
            {"wave": r[0], "planned": float(r[1] or 0), "actual": float(r[2] or 0), "gcp_monthly": float(r[3] or 0)}
            for r in budget
        ],
    }


async def _get_integration_context(args: dict[str, Any]) -> dict:
    service = (args.get("service") or "").strip().lower()
    async with db() as conn:
        if service:
            row = await conn.execute_fetchone(
                "SELECT service, enabled, config_json, status, last_sync FROM integration_settings WHERE service=?",
                (service,),
            )
            if not row:
                return {"service": None}
            return {
                "service": {
                    "name": row[0],
                    "enabled": bool(row[1]),
                    "config": json.loads(row[2] or "{}"),
                    "status": row[3],
                    "last_sync": row[4],
                }
            }

        rows = await conn.execute_fetchall(
            "SELECT service, enabled, status, last_sync FROM integration_settings ORDER BY service"
        )
    return {
        "services": [
            {"service": r[0], "enabled": bool(r[1]), "status": r[2], "last_sync": r[3]}
            for r in rows
        ]
    }


async def _semantic_context_search(args: dict[str, Any]) -> dict:
    query = (args.get("query") or "").strip()
    limit = int(args.get("limit") or 6)
    if not query:
        return {"results": []}

    q_vec = embed_text(query)
    async with db() as conn:
        rows = await conn.execute_fetchall(
            "SELECT app_id, file_path, chunk_text, embedding_json FROM code_chunks"
        )

    scored = []
    for row in rows:
        try:
            emb = json.loads(row[3] or "[]")
            score = cosine_similarity(q_vec, emb)
            scored.append(
                {
                    "app_id": row[0],
                    "file_path": row[1],
                    "chunk_text": (row[2] or "")[:600],
                    "score": round(float(score), 4),
                }
            )
        except Exception:
            continue

    scored.sort(key=lambda x: x["score"], reverse=True)
    return {"results": scored[: max(1, min(limit, 10))]}


async def _get_platform_kpis(args: dict[str, Any]) -> dict:
    async with db() as conn:
        apps = await conn.execute_fetchone("SELECT COUNT(*) FROM applications")
        runs = await conn.execute_fetchone("SELECT COUNT(*) FROM scan_runs")
        jobs = await conn.execute_fetchone("SELECT COUNT(*) FROM migration_jobs")
    return {
        "apps": int((apps or [0])[0]),
        "scan_runs": int((runs or [0])[0]),
        "migration_jobs": int((jobs or [0])[0]),
    }


async def invoke_mcp_tool(tool_name: str, args: dict[str, Any] | None = None) -> dict:
    tool = (tool_name or "").strip()
    params = args or {}

    if tool not in MCP_TOOLS:
        raise ValueError(f"Unsupported MCP tool: {tool}")

    if tool == "get_application_context":
        return await _get_application_context(params)
    if tool == "get_wave_context":
        return await _get_wave_context(params)
    if tool == "get_latest_job_context":
        return await _get_latest_job_context(params)
    if tool == "get_testing_context":
        return await _get_testing_context(params)
    if tool == "get_pmo_context":
        return await _get_pmo_context(params)
    if tool == "get_integration_context":
        return await _get_integration_context(params)
    if tool == "semantic_context_search":
        return await _semantic_context_search(params)
    if tool == "get_platform_kpis":
        return await _get_platform_kpis(params)

    raise ValueError(f"Unhandled MCP tool: {tool}")
