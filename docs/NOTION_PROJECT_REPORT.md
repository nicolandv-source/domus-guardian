# DOMUS Guardian — Report completo di progetto

> Pronto per Notion — 2 agosto 2026. Ambito: branch locale `feature/guardian-1.0.4-watchdog-recovery`. Non sono stati eseguiti deploy, riavvii, interrogazioni live di Home Assistant/PostgreSQL o pubblicazioni Notion.

## Sintesi

DOMUS Guardian è una Home Assistant App locale FastAPI per monitorare dispositivi fisici, persistere incidenti e notifiche su PostgreSQL e calcolare una health ponderata. La 1.0.4 è implementata nel worktree ma richiede ancora suite completa in ambiente Python compatibile e deploy controllato.

| Area | Stato |
|---|---|
| Implementazione 1.0.4 | Presente: 9 file, `+261/-29` |
| Lint | Riportato verde (`ruff check app tests alembic`) |
| Test mirati | Riportati 14 passati (watchdog + riconciliazione) |
| Suite completa | Pendente: `.venv` locale Python 3.9.6 incompatibile |
| Deploy e stato live | Non eseguiti/verificati |

## Obiettivi

- Rilevare indisponibilità di dispositivi fisici Home Assistant, evitando helper e servizi.
- Conservare dispositivi, incidenti, manutenzioni e notifiche in PostgreSQL.
- Fornire health score pesato, API, dashboard e notifiche persistenti.
- Recuperare da problemi DB/WebSocket/EventBus senza riavviare servizi esterni.

## Architettura

```text
Home Assistant REST/Supervisor ─┐
Home Assistant WebSocket ───────┼─ Adapter → EventBus → DeviceService
                                │                    ├─ grouping + debounce
                                │                    ├─ incidenti/manutenzioni
                                │                    └─ HealthEngine → PostgreSQL
                                └─ NotificationEngine → notifiche HA

Watchdog → PostgreSQL, WebSocket, EventBus, loop async, memoria
FastAPI → dashboard e API /api/v1/*
```

Il lifecycle FastAPI avvia WebSocket e worker per debounce, retry notifiche, riconciliazione incidenti e watchdog; li annulla ordinatamente allo stop.

## Integrazione Home Assistant e PostgreSQL

- L'App usa `homeassistant_api: true`, Supervisor REST e WebSocket (`state_changed`); il token arriva dal Supervisor e non va versionato.
- Un dispositivo con più entità resta disponibile se almeno una delle sue entità è disponibile. Solo entità di registro o con `device_id` esplicito sono monitorate.
- SQLAlchemy, psycopg e Alembic gestiscono PostgreSQL e schema. Le credenziali arrivano dalle opzioni runtime, non dal repository.

## Funzionalità e manutenzioni

- Debounce configurabile (predefinito 45 s) prima di aprire/risolvere incidenti.
- Health ponderata con profili `critical`, `important`, `optional` da `app/config/health_weights.json`.
- Finestre di manutenzione per `device_id`: escludono temporaneamente health, incidenti e notifiche; API `GET/PUT/DELETE /api/v1/maintenance`.
- Notifiche persistenti HA con deduplica, cooldown e retry.
- Endpoint chiave: `/api/v1/ha/health`, `/api/v1/watchdog/health`, `/api/v1/db/ping`, `/api/v1/incidents`, `/api/v1/devices`, `/api/v1/notifications`.

## Watchdog, sicurezza e resilienza

Il watchdog misura DB, WebSocket (connessione e staleness), EventBus, ritardo loop, memoria e task. Espone `/api/v1/watchdog/health`; può resettare il pool SQLAlchemy e chiedere una riconnessione WebSocket, ma non riavvia Home Assistant, Supervisor o PostgreSQL. I worker catturano errori transitori e non riportano segreti nei log.

In 1.0.4 il DB riceve fino a 3 retry configurabili con backoff esponenziale cooperativo. Ping/reset eseguono in thread e `asyncio.sleep` lascia libero l'event loop. Un reset pool fallito viene registrato ma non termina il watchdog.

## Cronologia release

| Versione | Contenuto |
|---|---|
| 0.1.0 | Struttura App, FastAPI, REST Supervisor |
| 0.2.0 | PostgreSQL, SQLAlchemy, Alembic, dispositivi/incidenti |
| 0.3.0 | WebSocket, EventBus, repository e lifecycle |
| 0.4.0 | Raggruppamento fisico, debounce, bootstrap stati |
| 0.5.0 | Health pesato |
| 0.6.0 | Notifiche, deduplica, cooldown, retry |
| 0.7.0 | Watchdog interno |
| 1.0.0 | Worker resilienti e dashboard affidabile |
| 1.0.1 | Manutenzioni e filtro TTS/STT |
| 1.0.2 | Bearer token nell'upgrade WebSocket |
| 1.0.3 | Riconciliazione incidenti e debounce deterministico |
| 1.0.4 (in preparazione) | Retry DB cooperativo e filtro/reconciliation DLNA |

## Incidenti

### Risolti nel codice

- Falsi incidenti da helper/gruppi UI senza dispositivo fisico.
- Falsi incidenti TTS/STT e DLNA; lo storico viene preservato, gli incidenti invalidi sono risolti.
- Recupero prima del debounce che lasciava transizioni offline pendenti.
- Errore DB o reset pool che poteva degradare il watchdog.
- Upgrade WebSocket Supervisor senza bearer token.

### Da verificare

- Il conteggio e l'elenco degli incidenti live: verificare dopo deploy con `GET /api/v1/incidents?status=open`.
- Suite completa su Python compatibile: è il gate di rilascio residuo.

## Configurazione e dipendenze

| Componente | Valore |
|---|---|
| Container | Python 3.13 slim |
| Runtime | FastAPI, Uvicorn, httpx, websockets, Pydantic 2 |
| Dati | SQLAlchemy 2, psycopg 3, Alembic |
| Watchdog | 60 s; stale WS 10 min; memoria 512 MB |
| Retry DB 1.0.4 | 3 tentativi; backoff iniziale 1 s |

I limiti sono applicati dal codice: intervallo 10–3600 s, stale 1–1440 min, memoria almeno 64 MB, retry 1–5, backoff 0–30 s.

## Piano deploy, rollback e roadmap

1. Creare un ambiente pulito con Python compatibile con il container e installare `requirements-dev.txt`.
2. Eseguire `ruff check app tests alembic`, `pytest -q` e controllo diff.
3. Committare; verificare remote/autenticazione e chiedere conferma prima del push.
4. Deploy controllato dell'App. Verificare watchdog healthy, DB, WebSocket, EventBus e mancata riapertura TTS/STT/DLNA.
5. In caso di regressione, interrompere rollout e ripristinare l'ultima versione App funzionante senza cancellare PostgreSQL. La 1.0.4 non introduce migrazioni.

Roadmap: CI riproducibile sulla versione Python del container; osservazione post-release; dashboard incidenti più esplicita; metriche/alerting esterno; formalizzazione tag e release.
