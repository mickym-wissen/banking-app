#!/bin/bash
set -e

echo "[entrypoint] Banking application starting — running cleanup..."

# ── Clear log files ────────────────────────────────────────────────────────────
mkdir -p /app/logs
> /app/logs/banking-app.log
> /app/logs/banking-app-json.log
echo "[entrypoint] Log files cleared: banking-app.log, banking-app-json.log"

# ── Clear healing-agent DB incidents ──────────────────────────────────────────
# Connects to the host MySQL (via host.docker.internal) and truncates log_incidents.
# Skipped gracefully if credentials are missing or table doesn't exist yet.
if [ -n "$HEALING_DB_HOST" ] && [ -n "$HEALING_DB_USER" ] && [ -n "$HEALING_DB_PASSWORD" ] && [ -n "$HEALING_DB_NAME" ]; then
    mysql \
        -h "$HEALING_DB_HOST" \
        -P "${HEALING_DB_PORT:-3306}" \
        -u "$HEALING_DB_USER" \
        -p"$HEALING_DB_PASSWORD" \
        "$HEALING_DB_NAME" \
        -e "TRUNCATE TABLE log_incidents;" 2>/dev/null \
    && echo "[entrypoint] log_incidents table cleared." \
    || echo "[entrypoint] DB clear skipped (table may not exist yet — healing agent will create it on first run)."
else
    echo "[entrypoint] HEALING_DB_* vars not set — skipping DB clear."
fi

echo "[entrypoint] Cleanup done. Starting Spring Boot..."
echo "────────────────────────────────────────────────────"

# ── Start the application ──────────────────────────────────────────────────────
exec java -jar /app/banking-app.jar
