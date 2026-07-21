# DOMUS Guardian

Backend FastAPI per monitorare dispositivi, disponibilità e incidenti di una
installazione Home Assistant OS.

## Architettura

Gli eventi `state_changed` seguono questo percorso:

```text
Home Assistant WebSocket
  -> EventBus
  -> HomeAssistantAdapter
  -> StateChangedDTO
  -> DeviceService
  -> Repository
  -> PostgreSQL
```

Quando un’entità entra nello stato `unavailable`, DOMUS apre un singolo
incidente critico di disponibilità. Quando torna in qualsiasi stato disponibile,
l’incidente viene marcato `resolved`.

## Installazione Home Assistant

Il progetto viene eseguito come App locale in:

```text
/addons/domus_guardian
```

Le credenziali PostgreSQL vengono lette da `/data/options.json`; il token Home
Assistant viene fornito dal Supervisor. Nessuna credenziale deve essere salvata
nel repository.

## Endpoint

- `/`
- `/docs`
- `/api/v1/db/ping`
- `/api/v1/ha/ping`
- `/api/v1/ha/health`
- `/api/v1/devices`
- `/api/v1/incidents`

## Test locali

```bash
python -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/ruff check app tests alembic
.venv/bin/pytest -q
```
