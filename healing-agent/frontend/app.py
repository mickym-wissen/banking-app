"""
Self-Healing Agent Dashboard — Flask backend
Run from the healing-agent/ directory:
    python frontend/app.py
"""
import sys
import os
import logging
import threading
import queue
import json
import time
from typing import Optional

# ── Path setup ────────────────────────────────────────────────────────────────
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(_ROOT, ".env"))

from flask import Flask, render_template, request, jsonify, Response, stream_with_context

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
log = logging.getLogger("dashboard")

app = Flask(__name__)

# ── SSE broadcast registry ─────────────────────────────────────────────────────
# Each connected browser tab gets its own queue; we broadcast to all of them.

_log_subs: list[queue.Queue] = []
_inc_subs: list[queue.Queue] = []
_sub_lock = threading.Lock()


def _broadcast(bucket: list, item: dict) -> None:
    with _sub_lock:
        dead = []
        for q in bucket:
            try:
                q.put_nowait(item)
            except queue.Full:
                dead.append(q)
        for q in dead:
            bucket.remove(q)


def _subscribe(bucket: list) -> queue.Queue:
    q: queue.Queue = queue.Queue(maxsize=600)
    with _sub_lock:
        bucket.append(q)
    return q


def _unsubscribe(bucket: list, q: queue.Queue) -> None:
    with _sub_lock:
        if q in bucket:
            bucket.remove(q)


# ── Shared monitor state ───────────────────────────────────────────────────────

_config: dict = {
    "source":     None,
    "local_path": os.getenv("LOG_FILE_PATH", "../bank-application/logs/banking-app.log"),
    "dd_api_key": "",
    "dd_app_key": "",
    "dd_site":    os.getenv("DD_SITE", "us5.datadoghq.com"),
    "dd_query":   os.getenv("DD_QUERY", "service:banking-app"),
}

_monitor_thread: Optional[threading.Thread] = None
_stop_event = threading.Event()
_stats = {"total_logs": 0, "incidents": 0, "critical": 0, "high": 0, "medium": 0}
_stats_lock = threading.Lock()


# ── Log entry processing ───────────────────────────────────────────────────────

def _process_entry(raw: str, source_name: str = "local") -> None:
    """Parse one raw log entry, broadcast it, and kick off analysis if actionable."""
    from utils.log_parser import parse_entry, is_actionable

    parsed = parse_entry(raw)
    level = parsed.get("log_level", "UNKNOWN")
    actionable = is_actionable(parsed)

    with _stats_lock:
        _stats["total_logs"] += 1

    _broadcast(_log_subs, {
        "type":      "log",
        "level":     level,
        "timestamp": parsed.get("timestamp", ""),
        "service":   parsed.get("service", ""),
        "message":   parsed.get("message", raw[:300]),
        "raw":       raw,
        "source":    source_name,
        "actionable": actionable,
    })

    if actionable:
        threading.Thread(
            target=_analyze_and_store,
            args=(raw, parsed),
            daemon=True,
        ).start()


def _analyze_and_store(raw: str, parsed: dict) -> None:
    """Run Gemini analysis and store the incident to MySQL; broadcast the result."""
    try:
        from agents.log_monitor.nodes import analyze_with_gemini, store_to_db, LogMonitorState

        state: LogMonitorState = {
            "raw_log_entry":  raw,
            "parsed_entry":   parsed,
            "analysis":       None,
            "db_incident_id": None,
            "should_skip":    False,
            "error":          None,
        }

        state.update(analyze_with_gemini(state))
        state.update(store_to_db(state))

        analysis = state.get("analysis") or {}
        severity = (analysis.get("severity") or "HIGH").upper()

        with _stats_lock:
            _stats["incidents"] += 1
            if severity == "CRITICAL":
                _stats["critical"] += 1
            elif severity == "HIGH":
                _stats["high"] += 1
            elif severity == "MEDIUM":
                _stats["medium"] += 1

        _broadcast(_inc_subs, {
            "type":             "incident",
            "id":               state.get("db_incident_id"),
            "timestamp":        parsed.get("timestamp", ""),
            "service":          parsed.get("service", ""),
            "level":            parsed.get("log_level", ""),
            "severity":         severity,
            "exception_type":   analysis.get("exception_type") or parsed.get("exception_type"),
            "trace_id":         parsed.get("trace_id"),
            "analysis":         analysis.get("analysis", ""),
            "suggested_action": analysis.get("suggested_action", ""),
            "application_name": analysis.get("application_name", "Banking Core Platform"),
            "raw_log":          raw,
        })

    except Exception as exc:
        log.error("Analysis/store failed: %s", exc, exc_info=True)
        _broadcast(_inc_subs, {
            "type":             "incident",
            "id":               None,
            "timestamp":        parsed.get("timestamp", ""),
            "service":          parsed.get("service", ""),
            "level":            parsed.get("log_level", ""),
            "severity":         "HIGH",
            "exception_type":   parsed.get("exception_type"),
            "trace_id":         parsed.get("trace_id"),
            "analysis":         f"Analysis failed: {exc}",
            "suggested_action": "Check Gemini API key and DB connection.",
            "application_name": "Banking Core Platform",
            "raw_log":          raw,
        })


# ── Monitor threads ────────────────────────────────────────────────────────────

def _run_local() -> None:
    from agents.log_monitor.sources.local import LocalFileSource

    path = _config["local_path"]
    _broadcast(_log_subs, {"type": "system", "message": f"Watching local file: {path}"})

    source = LocalFileSource(path=path, stop_event=_stop_event)
    for raw in source.stream():
        _process_entry(raw, source_name="local")

    _broadcast(_log_subs, {"type": "system", "message": "Local file monitoring stopped."})


def _run_datadog() -> None:
    from agents.log_monitor.sources.datadog import DatadogSource

    site  = _config.get("dd_site",    "datadoghq.com")
    query = _config.get("dd_query",   "service:banking-app")
    _broadcast(_log_subs, {
        "type":    "system",
        "message": f"Polling Datadog [{site}] | query: {query}",
    })

    source = DatadogSource(
        api_key       = _config["dd_api_key"],
        app_key       = _config["dd_app_key"],
        site          = site,
        query         = query,
        stop_event    = _stop_event,
    )
    try:
        for raw in source.stream():
            _process_entry(raw, source_name="datadog")
    except EnvironmentError as exc:
        _broadcast(_log_subs, {"type": "error", "message": str(exc)})

    _broadcast(_log_subs, {"type": "system", "message": "Datadog polling stopped."})


# ── REST routes ────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html", default_path=_config["local_path"])


@app.route("/api/config", methods=["GET", "POST"])
def config_route():
    global _config
    if request.method == "POST":
        _config.update(request.get_json(force=True) or {})
        return jsonify({"status": "ok"})
    # Don't expose credentials on GET
    safe = {k: v for k, v in _config.items() if k not in ("dd_api_key", "dd_app_key")}
    return jsonify(safe)


@app.route("/api/start", methods=["POST"])
def start():
    global _monitor_thread
    if _monitor_thread and _monitor_thread.is_alive():
        return jsonify({"status": "already_running"})

    _stop_event.clear()
    source = _config.get("source")

    if source == "local":
        target = _run_local
    elif source == "datadog":
        target = _run_datadog
    else:
        return jsonify({"status": "error", "message": "No source configured"}), 400

    _monitor_thread = threading.Thread(target=target, daemon=True)
    _monitor_thread.start()
    return jsonify({"status": "started"})


@app.route("/api/stop", methods=["POST"])
def stop():
    _stop_event.set()
    return jsonify({"status": "stopped"})


@app.route("/api/incidents")
def get_incidents():
    """Return all incidents already stored in the DB (no Gemini re-analysis)."""
    try:
        from db.database import fetch_all_incidents
        return jsonify(fetch_all_incidents())
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/status")
def status():
    running = bool(_monitor_thread and _monitor_thread.is_alive())
    with _stats_lock:
        s = dict(_stats)
    return jsonify({"running": running, "source": _config.get("source"), "stats": s})


# ── SSE endpoints ──────────────────────────────────────────────────────────────

@app.route("/api/logs/stream")
def stream_logs():
    q = _subscribe(_log_subs)

    def generate():
        try:
            while True:
                try:
                    item = q.get(timeout=15)
                    yield f"data: {json.dumps(item)}\n\n"
                except queue.Empty:
                    yield ": heartbeat\n\n"
        except GeneratorExit:
            _unsubscribe(_log_subs, q)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/api/incidents/stream")
def stream_incidents():
    q = _subscribe(_inc_subs)

    def generate():
        try:
            while True:
                try:
                    item = q.get(timeout=15)
                    yield f"data: {json.dumps(item)}\n\n"
                except queue.Empty:
                    yield ": heartbeat\n\n"
        except GeneratorExit:
            _unsubscribe(_inc_subs, q)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Startup ────────────────────────────────────────────────────────────────────

def _init_db() -> None:
    try:
        from db.database import initialize_database
        initialize_database()
        log.info("Database initialised.")
    except Exception as exc:
        log.warning("DB init skipped (will retry on first insert): %s", exc)


if __name__ == "__main__":
    _init_db()
    log.info("Dashboard → http://localhost:5000")
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
