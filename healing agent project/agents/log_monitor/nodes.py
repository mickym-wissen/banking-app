import json
import logging
from datetime import datetime
from typing import Optional, TypedDict

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage, SystemMessage

from config.settings import settings
from db.database import insert_incident
from utils.log_parser import is_actionable, parse_entry

logger = logging.getLogger(__name__)

# ── State ─────────────────────────────────────────────────────────────────────

class LogMonitorState(TypedDict):
    raw_log_entry:  str
    parsed_entry:   Optional[dict]
    analysis:       Optional[dict]
    db_incident_id: Optional[int]
    should_skip:    bool
    error:          Optional[str]


# ── LLM ───────────────────────────────────────────────────────────────────────

_llm = ChatGoogleGenerativeAI(
    model="gemini-2.5-flash",
    google_api_key=settings.GEMINI_API_KEY,
    temperature=0,
    max_output_tokens=1024,
)

_SYSTEM_PROMPT = """You are a senior SRE analyzing banking microservice log entries.
Return ONLY a valid JSON object — no markdown, no explanation.

{
  "exception_type": "<specific exception class or null>",
  "severity": "<CRITICAL | HIGH | MEDIUM | LOW>",
  "application_name": "<inferred app name>",
  "affected_component": "<class or subsystem>",
  "analysis": "<1-2 sentence root-cause>",
  "suggested_action": "<one actionable L1 remediation step>"
}

Severity guide: CRITICAL=data loss/OOM/DB down, HIGH=payment/auth failure, MEDIUM=latency/retry, LOW=warnings."""

# Known keys returned by the base Gemini prompt — anything else is an extra field
_KNOWN_ANALYSIS_KEYS = frozenset({
    "exception_type", "severity", "application_name",
    "affected_component", "analysis", "suggested_action",
})


# ── ANSI colours ──────────────────────────────────────────────────────────────

_R    = "\033[0m"
_BOLD = "\033[1m"
_WARN = "\033[93m"
_COLOURS = {"CRITICAL": "\033[91m", "HIGH": "\033[91m", "MEDIUM": "\033[93m", "LOW": "\033[96m"}


# ── Nodes ─────────────────────────────────────────────────────────────────────

def parse_log_entry(state: LogMonitorState) -> dict:
    return {"parsed_entry": parse_entry(state["raw_log_entry"])}


def route_after_parse(state: LogMonitorState) -> str:
    return "continue" if state["parsed_entry"] and is_actionable(state["parsed_entry"]) else "skip"


def analyze_with_gemini(state: LogMonitorState) -> dict:
    parsed = state["parsed_entry"]
    messages = [
        SystemMessage(content=_SYSTEM_PROMPT),
        HumanMessage(content=(
            f"Log entry:\n```\n{state['raw_log_entry']}\n```\n"
            f"Service: {parsed.get('service')} | Level: {parsed.get('log_level')} | "
            f"Message: {parsed.get('message')} | Exception hint: {parsed.get('exception_type')}"
        )),
    ]
    try:
        raw = _llm.invoke(messages).content.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1].lstrip("json").strip()
        return {"analysis": json.loads(raw)}
    except json.JSONDecodeError:
        return {"analysis": {
            "exception_type":     parsed.get("exception_type"),
            "severity":           "HIGH" if parsed.get("log_level") == "ERROR" else "MEDIUM",
            "application_name":   "Banking Core Platform",
            "affected_component": parsed.get("service"),
            "analysis":           (parsed.get("message") or "")[:250],
            "suggested_action":   "Investigate logs manually.",
        }}
    except Exception as exc:
        logger.error("Gemini error: %s", exc)
        return {"error": str(exc), "analysis": None}


def store_to_db(state: LogMonitorState) -> dict:
    parsed   = state["parsed_entry"] or {}
    analysis = state["analysis"] or {}

    # Convert log timestamp string → MySQL DATETIME string
    log_ts = None
    raw_ts = parsed.get("timestamp")
    if raw_ts:
        try:
            log_ts = datetime.strptime(raw_ts, "%Y-%m-%d %H:%M:%S.%f").strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass

    record = {
        "application_name":   analysis.get("application_name") or "Banking Core Platform",
        "trace_id":           parsed.get("trace_id"),
        "exception_type":     analysis.get("exception_type") or parsed.get("exception_type"),
        "service":            parsed.get("service"),
        "log_level":          parsed.get("log_level"),
        "severity":           analysis.get("severity"),
        "message":            parsed.get("message"),
        "analysis":           analysis.get("analysis"),
        "suggested_action":   analysis.get("suggested_action"),
        "affected_component": analysis.get("affected_component"),
        "thread":             parsed.get("thread"),
        "log_timestamp":      log_ts,
        "raw_log":            parsed.get("raw_log"),
        "missing_fields":     parsed.get("missing_fields"),
    }

    # Pass through any extra fields Gemini returned beyond the base prompt keys.
    # insert_incident() will call ensure_column() for each unknown column.
    for key, val in analysis.items():
        if key not in _KNOWN_ANALYSIS_KEYS:
            record[key] = str(val) if val is not None else None

    try:
        incident_id = insert_incident(record)
        return {"db_incident_id": incident_id}
    except Exception as exc:
        logger.error("DB insert failed: %s", exc)
        return {"error": str(exc), "db_incident_id": None}


def send_alert(state: LogMonitorState) -> dict:
    parsed   = state["parsed_entry"] or {}
    analysis = state["analysis"] or {}
    severity = (analysis.get("severity") or "HIGH").upper()
    colour   = _COLOURS.get(severity, "\033[97m")
    iid      = state.get("db_incident_id")
    w        = 72

    missing_raw = parsed.get("missing_fields")
    missing     = json.loads(missing_raw) if missing_raw else []

    trace_display = (
        parsed.get("trace_id")
        or f"{_WARN}N/A — not present in log format{_R}"
    )

    print(f"\n{colour}{_BOLD}{'=' * w}{_R}")
    print(f"{colour}{_BOLD}{'  INCIDENT ALERT':^{w}}{_R}")
    print(f"{colour}{_BOLD}{'=' * w}{_R}")
    print(f"  {_BOLD}Time           :{_R} {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  {_BOLD}Log Timestamp  :{_R} {parsed.get('timestamp', 'N/A')}")
    if iid:
        print(f"  {_BOLD}Incident ID    :{_R} \033[92m#{iid}{_R}")
    else:
        print(f"  {_BOLD}Incident ID    :{_R} \033[91mNOT STORED{_R}")
    print(f"  {_BOLD}Severity       :{_R} {colour}{severity}{_R}")
    print(f"  {_BOLD}Service        :{_R} {parsed.get('service', 'unknown')}")
    print(f"  {_BOLD}Level          :{_R} {parsed.get('log_level', 'unknown')}")
    print(f"  {_BOLD}Thread         :{_R} {parsed.get('thread', 'N/A')}")
    print(f"  {_BOLD}Trace ID       :{_R} {trace_display}")
    print(f"  {_BOLD}Component      :{_R} {analysis.get('affected_component', 'N/A')}")
    print(f"  {_BOLD}Exception      :{_R} {analysis.get('exception_type') or 'N/A'}")
    if missing:
        print(f"  {_BOLD}Missing Fields :{_R} {_WARN}{', '.join(missing)}{_R}  ← stored as NULL in DB")
    print(f"\n  {_BOLD}Analysis:{_R}")
    print(f"    {analysis.get('analysis', 'N/A')}")
    print(f"\n  {_BOLD}Action:{_R}")
    print(f"    \033[96m{analysis.get('suggested_action', 'N/A')}{_R}")
    print(f"{colour}{'-' * w}{_R}\n", flush=True)
    return {}
