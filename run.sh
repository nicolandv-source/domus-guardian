#!/usr/bin/env bash
set -Eeuo pipefail

if [[ -z "${SUPERVISOR_TOKEN:-}" ]]; then
    echo "ERRORE: SUPERVISOR_TOKEN non disponibile."
    exit 1
fi

export HA_TOKEN="${SUPERVISOR_TOKEN}"

OPTIONS_EXPORTS="$(python3 - <<'PY'
import json
import shlex
from pathlib import Path
from urllib.parse import quote_plus

options = json.loads(Path("/data/options.json").read_text(encoding="utf-8"))
host = options.get("database_host", "db21ed7f-postgres-latest")
port = int(options.get("database_port", 5432))
database = options.get("database_name", "domus_guardian")
user = options.get("database_user", "domus_guardian")
password = options.get("database_password")
if not password:
    raise SystemExit("ERRORE: database_password non configurata")

database_url = (
    "postgresql+psycopg://"
    f"{quote_plus(user)}:{quote_plus(password)}@{host}:{port}/{quote_plus(database)}"
)
for key, value in {
    "DATABASE_URL": database_url,
    "DB_HOST": host,
    "DB_PORT": str(port),
    "DB_NAME": database,
    "DB_USER": user,
    "DB_PASSWORD": password,
}.items():
    print(f"export {key}={shlex.quote(value)}")
PY
)"
eval "${OPTIONS_EXPORTS}"

echo "Attendo PostgreSQL su ${DB_HOST}:${DB_PORT}..."
python3 - <<'PY'
import os
import time
import psycopg

last_error = None
for attempt in range(1, 31):
    try:
        with psycopg.connect(
            host=os.environ["DB_HOST"],
            port=int(os.environ["DB_PORT"]),
            dbname=os.environ["DB_NAME"],
            user=os.environ["DB_USER"],
            password=os.environ["DB_PASSWORD"],
            connect_timeout=3,
        ):
            print("PostgreSQL disponibile.")
            break
    except Exception as exc:
        last_error = exc
        print(f"Tentativo PostgreSQL {attempt}/30 fallito")
        time.sleep(2)
else:
    raise SystemExit(f"PostgreSQL non disponibile: {type(last_error).__name__}")
PY
unset DB_PASSWORD

echo "Applicazione migrazioni database..."
python3 -m alembic upgrade head

echo "Avvio DOMUS Guardian sulla porta 8000..."
exec python3 -m uvicorn app.main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --log-level info
