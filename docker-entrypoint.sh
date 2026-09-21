#!/bin/sh
# Container start: bring the database schema up to date, then hand over to gunicorn.
set -e
flask --app run db upgrade
exec gunicorn run:app \
  --bind "0.0.0.0:${PORT:-8000}" \
  --workers "${WEB_CONCURRENCY:-2}" \
  --access-logfile -
