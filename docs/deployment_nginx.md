# Nginx Reverse Proxy & Gunicorn Production Deployment Guide
**ProcureSphere 360 — Enterprise Procurement ERP**
**PRD Reference:** HPE-PRD-2026-PROC01 (Section 3 & Section 16)

---

## 1. Overview & Architecture

Per PRD Section 3 (*Mandatory Technology & Architecture Layer*), production HTTP serving requires **Gunicorn behind Nginx** with HTTPS/TLS termination, secure headers, Gzip compression, and reverse proxy routing.

```
                    +------------------------------------+
                    |       Client / Web Browser         |
                    +------------------------------------+
                                      |
                         HTTP (80) / HTTPS (443)
                                      v
                    +------------------------------------+
                    |         Nginx 1.25 (Alpine)        |
                    |   (TLS, Security Headers, Gzip)    |
                    +------------------------------------+
                         /            |             \
             /static/   /   /media/   |    App Proxy \
            +----------+  +----------+                v
            |  Static  |  |  Media   |       +----------------+
            |  Volume  |  |  Volume  |       | Gunicorn 21.2+ |
            +----------+  +----------+       |  (Port 8000)   |
                                             +----------------+
                                                      |
                                                      v
                                             +----------------+
                                             | Django 4.2+ /  |
                                             |   DRF 3.14+    |
                                             +----------------+
```

---

## 2. Implemented Components

### 2.1 Nginx Reverse Proxy (`nginx/nginx.conf`)
- **Upstream Connection:** Proxies web traffic to `web:8000` (`django_app`) with keepalive connection pools.
- **Client Body Limit:** Configured to `client_max_body_size 25M` to allow document uploads (KYC compliance packs, signed contracts, invoices).
- **Static & Media Offloading:** Directly serves `/static/` (cache 30d, immutable) and `/media/` (cache 7d), bypassing Python workers for optimal latency and throughput.
- **Health Check Routing:** `/health/` is directly proxied to Django's health endpoint with silent access logging.
- **Gzip Compression:** Enabled for `text/plain`, `text/css`, `application/json`, `application/javascript`, `image/svg+xml`, and XML.
- **Security Baseline Headers:**
  - `X-Frame-Options: DENY`
  - `X-Content-Type-Options: nosniff`
  - `X-XSS-Protection: 1; mode=block`
  - `Referrer-Policy: strict-origin-when-cross-origin`
  - `Permissions-Policy: geolocation=(), microphone=(), camera=()`

### 2.2 Gunicorn WSGI Server (`gunicorn.conf.py`)
- **Worker Configuration:** Dynamic worker pool defaulting to 3 workers (`gthread` worker class with 2 threads per worker) for concurrent I/O.
- **Timeouts:** 120s request timeout preventing worker kills during large report generation or PDF rendering.
- **Logging:** Structured logging to `stdout`/`stderr` adhering to Docker observability guidelines.

### 2.3 Docker Compose Production Stack (`docker-compose.prod.yml`)
- Orchestrates:
  1. `procuresphere_nginx` (Ports 80, 443)
  2. `procuresphere_web` (Gunicorn WSGI)
  3. `procuresphere_db` (PostgreSQL 15)
  4. `procuresphere_redis` (Redis 7)
  5. `procuresphere_worker` (Celery Worker)
  6. `procuresphere_beat` (Celery Beat)
- Shared Docker volumes:
  - `static_volume` (shared between `web` and `nginx`)
  - `media_volume` (shared between `web`, `worker`, and `nginx`)
  - `postgres_data` (persistent database storage)

---

## 3. Operational Commands

### 3.1 Test Nginx Configuration Syntax
```bash
docker compose -f docker-compose.prod.yml run --rm nginx nginx -t
```
*Expected output:*
```
nginx: the configuration file /etc/nginx/nginx.conf syntax is ok
nginx: configuration file /etc/nginx/nginx.conf test is successful
```

### 3.2 Launch the Production Stack
```bash
docker compose -f docker-compose.prod.yml up --build -d
```

### 3.3 Verify Container Status
```bash
docker compose -f docker-compose.prod.yml ps
```

### 3.4 Check Nginx Logs
```bash
docker compose -f docker-compose.prod.yml logs -f nginx
```

### 3.5 Stop Production Stack
```bash
docker compose -f docker-compose.prod.yml down
```
