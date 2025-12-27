"""Gunicorn configuration file."""

import os

# Bind to port from environment
bind = f"0.0.0.0:{os.getenv('PORT', '5000')}"

# Single worker to avoid multiple parser threads
workers = 1
threads = 2

# Timeout for long-running requests
timeout = 120

# Logging
accesslog = "-"
errorlog = "-"
loglevel = "info"

# Preload app to start parser thread once
preload_app = True


def on_starting(server):
    """Called just before the master process is initialized."""
    pass


def post_fork(server, worker):
    """Called just after a worker has been forked."""
    # Start parser thread after fork
    from server import start_parser
    start_parser()