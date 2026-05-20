import json
import logging
from mysql.connector import pooling
from config.settings import settings

logger = logging.getLogger(__name__)

_pool = None


def _get_pool():
    global _pool
    if _pool is None:
        _pool = pooling.MySQLConnectionPool(
            pool_name="healing_agent",
            pool_size=10,
            host=settings.DB_HOST,
            port=settings.DB_PORT,
            database=settings.DB_NAME,
            user=settings.DB_USER,
            password=settings.DB_PASSWORD,
            autocommit=False,
        )
    return _pool


def _get_conn():
    return _get_pool().get_connection()


# ── log_incidents ─────────────────────────────────────────────────────────────

_INCIDENTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS log_incidents (
    id               INT AUTO_INCREMENT PRIMARY KEY,
    application_name VARCHAR(100) NOT NULL,
    trace_id         VARCHAR(100),
    exception_type   VARCHAR(200),
    status           VARCHAR(50)  NOT NULL DEFAULT 'new',
    service          VARCHAR(100),
    severity         VARCHAR(20),
    analysis         TEXT,
    suggested_action TEXT,
    log_timestamp    DATETIME,
    created_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

# ── service_repo_map ──────────────────────────────────────────────────────────
# Maps service names to their GitHub repos for RCA code lookups.

_SERVICE_REPO_MAP_SCHEMA = """
CREATE TABLE IF NOT EXISTS service_repo_map (
    service_name   VARCHAR(255) NOT NULL PRIMARY KEY,
    github_org     VARCHAR(255) NOT NULL,
    github_repo    VARCHAR(255) NOT NULL,
    default_branch VARCHAR(100) NOT NULL DEFAULT 'main',
    language       VARCHAR(100),
    onboarded_at   DATETIME DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

# ── service_context_cache ─────────────────────────────────────────────────────
# GitHub file/tree content cached to avoid redundant API calls during RCA.

_SERVICE_CONTEXT_CACHE_SCHEMA = """
CREATE TABLE IF NOT EXISTS service_context_cache (
    id             INT          NOT NULL AUTO_INCREMENT PRIMARY KEY,
    service_name   VARCHAR(255) NOT NULL,
    cache_key      VARCHAR(500) NOT NULL,
    content        MEDIUMTEXT   NOT NULL,
    commit_sha     VARCHAR(40),
    created_at     DATETIME     DEFAULT CURRENT_TIMESTAMP,
    last_used_at   DATETIME     DEFAULT CURRENT_TIMESTAMP,
    invalidated_at DATETIME,
    UNIQUE KEY uq_cache (service_name, cache_key),
    INDEX idx_scc_service (service_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

# ── rca_jobs ──────────────────────────────────────────────────────────────────
# Persists every RCA job so job history and reports survive Flask restarts.

_RCA_JOBS_SCHEMA = """
CREATE TABLE IF NOT EXISTS rca_jobs (
    job_id       VARCHAR(36)  NOT NULL PRIMARY KEY,
    incident_id  INT          NOT NULL,
    status       VARCHAR(50)  NOT NULL DEFAULT 'queued',
    started_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at DATETIME,
    report       MEDIUMTEXT,
    error        TEXT,
    INDEX idx_rca_jobs_incident (incident_id),
    INDEX idx_rca_jobs_status   (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

_BASE_COLUMNS = frozenset({
    "application_name", "trace_id", "exception_type", "status",
    "service", "severity", "analysis", "suggested_action", "log_timestamp",
})

# Dynamic columns added to log_incidents when missing (schema migration)
_MIGRATION_COLUMNS = [
    ("raw_log",          "TEXT"),
    ("rca_status",       "VARCHAR(50)"),
    ("rca_report",       "MEDIUMTEXT"),
    ("rca_error",        "TEXT"),
    ("rca_started_at",   "DATETIME"),
    ("rca_completed_at", "DATETIME"),
]


def ensure_column(col_name: str, col_type: str = "TEXT") -> None:
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() "
            "  AND TABLE_NAME = 'log_incidents' "
            "  AND COLUMN_NAME = %s",
            (col_name,),
        )
        (exists,) = cur.fetchone()
        if not exists:
            cur.execute(f"ALTER TABLE log_incidents ADD COLUMN `{col_name}` {col_type}")
            conn.commit()
            logger.info("Schema migration: added column '%s %s'.", col_name, col_type)
        cur.close()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _recreate_rca_jobs_if_stale() -> None:
    """Drop rca_jobs if it exists without the job_id primary key column."""
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() "
            "  AND TABLE_NAME   = 'rca_jobs' "
            "  AND COLUMN_NAME  = 'job_id'"
        )
        (has_job_id,) = cur.fetchone()
        if not has_job_id:
            cur.execute("DROP TABLE IF EXISTS rca_jobs")
            conn.commit()
            logger.warning("Dropped stale rca_jobs table — will recreate with correct schema.")
        cur.close()
    except Exception:
        conn.rollback()
    finally:
        conn.close()


def clear_startup_tables() -> None:
    """Truncate transient tables on every server start. Leaves log_incidents and service_repo_map intact."""
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM rca_jobs")
        cur.execute("DELETE FROM service_context_cache")
        conn.commit()
        cur.close()
        logger.info("Startup clean: rca_jobs and service_context_cache cleared.")
    except Exception as exc:
        conn.rollback()
        logger.warning("Startup clean skipped: %s", exc)
    finally:
        conn.close()


def initialize_database() -> None:
    _recreate_rca_jobs_if_stale()
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(_INCIDENTS_SCHEMA)
        cur.execute(_SERVICE_REPO_MAP_SCHEMA)
        cur.execute(_SERVICE_CONTEXT_CACHE_SCHEMA)
        cur.execute(_RCA_JOBS_SCHEMA)
        conn.commit()
        cur.close()
        logger.info(
            "Tables ready: log_incidents, service_repo_map, "
            "service_context_cache, rca_jobs."
        )
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    for col_name, col_type in _MIGRATION_COLUMNS:
        ensure_column(col_name, col_type)


# ── log_incidents helpers ──────────────────────────────────────────────────────

def fetch_all_incidents() -> list[dict]:
    """Return all incidents ordered newest-first (no rca_report blob)."""
    conn = _get_conn()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("""
            SELECT id, application_name, trace_id, exception_type,
                   service, severity, analysis, suggested_action,
                   log_timestamp, created_at,
                   rca_status, rca_started_at, rca_completed_at
            FROM log_incidents
            ORDER BY created_at DESC
        """)
        rows = cur.fetchall()
        cur.close()
        result = []
        for row in rows:
            r = {}
            for k, v in row.items():
                r[k] = v.isoformat() if hasattr(v, "isoformat") else v
            result.append(r)
        return result
    except Exception as exc:
        logger.error("fetch_all_incidents failed: %s", exc)
        return []
    finally:
        conn.close()


def insert_incident(data: dict) -> int:
    defaults = {
        "application_name": "Banking Core Platform",
        "trace_id":         None,
        "exception_type":   None,
        "status":           "new",
        "service":          None,
        "severity":         None,
        "analysis":         None,
        "suggested_action": None,
        "log_timestamp":    None,
    }
    record = {**defaults, **data}

    base_record  = {k: v for k, v in record.items() if k in _BASE_COLUMNS}
    extra_record = {k: v for k, v in record.items()
                    if k not in _BASE_COLUMNS and k not in ("id", "created_at")}

    for col in extra_record:
        ensure_column(col)

    full_record  = {**base_record, **extra_record}
    cols         = list(full_record.keys())
    col_list     = ", ".join(f"`{c}`" for c in cols)
    placeholders = ", ".join(f"%({c})s" for c in cols)

    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            f"INSERT INTO log_incidents ({col_list}) VALUES ({placeholders})",
            full_record,
        )
        incident_id = cur.lastrowid
        conn.commit()
        cur.close()
        return incident_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ── rca_jobs helpers ───────────────────────────────────────────────────────────

def insert_rca_job(job_id: str, incident_id: int) -> None:
    """Create a new RCA job row in the queued state."""
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO rca_jobs (job_id, incident_id, status) VALUES (%s, %s, 'queued')",
            (job_id, incident_id),
        )
        conn.commit()
        cur.close()
    finally:
        conn.close()


def update_rca_job(
    job_id: str,
    status: str,
    report_json: str | None = None,
    error: str | None = None,
) -> None:
    """Update job status; attach report or error text on terminal states."""
    conn = _get_conn()
    try:
        cur = conn.cursor()
        if status == "running":
            cur.execute(
                "UPDATE rca_jobs SET status = 'running' WHERE job_id = %s",
                (job_id,),
            )
            # Also mark the incident as in-progress
            cur.execute(
                "UPDATE log_incidents SET rca_status = 'running', rca_started_at = NOW() "
                "WHERE id = (SELECT incident_id FROM rca_jobs WHERE job_id = %s)",
                (job_id,),
            )
        elif status == "completed":
            cur.execute(
                "UPDATE rca_jobs SET status = 'completed', completed_at = NOW(), report = %s "
                "WHERE job_id = %s",
                (report_json, job_id),
            )
            cur.execute(
                "UPDATE log_incidents SET rca_status = 'completed', rca_completed_at = NOW() "
                "WHERE id = (SELECT incident_id FROM rca_jobs WHERE job_id = %s)",
                (job_id,),
            )
        elif status == "failed":
            cur.execute(
                "UPDATE rca_jobs SET status = 'failed', completed_at = NOW(), error = %s "
                "WHERE job_id = %s",
                (error or "", job_id),
            )
            cur.execute(
                "UPDATE log_incidents SET rca_status = 'failed', rca_completed_at = NOW() "
                "WHERE id = (SELECT incident_id FROM rca_jobs WHERE job_id = %s)",
                (job_id,),
            )
        conn.commit()
        cur.close()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def fetch_rca_jobs() -> list[dict]:
    """Return all RCA jobs (no report blob) ordered newest-first."""
    conn = _get_conn()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT job_id, incident_id, status, started_at, completed_at, error "
            "FROM rca_jobs ORDER BY started_at DESC"
        )
        rows = cur.fetchall()
        cur.close()
        result = []
        for row in rows:
            r = {}
            for k, v in row.items():
                r[k] = v.isoformat() if hasattr(v, "isoformat") else v
            result.append(r)
        return result
    except Exception as exc:
        logger.error("fetch_rca_jobs failed: %s", exc)
        return []
    finally:
        conn.close()


def fetch_rca_job(job_id: str) -> dict | None:
    """Return a single RCA job including the full report JSON."""
    conn = _get_conn()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM rca_jobs WHERE job_id = %s", (job_id,))
        row = cur.fetchone()
        cur.close()
        if not row:
            return None
        result = {}
        for k, v in row.items():
            result[k] = v.isoformat() if hasattr(v, "isoformat") else v
        # Parse report JSON if stored as string
        if result.get("report") and isinstance(result["report"], str):
            try:
                result["report"] = json.loads(result["report"])
            except Exception:
                pass
        return result
    except Exception as exc:
        logger.error("fetch_rca_job failed: %s", exc)
        return None
    finally:
        conn.close()
