#!/usr/bin/env bash
set -Eeuo pipefail

if [[ -z "${SUPERVISOR_TOKEN:-}" ]]; then
    echo "ERRORE: SUPERVISOR_TOKEN non disponibile."
    exit 1
fi

export HA_TOKEN="${SUPERVISOR_TOKEN}"

echo "Avvio DOMUS Guardian sulla porta 8000..."

exec python -m uvicorn app.main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --log-level info
