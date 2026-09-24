#!/bin/sh
# Container start: bring the database schema up to date, then hand over to gunicorn.
set -e
flask --app run db upgrade
flask --app run seed-stores --missing-only   # the Durban supermarket and clothing branches
flask --app run seed-admin --keep-password   # the built-in admin account (DEFAULT_ADMIN_EMAIL)
exec gunicorn run:app \
  --bind "0.0.0.0:${PORT:-8000}" \
  --workers "${WEB_CONCURRENCY:-2}" \
  --access-logfile -
