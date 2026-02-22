"""
J.A.R.V.I.S. Database Layer
SQLite with sqlite-vec for vector embeddings + standard relational tables.
"""

import os, json, logging, aiosqlite
from pathlib import Path

logger = logging.getLogger("jarvis.db")

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "jarvis.db"
DB_PATH = Path(os.getenv("JARVIS_DB_PATH", str(DEFAULT_DB_PATH))).resolve()


# ── Schema ───────────────────────────────────────────────────
DDL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

/* ─── GitHub / Repos ─────────────────────────────────── */
CREATE TABLE IF NOT EXISTS github_tokens (
    id          INTEGER PRIMARY KEY,
    username    TEXT    NOT NULL UNIQUE,
    token       TEXT    NOT NULL,
    connected_at TEXT   NOT NULL DEFAULT (datetime('now')),
    profile_json TEXT
);

CREATE TABLE IF NOT EXISTS repos (
    id          INTEGER PRIMARY KEY,
    github_id   INTEGER UNIQUE,
    owner       TEXT    NOT NULL,
    name        TEXT    NOT NULL,
    full_name   TEXT    NOT NULL UNIQUE,
    description TEXT,
    language    TEXT,
    stars       INTEGER DEFAULT 0,
    forks       INTEGER DEFAULT 0,
    size_kb     INTEGER DEFAULT 0,
    default_branch TEXT  DEFAULT 'main',
    private     INTEGER DEFAULT 0,
    html_url    TEXT,
    clone_url   TEXT,
    topics_json TEXT    DEFAULT '[]',
    fetched_at  TEXT    DEFAULT (datetime('now'))
);

/* ─── Assessment Runs ───────────────────────────────── */
CREATE TABLE IF NOT EXISTS scan_runs (
    id          TEXT    PRIMARY KEY,   -- uuid
    status      TEXT    NOT NULL DEFAULT 'pending',  -- pending|running|complete|failed
    repos_json  TEXT    NOT NULL DEFAULT '[]',
    started_at  TEXT    DEFAULT (datetime('now')),
    completed_at TEXT,
    summary_json TEXT,
    error_msg   TEXT
);

/* ─── Applications ──────────────────────────────────── */
CREATE TABLE IF NOT EXISTS applications (
    id          TEXT    PRIMARY KEY,   -- APP-NNN
    scan_run_id TEXT    REFERENCES scan_runs(id),
    name        TEXT    NOT NULL,
    repo_full_name TEXT,
    language    TEXT,
    framework   TEXT,
    loc         INTEGER DEFAULT 0,
    complexity  TEXT    DEFAULT 'medium',   -- low|medium|high
    risk_score  REAL    DEFAULT 5.0,
    pattern_id  TEXT,               -- P1..P5
    pattern_name TEXT,
    gcp_target  TEXT,               -- GCE|GKE|CloudRun|CloudSQL|PubSub
    has_dockerfile INTEGER DEFAULT 0,
    has_terraform  INTEGER DEFAULT 0,
    has_jenkinsfile INTEGER DEFAULT 0,
    has_github_actions INTEGER DEFAULT 0,
    has_pcf     INTEGER DEFAULT 0,
    has_db      INTEGER DEFAULT 0,
    has_messaging INTEGER DEFAULT 0,
    db_types_json TEXT DEFAULT '[]',
    dependencies_json TEXT DEFAULT '[]',
    files_json  TEXT  DEFAULT '[]',
    findings_json TEXT DEFAULT '[]',
    created_at  TEXT  DEFAULT (datetime('now'))
);

/* ─── Migration Waves ──────────────────────────────── */
CREATE TABLE IF NOT EXISTS migration_waves (
    id          TEXT    PRIMARY KEY,
    name        TEXT    NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'planned',  -- planned|active|complete|paused
    apps_json   TEXT    NOT NULL DEFAULT '[]',
    progress    REAL    DEFAULT 0.0,
    started_at  TEXT,
    completed_at TEXT,
    created_at  TEXT    DEFAULT (datetime('now'))
);

/* ─── Migration Jobs ────────────────────────────────── */
CREATE TABLE IF NOT EXISTS migration_jobs (
    id          TEXT    PRIMARY KEY,
    wave_id     TEXT    REFERENCES migration_waves(id),
    app_id      TEXT    REFERENCES applications(id),
    pattern_id  TEXT    NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'pending', -- pending|running|awaiting_approval|approved|complete|failed
    progress    REAL    DEFAULT 0.0,
    terraform_hcl TEXT,
    pipeline_yaml TEXT,
    jenkinsfile TEXT,
    gcp_arch_json TEXT,
    diff_json   TEXT,
    logs_json   TEXT    DEFAULT '[]',
    started_at  TEXT,
    completed_at TEXT,
    approved_by TEXT,
    approval_comment TEXT,
    created_at  TEXT    DEFAULT (datetime('now'))
);

/* ─── Terraform Artifacts ───────────────────────────── */
CREATE TABLE IF NOT EXISTS terraform_artifacts (
    id          TEXT PRIMARY KEY,
    app_id      TEXT REFERENCES applications(id),
    pattern_id  TEXT NOT NULL,
    hcl_main    TEXT,
    hcl_variables TEXT,
    hcl_outputs TEXT,
    github_actions_yaml TEXT,
    jenkinsfile TEXT,
    changed_files_json TEXT DEFAULT '[]',
    generated_at TEXT DEFAULT (datetime('now'))
);

/* ─── Integration Settings ──────────────────────────── */
CREATE TABLE IF NOT EXISTS integration_settings (
    id          INTEGER PRIMARY KEY,
    service     TEXT    NOT NULL UNIQUE,  -- servicenow|sharepoint|jenkins
    enabled     INTEGER DEFAULT 0,
    config_json TEXT    DEFAULT '{}',
    status      TEXT    DEFAULT 'disconnected',
    last_sync   TEXT
);

CREATE TABLE IF NOT EXISTS pattern_instructions (
    pattern_id  TEXT PRIMARY KEY,         -- P1..P5
    instructions TEXT NOT NULL DEFAULT '',
    updated_at  TEXT DEFAULT (datetime('now'))
);

/* ─── Embeddings (virtual table via sqlite-vec if available) ── */
CREATE TABLE IF NOT EXISTS app_embeddings (
    app_id      TEXT    PRIMARY KEY,
    embedding_json TEXT NOT NULL,   -- JSON array of floats (fallback when sqlite-vec absent)
    model       TEXT    DEFAULT 'all-MiniLM-L6-v2',
    created_at  TEXT    DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS code_chunks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    app_id      TEXT    NOT NULL,
    file_path   TEXT    NOT NULL,
    chunk_text  TEXT    NOT NULL,
    chunk_index INTEGER DEFAULT 0,
    embedding_json TEXT,
    created_at  TEXT    DEFAULT (datetime('now'))
);

/* ─── PMO ────────────────────────────────────────────── */
CREATE TABLE IF NOT EXISTS pmo_risks (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    app_id      TEXT,
    probability TEXT,
    impact      TEXT,
    rating      TEXT,
    owner       TEXT,
    status      TEXT DEFAULT 'open',
    mitigation  TEXT,
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS pmo_budget (
    id          INTEGER PRIMARY KEY,
    wave        TEXT NOT NULL,
    planned     REAL DEFAULT 0,
    actual      REAL DEFAULT 0,
    gcp_monthly REAL DEFAULT 0
);

/* ─── Indexes ────────────────────────────────────────── */
CREATE INDEX IF NOT EXISTS idx_apps_scan    ON applications(scan_run_id);
CREATE INDEX IF NOT EXISTS idx_apps_pattern ON applications(pattern_id);
CREATE INDEX IF NOT EXISTS idx_jobs_app     ON migration_jobs(app_id);
CREATE INDEX IF NOT EXISTS idx_jobs_wave    ON migration_jobs(wave_id);
CREATE INDEX IF NOT EXISTS idx_chunks_app   ON code_chunks(app_id);
"""

# ── VEC extension (optional) ────────────────────────────────
_VEC_READY = False


async def _try_load_vec(conn: aiosqlite.Connection):
    """Try to load sqlite-vec extension. Falls back gracefully."""
    global _VEC_READY
    try:
        vec_path = os.getenv("SQLITE_VEC_PATH", "")
        if vec_path:
            await conn.execute(f"SELECT load_extension('{vec_path}')")
            await conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS vec_app_embeddings
                USING vec0(app_id TEXT, embedding FLOAT[384])
            """)
            _VEC_READY = True
            logger.info("sqlite-vec loaded — vector search enabled ✓")
    except Exception as e:
        logger.warning(f"sqlite-vec not available ({e}). Using JSON cosine fallback.")


async def init_db():
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        await _try_load_vec(conn)
        await conn.executescript(DDL)
        await _seed_default_data(conn)
        await conn.commit()


async def _seed_default_data(conn):
    """Seed integration rows and default waves if not present."""
    for svc in ["servicenow", "sharepoint", "jenkins"]:
        await conn.execute(
            "INSERT OR IGNORE INTO integration_settings (service, enabled, config_json, status) VALUES (?,0,'{}','disconnected')",
            (svc,)
        )
    for pid in ["P1", "P2", "P3", "P4", "P5"]:
        await conn.execute(
            "INSERT OR IGNORE INTO pattern_instructions(pattern_id, instructions) VALUES (?, '')",
            (pid,),
        )
    for i, (name, apps) in enumerate([
        ("Wave 1", "[]"),
        ("Wave 2", "[]"),
        ("Wave 3", "[]"),
    ], 1):
        wave_id = f"WAVE-{i:03d}"
        await conn.execute(
            "INSERT OR IGNORE INTO migration_waves(id,name,apps_json) VALUES (?,?,?)",
            (wave_id, name, apps)
        )
    for rid, title, prob, impact, rating, owner, mitigation in [
        ("R-001","Cloud SQL connection pool exhaustion","high","critical","critical","DBA Lead","Increase pool size, add pgbouncer"),
        ("R-002","PCF manifest incompatibilities with GKE","medium","high","high","Arch Lead","Run cf-to-k8s conversion tool, review each manifest"),
        ("R-003","Jenkins pipeline GCP auth not configured","high","high","critical","DevOps Lead","Configure GCP Workload Identity for Jenkins"),
        ("R-004","Service Bus → Pub/Sub flow mapping gaps","medium","medium","medium","Integration Lead","Document all topic/subscription mappings"),
        ("R-005","Data replication lag during cutover","low","high","high","DBA Lead","Use DMS continuous replication, test failover"),
    ]:
        await conn.execute(
            "INSERT OR IGNORE INTO pmo_risks(id,title,probability,impact,rating,owner,mitigation) VALUES (?,?,?,?,?,?,?)",
            (rid,title,prob,impact,rating,owner,mitigation)
        )
    budget_rows = [
        ("Wave 1",620000,0,0), ("Wave 2",780000,0,0),
        ("Wave 3",920000,0,0), ("Infra",280000,0,0), ("PMO",200000,0,0),
    ]
    for row in budget_rows:
        await conn.execute(
            "INSERT OR IGNORE INTO pmo_budget(wave,planned,actual,gcp_monthly) VALUES (?,?,?,?)", row
        )
    await _seed_demo_assessment_data(conn)


async def _seed_demo_assessment_data(conn):
    demo_run_id = "DEMO-RUN-001"
    patterns = ["P1", "P2", "P3", "P4", "P5"]
    pattern_names = {
        "P1": "Web + DMZ Replatform",
        "P2": "Global L7 Load Balancer Modernization",
        "P3": "Database Migration and Rebuild",
        "P4": "PCF to GKE Replatform",
        "P5": "Messaging Modernization",
    }
    gcp_targets = {
        "P1": "GCE + Cloud Armor + Cloud SQL",
        "P2": "Global External HTTP(S) Load Balancer",
        "P3": "Cloud SQL + Database Migration Service",
        "P4": "GKE + Artifact Registry",
        "P5": "Pub/Sub + Cloud Run",
    }
    frameworks = {
        "P1": "spring",
        "P2": "node",
        "P3": "dotnet",
        "P4": "java",
        "P5": "python",
    }
    languages = {
        "P1": "Java",
        "P2": "TypeScript",
        "P3": "C#",
        "P4": "Java",
        "P5": "Python",
    }
    seeds = {
        "P1": ["customer-portal", "partner-gateway", "agent-hub", "claims-web", "payments-web", "retail-portal", "onboarding-web", "dealer-portal", "billing-web", "support-web"],
        "P2": ["traffic-edge", "api-edge", "checkout-edge", "catalog-edge", "policy-edge", "mobile-edge", "identity-edge", "pricing-edge", "routing-edge", "partner-edge"],
        "P3": ["ledger-core", "policy-db-sync", "order-ledger", "audit-store", "settlement-db", "recon-db", "customer-master", "claims-ledger", "risk-warehouse", "payment-ledger"],
        "P4": ["pcf-order", "pcf-billing", "pcf-reporting", "pcf-pricing", "pcf-notify", "pcf-session", "pcf-eligibility", "pcf-catalog", "pcf-search", "pcf-analytics"],
        "P5": ["event-router", "notification-bus", "integration-stream", "risk-events", "payment-events", "customer-events", "order-events", "claims-events", "audit-events", "telemetry-events"],
    }

    demo_repos = []
    demo_apps = []
    app_counter = 1
    for pid in patterns:
        for idx, base in enumerate(seeds[pid], start=1):
            app_id = f"DEMO-APP-{app_counter:03d}"
            repo_full = f"predefined/demo-org/{base}"
            app_name = base.replace("-", " ").title().replace("Pcf", "PCF")

            has_db = 1 if pid in {"P1", "P3", "P4"} else 0
            has_messaging = 1 if pid in {"P1", "P5"} else 0
            db_types = ["postgres"] if pid in {"P1", "P4"} else (["mysql"] if pid == "P3" else [])

            integration_targets = [
                f"DEMO-APP-{((app_counter) % 50) + 1:03d}",
                f"DEMO-APP-{((app_counter + 7) % 50) + 1:03d}",
            ]
            primary_coupling = "tight" if pid in {"P1", "P3", "P4"} and idx % 2 == 0 else "loose"
            db_coupling = "tight" if pid in {"P3", "P4"} else "loose"
            integration_points = [
                {"target": integration_targets[0], "coupling": primary_coupling},
                {"target": integration_targets[1], "coupling": "loose" if primary_coupling == "tight" else "tight"},
            ]
            files = [
                "src/main/app.py" if languages[pid] == "Python" else "src/main/App.java",
                "Jenkinsfile",
                ".github/workflows/deploy.yml",
                "terraform/main.tf",
                "network/dmz.tf" if pid == "P1" else "network/lb.tf" if pid == "P2" else "database/migration.tf" if pid == "P3" else "k8s/deployment.yaml" if pid == "P4" else "messaging/pubsub.tf",
            ]
            findings = [
                {
                    "type": "metadata",
                    "tags": ["predefined", "demo", "assessment-catalog"],
                    "portfolio": "enterprise-migration",
                    "coupling_profile": {
                        "app": primary_coupling,
                        "database": db_coupling,
                    },
                },
                {
                    "type": "app_to_app_integration",
                    "protocols": ["REST", "gRPC"] if pid in {"P1", "P2", "P4"} else ["event"],
                    "targets": integration_targets,
                    "integration_points": integration_points,
                    "coupling": primary_coupling,
                },
                {
                    "type": "app_to_db_integration",
                    "datastores": db_types or ["none"],
                    "mode": "read-write" if has_db else "n/a",
                    "coupling": db_coupling,
                },
                {
                    "type": "scan_details",
                    "code_artifacts": {
                        "languages": [languages[pid]],
                        "pipelines": ["Jenkinsfile", ".github/workflows/deploy.yml"],
                        "iac": ["terraform/main.tf"],
                        "key_files": files,
                    },
                },
            ]

            demo_repos.append(repo_full)
            demo_apps.append(
                {
                    "id": app_id,
                    "scan_run_id": demo_run_id,
                    "name": app_name,
                    "repo_full_name": repo_full,
                    "language": languages[pid],
                    "framework": frameworks[pid],
                    "loc": 12000 + (idx * 1400) + (patterns.index(pid) * 900),
                    "complexity": "high" if pid in {"P1", "P3", "P4"} and idx % 3 == 0 else "medium",
                    "risk_score": float(38 + (idx * 3) + (patterns.index(pid) * 4)),
                    "pattern_id": pid,
                    "pattern_name": pattern_names[pid],
                    "gcp_target": gcp_targets[pid],
                    "has_dockerfile": 1 if pid in {"P1", "P4", "P5"} else 0,
                    "has_terraform": 1,
                    "has_jenkinsfile": 1,
                    "has_github_actions": 1,
                    "has_pcf": 1 if pid == "P4" else 0,
                    "has_db": has_db,
                    "has_messaging": has_messaging,
                    "db_types_json": json.dumps(db_types),
                    "dependencies_json": json.dumps(["maven", "redis"] if pid in {"P1", "P4"} else ["npm", "nginx"] if pid == "P2" else ["dotnet", "sqlclient"] if pid == "P3" else ["pip", "pubsub"]),
                    "files_json": json.dumps(files),
                    "findings_json": json.dumps(findings),
                }
            )
            app_counter += 1

    summary = {
        "repo_count": len(demo_repos),
        "app_count": len(demo_apps),
        "total_loc": sum(a["loc"] for a in demo_apps),
        "stage": "Demo scan seeded",
        "progress": 100,
        "seeded": True,
    }

    await conn.execute(
        """
        INSERT OR REPLACE INTO scan_runs(id,status,repos_json,started_at,completed_at,summary_json)
        VALUES (?,?,?,datetime('now','-2 day'),datetime('now','-2 day'),?)
        """,
        (demo_run_id, "complete", json.dumps(demo_repos), json.dumps(summary)),
    )

    for app in demo_apps:
        await conn.execute(
            """
            INSERT OR REPLACE INTO applications(
                id, scan_run_id, name, repo_full_name, language, framework, loc, complexity,
                risk_score, pattern_id, pattern_name, gcp_target, has_dockerfile, has_terraform,
                has_jenkinsfile, has_github_actions, has_pcf, has_db, has_messaging,
                db_types_json, dependencies_json, files_json, findings_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                app["id"], app["scan_run_id"], app["name"], app["repo_full_name"], app["language"], app["framework"],
                app["loc"], app["complexity"], app["risk_score"], app["pattern_id"], app["pattern_name"],
                app["gcp_target"], app["has_dockerfile"], app["has_terraform"], app["has_jenkinsfile"],
                app["has_github_actions"], app["has_pcf"], app["has_db"], app["has_messaging"],
                app["db_types_json"], app["dependencies_json"], app["files_json"], app["findings_json"],
            ),
        )


# ── Connection helper ────────────────────────────────────────
class DB:
    """Async context-manager wrapping aiosqlite."""
    def __init__(self):
        self._conn = None

    async def __aenter__(self) -> aiosqlite.Connection:
        self._conn = await aiosqlite.connect(DB_PATH)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA temp_store=MEMORY")
        await self._conn.execute("PRAGMA cache_size=-20000")

        if not hasattr(self._conn, "execute_fetchone"):
            async def _execute_fetchone(sql, params=()):
                cur = await self._conn.execute(sql, params)
                return await cur.fetchone()

            async def _execute_fetchall(sql, params=()):
                cur = await self._conn.execute(sql, params)
                return await cur.fetchall()

            self._conn.execute_fetchone = _execute_fetchone
            self._conn.execute_fetchall = _execute_fetchall
        return self._conn

    async def __aexit__(self, *_):
        if self._conn:
            await self._conn.close()


def db() -> DB:
    return DB()


def row2dict(row) -> dict:
    if row is None:
        return {}
    d = dict(row)
    # Auto-parse _json suffix fields
    for k, v in d.items():
        if k.endswith("_json") and isinstance(v, str):
            try:
                d[k] = json.loads(v)
            except Exception:
                pass
    return d


def rows2list(rows) -> list:
    return [row2dict(r) for r in rows]
