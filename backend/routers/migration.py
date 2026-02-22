import json
import uuid
from fastapi import APIRouter, HTTPException

from database import db, row2dict, rows2list
from models import ApprovalRequest, MigrationRunRequest
from services.agentic_orchestrator import orchestrate_workload, run_specialist_agent_json
from services.migration_planner import (
    build_diff_payload,
    generate_jenkinsfile,
    generate_pipeline_yaml,
    generate_terraform,
    get_architecture,
)

router = APIRouter()


@router.get("/waves")
async def list_waves():
    async with db() as conn:
        rows = await conn.execute_fetchall("SELECT * FROM migration_waves ORDER BY id")
    return {"count": len(rows), "items": rows2list(rows)}


@router.get("/waves/{wave_id}")
async def get_wave(wave_id: str):
    async with db() as conn:
        row = await conn.execute_fetchone("SELECT * FROM migration_waves WHERE id=?", (wave_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Wave not found")
    return row2dict(row)


@router.post("/waves/{wave_id}/start")
async def start_wave(wave_id: str):
    async with db() as conn:
        await conn.execute(
            "UPDATE migration_waves SET status='active', started_at=COALESCE(started_at, datetime('now')) WHERE id=?",
            (wave_id,),
        )
        await conn.commit()
    return {"wave_id": wave_id, "status": "active"}


@router.get("/agents")
async def pattern_agents():
    return {
        "agents": [
            {"pattern": "P1", "name": "GCE Replatform Agent", "provider": "ollama", "mcp_enabled": True},
            {"pattern": "P2", "name": "Load Balancer Agent", "provider": "ollama", "mcp_enabled": True},
            {"pattern": "P3", "name": "Database Rebuild Agent", "provider": "ollama", "mcp_enabled": True},
            {"pattern": "P4", "name": "PCF to GKE Agent", "provider": "ollama", "mcp_enabled": True},
            {"pattern": "P5", "name": "Messaging Rebuild Agent", "provider": "ollama", "mcp_enabled": True},
        ]
    }


@router.post("/run")
async def run_migration(req: MigrationRunRequest):
    async with db() as conn:
        app_row = await conn.execute_fetchone("SELECT * FROM applications WHERE id=?", (req.app_id,))
        if not app_row:
            raise HTTPException(status_code=404, detail="Application not found")

        app = row2dict(app_row)
        pattern = req.pattern or app.get("pattern_id") or "P1"

        orchestration = await orchestrate_workload(
            objective=f"Execute migration plan for app {app.get('name', req.app_id)} with pattern {pattern}",
            tasks=[
                {
                    "agent": "migration",
                    "objective": "Create migration approach, key architecture decisions, and infra transformation strategy.",
                    "context": {"app_id": req.app_id, "pattern": pattern, "wave_id": req.wave_id},
                    "mcp_calls": [
                        {"tool": "get_application_context", "args": {"app_id": req.app_id}},
                        {"tool": "get_wave_context", "args": {"wave_id": req.wave_id or ""}},
                    ],
                    "max_words": 180,
                },
                {
                    "agent": "testing",
                    "objective": "Generate quality gates and release readiness checks for this migration.",
                    "context": {"app_id": req.app_id, "pattern": pattern},
                    "mcp_calls": [{"tool": "get_testing_context", "args": {"app_id": req.app_id}}],
                    "max_words": 140,
                },
                {
                    "agent": "pmo",
                    "objective": "Assess governance risks and stakeholder actions for go-live.",
                    "context": {"app_id": req.app_id, "pattern": pattern},
                    "mcp_calls": [{"tool": "get_pmo_context", "args": {}}],
                    "max_words": 140,
                },
            ],
            shared_context={"app_id": req.app_id, "pattern": pattern, "wave_id": req.wave_id},
        )

        artifact_schema = {
            "terraform_hcl": "string",
            "pipeline_yaml": "string",
            "jenkinsfile": "string",
            "gcp_architecture": {
                "target": "string",
                "services": ["string"],
                "network": "string",
            },
            "changed_files": [
                {"path": "string", "change": "string", "reason": "string"}
            ],
        }

        terraform_hcl = ""
        pipeline_yaml = ""
        jenkinsfile = ""
        gcp_arch = {}
        changed_files = []
        try:
            structured = await run_specialist_agent_json(
                agent="migration",
                objective=(
                    f"Generate implementation artifacts for {app.get('name', req.app_id)} using pattern {pattern}. "
                    "Return practical Terraform, GitHub Actions workflow, Jenkinsfile, and changed file list."
                ),
                schema_hint=artifact_schema,
                context={
                    "app_id": req.app_id,
                    "pattern": pattern,
                    "app_name": app.get("name", req.app_id),
                    "orchestrator_summary": orchestration.get("summary"),
                },
                mcp_calls=[
                    {"tool": "get_application_context", "args": {"app_id": req.app_id}},
                    {"tool": "get_wave_context", "args": {"wave_id": req.wave_id or ""}},
                    {"tool": "get_latest_job_context", "args": {"app_id": req.app_id}},
                ],
            )
            data = structured.get("data") or {}
            terraform_hcl = str(data.get("terraform_hcl") or "").strip()
            pipeline_yaml = str(data.get("pipeline_yaml") or "").strip()
            jenkinsfile = str(data.get("jenkinsfile") or "").strip()
            gcp_arch = data.get("gcp_architecture") if isinstance(data.get("gcp_architecture"), dict) else {}
            changed_files = data.get("changed_files") if isinstance(data.get("changed_files"), list) else []
        except Exception:
            terraform_hcl = ""

        if not terraform_hcl:
            terraform_hcl = generate_terraform(app.get("name", req.app_id), pattern)
        if not pipeline_yaml:
            pipeline_yaml = generate_pipeline_yaml(app.get("name", req.app_id), pattern)
        if not jenkinsfile:
            jenkinsfile = generate_jenkinsfile(app.get("name", req.app_id), pattern)
        if not gcp_arch:
            gcp_arch = get_architecture(pattern)

        diff_payload = build_diff_payload(pattern)
        if changed_files:
            diff_payload["changed_files"] = changed_files

        job_id = f"JOB-{uuid.uuid4().hex[:10]}"
        await conn.execute(
            """
            INSERT INTO migration_jobs(
              id, wave_id, app_id, pattern_id, status, progress,
              terraform_hcl, pipeline_yaml, jenkinsfile, gcp_arch_json, diff_json,
              started_at, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))
            """,
            (
                job_id,
                req.wave_id,
                req.app_id,
                pattern,
                "awaiting_approval",
                100.0,
                terraform_hcl,
                pipeline_yaml,
                jenkinsfile,
                json.dumps(gcp_arch),
                json.dumps(diff_payload),
            ),
        )

        artifact_id = f"TFA-{uuid.uuid4().hex[:8]}"
        await conn.execute(
            """
            INSERT INTO terraform_artifacts(
              id, app_id, pattern_id, hcl_main, hcl_variables, hcl_outputs,
              github_actions_yaml, jenkinsfile, changed_files_json, generated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,datetime('now'))
            """,
            (
                artifact_id,
                req.app_id,
                pattern,
                terraform_hcl,
                "variable \"project_id\" { type = string }\nvariable \"region\" { type = string }",
                "output \"target\" { value = \"gcp\" }",
                pipeline_yaml,
                jenkinsfile,
                json.dumps(diff_payload.get("changed_files", [])),
            ),
        )

        await conn.commit()

    return {
        "job_id": job_id,
        "status": "awaiting_approval",
        "app_id": req.app_id,
        "pattern": pattern,
        "destination_architecture": gcp_arch,
        "changed_files": diff_payload.get("changed_files", []),
        "agentic": {
            "orchestrator": "enabled",
            "provider": "ollama",
            "summary": orchestration.get("summary"),
        },
    }


@router.get("/jobs/{job_id}")
async def get_job(job_id: str):
    async with db() as conn:
        row = await conn.execute_fetchone("SELECT * FROM migration_jobs WHERE id=?", (job_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Migration job not found")
    return row2dict(row)


@router.post("/terraform/{app_id}")
async def generate_tf(app_id: str):
    async with db() as conn:
        row = await conn.execute_fetchone("SELECT name, pattern_id FROM applications WHERE id=?", (app_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Application not found")
    name, pattern = row[0], row[1] or "P1"
    terraform_hcl = ""
    try:
        structured = await run_specialist_agent_json(
            agent="migration",
            objective=f"Generate Terraform for app {name} with pattern {pattern}",
            schema_hint={"terraform_hcl": "string"},
            context={"app_id": app_id, "app_name": name, "pattern": pattern},
            mcp_calls=[{"tool": "get_application_context", "args": {"app_id": app_id}}],
        )
        terraform_hcl = str((structured.get("data") or {}).get("terraform_hcl") or "").strip()
    except Exception:
        terraform_hcl = ""
    if not terraform_hcl:
        terraform_hcl = generate_terraform(name, pattern)
    return {"app_id": app_id, "pattern": pattern, "terraform": terraform_hcl}


@router.post("/pipeline/{app_id}")
async def generate_pipeline(app_id: str):
    async with db() as conn:
        row = await conn.execute_fetchone("SELECT name, pattern_id FROM applications WHERE id=?", (app_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Application not found")
    name, pattern = row[0], row[1] or "P1"
    github_actions = ""
    jenkinsfile = ""
    try:
        structured = await run_specialist_agent_json(
            agent="migration",
            objective=f"Generate CI/CD pipeline for app {name} with pattern {pattern}",
            schema_hint={"github_actions": "string", "jenkinsfile": "string"},
            context={"app_id": app_id, "app_name": name, "pattern": pattern},
            mcp_calls=[{"tool": "get_application_context", "args": {"app_id": app_id}}],
        )
        data = structured.get("data") or {}
        github_actions = str(data.get("github_actions") or "").strip()
        jenkinsfile = str(data.get("jenkinsfile") or "").strip()
    except Exception:
        github_actions = ""
        jenkinsfile = ""
    if not github_actions:
        github_actions = generate_pipeline_yaml(name, pattern)
    if not jenkinsfile:
        jenkinsfile = generate_jenkinsfile(name, pattern)

    return {
        "app_id": app_id,
        "pattern": pattern,
        "github_actions": github_actions,
        "jenkinsfile": jenkinsfile,
    }


@router.post("/approve/{job_id}")
async def approve(job_id: str, req: ApprovalRequest):
    status = "approved" if req.approve else "failed"
    async with db() as conn:
        await conn.execute(
            """
            UPDATE migration_jobs
            SET status=?, approved_by=?, approval_comment=?, completed_at=CASE WHEN ?='approved' THEN datetime('now') ELSE completed_at END
            WHERE id=?
            """,
            (status, req.approved_by, req.comment, status, job_id),
        )
        await conn.commit()
    return {"job_id": job_id, "status": status, "approved_by": req.approved_by, "comment": req.comment}


@router.get("/diff/{app_id}")
async def migration_diff(app_id: str):
    async with db() as conn:
        row = await conn.execute_fetchone(
            "SELECT id, diff_json, gcp_arch_json FROM migration_jobs WHERE app_id=? ORDER BY created_at DESC LIMIT 1",
            (app_id,),
        )
    if not row:
        raise HTTPException(status_code=404, detail="No migration job found for app")

    diff = json.loads(row[1] or "{}")
    arch = json.loads(row[2] or "{}")
    return {
        "app_id": app_id,
        "job_id": row[0],
        "destination_architecture": arch,
        "changed_files": diff.get("changed_files", []),
        "diff": diff,
    }


@router.post("/issue")
async def create_issue(payload: dict):
    return {
        "created": True,
        "source": "migration",
        "app_id": payload.get("app_id"),
        "error": payload.get("error"),
        "issue_id": f"MIG-{uuid.uuid4().hex[:8]}",
    }
