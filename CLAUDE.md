# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Layout

Two independent components that work together:

```
banking-app/
├── bank-application/   Java 17 Spring Boot REST API (port 8080)
└── healing-agent/      Python self-healing agent (Flask dashboard port 5000)
```

The bank-application writes structured JSON logs; the healing-agent tails those logs, sends WARN/ERROR entries to Gemini for root-cause analysis, stores incidents in MySQL, and streams results to a browser dashboard via SSE.

---

## bank-application

### Run

```bash
# Docker (recommended — also clears logs and DB on each start via entrypoint.sh)
cd bank-application
docker compose up --build

# Local Maven
mvn spring-boot:run -pl bank-application
```

Log output: `bank-application/logs/banking-app.log` and `banking-app-json.log` (LogstashEncoder format for Datadog).

### Key config

`src/main/resources/application.yml` — H2 in-memory DB (`jdbc:h2:mem:bankingdb`), port 8080, Datadog APM on `localhost:8126`.

### API surface

All routes under `/api/v1/accounts`: CRUD, `/deposit`, `/withdraw`, `/transfer`, `/{id}/transactions`.

---

## healing-agent

### Environment

```bash
cd healing-agent
cp .env.example .env   # then fill in required values
pip install -r requirements.txt
```

Required `.env` keys:
- `GEMINI_API_KEY` — Google Gemini API key
- `DB_PASSWORD` — MySQL password (host defaults to `localhost:3306`, db `healing_agent_db`)
- `LOG_SOURCE` — `local` or `datadog`

Datadog mode also requires: `DD_API_KEY`, `DD_APP_KEY`, `DD_SITE` (default `us5.datadoghq.com`), `DD_QUERY`.

### Run

```bash
# Headless agent only
python main.py

# Full dashboard (Flask SSE + REST on port 5000) — run from healing-agent/
python frontend/app.py
```

### Architecture

**LangGraph pipeline** (`agents/log_monitor/`):

```
LocalFileSource / DatadogSource
        ↓  (plain-text log string)
parse_log_entry   — regex extracts: timestamp, log_level, service, trace_id, exception_type
        ↓
route_after_parse — skips INFO/DEBUG; passes WARN/ERROR/FATAL onward
        ↓
analyze_with_gemini — Gemini 2.5 Flash returns JSON: {exception_type, severity,
                       application_name, analysis, suggested_action}
        ↓
store_to_db       — INSERT into MySQL log_incidents; unknown Gemini keys trigger
                    ALTER TABLE automatically (see db/database.py ensure_column)
        ↓
send_alert        — colour-coded console output
```

**Log source abstraction** — both `LocalFileSource` and `DatadogSource` expose a `.stream()` generator that yields plain-text log strings in the same format, so the pipeline is source-agnostic.

`LocalFileSource` seeks to EOF on startup (no reprocessing old entries). `DatadogSource` polls the Logs v2 API every 2 s starting from `now` (no lookback).

**Flask dashboard** (`frontend/app.py`) — per-subscriber `queue.Queue` broadcast; `GET /api/incidents` returns all rows from DB (no re-analysis); SSE streams at `/api/logs/stream` and `/api/incidents/stream`.

### MySQL schema

Table `log_incidents`: `id`, `application_name`, `trace_id`, `exception_type`, `status` (default `new`), `service`, `severity`, `analysis`, `suggested_action`, `log_timestamp`, `created_at`. Extra columns are added on-demand by `ensure_column()`.

### Key settings defaults

| Setting | Default |
|---------|---------|
| `LOG_FILE_PATH` | `../bank-application/logs/banking-app.log` |
| `LOG_CHECK_INTERVAL` | `2` seconds |
| `DD_SITE` | `us5.datadoghq.com` |

Relative `LOG_FILE_PATH` values are resolved relative to the healing-agent root (not shell cwd) — see `agents/log_monitor/sources/local.py _resolve()`.

### Timestamps

The bank-application writes UTC timestamps in its plain-text log. `utils/log_parser.py` converts them to local system timezone on parse. `DatadogSource._norm_ts()` does the same for Datadog API responses.
