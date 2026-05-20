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
import uuid
from datetime import datetime, timezone
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


@app.errorhandler(Exception)
def handle_exception(e):
    log.error("Unhandled exception: %s", e, exc_info=True)
    return jsonify({"error": str(e)}), 500


# ── SSE broadcast registry ─────────────────────────────────────────────────────

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

# ── RCA job state ──────────────────────────────────────────────────────────────
# In-memory dict: used for SSE event delivery and fast status lookups.
# The rca_jobs DB table is the durable store; _rca_jobs is populated from it on startup.

_rca_jobs: dict         = {}   # job_id → {incident_id, status, started_at, completed_at, report, error}
_rca_subs: dict         = {}   # job_id → list[queue.Queue]  (per-job SSE subscribers)
_rca_events: dict       = {}   # job_id → list[dict]  (full event history for replay on reconnect)
_rca_stop_signals: dict = {}   # job_id → threading.Event
_rca_lock      = threading.Lock()
_rca_subs_lock = threading.Lock()
_rca_ev_lock   = threading.Lock()


def _broadcast_rca(job_id: str, event: dict) -> None:
    # Persist event for replay on reconnect (skip sentinel)
    if event.get("type") != "_sentinel_":
        with _rca_ev_lock:
            _rca_events.setdefault(job_id, []).append(event)

    with _rca_subs_lock:
        subs = _rca_subs.get(job_id, [])
        dead = []
        for q in subs:
            try:
                q.put_nowait(event)
            except queue.Full:
                dead.append(q)
        for q in dead:
            subs.remove(q)


def _subscribe_rca(job_id: str) -> queue.Queue:
    q: queue.Queue = queue.Queue(maxsize=500)
    with _rca_subs_lock:
        _rca_subs.setdefault(job_id, []).append(q)
    return q


def _unsubscribe_rca(job_id: str, q: queue.Queue) -> None:
    with _rca_subs_lock:
        subs = _rca_subs.get(job_id, [])
        if q in subs:
            subs.remove(q)


# ── Log entry processing ───────────────────────────────────────────────────────

def _process_entry(raw: str, source_name: str = "local") -> None:
    from utils.log_parser import parse_entry, is_actionable

    parsed = parse_entry(raw)
    level = parsed.get("log_level", "UNKNOWN")
    actionable = is_actionable(parsed)

    with _stats_lock:
        _stats["total_logs"] += 1

    _broadcast(_log_subs, {
        "type":       "log",
        "level":      level,
        "timestamp":  parsed.get("timestamp", ""),
        "service":    parsed.get("service", ""),
        "message":    parsed.get("message", raw[:300]),
        "raw":        raw,
        "source":     source_name,
        "actionable": actionable,
    })

    if actionable:
        threading.Thread(
            target=_analyze_and_store,
            args=(raw, parsed),
            daemon=True,
        ).start()


def _analyze_and_store(raw: str, parsed: dict) -> None:
    try:
        from agents.log_monitor.nodes import analyze_with_claude, store_to_db, LogMonitorState

        state: LogMonitorState = {
            "raw_log_entry":  raw,
            "parsed_entry":   parsed,
            "analysis":       None,
            "db_incident_id": None,
            "should_skip":    False,
            "error":          None,
        }

        state.update(analyze_with_claude(state))
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

    site  = _config.get("dd_site",  "datadoghq.com")
    query = _config.get("dd_query", "service:banking-app")
    _broadcast(_log_subs, {
        "type":    "system",
        "message": f"Polling Datadog [{site}] | query: {query}",
    })

    source = DatadogSource(
        api_key    = _config["dd_api_key"],
        app_key    = _config["dd_app_key"],
        site       = site,
        query      = query,
        stop_event = _stop_event,
    )
    try:
        for raw in source.stream():
            _process_entry(raw, source_name="datadog")
    except EnvironmentError as exc:
        _broadcast(_log_subs, {"type": "error", "message": str(exc)})

    _broadcast(_log_subs, {"type": "system", "message": "Datadog polling stopped."})


# ── RCA job runner ─────────────────────────────────────────────────────────────

def _run_rca_job(job_id: str, incident_id: int) -> None:
    """Background thread: runs the RCA agent and persists results to DB."""
    from db.database import update_rca_job

    try:
        from agents.rca.agent import RCAAgent
        from agents.rca.healing_adapter import HealingDBAdapter, NoCICDAdapter

        obs   = HealingDBAdapter()
        cicd  = NoCICDAdapter()
        agent = RCAAgent(obs_adapter=obs, cicd_adapter=cicd)

        # Register stop signal for this job
        stop_event = threading.Event()
        with _rca_lock:
            _rca_stop_signals[job_id] = stop_event

        def trace_callback(event: dict) -> None:
            _broadcast_rca(job_id, event)

        def stop_check() -> bool:
            return stop_event.is_set()

        # Mark running in memory + DB
        with _rca_lock:
            _rca_jobs[job_id]["status"] = "running"
        update_rca_job(job_id, "running")

        report = agent.run(str(incident_id), trace_callback=trace_callback, stop_check=stop_check)

        completed_at = datetime.now(timezone.utc).isoformat()
        report_json  = json.dumps(report.model_dump(mode="json"))

        # Persist to DB first, then update memory
        update_rca_job(job_id, "completed", report_json=report_json)

        with _rca_lock:
            _rca_jobs[job_id]["status"]       = "completed"
            _rca_jobs[job_id]["report"]        = report.model_dump(mode="json")
            _rca_jobs[job_id]["completed_at"]  = completed_at

        _broadcast_rca(job_id, {
            "type":   "done",
            "report": report.model_dump(mode="json"),
            "ts":     completed_at,
        })

    except Exception as exc:
        log.error("RCA job %s failed: %s", job_id, exc, exc_info=True)
        err_msg = str(exc)

        update_rca_job(job_id, "failed", error=err_msg)

        with _rca_lock:
            _rca_jobs[job_id]["status"] = "failed"
            _rca_jobs[job_id]["error"]  = err_msg

        _broadcast_rca(job_id, {
            "type":    "error",
            "message": err_msg,
            "ts":      datetime.now(timezone.utc).isoformat(),
        })
    finally:
        # Clean up stop signal
        with _rca_lock:
            _rca_stop_signals.pop(job_id, None)
        # Sentinel tells the SSE generator to close the connection
        _broadcast_rca(job_id, {"type": "_sentinel_"})


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


# ── RCA endpoints ──────────────────────────────────────────────────────────────

@app.route("/api/rca/run", methods=["POST"])
def rca_run():
    """Start an RCA job for a stored incident. Returns {job_id}."""
    body = request.get_json(force=True) or {}
    incident_id = body.get("incident_id")
    if not incident_id:
        return jsonify({"error": "incident_id is required"}), 400

    try:
        incident_id = int(incident_id)
    except (TypeError, ValueError):
        return jsonify({"error": "incident_id must be an integer"}), 400

    from config.settings import settings
    if not settings.ANTHROPIC_API_KEY or settings.ANTHROPIC_API_KEY == "your_anthropic_api_key_here":
        return jsonify({"error": "ANTHROPIC_API_KEY is not configured in .env"}), 503

    job_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc).isoformat()

    try:
        from db.database import insert_rca_job, initialize_database
        try:
            insert_rca_job(job_id, incident_id)
        except Exception:
            # Table may not exist yet — re-init and retry once
            initialize_database()
            insert_rca_job(job_id, incident_id)
    except Exception as exc:
        log.error("Failed to persist RCA job to DB: %s", exc)
        return jsonify({"error": f"DB error: {exc}"}), 500

    with _rca_lock:
        _rca_jobs[job_id] = {
            "incident_id":   incident_id,
            "status":        "queued",
            "started_at":    started_at,
            "completed_at":  None,
            "report":        None,
            "error":         None,
        }

    thread = threading.Thread(
        target=_run_rca_job, args=(job_id, incident_id), daemon=True
    )
    thread.start()

    return jsonify({"job_id": job_id, "incident_id": incident_id, "status": "queued"})


@app.route("/api/rca/jobs")
def rca_jobs():
    """List all RCA jobs (from DB — survives restarts). No report payloads."""
    try:
        from db.database import fetch_rca_jobs
        jobs = fetch_rca_jobs()
        # Overlay live status from memory for currently-running jobs
        with _rca_lock:
            for j in jobs:
                mem = _rca_jobs.get(j["job_id"])
                if mem and mem["status"] in ("queued", "running"):
                    j["status"] = mem["status"]
        return jsonify(jobs)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/rca/jobs/<job_id>")
def rca_job_detail(job_id: str):
    """Full job detail including the RCA report (from DB)."""
    try:
        from db.database import fetch_rca_job
        job = fetch_rca_job(job_id)
        if not job:
            return jsonify({"error": "Job not found"}), 404
        # If running, overlay live status
        with _rca_lock:
            mem = _rca_jobs.get(job_id)
        if mem and mem["status"] in ("queued", "running"):
            job["status"] = mem["status"]
        return jsonify(job)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/rca/stream/<job_id>")
def rca_stream(job_id: str):
    """SSE stream of trace events for an RCA job. Replays full history on reconnect."""
    with _rca_lock:
        in_memory = job_id in _rca_jobs
    if not in_memory:
        from db.database import fetch_rca_job
        db_job = fetch_rca_job(job_id)
        if not db_job:
            return jsonify({"error": "Job not found"}), 404
        with _rca_lock:
            _rca_jobs[job_id] = {k: db_job[k] for k in
                                  ("incident_id", "status", "started_at",
                                   "completed_at", "report", "error")}

    # Snapshot past events and current status before subscribing
    with _rca_ev_lock:
        past_events = list(_rca_events.get(job_id, []))
    with _rca_lock:
        job = dict(_rca_jobs[job_id])

    q = _subscribe_rca(job_id)

    # If already terminal, push final event + sentinel after replay
    if job["status"] in ("completed", "failed"):
        if job["status"] == "completed":
            _broadcast_rca(job_id, {"type": "done"})
        else:
            _broadcast_rca(job_id, {"type": "error", "message": job.get("error", "")})
        _broadcast_rca(job_id, {"type": "_sentinel_"})

    def generate():
        # Replay full event history first so reconnects see everything
        for event in past_events:
            yield f"data: {json.dumps(event)}\n\n"
        try:
            while True:
                try:
                    item = q.get(timeout=30)
                    if item.get("type") == "_sentinel_":
                        break
                    yield f"data: {json.dumps(item)}\n\n"
                except queue.Empty:
                    yield ": heartbeat\n\n"
        except GeneratorExit:
            _unsubscribe_rca(job_id, q)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/api/rca/stop/<job_id>", methods=["POST"])
def rca_stop(job_id: str):
    """Signal a running RCA job to stop."""
    with _rca_lock:
        signal = _rca_stop_signals.get(job_id)
        job    = _rca_jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    if job.get("status") not in ("queued", "running"):
        return jsonify({"error": "Job is not running"}), 400
    if signal:
        signal.set()
    return jsonify({"status": "stopping", "job_id": job_id})


@app.route("/api/rca/report/<job_id>")
def rca_report_html(job_id: str):
    """Render the completed RCA report as a self-contained HTML page."""
    # Try memory first (fastest), fall back to DB (survives restarts)
    with _rca_lock:
        mem_job = _rca_jobs.get(job_id)

    report_dict = None
    job_status  = None

    if mem_job:
        job_status  = mem_job.get("status")
        report_dict = mem_job.get("report")

    if not report_dict:
        from db.database import fetch_rca_job
        db_job = fetch_rca_job(job_id)
        if not db_job:
            return "Job not found", 404
        job_status  = db_job.get("status")
        report_dict = db_job.get("report")

    if not report_dict:
        return (
            f"<html><body><h2>RCA {job_status}</h2>"
            f"<p>Report not ready yet. Status: {job_status}</p></body></html>"
        ), 202

    try:
        from agents.rca.report_renderer import render_rca_html
        return Response(render_rca_html(report_dict), mimetype="text/html")
    except Exception as exc:
        return f"Render error: {exc}", 500


@app.route("/api/rca/service-map", methods=["GET"])
def rca_service_map_get():
    """Return all entries in service_repo_map."""
    try:
        from db.database import _get_conn
        conn = _get_conn()
        try:
            cur = conn.cursor(dictionary=True)
            cur.execute("SELECT * FROM service_repo_map ORDER BY service_name")
            rows = cur.fetchall()
            cur.close()
        finally:
            conn.close()
        return jsonify(rows)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/rca/service-map", methods=["POST"])
def rca_service_map_post():
    """Add or update a service → GitHub repo mapping."""
    body = request.get_json(force=True) or {}
    required = ("service_name", "github_org", "github_repo")
    if not all(body.get(k) for k in required):
        return jsonify({"error": f"Required fields: {required}"}), 400
    try:
        from db.database import _get_conn
        conn = _get_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                """INSERT INTO service_repo_map
                       (service_name, github_org, github_repo, default_branch)
                   VALUES (%s, %s, %s, %s)
                   ON DUPLICATE KEY UPDATE
                       github_org     = VALUES(github_org),
                       github_repo    = VALUES(github_repo),
                       default_branch = VALUES(default_branch)""",
                (
                    body["service_name"],
                    body["github_org"],
                    body["github_repo"],
                    body.get("default_branch", "main"),
                ),
            )
            conn.commit()
            cur.close()
        finally:
            conn.close()
        return jsonify({"status": "ok"})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


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


def _load_rca_jobs_from_db() -> None:
    """Populate in-memory _rca_jobs from the DB on startup (no report blobs)."""
    try:
        from db.database import fetch_rca_jobs
        rows = fetch_rca_jobs()
        with _rca_lock:
            for r in rows:
                _rca_jobs[r["job_id"]] = {
                    "incident_id":  r["incident_id"],
                    "status":       r["status"],
                    "started_at":   r["started_at"],
                    "completed_at": r.get("completed_at"),
                    "report":       None,   # loaded on demand from DB
                    "error":        r.get("error"),
                }
        log.info("Loaded %d RCA job(s) from DB.", len(rows))
    except Exception as exc:
        log.warning("Could not load RCA jobs from DB: %s", exc)


if __name__ == "__main__":
    _init_db()

    with _rca_lock:
        _rca_jobs.clear()
        _rca_stop_signals.clear()
    with _rca_ev_lock:
        _rca_events.clear()

    log.info("Dashboard → http://localhost:5000")
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)

