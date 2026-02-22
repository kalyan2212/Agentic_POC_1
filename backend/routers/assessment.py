import json
import uuid
from datetime import datetime
from typing import List

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query

from database import db, row2dict, rows2list
from models import ScanRequest
from services.classifier import PATTERNS, classify_repo
from services.agentic_orchestrator import run_specialist_agent_json
from services.embeddings import cosine_similarity, embed_text
from services.github_client import (
    get_repo,
    get_repo_tree,
    get_readme,
    parse_full_name,
    get_file_content,
)

router = APIRouter()


async def _get_latest_token() -> str | None:
    async with db() as conn:
        row = await conn.execute_fetchone("SELECT token FROM github_tokens ORDER BY connected_at DESC LIMIT 1")
        return row[0] if row else None


async def _upsert_application(conn, app: dict):
    await conn.execute(
        """
        INSERT INTO applications(
            id, scan_run_id, name, repo_full_name, language, framework, loc, complexity,
            risk_score, pattern_id, pattern_name, gcp_target, has_dockerfile, has_terraform,
            has_jenkinsfile, has_github_actions, has_pcf, has_db, has_messaging,
            db_types_json, dependencies_json, files_json, findings_json
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
            scan_run_id=excluded.scan_run_id,
            name=excluded.name,
            repo_full_name=excluded.repo_full_name,
            language=excluded.language,
            framework=excluded.framework,
            loc=excluded.loc,
            complexity=excluded.complexity,
            risk_score=excluded.risk_score,
            pattern_id=excluded.pattern_id,
            pattern_name=excluded.pattern_name,
            gcp_target=excluded.gcp_target,
            has_dockerfile=excluded.has_dockerfile,
            has_terraform=excluded.has_terraform,
            has_jenkinsfile=excluded.has_jenkinsfile,
            has_github_actions=excluded.has_github_actions,
            has_pcf=excluded.has_pcf,
            has_db=excluded.has_db,
            has_messaging=excluded.has_messaging,
            db_types_json=excluded.db_types_json,
            dependencies_json=excluded.dependencies_json,
            files_json=excluded.files_json,
            findings_json=excluded.findings_json
        """,
        (
            app["id"], app["scan_run_id"], app["name"], app["repo_full_name"], app["language"], app["framework"],
            app["loc"], app["complexity"], app["risk_score"], app["pattern_id"], app["pattern_name"],
            app["gcp_target"], app["has_dockerfile"], app["has_terraform"], app["has_jenkinsfile"],
            app["has_github_actions"], app["has_pcf"], app["has_db"], app["has_messaging"],
            json.dumps(app["db_types"]), json.dumps(app["dependencies"]), json.dumps(app["files"]), json.dumps(app["findings"]),
        ),
    )


async def _set_scan_progress(conn, run_id: str, *, status: str, stage: str, progress: int, summary_extra: dict | None = None):
    summary = {"stage": stage, "progress": progress}
    if summary_extra:
        summary.update(summary_extra)
    await conn.execute(
        "UPDATE scan_runs SET status=?, summary_json=? WHERE id=?",
        (status, json.dumps(summary), run_id),
    )
    await conn.commit()


def _diagnose_scan_error(exc: Exception) -> dict:
    raw = str(exc) or "Unknown scan error"
    lowered = raw.lower()

    if "409" in lowered and "conflict" in lowered:
        return {
            "error_code": "REPO_CONFLICT",
            "failure_reason": "One or more selected repositories cannot be scanned (commonly empty repos without commits).",
            "remediation": "Remove empty repositories from selection or create an initial commit (for example a README) and rerun.",
            "raw_error": raw,
        }
    if "403" in lowered and ("rate limit" in lowered or "api rate limit" in lowered):
        return {
            "error_code": "GITHUB_RATE_LIMIT",
            "failure_reason": "GitHub API rate limit exceeded for the current token.",
            "remediation": "Wait for GitHub rate limit reset or use a token with higher quota and rerun the scan.",
            "raw_error": raw,
        }
    if "401" in lowered or "unauthorized" in lowered:
        return {
            "error_code": "GITHUB_AUTH",
            "failure_reason": "GitHub authentication failed while scanning repositories.",
            "remediation": "Reconnect GitHub with a valid token that has repository read permissions.",
            "raw_error": raw,
        }
    if "404" in lowered or "not found" in lowered:
        return {
            "error_code": "REPO_NOT_FOUND",
            "failure_reason": "One or more selected repositories were not found or are inaccessible.",
            "remediation": "Verify repository names and token access permissions, then rerun the scan.",
            "raw_error": raw,
        }
    return {
        "error_code": "SCAN_FAILED",
        "failure_reason": "Unexpected failure occurred during orchestrated scan execution.",
        "remediation": "Check selected repositories and integration connectivity, then retry the scan.",
        "raw_error": raw,
    }


async def _save_embeddings(conn, app_id: str, files: List[str], sampled_texts: List[str]):
    full_text = "\n".join(sampled_texts)
    emb = embed_text(full_text)
    await conn.execute(
        "INSERT OR REPLACE INTO app_embeddings(app_id, embedding_json, model, created_at) VALUES (?,?,?,datetime('now'))",
        (app_id, json.dumps(emb), "hash-384"),
    )

    for idx, text in enumerate(sampled_texts[:50]):
        await conn.execute(
            "INSERT INTO code_chunks(app_id,file_path,chunk_text,chunk_index,embedding_json,created_at) VALUES (?,?,?,?,?,datetime('now'))",
            (app_id, files[idx] if idx < len(files) else f"chunk_{idx}", text[:2000], idx, json.dumps(embed_text(text))),
        )


def _framework_from_files(files: List[str]) -> str:
    lower = "\n".join(files).lower()
    if "pom.xml" in lower or "build.gradle" in lower:
        return "spring"
    if "package.json" in lower:
        return "node"
    if "requirements.txt" in lower or ".py" in lower:
        return "python"
    if ".csproj" in lower:
        return ".net"
    return "unknown"


def _parse_findings(findings_raw) -> list:
    if findings_raw is None:
        return []
    if isinstance(findings_raw, list):
        return findings_raw
    if isinstance(findings_raw, dict):
        return [findings_raw]
    if isinstance(findings_raw, str):
        try:
            parsed = json.loads(findings_raw)
            if isinstance(parsed, list):
                return parsed
            if isinstance(parsed, dict):
                return [parsed]
        except Exception:
            return []
    return []


def _extract_integrations(findings_raw) -> tuple[list[str], list[str]]:
    findings = _parse_findings(findings_raw)
    app_targets: list[str] = []
    db_targets: list[str] = []
    for item in findings:
        if not isinstance(item, dict):
            continue
        ftype = str(item.get("type", "")).lower()
        if ftype == "app_to_app_integration":
            targets = item.get("targets") or []
            if isinstance(targets, list):
                app_targets.extend([str(t) for t in targets if t])
        if ftype == "app_to_db_integration":
            stores = item.get("datastores") or []
            if isinstance(stores, list):
                db_targets.extend([str(d).lower() for d in stores if d and str(d).lower() != "none"])
    return app_targets, db_targets


def _extract_integration_model(findings_raw) -> dict:
    findings = _parse_findings(findings_raw)
    app_links = []
    db_links = []
    tags = []
    for item in findings:
        if not isinstance(item, dict):
            continue
        ftype = str(item.get("type", "")).lower()
        if ftype == "metadata":
            raw_tags = item.get("tags") or []
            if isinstance(raw_tags, list):
                tags.extend([str(t).lower() for t in raw_tags if t])
        elif ftype == "app_to_app_integration":
            points = item.get("integration_points")
            if isinstance(points, list) and points:
                for p in points:
                    if isinstance(p, dict) and p.get("target"):
                        app_links.append(
                            {
                                "target": str(p.get("target")),
                                "coupling": str(p.get("coupling") or item.get("coupling") or "loose").lower(),
                            }
                        )
            else:
                for t in item.get("targets") or []:
                    app_links.append(
                        {
                            "target": str(t),
                            "coupling": str(item.get("coupling") or "loose").lower(),
                        }
                    )
        elif ftype == "app_to_db_integration":
            coupling = str(item.get("coupling") or "loose").lower()
            for d in item.get("datastores") or []:
                ds = str(d).lower()
                if ds and ds != "none":
                    db_links.append({"datastore": ds, "coupling": coupling})
    return {"app_links": app_links, "db_links": db_links, "tags": sorted(set(tags))}


def _apply_pattern_instructions(classified_pattern: str, content_sample: str, instructions: dict) -> str:
    content = (content_sample or "").lower()
    scores = {pid: 0 for pid in ["P1", "P2", "P3", "P4", "P5"]}
    for pid, text in instructions.items():
        if not text:
            continue
        keywords = [k.strip().lower() for k in text.split(",") if k.strip()]
        for kw in keywords[:50]:
            if kw in content:
                scores[pid] += 1

    best = max(scores, key=lambda k: scores[k])
    if scores.get(best, 0) >= 2:
        return best
    return classified_pattern


async def _run_scan_job(run_id: str, repos: List[str]):
    token = await _get_latest_token()
    if not token:
        async with db() as conn:
            summary = {
                "stage": "Scan failed",
                "progress": 100,
                "error_code": "GITHUB_NOT_CONNECTED",
                "failure_reason": "GitHub token is missing for scan execution.",
                "remediation": "Connect GitHub first, then restart the scan.",
            }
            await conn.execute(
                "UPDATE scan_runs SET status='failed', error_msg=?, summary_json=?, completed_at=datetime('now') WHERE id=?",
                ("GitHub not connected", json.dumps(summary), run_id),
            )
            await conn.commit()
        return

    try:
        async with db() as conn:
            inst_rows = await conn.execute_fetchall("SELECT pattern_id, instructions FROM pattern_instructions")
            pattern_instructions = {r[0]: r[1] for r in inst_rows}

            await _set_scan_progress(conn, run_id, status="running", stage="Initializing scan", progress=5)

            apps = []
            total_loc = 0
            total = max(len(repos), 1)

            for idx, full_name in enumerate(repos, start=1):
                pct_base = int(((idx - 1) / total) * 90)
                await _set_scan_progress(conn, run_id, status="running", stage=f"Analyzing {full_name}", progress=max(8, pct_base))

                owner, repo = parse_full_name(full_name)
                meta = get_repo(owner, repo, token)
                branch = meta.get("default_branch", "main")
                tree = get_repo_tree(owner, repo, branch, token)
                files = [x.get("path") for x in tree if x.get("type") == "blob"][:1500]

                interesting = [
                    p for p in files if p.endswith((".py", ".js", ".ts", ".java", ".tf", ".yml", ".yaml", ".json", ".md", "Jenkinsfile"))
                ][:40]

                sampled_texts = []
                for p in interesting:
                    try:
                        sampled_texts.append(get_file_content(owner, repo, p, token)[:4000])
                    except Exception:
                        continue

                if not sampled_texts:
                    sampled_texts.append(get_readme(owner, repo, token)[:4000])

                content_sample = "\n\n".join(sampled_texts)
                result = classify_repo(files=files, content_sample=content_sample)
                selected_pattern = _apply_pattern_instructions(result.pattern_id, content_sample, pattern_instructions)
                if selected_pattern != result.pattern_id:
                    result.pattern_id = selected_pattern
                    result.pattern_name = PATTERNS[selected_pattern].name
                    result.gcp_target = PATTERNS[selected_pattern].gcp_target

                app_id = f"{run_id[:8]}-APP-{idx:03d}"
                framework = _framework_from_files(files)
                loc = sum(len(t.splitlines()) for t in sampled_texts)
                total_loc += loc

                deps = []
                if any("package.json" in f for f in files):
                    deps.append("npm")
                if any("requirements.txt" in f for f in files):
                    deps.append("pip")
                if any("pom.xml" in f for f in files):
                    deps.append("maven")
                if any("build.gradle" in f for f in files):
                    deps.append("gradle")

                db_types = []
                blob = ("\n".join(files) + "\n" + content_sample).lower()
                if "postgres" in blob:
                    db_types.append("postgres")
                if "mysql" in blob:
                    db_types.append("mysql")
                if "mssql" in blob or "sqlserver" in blob:
                    db_types.append("mssql")

                app = {
                    "id": app_id,
                    "scan_run_id": run_id,
                    "name": repo,
                    "repo_full_name": full_name,
                    "language": meta.get("language") or "unknown",
                    "framework": framework,
                    "loc": loc,
                    "complexity": result.complexity,
                    "risk_score": result.risk_score,
                    "pattern_id": result.pattern_id,
                    "pattern_name": result.pattern_name,
                    "gcp_target": result.gcp_target,
                    "has_dockerfile": 1 if any("dockerfile" in f.lower() for f in files) else 0,
                    "has_terraform": 1 if any(f.endswith(".tf") for f in files) else 0,
                    "has_jenkinsfile": 1 if any("jenkinsfile" in f.lower() for f in files) else 0,
                    "has_github_actions": 1 if any(".github/workflows" in f.lower() for f in files) else 0,
                    "has_pcf": 1 if any("manifest.yml" in f.lower() for f in files) else 0,
                    "has_db": 1 if bool(db_types) else 0,
                    "has_messaging": 1 if any(k in blob for k in ["service bus", "event hub", "queue", "topic", "pub/sub", "pubsub"]) else 0,
                    "db_types": db_types,
                    "dependencies": deps,
                    "files": files[:300],
                    "findings": result.findings,
                }
                await _upsert_application(conn, app)
                await _save_embeddings(conn, app_id, interesting, sampled_texts)
                apps.append(app)

                pct_done = int((idx / total) * 90)
                await _set_scan_progress(conn, run_id, status="running", stage=f"Processed {idx}/{total} repositories", progress=max(10, pct_done))

            summary = {
                "repo_count": len(repos),
                "app_count": len(apps),
                "total_loc": total_loc,
                "stage": "Scan complete",
                "progress": 100,
                "completed_at": datetime.utcnow().isoformat() + "Z",
            }

            await conn.execute(
                "UPDATE scan_runs SET status='complete', completed_at=datetime('now'), summary_json=? WHERE id=?",
                (json.dumps(summary), run_id),
            )
            await conn.commit()
    except Exception as e:
        diag = _diagnose_scan_error(e)
        summary = {
            "stage": "Scan failed",
            "progress": 100,
            "error_code": diag["error_code"],
            "failure_reason": diag["failure_reason"],
            "remediation": diag["remediation"],
        }
        async with db() as conn:
            await conn.execute(
                "UPDATE scan_runs SET status='failed', error_msg=?, summary_json=?, completed_at=datetime('now') WHERE id=?",
                (diag["raw_error"], json.dumps(summary), run_id),
            )
            await conn.commit()


@router.get("/repos")
async def get_repos(user: str = Query(...)):
    token = await _get_latest_token()
    if not token:
        async with db() as conn:
            rows = await conn.execute_fetchall(
                "SELECT full_name, name, description, language, stars, default_branch FROM repos WHERE owner=? ORDER BY fetched_at DESC",
                (user,),
            )
        if rows:
            return {
                "user": user,
                "count": len(rows),
                "repos": [
                    {
                        "full_name": r[0],
                        "name": r[1],
                        "description": r[2],
                        "language": r[3],
                        "stars": r[4],
                        "default_branch": r[5] or "main",
                    }
                    for r in rows
                ],
            }
        raise HTTPException(status_code=400, detail="GitHub not connected. Connect token first.")

    from services.github_client import list_repos

    repos = list_repos(user, token)
    return {
        "user": user,
        "count": len(repos),
        "repos": [
            {
                "full_name": r.get("full_name"),
                "name": r.get("name"),
                "description": r.get("description"),
                "language": r.get("language"),
                "default_branch": r.get("default_branch", "main"),
            }
            for r in repos
        ],
    }


@router.post("/scan")
async def start_scan(req: ScanRequest, background_tasks: BackgroundTasks):
    if not req.repos:
        raise HTTPException(status_code=400, detail="Select at least one repository")

    if not await _get_latest_token():
        raise HTTPException(status_code=400, detail="GitHub not connected")

    async with db() as conn:
        active = await conn.execute_fetchone(
            "SELECT id FROM scan_runs WHERE status IN ('pending','running') ORDER BY started_at DESC LIMIT 1"
        )
    if active:
        raise HTTPException(
            status_code=409,
            detail=f"Another scan is already running (run_id={active[0]}). Wait for completion, then start a new scan.",
        )

    run_id = str(uuid.uuid4())
    async with db() as conn:
        await conn.execute(
            "INSERT INTO scan_runs(id,status,repos_json,started_at) VALUES (?,?,?,datetime('now'))",
            (run_id, "pending", json.dumps(req.repos)),
        )
        await conn.commit()

    background_tasks.add_task(_run_scan_job, run_id, req.repos)

    return {"run_id": run_id, "status": "pending", "message": "Scan queued and running in background"}


@router.get("/scan/{run_id}")
async def scan_status(run_id: str):
    async with db() as conn:
        row = await conn.execute_fetchone("SELECT * FROM scan_runs WHERE id=?", (run_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Scan run not found")
    data = row2dict(row)
    summary = data.get("summary_json") or {}
    data["stage"] = summary.get("stage", "Queued")
    data["progress"] = int(summary.get("progress", 0))
    data["error_code"] = summary.get("error_code")
    data["failure_reason"] = summary.get("failure_reason")
    data["remediation"] = summary.get("remediation")
    return data


@router.get("/scans")
async def list_scans(limit: int = 10):
    lim = max(1, min(int(limit), 100))
    async with db() as conn:
        rows = await conn.execute_fetchall(
            "SELECT id, status, repos_json, started_at, completed_at, summary_json, error_msg FROM scan_runs ORDER BY started_at DESC LIMIT ?",
            (lim,),
        )
    items = []
    for r in rows:
        repos = json.loads(r[2]) if r[2] else []
        summary = json.loads(r[5]) if r[5] else {}
        items.append(
            {
                "id": r[0],
                "status": r[1],
                "repos": repos,
                "started_at": r[3],
                "completed_at": r[4],
                "summary": summary,
                "error_msg": r[6],
            }
        )
    return {"count": len(items), "items": items}


@router.get("/applications")
async def list_applications(run_id: str | None = None):
    async with db() as conn:
        if run_id:
            rows = await conn.execute_fetchall("SELECT * FROM applications WHERE scan_run_id=? ORDER BY id", (run_id,))
        else:
            rows = await conn.execute_fetchall("SELECT * FROM applications ORDER BY created_at DESC")
    return {"count": len(rows), "items": rows2list(rows)}


@router.get("/applications/{app_id}")
async def get_application(app_id: str):
    async with db() as conn:
        row = await conn.execute_fetchone("SELECT * FROM applications WHERE id=?", (app_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Application not found")
    return row2dict(row)


@router.get("/graph/{run_id}")
async def dependency_graph(run_id: str):
    async with db() as conn:
        rows = await conn.execute_fetchall(
            "SELECT id, name, pattern_id, gcp_target, risk_score, findings_json FROM applications WHERE scan_run_id=?",
            (run_id,),
        )
    apps = rows2list(rows)
    nodes = []
    edges = []
    seen_nodes = set()
    app_ids = {a["id"] for a in apps}

    def add_node(node_id: str, **attrs):
        if node_id in seen_nodes:
            return
        seen_nodes.add(node_id)
        payload = {"id": node_id}
        payload.update(attrs)
        nodes.append(payload)

    for app in apps:
        risk = float(app.get("risk_score") or 0)
        add_node(
            app["id"],
            label=app["name"],
            type="app",
            pattern=app["pattern_id"],
            risk_score=risk,
            risk="high" if risk >= 70 else "medium" if risk >= 40 else "low",
            critical=risk >= 70,
        )
        target_id = f"gcp-{app['id']}"
        add_node(target_id, label=app.get("gcp_target") or "GCP", type="gcp")
        edges.append({"from": app["id"], "to": target_id, "type": "api", "label": "migrates_to"})

        model = _extract_integration_model(app.get("findings_json"))
        for link in model["app_links"]:
            target = link["target"]
            coupling = link.get("coupling", "loose")
            if target in app_ids:
                edges.append({
                    "from": app["id"],
                    "to": target,
                    "type": "app",
                    "label": "calls",
                    "coupling": coupling,
                    "weight": 3 if coupling == "tight" else 1,
                })
            else:
                ext_id = f"ext-{target}"
                add_node(ext_id, label=target, type="external_app")
                edges.append({"from": app["id"], "to": ext_id, "type": "app", "label": "calls", "coupling": coupling, "weight": 1})

        for db_link in model["db_links"]:
            datastore = db_link["datastore"]
            coupling = db_link.get("coupling", "loose")
            db_node = f"db-{datastore}"
            add_node(db_node, label=datastore.upper(), type="db")
            edges.append({
                "from": app["id"],
                "to": db_node,
                "type": "db",
                "label": "reads/writes",
                "coupling": coupling,
                "weight": 4 if coupling == "tight" else 2,
            })

    return {"run_id": run_id, "nodes": nodes, "edges": edges}


@router.get("/insights/{run_id}")
async def assessment_insights(run_id: str):
    async with db() as conn:
        rows = await conn.execute_fetchall(
            "SELECT id, name, pattern_id, risk_score, findings_json FROM applications WHERE scan_run_id=?",
            (run_id,),
        )

    apps = rows2list(rows)
    if not apps:
        return {
            "run_id": run_id,
            "app_count": 0,
            "avg_risk": 0,
            "pattern_distribution": [],
            "integration_summary": {"app_to_app": 0, "app_to_db": 0, "unique_datastores": []},
            "top_risks": [],
        }

    pattern_counts: dict[str, int] = {}
    app_to_app_count = 0
    app_to_db_count = 0
    datastores = set()

    for app in apps:
        pid = app.get("pattern_id") or "P1"
        pattern_counts[pid] = pattern_counts.get(pid, 0) + 1
        app_targets, db_targets = _extract_integrations(app.get("findings_json"))
        app_to_app_count += len(app_targets)
        app_to_db_count += len(db_targets)
        datastores.update(db_targets)

    avg_risk = round(sum(float(a.get("risk_score") or 0) for a in apps) / len(apps), 2)
    top_risks = sorted(
        [
            {"id": a["id"], "name": a["name"], "risk_score": float(a.get("risk_score") or 0), "pattern_id": a.get("pattern_id")}
            for a in apps
        ],
        key=lambda x: x["risk_score"],
        reverse=True,
    )[:5]

    return {
        "run_id": run_id,
        "app_count": len(apps),
        "avg_risk": avg_risk,
        "pattern_distribution": [{"pattern_id": k, "count": v} for k, v in sorted(pattern_counts.items())],
        "integration_summary": {
            "app_to_app": app_to_app_count,
            "app_to_db": app_to_db_count,
            "unique_datastores": sorted(datastores),
        },
        "top_risks": top_risks,
    }


@router.get("/bundles/{run_id}")
async def bundles(run_id: str):
    async with db() as conn:
        rows = await conn.execute_fetchall(
            "SELECT id,name,pattern_id,risk_score,findings_json FROM applications WHERE scan_run_id=?",
            (run_id,),
        )
    apps = rows2list(rows)
    if not apps:
        return {"run_id": run_id, "count": 0, "items": []}

    app_map = {a["id"]: a for a in apps}
    neighbors: dict[str, set[str]] = {a["id"]: set() for a in apps}
    affinity_score: dict[tuple[str, str], int] = {}

    for app in apps:
        model = _extract_integration_model(app.get("findings_json"))
        for link in model["app_links"]:
            target = link["target"]
            if target not in app_map:
                continue
            neighbors[app["id"]].add(target)
            neighbors[target].add(app["id"])
            key = tuple(sorted((app["id"], target)))
            affinity_score[key] = max(affinity_score.get(key, 0), 3 if link.get("coupling") == "tight" else 1)

        for db_link in model["db_links"]:
            if db_link.get("coupling") == "tight":
                for other in apps:
                    if other["id"] == app["id"]:
                        continue
                    other_model = _extract_integration_model(other.get("findings_json"))
                    other_dbs = {d["datastore"] for d in other_model["db_links"]}
                    if db_link["datastore"] in other_dbs:
                        neighbors[app["id"]].add(other["id"])
                        neighbors[other["id"]].add(app["id"])
                        key = tuple(sorted((app["id"], other["id"])))
                        affinity_score[key] = max(affinity_score.get(key, 0), 4)

    visited = set()
    components = []
    for app_id in neighbors:
        if app_id in visited:
            continue
        stack = [app_id]
        comp = []
        visited.add(app_id)
        while stack:
            cur = stack.pop()
            comp.append(cur)
            for nxt in neighbors[cur]:
                if nxt not in visited:
                    visited.add(nxt)
                    stack.append(nxt)
        components.append(comp)

    components.sort(key=lambda c: len(c), reverse=True)
    items = []
    for i, comp in enumerate(components, start=1):
        members = [app_map[x] for x in comp]
        pattern = max(
            {m.get("pattern_id", "P1"): sum(1 for x in members if x.get("pattern_id", "P1") == m.get("pattern_id", "P1")) for m in members},
            key=lambda k: {m.get("pattern_id", "P1"): sum(1 for x in members if x.get("pattern_id", "P1") == m.get("pattern_id", "P1")) for m in members}[k],
        )
        internal_pairs = [tuple(sorted((a, b))) for a in comp for b in comp if a < b]
        bundle_affinity = sum(affinity_score.get(p, 0) for p in internal_pairs)
        coupling = "tight" if bundle_affinity >= max(4, len(comp) * 2) else "loose"
        items.append(
            {
                "bundle_id": f"BUNDLE-{i:03d}",
                "pattern_id": pattern,
                "app_ids": [m["id"] for m in members],
                "apps": members,
                "avg_risk": round(sum(float(m.get("risk_score", 0)) for m in members) / max(len(members), 1), 2),
                "coupling": coupling,
                "affinity_score": bundle_affinity,
                "bundle_reason": "Tightly coupled via shared DB/integration" if coupling == "tight" else "Loosely coupled; can migrate in silos",
            }
        )
    return {"run_id": run_id, "count": len(items), "items": items}


@router.post("/bundles/{bundle_id}/approve")
async def approve_bundle(bundle_id: str, payload: dict):
    return {"bundle_id": bundle_id, "approved": bool(payload.get("approve", True))}


@router.get("/migration-plan/{bundle_id}")
async def migration_plan(bundle_id: str):
    plan = [
        "Provision target GCP infrastructure",
        "Migrate application artifacts",
        "Run SIT/UAT",
        "Cutover and monitor",
    ]
    try:
        structured = await run_specialist_agent_json(
            agent="assessment",
            objective=f"Create migration plan for bundle {bundle_id}",
            schema_hint={"plan": ["string"]},
            context={"bundle_id": bundle_id},
            mcp_calls=[
                {"tool": "semantic_context_search", "args": {"query": f"bundle {bundle_id} migration", "limit": 6}},
                {"tool": "get_platform_kpis", "args": {}},
            ],
        )
        data = structured.get("data") or {}
        returned_plan = data.get("plan") if isinstance(data.get("plan"), list) else []
        cleaned = [str(p).strip() for p in returned_plan if str(p).strip()]
        if cleaned:
            plan = cleaned
    except Exception:
        pass

    return {
        "bundle_id": bundle_id,
        "plan": plan,
        "provider": "ollama",
        "mcp_enabled": True,
    }


@router.post("/upload")
async def upload_repo():
    raise HTTPException(status_code=501, detail="Upload flow not implemented yet. Use GitHub repo scan.")


@router.post("/semantic-search")
async def semantic_search(payload: dict):
    query = (payload.get("query") or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="query required")

    q_vec = embed_text(query)
    async with db() as conn:
        rows = await conn.execute_fetchall("SELECT id, app_id, file_path, chunk_text, embedding_json FROM code_chunks")

    scored = []
    for row in rows:
        emb = json.loads(row[4]) if row[4] else []
        score = cosine_similarity(q_vec, emb)
        scored.append({
            "id": row[0],
            "app_id": row[1],
            "file_path": row[2],
            "chunk_text": row[3],
            "score": round(float(score), 4),
        })

    scored.sort(key=lambda x: x["score"], reverse=True)
    return {"query": query, "results": scored[:10]}


@router.get("/pattern-instructions")
async def get_pattern_instructions():
    async with db() as conn:
        rows = await conn.execute_fetchall("SELECT pattern_id, instructions, updated_at FROM pattern_instructions ORDER BY pattern_id")
    return {
        "items": [
            {"pattern_id": r[0], "instructions": r[1], "updated_at": r[2]}
            for r in rows
        ]
    }


@router.post("/pattern-instructions")
async def save_pattern_instructions(payload: dict):
    pattern_id = (payload.get("pattern_id") or "").upper()
    instructions = payload.get("instructions") or ""
    if pattern_id not in {"P1", "P2", "P3", "P4", "P5"}:
        raise HTTPException(status_code=400, detail="pattern_id must be P1..P5")

    async with db() as conn:
        await conn.execute(
            """
            INSERT INTO pattern_instructions(pattern_id, instructions, updated_at)
            VALUES (?,?,datetime('now'))
            ON CONFLICT(pattern_id) DO UPDATE SET
              instructions=excluded.instructions,
              updated_at=datetime('now')
            """,
            (pattern_id, instructions),
        )
        await conn.commit()

    return {"saved": True, "pattern_id": pattern_id, "instructions": instructions}
