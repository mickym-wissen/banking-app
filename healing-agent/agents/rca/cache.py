"""
RCA context cache — GitHub file content cached in service_context_cache table.
Uses the healing agent's existing mysql-connector-python connection pool.
"""
import json
import logging
from db.database import _get_conn

logger = logging.getLogger(__name__)


def _json_load(v):
    """Handle both already-parsed dict/list (connector may auto-parse JSON) and raw strings."""
    if v is None:
        return None
    if isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v)
    except Exception:
        return None


def read_cache(service_name: str, cache_key: str) -> dict | None:
    conn = _get_conn()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT content, invalidated_at FROM service_context_cache "
            "WHERE service_name = %s AND cache_key = %s",
            (service_name, cache_key),
        )
        row = cur.fetchone()
        cur.close()

        if not row or row["invalidated_at"] is not None:
            return None

        # Update last_used_at
        cur2 = conn.cursor()
        cur2.execute(
            "UPDATE service_context_cache SET last_used_at = NOW() "
            "WHERE service_name = %s AND cache_key = %s",
            (service_name, cache_key),
        )
        conn.commit()
        cur2.close()

        logger.debug("Cache HIT: %s / %s", service_name, cache_key)
        return _json_load(row["content"])
    finally:
        conn.close()


def write_cache(
    service_name: str, cache_key: str, content: dict, commit_sha: str | None = None
) -> None:
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO service_context_cache
                   (service_name, cache_key, content, commit_sha)
               VALUES (%s, %s, %s, %s)
               ON DUPLICATE KEY UPDATE
                   content        = VALUES(content),
                   commit_sha     = VALUES(commit_sha),
                   created_at     = NOW(),
                   last_used_at   = NOW(),
                   invalidated_at = NULL""",
            (service_name, cache_key, json.dumps(content), commit_sha),
        )
        conn.commit()
        cur.close()
        logger.debug("Cache WRITE: %s / %s", service_name, cache_key)
    finally:
        conn.close()


def append_rca_history(service_name: str, summary: dict) -> None:
    existing = read_cache(service_name, "rca_history") or {"entries": []}
    entries = existing.get("entries", [])
    entries.append(summary)
    entries = entries[-10:]
    write_cache(service_name, "rca_history", {"entries": entries})
