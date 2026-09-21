"""gunicorn settings, picked up automatically because this file sits next to run.py.

Worker count comes from WEB_CONCURRENCY and the port from PORT (gunicorn reads both itself), so the Procfile stays
``web: gunicorn run:app``.
"""

import os

# A search can wait for an Apify actor run (APIFY_RUN_TIMEOUT seconds, default 120). A worker that is silent for longer
# than `timeout` is killed, so allow a little more than the longest run.
timeout = int(os.getenv("GUNICORN_TIMEOUT") or (int(os.getenv("APIFY_RUN_TIMEOUT") or 120) + 30))
