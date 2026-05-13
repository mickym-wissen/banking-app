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


# Base schema — uses IF NOT EXISTS so restarts don't wipe data
_SCHEMA = """
CREATE TABLE IF NOT EXISTS log_incidents (
    id                 INT AUTO_INCREMENT PRIMARY KEY,
    application_name   VARCHAR(100) NOT NULL,
    trace_id           VARCHAR(100),
    exception_type     VARCHAR(200),
    status             VARCHAR(50)  NOT NULL DEFAULT 'new',
    service            VARCHAR(100),
    log_level          VARCHAR(20),
    severity           VARCHAR(20),
    message            TEXT,
    analysis           TEXT,
    suggested_action   TEXT,
    affected_component VARCHAR(200),
    thread             VARCHAR(200),
    log_timestamp      DATETIME,
    raw_log            MEDIUMTEXT,
    missing_fields     TEXT,
    created_at         DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_service (service),
    INDEX idx_status  (status),
    INDEX idx_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

# Columns guaranteed present after schema creation (used to route extra Gemini fields)
_BASE_COLUMNS = frozenset({
    "application_name", "trace_id", "exception_type", "status", "service",
    "log_level", "severity", "message", "analysis", "suggested_action",
    "affected_component", "thread", "log_timestamp", "raw_log", "missing_fields",
})

# New columns added compared with the previous schema — migrated on each startup
_MIGRATION_COLUMNS = [
    ("affected_component", "VARCHAR(200)"),
    ("thread",             "VARCHAR(200)"),
    ("log_timestamp",      "DATETIME"),
    ("raw_log",            "MEDIUMTEXT"),
    ("missing_fields",     "TEXT"),
]


def ensure_column(col_name: str, col_type: str = "TEXT") -> None:
    """Add col_name to log_incidents if it does not already exist."""
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
            logger.info("Schema migration: added column '%s %s' to log_incidents.", col_name, col_type)
        cur.close()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def initialize_database() -> None:
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(_SCHEMA)
        conn.commit()
        cur.close()
        logger.info("Table log_incidents ready.")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    # Run column migrations for any fields added since the original schema
    for col_name, col_type in _MIGRATION_COLUMNS:
        ensure_column(col_name, col_type)


def insert_incident(data: dict) -> int:
    defaults = {
        "application_name":   "Banking Core Platform",
        "trace_id":           None,
        "exception_type":     None,
        "status":             "new",
        "service":            None,
        "log_level":          None,
        "severity":           None,
        "message":            None,
        "analysis":           None,
        "suggested_action":   None,
        "affected_component": None,
        "thread":             None,
        "log_timestamp":      None,
        "raw_log":            None,
        "missing_fields":     None,
    }
    record = {**defaults, **data}

    # Split into known base columns vs. any extra fields Gemini returned
    base_record  = {k: v for k, v in record.items() if k in _BASE_COLUMNS}
    extra_record = {k: v for k, v in record.items()
                    if k not in _BASE_COLUMNS and k not in ("id", "created_at")}

    # Dynamically add any unrecognised columns that Gemini may have returned
    for col in extra_record:
        ensure_column(col)

    full_record = {**base_record, **extra_record}
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
