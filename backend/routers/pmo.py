from fastapi import APIRouter

from database import db, rows2list
from models import ReportRequest
from services.agentic_orchestrator import orchestrate_workload, run_specialist_agent_json

router = APIRouter()


@router.get("/dashboard")
async def dashboard():
    async with db() as conn:
        apps = await conn.execute_fetchone("SELECT COUNT(*) FROM applications")
        migrated = await conn.execute_fetchone("SELECT COUNT(*) FROM migration_jobs WHERE status IN ('approved','complete')")
        risks = await conn.execute_fetchone("SELECT COUNT(*) FROM pmo_risks WHERE rating IN ('critical','high') AND status='open'")
        budget = await conn.execute_fetchone("SELECT COALESCE(SUM(actual),0), COALESCE(SUM(planned),0) FROM pmo_budget")

    apps_n = apps[0] if apps else 0
    mig_n = migrated[0] if migrated else 0
    risk_n = risks[0] if risks else 0
    actual = float(budget[0] if budget else 0)
    planned = float(budget[1] if budget else 0)

    return {
        "total_apps": apps_n,
        "migrated_apps": mig_n,
        "migration_pct": round((mig_n / apps_n) * 100, 2) if apps_n else 0,
        "open_high_risks": risk_n,
        "budget_used_pct": round((actual / planned) * 100, 2) if planned else 0,
        "actual_spend": actual,
        "planned_spend": planned,
    }


@router.get("/phases")
async def phases():
    async with db() as conn:
        rows = await conn.execute_fetchall("SELECT * FROM migration_waves ORDER BY id")
    return {"count": len(rows), "items": rows2list(rows)}


@router.get("/risks")
async def risks():
    async with db() as conn:
        rows = await conn.execute_fetchall("SELECT * FROM pmo_risks ORDER BY id")
    return {"count": len(rows), "items": rows2list(rows)}


@router.get("/budget")
async def budget():
    async with db() as conn:
        rows = await conn.execute_fetchall("SELECT * FROM pmo_budget ORDER BY id")
    return {"count": len(rows), "items": rows2list(rows)}


@router.get("/timeline")
async def timeline():
    return {
        "milestones": [
            {"name": "Wave 1 complete", "planned": "2026-03-15", "status": "planned"},
            {"name": "Wave 2 complete", "planned": "2026-04-30", "status": "planned"},
            {"name": "Cutover", "planned": "2026-06-01", "status": "planned"},
        ]
    }


@router.get("/reports/{report_type}")
async def get_report(report_type: str):
    summary = ""
    try:
        structured = await run_specialist_agent_json(
            agent="pmo",
            objective=f"Create executive summary for report type {report_type}",
            schema_hint={"summary": "string"},
            context={"report_type": report_type},
            mcp_calls=[{"tool": "get_pmo_context", "args": {}}],
        )
        summary = str((structured.get("data") or {}).get("summary") or "").strip()
    except Exception:
        summary = "Use /pmo/reports/generate to generate a fresh report."

    return {
        "type": report_type,
        "summary": summary or "Use /pmo/reports/generate to generate a fresh report.",
    }


@router.post("/reports/generate")
async def generate_report(req: ReportRequest):
    orchestration = await orchestrate_workload(
        objective=f"Generate {req.type} report for period {req.period}",
        tasks=[
            {
                "agent": "pmo",
                "objective": "Provide executive summary, risk posture and budget callouts.",
                "context": {"report_type": req.type, "period": req.period},
                "mcp_calls": [{"tool": "get_pmo_context", "args": {}}],
                "max_words": 180,
            },
            {
                "agent": "migration",
                "objective": "Summarize migration delivery progress and blockers.",
                "context": {"report_type": req.type, "period": req.period},
                "mcp_calls": [{"tool": "get_wave_context", "args": {}}],
                "max_words": 130,
            },
        ],
        shared_context={"report_type": req.type, "period": req.period},
    )

    sections = [
        "Executive Summary",
        "Wave Progress",
        "Top Risks",
        "Budget Health",
    ]
    try:
        structured = await run_specialist_agent_json(
            agent="pmo",
            objective="Return a JSON report payload with title and sections list",
            schema_hint={"title": "string", "sections": ["string"]},
            context={
                "report_type": req.type,
                "period": req.period,
                "orchestrator_summary": orchestration.get("summary"),
            },
            mcp_calls=[{"tool": "get_pmo_context", "args": {}}],
        )
        data = structured.get("data") or {}
        title = str(data.get("title") or f"{req.type.title()} Report")
        returned_sections = data.get("sections") if isinstance(data.get("sections"), list) else []
        sections = [str(s) for s in returned_sections if str(s).strip()] or sections
    except Exception:
        title = f"{req.type.title()} Report"

    return {
        "generated": True,
        "type": req.type,
        "period": req.period,
        "title": title,
        "sections": sections,
        "agentic_summary": orchestration.get("summary"),
        "provider": "ollama",
    }
