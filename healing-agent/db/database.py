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


_SCHEMA = """
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

_BASE_COLUMNS = frozenset({
    "application_name", "trace_id", "exception_type", "status",
    "service", "severity", "analysis", "suggested_action", "log_timestamp",
})

_MIGRATION_COLUMNS = []


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

    for col_name, col_type in _MIGRATION_COLUMNS:
        ensure_column(col_name, col_type)


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
