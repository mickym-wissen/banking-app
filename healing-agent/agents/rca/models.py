from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from pydantic import BaseModel


@dataclass
class ErrorLogEntry:
    id: str
    service_name: str
    environment: str
    error_type: str
    error_message: str
    stack_trace: list[dict]
    severity: str
    occurred_at: datetime
    request_id: str | None = None
    metadata: dict = field(default_factory=dict)


@dataclass
class DeploymentRecord:
    service_name: str
    environment: str
    branch: str
    commit_sha: str
    deployed_at: datetime
    status: str
    github_repo: str | None = None
    commit_message: str | None = None
    deployer: str | None = None
    pipeline_id: str | None = None
    pipeline_url: str | None = None


@dataclass
class RepoMapping:
    service_name: str
    github_org: str
    github_repo: str
    default_branch: str = "main"
    language: str | None = None


# ── RCA Report (Pydantic for validation) ──────────────────────────────────────

class CodeReference(BaseModel):
    file: str
    line: int | None = None
    function: str | None = None
    repo: str
    commit_sha: str | None = None
    github_url: str | None = None


class RegressionInfo(BaseModel):
    commit_sha: str
    commit_message: str | None = None
    author: str | None = None
    deployed_at: str | None = None


class RootCause(BaseModel):
    summary: str
    confidence: str
    confidence_reason: str
    code_reference: CodeReference
    regression_introduced_by: RegressionInfo | None = None


class IncidentSummary(BaseModel):
    what: str
    when: str
    environment: str
    severity: str
    estimated_impact: str | None = None


class TimelineEvent(BaseModel):
    timestamp: str
    event: str


class Evidence(BaseModel):
    type: str
    description: str
    value: str


class ImpactAssessment(BaseModel):
    affected_service: str
    affected_environment: str
    affected_functionality: str | None = None
    inferred_error_rate: str | None = None
    duration_estimate: str | None = None


class SuggestedSolution(BaseModel):
    priority: int
    effort: str
    title: str
    description: str
    code_suggestion: str | None = None


class AnalysisMetadata(BaseModel):
    model: str
    react_iterations: int
    github_files_fetched: list[str] = []
    cache_hits: list[str] = []
    deployment_record_used: bool = False
    repo_discovered_via: str = "service_repo_map"


class RCAReport(BaseModel):
    schema_version: str = "1.0"
    rca_id: str
    error_log_id: str
    service_name: str
    generated_at: str
    incident_summary: IncidentSummary
    timeline: list[TimelineEvent]
    root_cause: RootCause
    contributing_factors: list[str] = []
    evidence: list[Evidence] = []
    impact_assessment: ImpactAssessment
    suggested_solutions: list[SuggestedSolution]
    prevention_recommendations: list[str] = []
    analysis_metadata: AnalysisMetadata
