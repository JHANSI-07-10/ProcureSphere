# ProcureSphere 360 — Staging Environment Bootstrap & Operations Guide

**Document Reference:** STG-BOOT-PS360-D15  
**PRD Reference:** HPE-PRD-2026-PROC01 (VER-1.0-DRAFT)  
**Target Milestone:** Days 1–15 Engineering Foundation & Staging Readiness  
**Target Branch:** `staging`  
**Date of Assessment:** 2026-10-12  

---

## 1. Overview & Objectives

This guide documents the exact, repeatable procedure for bootstrapping, configuring, and verifying a development or staging instance of **ProcureSphere 360**. 

### Staging Principles & Safety Boundaries:
1. **Isolated Infrastructure:** Staging operates on a dedicated PostgreSQL database and Redis cluster completely segregated from production.
2. **Zero Destructive Operations:** Never run `flush`, `dropdb`, or destructive SQL against live staging or production systems.
3. **Safe Credentials:** Real API tokens, secret keys, and database passwords are never hardcoded or committed to Git. All configuration is injected via environment variables.
4. **Verification Boundary:** This document distinguishes *Local/Docker Bootstrap Verification* from *Live Cloud Staging Deployment*. Live deployments are triggered only after pull request merge onto `staging`.

---

## 2. System Prerequisites & Runtime Dependencies

| Component | Minimum Version | Recommended / Container Image | Purpose |
|:---|:---|:---|:---|
| **Operating System** | Linux (Ubuntu 22.04 LTS) / Windows 11 / macOS | Containerized via Docker | Host OS |
| **Python Runtime** | Python 3.11+ | `python:3.11-slim` | Backend execution |
| **Relational DB** | PostgreSQL 15+ | `postgres:15-alpine` | Normalized ERP data |
| **In-Memory Broker** | Redis 7+ | `redis:7-alpine` | Celery broker, caching |
| **Process Manager** | Docker & Docker Compose | Docker Compose v2.20+ | Orchestrated stack |
| **Web Server / WSGI** | Gunicorn behind Nginx | Gunicorn 21+ / Nginx Alpine | Production HTTP serving |

---

## 3. Step-by-Step Bootstrap Procedure

### Step 1: Repository Checkout & Branch Alignment
```bash
git clone git@github.com:VPDTechnologies/ProcureSphere_360_Internal.git ProcureSphere_360
cd ProcureSphere_360

# Fetch all remote branches and align with staging
git fetch origin
git checkout staging
git pull origin staging
```

### Step 2: Virtual Environment & Dependency Installation (Bare-Metal / VM)
If running outside Docker:
```powershell
# Windows PowerShell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```
```bash
# Linux / macOS
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### Step 3: Environment Configuration (`.env`)
Copy the provided `.env.example` template to `.env`:
```powershell
# Windows
copy .env.example .env
```
```bash
# Linux / macOS
cp .env.example .env
```

#### Staging Environment Variables Reference:
```ini
# Core Django
DJANGO_SETTINGS_MODULE=config.settings.staging
DJANGO_SECRET_KEY=generate-a-strong-secret-key-for-staging
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=staging.procuresphere360.local,localhost,127.0.0.1,web

# PostgreSQL Connection
DB_NAME=procuresphere_staging_db
DB_USER=procure_staging_user
DB_PASSWORD=strong_staging_db_password
DB_HOST=127.0.0.1
DB_PORT=5432

# Redis & Celery
REDIS_URL=redis://127.0.0.1:6379/0
CELERY_BROKER_URL=redis://127.0.0.1:6379/0
CELERY_RESULT_BACKEND=redis://127.0.0.1:6379/0

# Security & Cookies
SESSION_COOKIE_SECURE=True
CSRF_COOKIE_SECURE=True

# File Upload Policies
MAX_UPLOAD_SIZE_BYTES=10485760
ALLOWED_FILE_EXTENSIONS=pdf,png,jpg,jpeg,doc,docx,xlsx,csv
```

### Step 4: Database Migrations
Apply the normalized schema across all 16 applications:
```bash
python manage.py migrate
```
*Verification:* Run `python manage.py showmigrations` to confirm all initial migrations are applied (`[X]`).

### Step 5: Master Data & Demo Seeding
Seed baseline roles, permissions, master cost centers, approval policies, and realistic simulated transaction data:
```bash
python manage.py seed_demo_data
```
*Note:* This script seeds realistic relational data for testing and demonstrations. It never replaces real domain service logic.

### Step 6: Static Asset Collection
Compile and compress CSS, JavaScript, HTMX, and FontAwesome assets for web server delivery:
```bash
python manage.py collectstatic --noinput
```

### Step 7: Starting Background Services (Celery Worker & Beat)
ProcureSphere 360 requires Celery for asynchronous notifications, scheduled contract renewal alerts, and export jobs.

```bash
# Terminal 1: Celery Worker
# Linux / Docker
celery -A config worker -l info

# Windows Development Mode
celery -A config worker -l info -P solo

# Terminal 2: Celery Beat Scheduler
celery -A config beat -l info
```

### Step 8: Starting Application Server
- **Development / Staging Test Server:**
  ```bash
  python manage.py runserver 0.0.0.0:8000
  ```
- **Staging Container / Gunicorn:**
  ```bash
  gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 4 --timeout 120
  ```

---

## 4. Containerized Bootstrap via Docker Compose

The repository includes a ready-to-use Docker environment defined in `Dockerfile` and `docker-compose.yml`.

### Launching the Full Stack:
```bash
docker compose up --build -d
```

### Stack Components Provisioned:
1. `procuresphere_db`: PostgreSQL 15 container listening on port `15432` with healthcheck.
2. `procuresphere_redis`: Redis 7 container on port `6379` with healthcheck.
3. `procuresphere_web`: Web container running Gunicorn/Django on port `8000`.
4. `procuresphere_worker`: Celery worker attached to Redis broker.
5. `procuresphere_beat`: Celery beat scheduler for contract/sourcing periodic tasks.

---

## 5. Health Checks & Verification Smoke Tests

Once running, execute smoke tests to verify system readiness:

### 1. System Health API Check
```bash
curl -i http://localhost:8000/api/v1/health/
```
**Expected Response:**
```json
HTTP/1.1 200 OK
Content-Type: application/json

{
  "status": "healthy",
  "service": "ProcureSphere 360",
  "database": "ok",
  "redis": "ok"
}
```

### 2. Swagger / OpenAPI Documentation
Navigate to `http://localhost:8000/api/docs/` to confirm OpenAPI schema generation and interactive Swagger UI.

### 3. Portal Authentication Smoke Test
- Navigate to `http://localhost:8000/login/`.
- Authenticate using seeded credentials (e.g., `admin@procuresphere.local` / `Password123!`).
- Confirm automated redirection to the role-specific dashboard.

---

## 6. Troubleshooting & Common Pitfalls

| Issue | Root Cause | Remediation |
|:---|:---|:---|
| **Database connection refused (`OperationalError`)** | PostgreSQL container or service is offline; incorrect `DB_HOST`/`DB_PORT`. | Verify `pg_isready -h localhost -p 5432`. Ensure Docker `db` container is running and healthy. |
| **Celery tasks not processing** | Redis broker is unreachable; Celery worker is not running. | Verify Redis connectivity via `redis-cli ping`. Start Celery worker using `celery -A config worker -l info`. |
| **`Account locked: too many login attempts`** | `django-axes` triggered brute-force lockout after failed logins. | Run `python manage.py axes_reset` to clear lockout records in staging. |
| **Static files returning 404 in staging** | `DEBUG=False` without running `collectstatic` or configuring WhiteNoise/Nginx. | Run `python manage.py collectstatic --noinput` and ensure static volume is mounted in Nginx. |
| **Celery worker crash on Windows (`ValueError`)** | Default prefork pool is unsupported on Windows multiprocessing. | Use `-P solo` or `-P threads` flag: `celery -A config worker -l info -P solo`. |
