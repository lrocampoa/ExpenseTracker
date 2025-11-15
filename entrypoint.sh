#!/bin/sh
set -e

python manage.py migrate --noinput
python manage.py collectstatic --noinput

if [ "$#" -eq 0 ]; then
    set -- gunicorn
fi

if [ "$1" = "gunicorn" ]; then
    shift
    PORT="${PORT:-8000}"
    WORKERS="${WEB_CONCURRENCY:-2}"
    THREADS="${GUNICORN_THREADS:-1}"
    TIMEOUT="${GUNICORN_TIMEOUT:-120}"
    set -- gunicorn \
        config.wsgi:application \
        --bind "0.0.0.0:${PORT}" \
        --workers "${WORKERS}" \
        --threads "${THREADS}" \
        --timeout "${TIMEOUT}" \
        "$@"
fi

exec "$@"
