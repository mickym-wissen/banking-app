# 🏦 Banking App — Spring Boot + Datadog

A demo RESTful banking application with full **CRUD** operations and end-to-end
**Datadog observability** (APM traces, custom metrics, structured logs, and
continuous profiling).

---

## Tech Stack

| Layer | Technology |
|---|---|
| Runtime | Java 17 + Spring Boot 3.2 |
| Persistence | JPA/Hibernate + H2 (swap for Postgres) |
| Observability | Datadog APM agent + Micrometer registry |
| Tracing | OpenTelemetry bridge → Datadog |
| Logging | Logback + Logstash JSON encoder |
| Container | Docker + Docker Compose |

---

## Quick Start

### 1 — Set your Datadog API key
```bash
export DD_API_KEY=<your_datadog_api_key>
export DD_APP_KEY=<your_datadog_app_key>   # optional
```

### 2 — Build & run with Docker Compose
```bash
docker compose up --build
```
This starts two containers:
- **banking-app** on http://localhost:8080
- **datadog-agent** (APM on 8126, DogStatsD on 8125)

### 3 — Local dev (no Docker)
```bash
# Download the Datadog Java agent once
curl -Lo dd-java-agent.jar https://dtdg.co/latest-java-tracer

# Run
mvn spring-boot:run
```

> The `pom.xml` already passes `-javaagent:dd-java-agent.jar` via `<jvmArguments>`.

### 4 — Run tests
```bash
mvn test
```

---

## API Reference

### Accounts

| Method | URL | Description |
|--------|-----|-------------|
| `POST` | `/api/v1/accounts` | Create account |
| `GET` | `/api/v1/accounts` | List all accounts |
| `GET` | `/api/v1/accounts/{id}` | Get by ID |
| `GET` | `/api/v1/accounts/number/{num}` | Get by account number |
| `PUT` | `/api/v1/accounts/{id}` | Update owner / status |
| `DELETE` | `/api/v1/accounts/{id}` | Delete account |

### Transactions

| Method | URL | Description |
|--------|-----|-------------|
| `POST` | `/api/v1/accounts/{id}/deposit` | Deposit money |
| `POST` | `/api/v1/accounts/{id}/withdraw` | Withdraw money |
| `POST` | `/api/v1/accounts/{id}/transfer` | Transfer to another account |
| `GET` | `/api/v1/accounts/{id}/transactions` | Transaction history |

### Example — Create account
```bash
curl -X POST http://localhost:8080/api/v1/accounts \
  -H 'Content-Type: application/json' \
  -d '{
    "ownerName": "Alice Smith",
    "accountType": "SAVINGS",
    "initialBalance": 1000.00
  }'
```

### Example — Deposit
```bash
curl -X POST http://localhost:8080/api/v1/accounts/1/deposit \
  -H 'Content-Type: application/json' \
  -d '{"amount": 500.00, "description": "Salary"}'
```

### Example — Transfer
```bash
curl -X POST http://localhost:8080/api/v1/accounts/1/transfer \
  -H 'Content-Type: application/json' \
  -d '{"toAccountId": 2, "amount": 200.00, "description": "Rent"}'
```

---

## Datadog Observability Details

### APM — Distributed Tracing
- The **dd-java-agent** instruments every HTTP request automatically.
- `@Timed` on `AccountController` emits per-endpoint latency histograms.
- Custom `Timer` in `AccountService` tracks transaction processing time.
- All traces carry **Unified Service Tags**: `service`, `env`, `version`.

### Custom Metrics (visible in Datadog Metrics Explorer)

| Metric | Type | Description |
|--------|------|-------------|
| `banking.accounts.created` | Counter | Accounts created |
| `banking.accounts.deleted` | Counter | Accounts deleted |
| `banking.accounts.active_total` | Gauge | Live count of active accounts |
| `banking.transactions.deposits` | Counter | Total deposits |
| `banking.transactions.withdrawals` | Counter | Total withdrawals |
| `banking.transactions.transfers` | Counter | Total transfers |
| `banking.transaction.duration` | Timer | Per-transaction latency |
| `banking.transactions.amount{type}` | Counter | Cumulative dollar volume |

### Logs
- Structured JSON via **Logstash encoder**.
- `dd.trace_id` / `dd.span_id` are injected automatically by the agent, linking
  every log line to its APM trace in Datadog.
- Logs written to `logs/banking-app.log`; the Datadog agent tails this file.

### Continuous Profiling
- Enabled via `-Ddd.profiling.enabled=true` (set in Dockerfile).
- Visible under **APM → Profiling** in Datadog.

---

## H2 Console (local dev)
Navigate to http://localhost:8080/h2-console  
JDBC URL: `jdbc:h2:mem:bankingdb`  Username: `sa`  Password: *(empty)*

---

## Switching to PostgreSQL
1. Replace the H2 dependency in `pom.xml` with `postgresql`.
2. Update `spring.datasource.*` in `application.yml`.
3. Change `hibernate.dialect` to `PostgreSQLDialect`.
