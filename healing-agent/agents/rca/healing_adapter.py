"""
HealingDBAdapter — bridges the healing agent's log_incidents table to the
ErrorLogEntry model expected by the RCA agent.
"""
import logging
from datetime import datetime, timezone
from db.database import _get_conn
from .models import ErrorLogEntry

logger = logging.getLogger(__name__)


class HealingDBAdapter:
    """Read incidents from log_incidents and wrap them as ErrorLogEntry."""

    def get_error_log(self, incident_id: str) -> ErrorLogEntry:
        conn = _get_conn()
        try:
            cur = conn.cursor(dictionary=True)
            cur.execute("SELECT * FROM log_incidents WHERE id = %s", (int(incident_id),))
            row = cur.fetchone()
            cur.close()
        finally:
            conn.close()

        if not row:
            raise ValueError(f"Incident #{incident_id} not found in log_incidents")

        occurred_at = row.get("log_timestamp") or row.get("created_at")
        if occurred_at is None:
            occurred_at = datetime.now(timezone.utc)
        elif isinstance(occurred_at, datetime) and occurred_at.tzinfo is None:
            occurred_at = occurred_at.replace(tzinfo=timezone.utc)

        # Build a rich error_message from all available context
        parts = []
        if row.get("exception_type"):
            parts.append(f"Exception: {row['exception_type']}")
        if row.get("analysis"):
            parts.append(f"Analysis: {row['analysis']}")
        if row.get("suggested_action"):
            parts.append(f"Suggested Action: {row['suggested_action']}")
        if row.get("raw_log"):
            parts.append(f"\nRaw Log:\n{row['raw_log']}")
        error_message = "\n".join(parts) or "No details available"

        service = row.get("service") or "banking-app"
        severity = (row.get("severity") or "HIGH").lower()

        return ErrorLogEntry(
            id=str(row["id"]),
            service_name=service,
            environment="production",
            error_type=row.get("exception_type") or "ApplicationError",
            error_message=error_message,
            stack_trace=[],
            severity=severity,
            occurred_at=occurred_at,
            request_id=row.get("trace_id"),
            metadata={
                "application_name": row.get("application_name"),
                "trace_id": row.get("trace_id"),
                "log_level": row.get("severity"),
            },
        )

    def get_service_metadata(self, service_name: str) -> dict:
        return {"service_name": service_name}


class NoCICDAdapter:
    """No CI/CD integration — resolver falls through to service_repo_map / sub-agent."""
    def get_recent_deployments(self, service_name, environment, since, limit=5):
        return []
