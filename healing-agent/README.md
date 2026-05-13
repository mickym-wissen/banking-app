# Self-Healing Agent

A Python-based log monitoring agent that tails the banking application log file, classifies WARN/ERROR entries using the Gemini LLM, and persists incidents to MySQL.

---

## Architecture

```
main.py
├── config/settings.py        — env-var configuration
├── db/database.py            — MySQL connection pool + schema + dynamic columns
├── utils/log_parser.py       — regex log parser
└── agents/
    ├── core.py               — BaseAgent ABC + AgentRegistry
    └── log_monitor/
        ├── agent.py          — LangGraph agent that tails the log file
        └── nodes.py          — graph nodes: parse → analyze → store → alert
```

**Flow:** `parse_log_entry` → `route_after_parse` (skip INFO/DEBUG) → `analyze_with_gemini` → `store_to_db` → `send_alert`

---

## Tech Stack

| Layer | Technology |
|---|---|
| LLM | Google Gemini 2.5 Flash via `langchain-google-genai` |
| Graph | LangGraph `StateGraph` |
| Database | MySQL via `mysql-connector-python` |
| Config | `python-dotenv` |

---

## Setup

### 1 — Prerequisites
- Python 3.10+
- MySQL 8+ running locally (or update `DB_HOST`)

### 2 — Install dependencies
```bash
pip install -r requirements.txt
```

### 3 — Configure environment
```bash
cp .env.example .env
# Edit .env with your Gemini API key and MySQL credentials
```

### 4 — Run
```bash
python main.py
```

The agent will watch `../bank-application/logs/banking-app.log` by default and print an incident alert for every WARN/ERROR entry it finds.

---

## Database Schema

Table: `log_incidents`

| Column | Type | Description |
|---|---|---|
| `id` | INT PK | Auto-increment |
| `application_name` | VARCHAR(100) | Source application |
| `status` | VARCHAR(50) | Workflow state (`new` → `resolved`) |
| `severity` | VARCHAR(20) | CRITICAL / HIGH / MEDIUM / LOW (Gemini) |
| `log_level` | VARCHAR(20) | WARN / ERROR / FATAL (from log) |
| `service` | VARCHAR(100) | Logger class name |
| `exception_type` | VARCHAR(200) | Parsed exception class |
| `trace_id` | VARCHAR(100) | MDC trace ID (nullable) |
| `message` | TEXT | Log message |
| `analysis` | TEXT | Gemini root-cause summary |
| `suggested_action` | TEXT | Gemini L1 remediation step |
| `log_timestamp` | DATETIME | Timestamp from the log line |
| `raw_log` | MEDIUMTEXT | Full raw log entry |
| `created_at` | DATETIME | Row insert time |

Additional columns from Gemini responses are added dynamically via `ALTER TABLE`.

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `GEMINI_API_KEY` | — | **Required.** Google Gemini API key |
| `DB_HOST` | `localhost` | MySQL host |
| `DB_PORT` | `3306` | MySQL port |
| `DB_NAME` | `healing_agent_db` | MySQL database name |
| `DB_USER` | `root` | MySQL user |
| `DB_PASSWORD` | — | MySQL password |
| `LOG_FILE_PATH` | `../bank-application/logs/banking-app.log` | Log file to monitor |
| `LOG_CHECK_INTERVAL` | `2` | Poll interval in seconds |
| `APP_ENV` | `development` | Environment tag |
| `APP_LOG_LEVEL` | `INFO` | Python logging level |
