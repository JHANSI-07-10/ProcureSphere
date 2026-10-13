#!/bin/bash
set -e

if [ "$1" = 'python' ] && [ "$2" = 'manage.py' ] && [ "$3" = 'runserver' ] || [[ "$1" == *"gunicorn"* ]]; then
    echo "Running database migrations..."
    python manage.py migrate --noinput

    echo "Collecting static files..."
    python manage.py collectstatic --noinput
fi

echo "Starting the application..."
exec "$@"
