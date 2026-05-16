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
from typing import Optional
from datetime import datetime

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

# ── RCA job state ──────────────────────────────────────────────────────────────
# Keyed by short job_id string. Jobs are also persisted to MySQL via rca_jobs table.

_rca_jobs: dict[str, dict] = {}
_rca_queues: dict[str, queue.Queue] = {}
_rca_lock = threading.Lock()

# ── Token usage tracking ───────────────────────────────────────────────────────

_token_log: list[dict] = []
_token_totals: dict = {
    "gemini_calls": 0, "gemini_input": 0, "gemini_output": 0,
    "groq_calls":   0, "groq_input":   0, "groq_output":   0,
}
_token_lock = threading.Lock()


def _record_tokens(family: str, model: str, input_tok: int, output_tok: int, ctx: str = "") -> None:
    with _token_lock:
        _token_log.append({
            "ts":      datetime.now().strftime("%H:%M:%S"),
            "family":  family,
            "model":   model,
            "input":   input_tok,
            "output":  output_tok,
            "total":   input_tok + output_tok,
            "context": ctx,
        })
        _token_totals[f"{family}_calls"]  += 1
        _token_totals[f"{family}_input"]  += input_tok
        _token_totals[f"{family}_output"] += output_tok
        if len(_token_log) > 500:
            _token_log.pop(0)


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

        # Estimate token usage for Gemini incident analysis
        est_in  = max(600, len(raw) // 4 + 500)
        est_out = 450
        _record_tokens("gemini", "gemini-2.5-flash", est_in, est_out,
                       f"incident #{state.get('db_incident_id')}")

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


# ── RCA routes ────────────────────────────────────────────────────────────────

@app.route("/api/rca/start", methods=["POST"])
def rca_start():
    """Start a GitHub RCA analysis job. Returns {job_id}."""
    data = request.get_json(force=True) or {}
    repo_url = (data.get("repo_url") or "").strip()
    incident = data.get("incident") or {}

    if not repo_url:
        return jsonify({"error": "repo_url is required"}), 400

    job_id = uuid.uuid4().hex[:10]
    db_job_id = None

    try:
        from db.database import insert_rca_job
        db_job_id = insert_rca_job(incident.get("id"), repo_url)
    except Exception as exc:
        log.warning("Could not persist rca_job to DB: %s", exc)

    with _rca_lock:
        _rca_jobs[job_id] = {
            "id":         job_id,
            "db_id":      db_job_id,
            "status":     "running",
            "incident":   incident,
            "repo_url":   repo_url,
            "steps":      [],
            "result":     None,
            "created_at": datetime.now().isoformat(),
        }
        _rca_queues[job_id] = queue.Queue(maxsize=300)

    t = threading.Thread(target=_run_rca_job, args=(job_id,), daemon=True)
    t.start()

    return jsonify({"job_id": job_id, "db_id": db_job_id})


def _run_rca_job(job_id: str) -> None:
    with _rca_lock:
        job = _rca_jobs.get(job_id)
    if not job:
        return

    incident = job["incident"]
    repo_url = job["repo_url"]
    db_id    = job.get("db_id")

    def progress(step: str, message: str) -> None:
        event = {"type": "progress", "step": step, "message": message}
        with _rca_lock:
            if job_id in _rca_jobs:
                _rca_jobs[job_id]["steps"].append({"step": step, "message": message})
            q = _rca_queues.get(job_id)
        if q:
            try:
                q.put_nowait(event)
            except queue.Full:
                pass

    try:
        from config.settings import settings

        if settings.GROQ_API_KEY:
            # Preferred: LangGraph tool-calling agent with Groq llama-3.3-70b-versatile
            from agents.rca.rca_agent import RCAAgent
            agent = RCAAgent(
                groq_api_key=settings.GROQ_API_KEY,
                github_token=settings.GITHUB_TOKEN,
            )
        else:
            # Fallback: one-shot Gemini agent
            log.warning("GROQ_API_KEY not set — falling back to Gemini RCA agent")
            from agents.rca.github_rca import GitHubRCAAgent
            agent = GitHubRCAAgent(
                github_token=settings.GITHUB_TOKEN,
                gemini_api_key=settings.GEMINI_API_KEY,
            )

        result = agent.analyze(repo_url, incident, progress)

        # Estimate token usage for RCA session (repo analysis is input-heavy)
        if settings.GROQ_API_KEY:
            _record_tokens("groq", "llama-3.3-70b-versatile", 9500, 2200, f"rca job {job_id}")
        else:
            _record_tokens("gemini", "gemini-2.5-flash", 12000, 3000, f"rca job {job_id}")

        with _rca_lock:
            if job_id in _rca_jobs:
                _rca_jobs[job_id].update({"status": "done", "result": result})
            q = _rca_queues.get(job_id)
        if q:
            try:
                q.put_nowait({"type": "done", **result})
            except queue.Full:
                pass

        if db_id:
            try:
                from db.database import update_rca_job
                update_rca_job(
                    db_id,
                    status="done",
                    result_type=result.get("status"),
                    pr_url=result.get("pr_url"),
                    pr_number=result.get("pr_number"),
                    fix_file=result.get("fix_file"),
                    fix_desc=result.get("fix_desc"),
                    rca_report=result.get("rca_report"),
                )
            except Exception as exc:
                log.warning("Could not update rca_job in DB: %s", exc)

    except Exception as exc:
        err = str(exc)
        log.error("RCA job %s failed: %s", job_id, exc, exc_info=True)
        with _rca_lock:
            if job_id in _rca_jobs:
                _rca_jobs[job_id].update({"status": "failed", "result": {"error": err}})
            q = _rca_queues.get(job_id)
        if q:
            try:
                q.put_nowait({"type": "error", "message": err})
            except queue.Full:
                pass

        if db_id:
            try:
                from db.database import update_rca_job
                update_rca_job(db_id, status="failed", error=err)
            except Exception:
                pass


@app.route("/api/rca/stream/<job_id>")
def rca_stream(job_id: str):
    """SSE stream for a specific RCA job — emits progress and final result."""
    with _rca_lock:
        job = _rca_jobs.get(job_id)
        q   = _rca_queues.get(job_id)

    if not job:
        return jsonify({"error": "Job not found"}), 404

    def generate():
        try:
            # Replay steps accumulated before the client connected
            for step in list(job.get("steps", [])):
                yield f"data: {json.dumps({'type': 'progress', **step})}\n\n"

            # If already finished, send final event and close
            if job["status"] in ("done", "failed"):
                result = job.get("result") or {}
                etype = "done" if job["status"] == "done" else "error"
                if etype == "error":
                    yield f"data: {json.dumps({'type': 'error', 'message': result.get('error', 'Unknown error')})}\n\n"
                else:
                    yield f"data: {json.dumps({'type': 'done', **result})}\n\n"
                return

            # Stream live events
            while q:
                try:
                    item = q.get(timeout=30)
                    yield f"data: {json.dumps(item)}\n\n"
                    if item.get("type") in ("done", "error"):
                        break
                except queue.Empty:
                    yield ": heartbeat\n\n"
        except GeneratorExit:
            pass

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/api/rca/jobs")
def rca_jobs_list():
    """Return summary of all in-memory RCA jobs (newest first)."""
    with _rca_lock:
        jobs = list(_rca_jobs.values())
    jobs.sort(key=lambda j: j["created_at"], reverse=True)
    return jsonify([
        {
            "id":             j["id"],
            "status":         j["status"],
            "repo_url":       j["repo_url"],
            "incident_id":    j.get("incident", {}).get("id"),
            "exception_type": j.get("incident", {}).get("exception_type"),
            "result_type":    (j.get("result") or {}).get("status"),
            "pr_url":         (j.get("result") or {}).get("pr_url"),
            "pr_number":      (j.get("result") or {}).get("pr_number"),
            "created_at":     j["created_at"],
        }
        for j in jobs
    ])


@app.route("/api/rca/jobs/<job_id>")
def rca_job_detail(job_id: str):
    """Return full detail for one RCA job."""
    with _rca_lock:
        job = _rca_jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(job)


@app.route("/api/token-usage")
def token_usage_route():
    with _token_lock:
        totals = dict(_token_totals)
        recent = list(_token_log[-100:])
    # Gemini 2.5 Flash pricing: $0.075/1M input, $0.30/1M output (non-thinking)
    gem_cost = (totals["gemini_input"] * 0.075 + totals["gemini_output"] * 0.30) / 1_000_000
    return jsonify({
        "totals":       totals,
        "gemini_cost":  round(gem_cost, 6),
        "groq_cost":    0.0,
        "total_calls":  totals["gemini_calls"] + totals["groq_calls"],
        "total_tokens": (totals["gemini_input"] + totals["gemini_output"]
                         + totals["groq_input"] + totals["groq_output"]),
        "log":          recent,
    })


@app.route("/api/github/token-status")
def github_token_status():
    from config.settings import settings
    return jsonify({
        "github_configured": bool(settings.GITHUB_TOKEN),
        "groq_configured":   bool(settings.GROQ_API_KEY),
        "rca_engine":        "groq-llama-3.3-70b" if settings.GROQ_API_KEY else "gemini-2.5-flash",
    })


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
