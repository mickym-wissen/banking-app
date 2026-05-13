import json
import re
from typing import Optional

# Matches both log formats emitted by logback-spring.xml:
#
#   Old (no trace bracket):
#   2026-05-13 10:00:00.123 [http-nio-8080-exec-1] INFO  c.d.b.service.AccountService - msg
#
#   New (with MDC traceId bracket):
#   2026-05-13 10:00:00.123 [http-nio-8080-exec-1] [abc123] INFO  c.d.b.service.AccountService - msg
#
# The (?:...) trace group is optional so both formats parse cleanly.
_LOG_START_RE = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+)\s+"
    r"\[(?P<thread>[^\]]+)\]\s+"
    r"(?:\[(?P<trace_id>[^\]]*)\]\s+)?"       # optional [traceId] from MDC
    r"(?P<level>INFO|WARN|WARNING|ERROR|DEBUG|FATAL|TRACE)\s+"
    r"(?P<logger>\S+)\s+-\s+"
    r"(?P<message>.+)"
)

_STACK_LINE_RE = re.compile(r"^\s+(at |Caused by:|java\.|javax\.|org\.|com\.|io\.|\.\.\. )")

# Fields tracked for absence — stored as JSON in missing_fields column
_STANDARD_FIELDS = ["trace_id", "exception_type", "span_id", "user_id", "transaction_id"]

# Sentinel values that mean "no real trace ID was provided"
_EMPTY_TRACE_SENTINELS = {"n/a", "-", ""}


def is_log_start(line: str) -> bool:
    return bool(_LOG_START_RE.match(line))


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

    # Normalise trace_id: treat sentinels and missing bracket as None
    raw_trace = (m.get("trace_id") or "").strip()
    trace_id = raw_trace if raw_trace.lower() not in _EMPTY_TRACE_SENTINELS else None

    parsed = {
        "timestamp":      m["timestamp"],
        "log_level":      m["level"].upper(),
        "thread":         m["thread"],
        "logger":         logger_name,
        "service":        service,
        "message":        m["message"],
        "stack_trace":    stack_trace,
        "exception_type": exception_type,
        "trace_id":       trace_id,
        "span_id":        None,
        "user_id":        None,
        "transaction_id": None,
        "raw_log":        raw,
    }

    # Record which standard fields are absent so the DB column reflects reality
    parsed["missing_fields"] = json.dumps(
        [f for f in _STANDARD_FIELDS if not parsed.get(f)]
    )

    return parsed


def is_actionable(parsed: dict) -> bool:
    return parsed.get("log_level") in ("WARN", "WARNING", "ERROR", "FATAL")
