"""
DatadogSource — polls the Datadog Logs v2 API and yields log entries as strings.

The bank-application writes JSON logs via LogstashEncoder → banking-app-json.log.
The Datadog Agent ships that file to Datadog.  When we query the Logs API we get
back structured attributes (level, logger_name, thread_name, message, stack_trace …).

We reconstruct a plain-text log line in the same format that log_parser.py expects:
  2026-05-15 10:00:00.123 [thread] [traceId] ERROR  com.demo.class - message
  <optional stack trace lines>

This lets log_parser.parse_entry() work identically for both sources.
"""
import time
import logging
import threading
from datetime import datetime, timezone, timedelta
from typing import Generator, Optional

import requests

log = logging.getLogger(__name__)


# ── Timestamp helpers ──────────────────────────────────────────────────────────

def _norm_ts(raw: str) -> str:
    """ISO-8601 UTC → 'YYYY-MM-DD HH:MM:SS.mmm' in local system timezone."""
    if not raw:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S.000")
    try:
        ts = raw.replace("Z", "+00:00")
        dt = datetime.fromisoformat(ts)
        # Convert UTC (or any tz-aware datetime) to local system timezone
        dt_local = dt.astimezone(tz=None)
        ms = dt_local.microsecond // 1000
        return dt_local.strftime("%Y-%m-%d %H:%M:%S.") + f"{ms:03d}"
    except Exception:
        # best-effort: strip the T and keep 23 chars
        return raw[:23].replace("T", " ")


def _norm_level(raw: str) -> str:
    """Normalise Datadog status string to uppercase log-level token."""
    mapping = {
        "ok":       "INFO",
        "info":     "INFO",
        "notice":   "INFO",
        "warn":     "WARN",
        "warning":  "WARN",
        "error":    "ERROR",
        "critical": "FATAL",
        "alert":    "FATAL",
        "emergency":"FATAL",
        "debug":    "DEBUG",
        "trace":    "DEBUG",
    }
    return mapping.get(raw.lower(), raw.upper())


# ── Log-line reconstruction ────────────────────────────────────────────────────

def _build_log_line(attrs: dict, nested: dict) -> str:
    """
    Build a plain-text log line from Datadog API response fields.

    attrs   = item["attributes"]            (top-level Datadog fields)
    nested  = item["attributes"]["attributes"]  (LogstashEncoder JSON payload)
    """
    # Prefer LogstashEncoder fields; fall back to top-level Datadog attrs
    ts        = _norm_ts(nested.get("@timestamp") or attrs.get("timestamp", ""))
    level_raw = nested.get("level") or attrs.get("status", "INFO")
    level     = _norm_level(str(level_raw))
    logger    = nested.get("logger_name") or attrs.get("service", "banking-app")
    thread    = nested.get("thread_name") or "main"
    message   = nested.get("message") or attrs.get("message") or ""
    # traceId may come from Datadog correlation or from MDC via LogstashEncoder
    trace_id  = (
        nested.get("dd.trace_id")
        or nested.get("traceId")
        or nested.get("X-B3-TraceId")
        or "N/A"
    )

    line = f"{ts} [{thread}] [{trace_id}] {level:<5} {logger} - {message}"

    # Include stack trace so log_parser can extract the exception type
    stack = nested.get("stack_trace") or nested.get("exception") or ""
    if stack:
        line += "\n" + str(stack)

    return line


# ── DatadogSource ──────────────────────────────────────────────────────────────

class DatadogSource:
    """
    Polls Datadog Logs Search API v2 every `poll_interval` seconds.
    Starts from `lookback_minutes` in the past on first call to avoid missing
    recent entries, then advances the window on each successful poll.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        app_key: Optional[str] = None,
        site: Optional[str] = None,
        query: Optional[str] = None,
        poll_interval: float = 2.0,
        lookback_minutes: int = 0,
        stop_event: Optional[threading.Event] = None,
    ) -> None:
        from config.settings import settings

        self.api_key  = api_key  or settings.DD_API_KEY
        self.app_key  = app_key  or settings.DD_APP_KEY
        self.site     = site     or settings.DD_SITE
        self.query    = query    or settings.DD_QUERY
        self.poll_interval  = poll_interval
        self.lookback_minutes = lookback_minutes
        self.stop_event = stop_event

        self._url = f"https://api.{self.site}/api/v2/logs/events/search"
        self._headers = {
            "DD-API-KEY":         self.api_key,
            "DD-APPLICATION-KEY": self.app_key,
            "Content-Type":       "application/json",
        }

    def stream(self) -> Generator[str, None, None]:
        if not self.api_key or not self.app_key:
            raise EnvironmentError(
                "Datadog credentials missing. Set DD_API_KEY and DD_APP_KEY in .env "
                "or pass them to DatadogSource()."
            )

        log.info(
            "DatadogSource: polling https://api.%s | query=%r | every %.0fs",
            self.site, self.query, self.poll_interval,
        )

        last_time: datetime = datetime.now(timezone.utc) - timedelta(minutes=self.lookback_minutes)
        seen_ids: set[str] = set()

        while not self._stopped():
            try:
                entries = self._fetch(last_time, seen_ids)
                for entry in entries:
                    yield entry
                if entries:
                    last_time = datetime.now(timezone.utc)
                    # Prevent unbounded memory growth
                    if len(seen_ids) > 10_000:
                        seen_ids = set(list(seen_ids)[-5_000:])
            except requests.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else "?"
                body = ""
                if exc.response is not None:
                    try:
                        body = exc.response.json().get("errors", exc.response.text[:300])
                    except Exception:
                        body = exc.response.text[:300]
                log.error("Datadog API HTTP %s: %s", status, body or exc)
                if status == 403:
                    log.error(
                        "403 Forbidden — fix: go to Datadog → Organization Settings → "
                        "Application Keys → edit your key → add scopes: "
                        "logs_read_data, logs_read_index_data"
                    )
            except requests.RequestException as exc:
                log.error("Datadog request failed: %s — retrying in %.0fs", exc, self.poll_interval)

            time.sleep(self.poll_interval)

    def _fetch(self, since: datetime, seen_ids: set[str]) -> list[str]:
        """One API call; returns new log-line strings (deduplicated)."""
        from_ts = since.strftime("%Y-%m-%dT%H:%M:%SZ")
        to_ts   = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        body = {
            "filter": {
                "query": self.query or "*",
                "from":  from_ts,
                "to":    to_ts,
            },
            "sort": "timestamp",
            "page": {"limit": 1000},
        }

        resp = requests.post(self._url, headers=self._headers, json=body, timeout=15)
        resp.raise_for_status()

        lines: list[str] = []
        for item in resp.json().get("data", []):
            log_id = item.get("id", "")
            if log_id in seen_ids:
                continue
            seen_ids.add(log_id)

            attrs  = item.get("attributes", {})
            nested = attrs.get("attributes") or {}
            # nested may be a string in some Datadog pipeline configurations
            if not isinstance(nested, dict):
                nested = {}

            lines.append(_build_log_line(attrs, nested))

        return lines

    def _stopped(self) -> bool:
        return self.stop_event is not None and self.stop_event.is_set()
