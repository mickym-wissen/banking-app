# EC2 Deployment Plan — Banking App + Healing Agent

## Context

The repository has two components that need to be deployed together on an Amazon Linux 2023 EC2 instance:
- **bank-application** — Java 17 Spring Boot REST API (port 8080), already Dockerized
- **healing-agent** — Python Flask + LangGraph AI log monitor (port 5000), no Dockerfile

They share a log directory: the bank-app writes logs that the healing-agent tails in real time. Both depend on a MySQL database (`healing_agent_db`). No CI/CD or nginx config exists yet.

---

## Architecture on EC2

```
EC2 (Amazon Linux 2023)
├── MySQL 8  (native or Docker — runs on port 3306)
├── bank-application  (Docker Compose — port 8080)
│   └── logs/ volume mounted at ~/banking-app/bank-application/logs/
└── healing-agent  (Python venv — port 5000)
    └── reads ../bank-application/logs/banking-app.log  (same host path)
```

---

## Step-by-Step Deployment

### Step 0 — Open EC2 Security Group ports

In AWS Console → EC2 → Security Groups → Inbound Rules, add:
- **Port 8080** (TCP) — source: your IP or 0.0.0.0/0
- **Port 5000** (TCP) — source: your IP or 0.0.0.0/0
- Port 3306 stays **closed** to the internet (MySQL is host-local only)

---

### Step 1 — SSH into EC2

```bash
ssh -i "healer-key.pem" ec2-user@<your-ec2-public-ip>
```

---

### Step 2 — Install prerequisites

```bash
# Docker
sudo dnf install -y docker
sudo systemctl enable --now docker
sudo usermod -aG docker ec2-user
newgrp docker   # apply group without re-login

# Docker Compose plugin
sudo dnf install -y docker-compose-plugin
docker compose version   # verify

# Python 3.11 + pip + venv
sudo dnf install -y python3.11 python3.11-pip python3.11-devel gcc

# MySQL 8 (community)
sudo dnf install -y https://dev.mysql.com/get/mysql80-community-release-el9-1.noarch.rpm
sudo dnf install -y mysql-community-server
sudo systemctl enable --now mysqld

# Grab the temporary root password
sudo grep 'temporary password' /var/log/mysqld.log
```

---

### Step 3 — Configure MySQL

```bash
sudo mysql_secure_installation   # set a strong root password when prompted
mysql -u root -p
```

Inside MySQL:
```sql
CREATE DATABASE healing_agent_db CHARACTER SET utf8mb4;
-- optional: create a dedicated user instead of using root
EXIT;
```

---

### Step 4 — Clone the repository

```bash
cd ~
git clone <your-repo-url> banking-app
cd banking-app
```

---

### Step 5 — Deploy bank-application (Docker Compose)

```bash
cd ~/banking-app/bank-application
```

Create `.env` from the example and fill in values:
```bash
cp .env.example .env
nano .env
# Set: DD_API_KEY (leave blank if not using Datadog)
```

Edit `docker-compose.yml` — update the MySQL password to match what you set in Step 3:
```yaml
environment:
  HEALING_DB_PASSWORD: <your-mysql-password>   # was hardcoded as 'varun'
```

Start the stack:
```bash
docker compose up -d --build
docker compose logs -f banking-app   # verify it starts on port 8080
```

Test:
```bash
curl http://localhost:8080/actuator/health
```

---

### Step 6 — Deploy healing-agent (Python venv)

```bash
cd ~/banking-app/healing-agent

# Create virtual environment
python3.11 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
nano .env
```

Key values to set in `.env`:
```
GEMINI_API_KEY=<your-gemini-key>
DB_HOST=localhost
DB_PORT=3306
DB_NAME=healing_agent_db
DB_USER=root
DB_PASSWORD=<your-mysql-password>
LOG_SOURCE=local
LOG_FILE_PATH=../bank-application/logs/banking-app.log
LOG_CHECK_INTERVAL=2
APP_ENV=production
```

Test run in foreground first:
```bash
python frontend/app.py
# Visit http://<ec2-public-ip>:5000
```

---

### Step 7 — Run both as systemd services (persistent)

**healing-agent service** (`/etc/systemd/system/healing-agent.service`):
```ini
[Unit]
Description=Healing Agent Dashboard
After=network.target mysqld.service

[Service]
User=ec2-user
WorkingDirectory=/home/ec2-user/banking-app/healing-agent
EnvironmentFile=/home/ec2-user/banking-app/healing-agent/.env
ExecStart=/home/ec2-user/banking-app/healing-agent/venv/bin/python frontend/app.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now healing-agent
sudo journalctl -u healing-agent -f   # watch logs
```

Bank-application is already managed by Docker Compose; make Docker auto-restart on reboot:
```bash
# Already covered by 'sudo systemctl enable docker' in Step 2
# Docker containers with restart: unless-stopped survive reboots
# Add restart policy to docker-compose.yml if not present:
#   restart: unless-stopped
docker compose up -d   # re-applies the policy
```

---

### Step 8 — Verify end-to-end

| Check | Command |
|-------|---------|
| Bank API health | `curl http://<ec2-ip>:8080/actuator/health` |
| Create an account | `curl -X POST http://<ec2-ip>:8080/api/v1/accounts -H 'Content-Type: application/json' -d '{"ownerName":"Test","balance":1000}'` |
| Healing dashboard | Open `http://<ec2-ip>:5000` in browser |
| Incidents in DB | `mysql -u root -p healing_agent_db -e "SELECT * FROM log_incidents LIMIT 5;"` |
| Logs flowing | `tail -f ~/banking-app/bank-application/logs/banking-app.log` |

---

## Critical Files Referenced

| File | Role |
|------|------|
| `bank-application/docker-compose.yml` | Add `restart: unless-stopped`; update `HEALING_DB_PASSWORD` |
| `bank-application/.env` | Datadog API key (optional) |
| `healing-agent/.env` | All required secrets — create from `.env.example` |
| `healing-agent/config/settings.py` | Shows all env var defaults |
| `healing-agent/frontend/app.py:622` | Flask binds `0.0.0.0:5000` |

## Notes

- **No code changes required** — deployment is purely configuration and infrastructure.
- The log path `../bank-application/logs/banking-app.log` resolves correctly when the repo is cloned flat as `~/banking-app/`.
- If Datadog is not needed, skip the `datadog-agent` service by running: `docker compose up -d banking-app` instead of `docker compose up -d`.
- MySQL password `varun` is hardcoded in `docker-compose.yml` — change it before deploying.