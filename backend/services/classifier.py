"""
J.A.R.V.I.S. Migration Pattern Classifier
Classifies repositories into one of 5 migration patterns by analysing
file structure, code content, and dependency markers.

Patterns:
  P1 — GCE Replatform   : App server → GCE VM (Terraform + GitHub Actions)
  P2 — Load Balancer     : Azure LB → GCP Cloud Load Balancing
  P3 — Database Rebuild  : Azure SQL / managed DB → Cloud SQL + DMS
  P4 — PCF → GKE         : Pivotal Cloud Foundry → Google Kubernetes Engine
  P5 — Messaging Rebuild : Azure Service Bus / Event Hub → Pub/Sub
"""

import re
from dataclasses import dataclass, field
from typing import List, Optional


# ── Pattern definitions ──────────────────────────────────────
@dataclass
class Pattern:
    id:          str
    name:        str
    short:       str
    gcp_target:  str
    description: str
    confidence_threshold: float = 0.4


PATTERNS = {
    "P1": Pattern("P1", "GCE Replatform",    "gce",     "GCE",
                  "Redeploy app server to GCE — update Terraform & GitHub Actions"),
    "P2": Pattern("P2", "Load Balancer Migration","lb",  "Cloud Load Balancing",
                  "Migrate Azure Load Balancer to GCP Cloud Load Balancing"),
    "P3": Pattern("P3", "Database Rebuild",  "db",      "Cloud SQL",
                  "Rebuild database on Cloud SQL + DMS data replication"),
    "P4": Pattern("P4", "PCF → GKE",         "gke",     "GKE",
                  "Migrate Pivotal Cloud Foundry apps to Google Kubernetes Engine"),
    "P5": Pattern("P5", "Messaging Rebuild",  "pubsub",  "Pub/Sub",
                  "Rebuild messaging platform — Azure Service Bus / Event Hub → Pub/Sub"),
}


# ── Signal weights ───────────────────────────────────────────
# Each entry: (regex_pattern, pattern_id, weight)
SIGNALS: List[tuple] = [
    # ── P1 (GCE) signals
    (r"terraform",                  "P1", 0.3),
    (r"\.tf$",                      "P1", 0.3),
    (r"github.?actions|\.github",   "P1", 0.2),
    (r"azure.?vm|azurerm_virtual",  "P1", 0.4),
    (r"startup.?script|systemd|service.?unit", "P1", 0.3),
    (r"nginx|apache|iis",           "P1", 0.25),
    (r"main\.tf|variables\.tf",     "P1", 0.35),
    (r"app.?server|web.?server",    "P1", 0.2),

    # ── P2 (LB) signals
    (r"load.?balanc",               "P2", 0.4),
    (r"azure_lb|azurerm_lb",        "P2", 0.5),
    (r"backend.?pool|frontend.?ip", "P2", 0.4),
    (r"health.?probe|health.?check","P2", 0.3),
    (r"ingress|ssl.?cert|https",    "P2", 0.2),
    (r"traffic.?manager|app.?gateway","P2", 0.35),
    (r"port.*80|port.*443",         "P2", 0.15),

    # ── P3 (Database) signals
    (r"sql|database|db",            "P3", 0.25),
    (r"azure.?sql|azurerm_sql|mssql","P3",0.5),
    (r"postgres|postgresql|pg",     "P3", 0.3),
    (r"mysql|mariadb",              "P3", 0.3),
    (r"flyway|liquibase|alembic|migrate","P3",0.3),
    (r"connectionstring|datasource.url","P3",0.35),
    (r"dms|data.?migration|replicat","P3",0.4),
    (r"entity.?framework|hibernate|jpa","P3",0.25),
    (r"\.sql$|schema\.sql|init\.sql","P3",0.4),

    # ── P4 (PCF/GKE) signals
    (r"manifest\.yml|cf.?manifest",  "P4", 0.6),
    (r"buildpack|cloudfoundry|pcf",  "P4", 0.6),
    (r"cf push|cf create",           "P4", 0.5),
    (r"kubernetes|k8s|kubectl",      "P4", 0.4),
    (r"deployment\.yaml|service\.yaml","P4",0.4),
    (r"dockerfile|docker-compose",   "P4", 0.35),
    (r"helm|helmfile",               "P4", 0.4),
    (r"spring.?boot|spring.?cloud",  "P4", 0.2),
    (r"\.jar$|mvn|gradle",           "P4", 0.15),

    # ── P5 (Messaging) signals
    (r"service.?bus|servicebus",     "P5", 0.6),
    (r"event.?hub|eventhub",         "P5", 0.5),
    (r"azure.*messag|amqp",          "P5", 0.5),
    (r"queue|topic|subscription",    "P5", 0.3),
    (r"pub.?sub|pubsub|google.*pub", "P5", 0.3),
    (r"kafka|rabbitmq|activemq",     "P5", 0.25),
    (r"message.?flow|integration.?flow","P5",0.35),
    (r"dead.?letter|dlq",            "P5", 0.4),
    (r"@serviceactivator|@messaginggateway","P5",0.4),
]


@dataclass
class ClassifyResult:
    pattern_id:   str
    pattern_name: str
    gcp_target:   str
    confidence:   float
    scores:       dict = field(default_factory=dict)
    signals_hit:  List[str] = field(default_factory=list)
    risk_score:   float = 5.0
    complexity:   str = "medium"
    findings:     List[str] = field(default_factory=list)


def classify_repo(
    files: List[str],         # file paths in repo
    content_sample: str = "", # concatenated sample of file contents
    app_meta: Optional[dict] = None,
) -> ClassifyResult:
    """
    Score the repo against each pattern using signal weights.
    Returns the best-matching pattern with confidence and findings.
    """
    scores  = {pid: 0.0 for pid in PATTERNS}
    signals_hit = []
    combined = " ".join(files).lower() + "\n" + content_sample.lower()

    for regex, pattern_id, weight in SIGNALS:
        if re.search(regex, combined, re.IGNORECASE):
            scores[pattern_id] = min(1.0, scores[pattern_id] + weight)
            signals_hit.append(f"{pattern_id}:{regex}")

    # Pick best
    best_pid = max(scores, key=lambda p: scores[p])
    best_score = scores[best_pid]

    # If nothing scored, default to P1 (GCE replatform) as safest default
    if best_score < 0.1:
        best_pid = "P1"
        best_score = 0.15

    pattern = PATTERNS[best_pid]
    confidence = min(0.99, best_score)

    # ── Risk scoring ─────────────────────────────────────────
    risk = _calc_risk(files, content_sample, best_pid)

    # ── Complexity ───────────────────────────────────────────
    loc_estimate = len(content_sample.split("\n"))
    complexity = "low" if loc_estimate < 500 else "high" if loc_estimate > 5000 else "medium"

    # ── Findings ─────────────────────────────────────────────
    findings = _generate_findings(files, content_sample, best_pid, scores)

    return ClassifyResult(
        pattern_id   = best_pid,
        pattern_name = pattern.name,
        gcp_target   = pattern.gcp_target,
        confidence   = round(confidence, 2),
        scores       = {k: round(v, 2) for k, v in scores.items()},
        signals_hit  = signals_hit[:20],
        risk_score   = round(risk, 1),
        complexity   = complexity,
        findings     = findings,
    )


def _calc_risk(files: List[str], content: str, pattern_id: str) -> float:
    """Heuristic risk score 1-10."""
    risk = 4.0
    if pattern_id == "P3":  risk += 2.0   # DB migrations are risky
    if pattern_id == "P4":  risk += 1.5   # PCF→GKE needs manifest work
    if pattern_id == "P5":  risk += 1.5   # Messaging flows are complex
    if re.search(r"secret|password|credential|api.?key", content, re.I): risk += 0.5
    if re.search(r"legacy|deprecated|eof|end.?of.?life", content, re.I):  risk += 0.5
    if any(f.endswith(".sql") for f in files): risk += 0.5
    return min(10.0, risk)


def _generate_findings(files, content, pattern_id, scores) -> List[str]:
    findings = []
    if re.search(r"TODO|FIXME|HACK|XXX", content): findings.append("Code contains TODO/FIXME markers")
    if re.search(r"localhost|127\.0\.0\.1", content): findings.append("Hardcoded localhost references detected — update for GCP")
    if re.search(r"azure\.com|azurewebsites|azurecontainer", content): findings.append("Azure-specific hostnames detected — must be replaced with GCP endpoints")
    if re.search(r"connectionstring.*azure|azure.*connectionstring", content, re.I): findings.append("Azure connection strings present — update to Cloud SQL / GCP credentials")
    if re.search(r"servicenow|snow\.com", content, re.I): findings.append("ServiceNow references present — configure integration")
    if re.search(r"jenkins|jenkinsfile", content, re.I): findings.append("Jenkins pipeline detected — will be updated for GCP deploy")
    if not any(f.endswith(".tf") for f in files): findings.append("No Terraform files found — will be generated by migration engine")
    if not any(".github" in f or "github/workflows" in f for f in files): findings.append("No GitHub Actions found — CI/CD pipeline will be generated")
    if pattern_id == "P4" and not any("dockerfile" in f.lower() for f in files): findings.append("PCF app without Dockerfile — Dockerfile will be generated for GKE")
    if pattern_id == "P3" and re.search(r"mssql|sqlserver|azure.*sql", content, re.I): findings.append("MSSQL/Azure SQL detected — DMS heterogeneous migration required")
    return findings[:10]
