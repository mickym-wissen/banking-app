# Banking App — Monorepo

This repository contains two related projects:

| Project | Path | Description |
|---|---|---|
| **bank-application** | [`bank-application/`](bank-application/) | Spring Boot REST API — accounts, transactions, and Datadog observability |
| **healing-agent** | [`healing-agent/`](healing-agent/) | Python LangGraph agent — monitors logs, classifies incidents with Gemini, persists to MySQL |

---

## Projects

### bank-application
A demo RESTful banking API built with **Java 17 + Spring Boot 3.2**.

- Full CRUD for accounts (SAVINGS, CHECKING, FIXED_DEPOSIT)
- Deposit, withdraw, and transfer operations
- Datadog APM traces, custom Micrometer metrics, and structured JSON logs
- H2 in-memory database for local dev (configurable to PostgreSQL)
- Docker + Docker Compose support

See [`bank-application/README.md`](bank-application/README.md) for full setup and API reference.

### healing-agent
A Python agent that watches the banking app's log file and automatically triages WARN/ERROR entries.

- Tails `bank-application/logs/banking-app.log` in real time
- Classifies incidents using **Google Gemini 2.5 Flash** via LangGraph
- Stores structured incident records in **MySQL** (dynamic column support for evolving Gemini responses)
- Prints colour-coded console alerts

See [`healing-agent/README.md`](healing-agent/README.md) for full setup and schema docs.

---

## Quick Start

```
banking-app/
├── bank-application/   # Java Spring Boot API
└── healing-agent/      # Python monitoring agent
```

1. Start the banking app (see `bank-application/README.md`)
2. Logs are written to `bank-application/logs/banking-app.log`
3. Run the healing agent (see `healing-agent/README.md`) to monitor those logs

---

## Repository Layout

```
.
├── bank-application/
│   ├── src/main/java/com/demo/banking/
│   │   ├── controller/      AccountController
│   │   ├── service/         AccountService
│   │   ├── model/           Account, Transaction
│   │   ├── dto/             AccountDto, TransactionDto
│   │   ├── repository/      AccountRepository, TransactionRepository
│   │   ├── exception/       GlobalExceptionHandler, custom exceptions
│   │   └── config/          DatadogObservabilityConfig
│   ├── src/main/resources/
│   │   ├── application.yml
│   │   └── logback-spring.xml
│   ├── Dockerfile
│   ├── docker-compose.yml
│   ├── .env.example
│   └── pom.xml
└── healing-agent/
    ├── main.py
    ├── requirements.txt
    ├── .env.example
    ├── agents/
    │   ├── core.py              BaseAgent, AgentRegistry
    │   └── log_monitor/
    │       ├── agent.py         LangGraph agent
    │       └── nodes.py         Graph nodes + state
    ├── config/settings.py
    ├── db/database.py
    └── utils/log_parser.py
```
