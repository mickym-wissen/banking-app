"""
Resolves a service name to a GitHub repo.
Priority: CI/CD adapter → service_repo_map table → sub-agent discovery.
Uses the healing agent's existing mysql-connector-python pool.
"""
import logging
from datetime import datetime, timedelta, timezone
from db.database import _get_conn
from .models import RepoMapping, DeploymentRecord

logger = logging.getLogger(__name__)


def _get_mapping(service_name: str) -> RepoMapping | None:
    conn = _get_conn()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT * FROM service_repo_map WHERE service_name = %s", (service_name,)
        )
        row = cur.fetchone()
        cur.close()
        if not row:
            return None
        return RepoMapping(
            service_name=row["service_name"],
            github_org=row["github_org"],
            github_repo=row["github_repo"],
            default_branch=row.get("default_branch", "main"),
            language=row.get("language"),
        )
    finally:
        conn.close()


def _write_mapping(service_name: str, org: str, repo: str) -> None:
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO service_repo_map (service_name, github_org, github_repo)
               VALUES (%s, %s, %s)
               ON DUPLICATE KEY UPDATE
                   github_org  = VALUES(github_org),
                   github_repo = VALUES(github_repo)""",
            (service_name, org, repo),
        )
        conn.commit()
        cur.close()
    finally:
        conn.close()


class RepoResolver:
    def __init__(self, cicd_adapter):
        self.cicd = cicd_adapter

    def resolve(self, service_name: str, environment: str, occurred_at: datetime) -> dict:
        """
        Returns: {org, repo, branch, commit_sha, deployment_record_used, discovered_via}
        """
        from config.settings import settings

        # Step 1: ask CI/CD adapter (may return empty for NoCICDAdapter)
        since = occurred_at - timedelta(hours=48)
        deployments = self.cicd.get_recent_deployments(service_name, environment, since)
        latest = deployments[0] if deployments else None

        github_repo = latest.github_repo if latest else None
        branch = latest.branch if latest else None
        commit_sha = latest.commit_sha if latest else None
        deployment_record_used = latest is not None

        # Step 2: service_repo_map lookup
        mapping = _get_mapping(service_name)

        if github_repo and mapping:
            return dict(
                org=mapping.github_org, repo=github_repo, branch=branch,
                commit_sha=commit_sha, deployment_record_used=deployment_record_used,
                discovered_via="cicd+mapping",
            )

        if github_repo and not mapping:
            _write_mapping(service_name, settings.GITHUB_ORG, github_repo)
            return dict(
                org=settings.GITHUB_ORG, repo=github_repo, branch=branch,
                commit_sha=commit_sha, deployment_record_used=deployment_record_used,
                discovered_via="cicd",
            )

        if not github_repo and mapping:
            return dict(
                org=mapping.github_org, repo=mapping.github_repo,
                branch=branch or mapping.default_branch,
                commit_sha=commit_sha, deployment_record_used=deployment_record_used,
                discovered_via="mapping",
            )

        # Step 3: sub-agent discovery
        logger.info("No repo found for %s — launching repo discovery sub-agent", service_name)
        from .sub_agents.repo_discovery import RepoDiscoverySubAgent
        result = RepoDiscoverySubAgent().discover(service_name)
        if "error" not in result:
            _write_mapping(service_name, result["github_org"], result["github_repo"])
            return dict(
                org=result["github_org"], repo=result["github_repo"],
                branch=branch or "main", commit_sha=commit_sha,
                deployment_record_used=deployment_record_used,
                discovered_via="sub_agent",
            )

        raise RuntimeError(
            f"Could not resolve GitHub repo for service '{service_name}'. "
            "Add a row to service_repo_map or set GITHUB_ORG and ensure the sub-agent "
            "can search the org."
        )
