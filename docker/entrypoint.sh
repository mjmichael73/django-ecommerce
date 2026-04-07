#!/bin/sh
set -e

if [ -n "${POSTGRES_HOST:-}" ]; then
  until python - <<'PY'
import os
import socket
import sys

host = os.environ["POSTGRES_HOST"]
port = int(os.environ.get("POSTGRES_PORT", "5432"))
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    sock.connect((host, port))
except OSError:
    sys.exit(1)
else:
    sys.exit(0)
finally:
    sock.close()
PY
  do
    echo "Waiting for PostgreSQL at ${POSTGRES_HOST}:${POSTGRES_PORT:-5432}..."
    sleep 1
  done
fi

python manage.py migrate --noinput
if [ "${SKIP_COLLECTSTATIC:-0}" != "1" ]; then
  python manage.py collectstatic --noinput
fi

exec "$@"
