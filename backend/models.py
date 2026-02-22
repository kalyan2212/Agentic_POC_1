"""Pydantic models for J.A.R.V.I.S. API."""

from typing import Any, List, Optional
from pydantic import BaseModel, Field


# ── Auth / GitHub ────────────────────────────────────────────
class GitHubConnectRequest(BaseModel):
    token: str
    user:  str


class GitHubConnectResponse(BaseModel):
    connected: bool
    username:  str
    name:      Optional[str] = None
    avatar_url: Optional[str] = None
    public_repos: int = 0
    message:   str = "Connected"


# ── Assessment ───────────────────────────────────────────────
class ScanRequest(BaseModel):
    repos:       List[str] = Field(..., description="List of repo full_names to scan, e.g. ['owner/repo']")
    deep_scan:   bool = False
    classify:    bool = True
    bundle:      bool = True


class BundleApproval(BaseModel):
    approve:  bool
    comment:  str = ""


# ── Migration ────────────────────────────────────────────────
class MigrationRunRequest(BaseModel):
    app_id:    str
    pattern:   str        # P1..P5
    wave_id:   Optional[str] = None


class ApprovalRequest(BaseModel):
    approve:   bool
    comment:   str = ""
    approved_by: str = "PMO"


class WaveStartRequest(BaseModel):
    wave_id: str


# ── Integration ──────────────────────────────────────────────
class ServiceNowConfig(BaseModel):
    instance_url:   str          # e.g. https://company.service-now.com
    username:       str
    password:       str
    change_table:   str = "change_request"
    incident_table: str = "incident"


class SharePointConfig(BaseModel):
    tenant_id:    str
    client_id:    str
    client_secret: str
    site_url:     str
    doc_library:  str = "Documents"


class JenkinsConfig(BaseModel):
    url:        str               # e.g. https://jenkins.company.com
    username:   str
    api_token:  str
    folder:     str = ""          # Jenkins folder containing migration jobs


class IntegrationSaveRequest(BaseModel):
    service:    str               # servicenow|sharepoint|jenkins
    enabled:    bool
    config:     dict


# ── PMO ─────────────────────────────────────────────────────
class ReportRequest(BaseModel):
    type:   str = "weekly"
    period: str = "this-week"
