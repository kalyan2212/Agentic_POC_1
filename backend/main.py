"""
J.A.R.V.I.S. Backend — FastAPI
Azure → Google Cloud Migration Intelligence Platform

Patterns supported:
  P1 — GCE Replatform    : App server → GCE (Terraform + GitHub Actions)
  P2 — Load Balancer      : Azure LB → GCP Cloud Load Balancing
  P3 — Database Rebuild   : Azure SQL → Cloud SQL + DMS replication
  P4 — PCF → GKE          : Pivotal Cloud Foundry → Google Kubernetes Engine
  P5 — Messaging Rebuild  : Azure Service Bus/Event Hub → Pub/Sub
"""

import os, logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from database import init_db
from routers import assessment, migration, github_router, testing, pmo, system, integrations

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s — %(message)s")
logger = logging.getLogger("jarvis")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("JARVIS Backend starting — initialising database…")
    await init_db()
    try:
        seeded = await github_router.bootstrap_env_credentials()
        if seeded:
            logger.info("GitHub credentials loaded from environment ✓")
    except Exception as e:
        logger.warning(f"GitHub env bootstrap skipped: {e}")
    logger.info("Database ready ✓")
    yield
    logger.info("JARVIS Backend shutting down.")


app = FastAPI(
    title="J.A.R.V.I.S. Migration Intelligence API",
    description="Azure → GCP cloud migration platform backend",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],           # tighten in prod
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ─────────────────────────────────────────────────
app.include_router(system.router,       prefix="/api",          tags=["system"])
app.include_router(github_router.router,prefix="/api/github",   tags=["github"])
app.include_router(assessment.router,   prefix="/api/assessment",tags=["assessment"])
app.include_router(migration.router,    prefix="/api/migration", tags=["migration"])
app.include_router(testing.router,      prefix="/api/testing",   tags=["testing"])
app.include_router(pmo.router,          prefix="/api/pmo",       tags=["pmo"])
app.include_router(integrations.router, prefix="/api/integrations", tags=["integrations"])
