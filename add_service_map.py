import requests

BASE = "http://localhost:5000/api/rca/service-map"
ORG  = "mickym-wissen"
REPO = "banking-app"
BRANCH = "master"

# All Java class names that appear as service in logs
SERVICES = [
    "AccountService",
    "AccountController",
    "GlobalExceptionHandler",
    "BankingApplication",
    "TransactionService",
    "TransactionController",
    "InsufficientFundsException",
    "banking-app",
]

for svc in SERVICES:
    r = requests.post(BASE, json={
        "service_name":   svc,
        "github_org":     ORG,
        "github_repo":    REPO,
        "default_branch": BRANCH,
    })
    print(f"{svc:35s} → {r.status_code} {r.text.strip()}")
