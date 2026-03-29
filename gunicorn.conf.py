import os

port = os.environ.get("PORT", "8000")
bind = f"0.0.0.0:{port}"

timeout = 300
workers = int(os.environ.get("WEB_CONCURRENCY", "1"))
threads = int(os.environ.get("GUNICORN_THREADS", "4"))
graceful_timeout = 60
