import json
from fastapi import APIRouter, HTTPException

from database import db, row2dict, rows2list
from services.agentic_orchestrator import run_specialist_agent

router = APIRouter()

_ALLOWED = {"servicenow", "sharepoint", "jenkins"}


@router.get("/services")
async def list_services():
    async with db() as conn:
        rows = await conn.execute_fetchall("SELECT * FROM integration_settings ORDER BY service")
    return {"count": len(rows), "items": rows2list(rows)}


@router.get("/{service}")
async def get_service(service: str):
    service = service.lower()
    if service not in _ALLOWED:
        raise HTTPException(status_code=404, detail="Unsupported integration service")
    async with db() as conn:
        row = await conn.execute_fetchone("SELECT * FROM integration_settings WHERE service=?", (service,))
    if not row:
        raise HTTPException(status_code=404, detail="Service not configured")
    return row2dict(row)


@router.post("/{service}")
async def save_service(service: str, payload: dict):
    service = service.lower()
    if service not in _ALLOWED:
        raise HTTPException(status_code=404, detail="Unsupported integration service")

    enabled = 1 if payload.get("enabled") else 0
    config = payload.get("config") or {}
    status = payload.get("status") or ("connected" if enabled else "disconnected")

    async with db() as conn:
        await conn.execute(
            """
            INSERT INTO integration_settings(service, enabled, config_json, status, last_sync)
            VALUES (?,?,?,?, datetime('now'))
            ON CONFLICT(service) DO UPDATE SET
              enabled=excluded.enabled,
              config_json=excluded.config_json,
              status=excluded.status,
              last_sync=datetime('now')
            """,
            (service, enabled, json.dumps(config), status),
        )
        await conn.commit()

    return {"saved": True, "service": service, "enabled": bool(enabled), "status": status}


@router.post("/{service}/test")
async def test_service(service: str):
    service = service.lower()
    if service not in _ALLOWED:
        raise HTTPException(status_code=404, detail="Unsupported integration service")

    async with db() as conn:
        row = await conn.execute_fetchone("SELECT * FROM integration_settings WHERE service=?", (service,))
    if not row:
        raise HTTPException(status_code=404, detail="Service not configured")

    item = row2dict(row)
    message = f"{service} configuration present"
    try:
        result = await run_specialist_agent(
            agent="integration",
            objective=f"Validate integration readiness and provide diagnostics for {service}",
            context={"service": service, "enabled": bool(item.get("enabled")), "status": item.get("status")},
            mcp_calls=[{"tool": "get_integration_context", "args": {"service": service}}],
            max_words=120,
        )
        message = str(result.get("reply") or "").strip() or message
    except Exception:
        message = f"{service} configuration present"

    return {
        "service": service,
        "ok": bool(item.get("enabled")),
        "status": "connected" if item.get("enabled") else "disconnected",
        "message": message,
        "provider": "ollama",
        "mcp_enabled": True,
    }
