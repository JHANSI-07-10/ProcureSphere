import os

bind = os.environ.get("GUNICORN_BIND", "0.0.0.0:8000")

# 2 * cores + 1 is standard recommendation for I/O bound web workloads
workers = int(os.environ.get("GUNICORN_WORKERS", 3))
threads = int(os.environ.get("GUNICORN_THREADS", 2))
worker_class = os.environ.get("GUNICORN_WORKER_CLASS", "gthread")

timeout = int(os.environ.get("GUNICORN_TIMEOUT", 120))
keepalive = int(os.environ.get("GUNICORN_KEEPALIVE", 5))

# Logging to stdout / stderr for container visibility
accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("GUNICORN_LOG_LEVEL", "info")
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)sµs'

# Security & Process management
proc_name = "procuresphere_gunicorn"
daemon = False
preload_app = False
max_requests = 1000
max_requests_jitter = 50
