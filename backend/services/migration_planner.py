import json
from typing import Dict, List

PATTERN_TO_ARCH = {
    "P1": {
        "target": "GCE",
    "components": ["Global External HTTP(S) LB", "Cloud Armor (WAF)", "DMZ subnet", "Managed Instance Group (GCE)", "Cloud SQL (private IP)", "Cloud Logging"],
        "diagram": [
      "Users -> Global External HTTP(S) LB",
      "LB -> Cloud Armor policy -> DMZ NEG",
      "DMZ tier -> Internal service tier (GCE MIG)",
      "Service tier -> Cloud SQL (private service networking)",
        ],
    "pipeline_focus": ["DMZ policy validation", "Blue/green deploy", "Synthetic health checks", "Canary cutover"],
    },
    "P2": {
        "target": "Cloud Load Balancing",
    "components": ["Global External HTTP(S) LB (Layer 7)", "Regional backends", "URL maps + host rules", "Cloud CDN", "Cloud Armor", "Health checks"],
        "diagram": [
            "Users -> Global External HTTP(S) LB",
      "LB -> URL map (host/path routing)",
      "URL map -> Backend service pools (active-active)",
      "Backend pools -> Existing app tier",
        ],
    "pipeline_focus": ["Traffic replay", "L7 route tests", "Canary weight shift", "Global failover drill"],
    },
    "P3": {
        "target": "Cloud SQL + DMS",
    "components": ["Cloud SQL HA", "Database Migration Service", "Connection profiles", "CDC validation jobs", "Secret Manager", "Cloud Monitoring"],
        "diagram": [
            "Source DB -> DMS replication job",
            "DMS -> Cloud SQL",
      "Validation runner -> row/hash diff checks",
      "App -> Cloud SQL",
        ],
    "pipeline_focus": ["Schema drift check", "Dry-run migration", "CDC lag SLO gates", "Cutover guardrails"],
    },
    "P4": {
        "target": "GKE",
        "components": ["Artifact Registry", "GKE Cluster", "ConfigMaps/Secrets", "Cloud SQL", "Ingress"],
        "diagram": [
            "CI/CD -> Artifact Registry",
            "Artifact Registry -> GKE Deployment",
            "Ingress -> GKE Service -> Pods",
            "Pods -> Cloud SQL",
        ],
          "pipeline_focus": ["Container security scan", "Helm upgrade", "Progressive delivery", "SLO checks"],
    },
    "P5": {
        "target": "Pub/Sub",
        "components": ["Pub/Sub Topics", "Subscriptions", "Dead Letter Topic", "Cloud Functions/Run Subscribers"],
        "diagram": [
            "Producer -> Pub/Sub Topic",
            "Topic -> Subscription(s)",
            "Subscriber -> App services",
            "Failures -> Dead Letter Topic",
        ],
          "pipeline_focus": ["Topic contract tests", "Backpressure test", "DLQ verification", "Replay simulation"],
    },
}


def generate_terraform(app_name: str, pattern_id: str) -> str:
    safe = app_name.lower().replace("_", "-").replace(" ", "-")
    if pattern_id == "P1":
        return f'''resource "google_compute_global_address" "{safe}_lb_ip" {{
  name = "{safe}-lb-ip"
}}

resource "google_compute_security_policy" "{safe}_armor" {{
  name = "{safe}-armor"
  rule {{
    action   = "allow"
    priority = "1000"
    match {{
      versioned_expr = "SRC_IPS_V1"
      config {{ src_ip_ranges = ["0.0.0.0/0"] }}
    }}
  }}
}}

resource "google_compute_region_instance_group_manager" "{safe}_svc_mig" {{
  name               = "{safe}-svc-mig"
  region             = "us-central1"
  base_instance_name = "{safe}-svc"
  target_size        = 3
}}
'''

    if pattern_id == "P2":
        return f'''resource "google_compute_url_map" "{safe}_urlmap" {{
  name            = "{safe}-global-url-map"
  default_service = google_compute_backend_service.{safe}_backend.id
}}

resource "google_compute_backend_service" "{safe}_backend" {{
  name                            = "{safe}-backend"
  load_balancing_scheme           = "EXTERNAL_MANAGED"
  protocol                        = "HTTP"
  timeout_sec                     = 30
  connection_draining_timeout_sec = 30
  locality_lb_policy              = "ROUND_ROBIN"
}}

resource "google_compute_target_https_proxy" "{safe}_proxy" {{
  name    = "{safe}-https-proxy"
  url_map = google_compute_url_map.{safe}_urlmap.id
}}
'''

    if pattern_id == "P3":
        return f'''resource "google_sql_database_instance" "{safe}_sql" {{
  name             = "{safe}-sql"
  database_version = "POSTGRES_15"
  region           = "us-central1"
  settings {{ tier = "db-custom-2-7680" availability_type = "REGIONAL" }}
}}

resource "google_database_migration_service_connection_profile" "{safe}_src" {{
  location              = "us-central1"
  connection_profile_id = "{safe}-src-profile"
}}

resource "google_database_migration_service_migration_job" "{safe}_job" {{
  location          = "us-central1"
  migration_job_id  = "{safe}-dms-job"
  type              = "CONTINUOUS"
}}
'''

    if pattern_id == "P4":
        return f'''resource "google_container_cluster" "{safe}" {{
  name     = "{safe}-gke"
  location = "us-central1"
  remove_default_node_pool = true
  initial_node_count = 1
}}

resource "google_container_node_pool" "{safe}_np" {{
  name       = "{safe}-np"
  location   = "us-central1"
  cluster    = google_container_cluster.{safe}.name
  node_count = 2
}}
'''

    if pattern_id == "P5":
        return f'''resource "google_pubsub_topic" "{safe}_topic" {{
  name = "{safe}-events"
}}

resource "google_pubsub_subscription" "{safe}_sub" {{
  name  = "{safe}-events-sub"
  topic = google_pubsub_topic.{safe}_topic.name
  dead_letter_policy {{
    dead_letter_topic     = google_pubsub_topic.{safe}_dlq.id
    max_delivery_attempts = 10
  }}
}}

resource "google_pubsub_topic" "{safe}_dlq" {{
  name = "{safe}-events-dlq"
}}
'''

    return f'''resource "google_compute_instance_template" "{safe}_tmpl" {{
  name_prefix  = "{safe}-tmpl-"
  machine_type = "e2-standard-2"
  tags         = ["{safe}"]
}}

resource "google_compute_region_instance_group_manager" "{safe}_mig" {{
  name               = "{safe}-mig"
  base_instance_name = "{safe}"
  region             = "us-central1"
  version {{
    instance_template = google_compute_instance_template.{safe}_tmpl.id
  }}
  target_size = 2
}}
'''


def generate_jenkinsfile(app_name: str, pattern_id: str) -> str:
    deploy_step = "sh 'kubectl apply -f k8s/'" if pattern_id == "P4" else "sh 'terraform apply -auto-approve'"
    pattern_gate = {
        "P1": "sh 'python scripts/validate_dmz_policies.py'",
        "P2": "sh 'python scripts/validate_l7_routes.py'",
        "P3": "sh 'python scripts/validate_dms_cutover.py'",
        "P4": "sh 'python scripts/validate_gke_rollout.py'",
        "P5": "sh 'python scripts/validate_pubsub_contracts.py'",
    }.get(pattern_id, "sh 'echo Validating deployment gates'")
    return f'''pipeline {{
  agent any
  stages {{
    stage('Checkout') {{
      steps {{ checkout scm }}
    }}
    stage('Build') {{
      steps {{ sh 'echo Building {app_name}' }}
    }}
    stage('Test') {{
      steps {{ sh 'echo Running tests' }}
    }}
    stage('Migration Gate') {{
      steps {{ {pattern_gate} }}
    }}
    stage('Deploy GCP') {{
      steps {{ {deploy_step} }}
    }}
    stage('Post Deploy Validation') {{
      steps {{ sh 'python scripts/synthetic_smoke.py --target gcp' }}
    }}
  }}
}}
'''


def generate_pipeline_yaml(app_name: str, pattern_id: str) -> str:
    deploy_cmd = "kubectl apply -f k8s/" if pattern_id == "P4" else "terraform apply -auto-approve"
    quality_gate = {
        "P1": "python scripts/dmz_security_gate.py",
        "P2": "python scripts/l7_global_lb_gate.py",
        "P3": "python scripts/db_cutover_gate.py",
        "P4": "python scripts/gke_release_gate.py",
        "P5": "python scripts/pubsub_reliability_gate.py",
    }.get(pattern_id, "echo gate")
    return f'''name: {app_name}-gcp-migration
on:
  push:
    branches: [ main ]
jobs:
  build-test-deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Setup Terraform
        uses: hashicorp/setup-terraform@v3
      - name: Build
        run: echo "Building {app_name}"
      - name: Test
        run: echo "Running tests"
      - name: Migration Gate
        run: {quality_gate}
      - name: Deploy
        run: {deploy_cmd}
      - name: Synthetic Validation
        run: python scripts/synthetic_smoke.py --target gcp
'''


def get_architecture(pattern_id: str) -> Dict:
    return PATTERN_TO_ARCH.get(pattern_id, PATTERN_TO_ARCH["P1"])


def get_changed_files(pattern_id: str) -> List[dict]:
    if pattern_id == "P1":
        return [
            {"file": "network/dmz.tf", "change": "Create DMZ subnet, firewall tiers, and private service perimeter"},
            {"file": "Jenkinsfile", "change": "Add DMZ security gate and blue/green cutover stage"},
            {"file": "terraform/web_mig.tf", "change": "Provision GCE MIG with Cloud Armor-protected ingress"},
        ]
    if pattern_id == "P2":
        return [
            {"file": "terraform/global_lb.tf", "change": "Create global L7 load balancer with URL maps and host rules"},
            {"file": "pipeline/global-routing-test.yml", "change": "Validate route/host behavior across regions before cutover"},
            {"file": "Jenkinsfile", "change": "Add weighted traffic shift stage and failback hooks"},
        ]
    if pattern_id == "P3":
        return [
            {"file": "terraform/cloudsql_dms.tf", "change": "Provision Cloud SQL HA and DMS continuous replication"},
            {"file": "database/cutover-runbook.md", "change": "Document checkpoint, CDC lag threshold, and rollback plan"},
            {"file": "Jenkinsfile", "change": "Add dry-run migration and data parity gate"},
        ]
    if pattern_id == "P4":
        return [
            {"file": "Jenkinsfile", "change": "Update deploy stage to deploy to GKE with kubectl"},
            {"file": "terraform/gke.tf", "change": "Add GKE cluster and node pool resources"},
            {"file": "k8s/deployment.yaml", "change": "Add Kubernetes deployment and service manifests"},
        ]
    if pattern_id == "P5":
        return [
            {"file": "terraform/pubsub.tf", "change": "Create Pub/Sub topics, subscriptions, and DLQ routing"},
            {"file": "services/subscriber.py", "change": "Add idempotent subscriber with retry semantics"},
            {"file": "Jenkinsfile", "change": "Add contract/replay validation stage for message flows"},
        ]
    return [
        {"file": "Jenkinsfile", "change": "Update deploy stage to execute Terraform for GCP"},
        {"file": "terraform/main.tf", "change": "Replace Azure resources with GCP compute/network resources"},
        {"file": "terraform/variables.tf", "change": "Add GCP project/region/network variables"},
    ]


def build_diff_payload(pattern_id: str) -> dict:
    files = get_changed_files(pattern_id)
    lines = [{"type": "@", "content": f"# {f['file']}"} for f in files]
    for f in files:
        lines.append({"type": "-", "content": "old deployment target: azure"})
        lines.append({"type": "+", "content": f"new deployment target: gcp ({f['change']})"})
    return {"changed_files": files, "lines": lines}
