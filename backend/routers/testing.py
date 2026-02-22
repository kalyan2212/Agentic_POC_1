import uuid
from fastapi import APIRouter, HTTPException

from services.agentic_orchestrator import run_specialist_agent, run_specialist_agent_json

router = APIRouter()

_DEMO_SUITES = [
    {"id": "SMOKE", "name": "Smoke Tests", "type": "smoke"},
    {"id": "SIT", "name": "System Integration Tests", "type": "sit"},
    {"id": "PERF", "name": "Performance Tests", "type": "perf"},
    {"id": "UAT", "name": "UAT Tests", "type": "uat"},
]

_RUNS = {}


@router.get("/suites")
async def suites():
    return {"count": len(_DEMO_SUITES), "items": _DEMO_SUITES}


@router.post("/suites/{suite_id}/run")
async def run_suite(suite_id: str, payload: dict):
    app_id = payload.get("app_id")
    if not app_id:
        raise HTTPException(status_code=400, detail="app_id required")

    schema_hint = {
        "status": "complete",
        "passed": 0,
        "failed": 0,
        "skipped": 0,
        "quality_summary": "string",
    }

    passed = 0
    failed = 0
    skipped = 0
    status = "complete"
    quality_summary = ""
    try:
        structured = await run_specialist_agent_json(
            agent="testing",
            objective=f"Execute {suite_id} for app {app_id} and return realistic test outcome counts.",
            schema_hint=schema_hint,
            context={"suite_id": suite_id, "app_id": app_id},
            mcp_calls=[
                {"tool": "get_application_context", "args": {"app_id": app_id}},
                {"tool": "get_latest_job_context", "args": {"app_id": app_id}},
                {"tool": "get_testing_context", "args": {"app_id": app_id}},
            ],
        )
        data = structured.get("data") or {}
        passed = max(0, int(data.get("passed") or 0))
        failed = max(0, int(data.get("failed") or 0))
        skipped = max(0, int(data.get("skipped") or 0))
        status = str(data.get("status") or "complete")
        quality_summary = str(data.get("quality_summary") or "").strip()
    except Exception:
        failed = 1 if suite_id in {"SIT", "PERF"} else 0
        passed = 20 if failed == 0 else 14
        skipped = 1

    run_id = f"RUN-{uuid.uuid4().hex[:10]}"
    _RUNS[run_id] = {
        "run_id": run_id,
        "suite_id": suite_id,
        "app_id": app_id,
        "status": status,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "quality_summary": quality_summary,
        "provider": "ollama",
        "mcp_enabled": True,
    }
    return _RUNS[run_id]


@router.get("/runs/{run_id}")
async def run_status(run_id: str):
    if run_id not in _RUNS:
        raise HTTPException(status_code=404, detail="run not found")
    return _RUNS[run_id]


@router.get("/results/{app_id}")
async def results(app_id: str):
    out = [r for r in _RUNS.values() if r.get("app_id") == app_id]
    return {"app_id": app_id, "count": len(out), "items": out}


@router.post("/run-all")
async def run_all(payload: dict):
    app_id = payload.get("app_id")
    if not app_id:
        raise HTTPException(status_code=400, detail="app_id required")

    all_plan = None
    try:
        all_plan = await run_specialist_agent_json(
            agent="testing",
            objective=(
                f"Run all suites for app {app_id}. Return a JSON object with key suites, "
                "where suites is an array of objects containing suite_id, passed, failed, skipped, and status."
            ),
            schema_hint={
                "suites": [
                    {"suite_id": "SMOKE", "passed": 0, "failed": 0, "skipped": 0, "status": "complete"}
                ],
                "overall_summary": "string",
            },
            context={"app_id": app_id, "suite_ids": [s["id"] for s in _DEMO_SUITES]},
            mcp_calls=[
                {"tool": "get_application_context", "args": {"app_id": app_id}},
                {"tool": "get_testing_context", "args": {"app_id": app_id}},
            ],
        )
    except Exception:
        all_plan = None

    plan_map = {}
    if all_plan:
        suites = (all_plan.get("data") or {}).get("suites") or []
        for item in suites:
            if isinstance(item, dict) and item.get("suite_id"):
                plan_map[str(item.get("suite_id"))] = item

    results = []
    for suite in _DEMO_SUITES:
        rid = f"RUN-{uuid.uuid4().hex[:10]}"
        proposed = plan_map.get(suite["id"], {})
        rec = {
            "run_id": rid,
            "suite_id": suite["id"],
            "app_id": app_id,
            "status": str(proposed.get("status") or "complete"),
            "passed": max(0, int(proposed.get("passed") or 20)),
            "failed": max(0, int(proposed.get("failed") or 0)),
            "skipped": max(0, int(proposed.get("skipped") or 0)),
            "provider": "ollama",
            "mcp_enabled": True,
        }
        _RUNS[rid] = rec
        results.append(rec)

    summary = ""
    try:
        agg = await run_specialist_agent(
            agent="testing",
            objective=f"Summarize test execution results for app {app_id}",
            context={"app_id": app_id, "results": results},
            mcp_calls=[{"tool": "get_testing_context", "args": {"app_id": app_id}}],
            max_words=140,
        )
        summary = str(agg.get("reply") or "").strip()
    except Exception:
        summary = ""

    return {"app_id": app_id, "items": results, "summary": summary}


@router.post("/issue")
async def create_issue(payload: dict):
    return {
        "created": True,
        "source": "testing",
        "test_id": payload.get("test_id"),
        "failure": payload.get("failure"),
        "issue_id": f"TEST-{uuid.uuid4().hex[:8]}",
    }


@router.get("/synthetic-data/{app_id}")
async def synthetic_data(app_id: str):
    return {
        "app_id": app_id,
        "datasets": [
            {"name": "users", "records": 500},
            {"name": "orders", "records": 1200},
            {"name": "products", "records": 80},
        ],
    }
