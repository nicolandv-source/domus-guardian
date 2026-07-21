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

## Stabilizzazione dispositivi instabili

DOMUS raggruppa le entità che Home Assistant associa allo stesso `device_id`.
Il dispositivo fisico è disponibile se almeno una delle sue entità è disponibile.
Un cambio di disponibilità deve restare invariato per `45` secondi prima di
aprire o risolvere un incidente; l’intervallo è configurabile nelle opzioni
dell’App (`device_debounce_seconds`, da 5 a 300 secondi).

## Score health pesato

Le regole in `app/config/health_weights.json` classificano ogni gruppo come
`critical`, `important` oppure `optional`. Il punteggio usa il rapporto tra il
peso dei dispositivi offline e il peso totale dei dispositivi stabilizzati;
TV, TTS e dispositivi di test incidono quindi molto meno di porte, allarmi e
luci principali.

## Notifiche incidenti

DOMUS crea notifiche persistenti in Home Assistant per i nuovi incidenti
critici e, se abilitato, importanti. Le notifiche sono deduplicate per
incidente, aggiornate alla risoluzione e protette da un cooldown configurabile.

## Watchdog interno

Un task interno controlla periodicamente PostgreSQL, la connessione WebSocket,
l'EventBus, il ritardo del loop async e la memoria del processo. In caso di DB
non disponibile resetta solo il pool SQLAlchemy; se il WebSocket rimane senza
eventi oltre la soglia richiede una riconnessione sicura. Lo stato è disponibile
in `/api/v1/watchdog/health` e come `watchdog_status` nell'health esistente.

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
- `/api/v1/health/weights`
- `/api/v1/notifications`
- `/api/v1/notifications/{id}`
- `/api/v1/devices`
- `/api/v1/devices/debounced`
- `/api/v1/incidents`
- `/api/v1/watchdog/health`

## Test locali

```bash
python -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/ruff check app tests alembic
.venv/bin/pytest -q
```
