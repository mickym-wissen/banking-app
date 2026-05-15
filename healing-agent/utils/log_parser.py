import re
from datetime import datetime, timezone
from typing import Optional

# Matches both log formats emitted by logback-spring.xml:
#
#   2026-05-15 10:00:00.123 [http-nio-8080-exec-1] INFO  c.d.b.service.AccountService - msg
#   2026-05-15 10:00:00.123 [http-nio-8080-exec-1] [abc123] INFO  c.d.b.service.AccountService - msg
#
_LOG_START_RE = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+)\s+"
    r"\[(?P<thread>[^\]]+)\]\s+"
    r"(?:\[(?P<trace_id>[^\]]*)\]\s+)?"
    r"(?P<level>INFO|WARN|WARNING|ERROR|DEBUG|FATAL|TRACE)\s+"
    r"(?P<logger>\S+)\s+-\s+"
    r"(?P<message>.+)"
)

_EMPTY_TRACE_SENTINELS = {"n/a", "-", ""}


def _utc_to_local(ts_str: str) -> str:
    """
    The banking app writes UTC timestamps (no timezone marker).
    Convert to local system timezone before storing/displaying.
    """
    try:
        dt_utc = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S.%f").replace(tzinfo=timezone.utc)
        dt_local = dt_utc.astimezone(tz=None)
        ms = dt_local.microsecond // 1000
        return dt_local.strftime("%Y-%m-%d %H:%M:%S.") + f"{ms:03d}"
    except Exception:
        return ts_str


def parse_entry(raw: str) -> dict:
    lines = raw.strip().splitlines()
    if not lines:
        return {}

    match = _LOG_START_RE.match(lines[0])
    if not match:
        return {"raw_log": raw, "message": raw, "log_level": "UNKNOWN"}

    m = match.groupdict()
    stack_lines = [ln for ln in lines[1:] if ln.strip()]
    stack_trace = "\n".join(stack_lines) if stack_lines else None

    exception_type: Optional[str] = None
    if stack_trace:
        first_exc = re.search(r"([\w.]+Exception|[\w.]+Error):", stack_trace)
        if first_exc:
            exception_type = first_exc.group(1).split(".")[-1]

    logger_name = m["logger"]
    service = logger_name.split(".")[-1] if logger_name else "banking-app"

    raw_trace = (m.get("trace_id") or "").strip()
    trace_id = raw_trace if raw_trace.lower() not in _EMPTY_TRACE_SENTINELS else None

    return {
        "timestamp":      _utc_to_local(m["timestamp"]),
        "log_level":      m["level"].upper(),
        "logger":         logger_name,
        "service":        service,
        "message":        m["message"],
        "stack_trace":    stack_trace,
        "exception_type": exception_type,
        "trace_id":       trace_id,
        "raw_log":        raw,
    }


def is_actionable(parsed: dict) -> bool:
    return parsed.get("log_level") in ("WARN", "WARNING", "ERROR", "FATAL")
