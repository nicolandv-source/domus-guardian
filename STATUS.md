# DOMUS Guardian — Stato progetto

> Vista d'insieme cross-progetto (v0.1, 05-08-2026): [DOMUS v0.1 — Punto di
> situazione e priorità](https://app.notion.com/p/3b3f7b385bcf81529854fdcc0e62a6c1)
> su Notion, e [STATUS.md di domus-platform](https://github.com/nicolandv-source/domus-platform/blob/main/STATUS.md)
> per Core/Platform/Finance. Guardian resta il modulo con più item P0 aperti
> sulla DOMUS Master Roadmap.

## Release locale: 1.0.4 (in preparazione)

DOMUS Guardian è una App locale per Home Assistant OS che monitora la salute
della casa e del servizio stesso. I dati rimangono in rete locale: PostgreSQL
conserva dispositivi, incidenti e consegne delle notifiche; il token Home
Assistant è fornito dal Supervisor e non viene salvato nel repository.

## Funzioni disponibili

- Connessione WebSocket autenticata a Home Assistant e sottoscrizione a
  `state_changed`.
- Bootstrap del catalogo e aggiornamento automatico dei dispositivi.
- Raggruppamento delle entità per dispositivo fisico con fallback a `entity_id`.
- Debounce configurabile per dispositivi instabili, per evitare incidenti in
  loop.
- Incidenti aperti e risolti su stato stabilizzato del dispositivo.
- Health score pesato: profili `critical`, `important` e `optional` evitano
  che dispositivi non essenziali falsino lo stato generale.
- Notifiche persistenti Home Assistant per incidenti rilevanti, con deduplica,
  cooldown e retry limitato.
- Watchdog interno: verifica PostgreSQL, WebSocket, EventBus, loop async,
  memoria e task attivi; può resettare il pool DB o richiedere una
  riconnessione del WebSocket in modo sicuro.
- Monitoraggio entità configurabile: `GUARDIAN_MONITORED_DOMAINS` abilita una
  allowlist di domini HA separati da virgola; `GUARDIAN_EXCLUDED_ENTITY_PATTERNS`
  esclude glob `fnmatch` sull'intero `entity_id`. Entrambe vuote preservano il
  comportamento storico. Il pattern per Domus Finance non è ancora impostato:
  va confermato nel registry HA live e configurato operativamente.

## Stato di validazione corrente

- Implementazione 1.0.4 presente: retry PostgreSQL cooperativo, reset pool
  non fatale e riconciliazione TTS/STT/DLNA.
- Regressione versione corretta: `run.sh` esporta ora `APP_VERSION="1.0.4"`,
  allineato a manifest, runtime e API.
- `ruff check app tests alembic` e la suite completa sono superati con Python
  3.12.13: 47 test passati e un avviso di deprecazione non bloccante.
- La build container non è stata eseguita perché Docker non è disponibile
  nell'ambiente di validazione.
- Nessun deploy o riavvio è stato eseguito per 1.0.4.
- **Fix event loop bloccato (2026-08-05, mergiato in `main`, commit
  `5d62244`)**: `EventBus.publish()` veniva chiamato in modo sincrono
  dentro il loop di lettura WebSocket (`app/ha/websocket.py`), e un handler
  che apriva sessioni SQLAlchemy sincrone bloccava il loop asyncio abbastanza
  da far scadere il keepalive WebSocket (log `watchdog.event_loop_blocked`,
  delay fino a 92s, disconnessioni). Corretto avvolgendo ogni chiamata a
  `publish()` con `asyncio.to_thread(...)`, mantenendo l'ordine di
  pubblicazione. Deployato manualmente sull'add-on live (nessun sync
  automatico da GitHub per add-on locali), rebuild e restart eseguiti,
  verificato sano via chiamata diretta a `/api/v1/watchdog/health`
  (`status: "healthy"`, `event_loop_delay_ms: 1`).
- **Bug trovato in produzione (2026-08-10) e corretto: stato "degraded"
  bloccato in modo permanente.** Osservato sull'istanza live:
  `sensor.domus_guardian_watchdog` fermo su `degraded` da giorni con
  `event_bus_handler_failures=427` costante — il contatore non cresceva più,
  quindi il sistema si era già ripreso, ma lo stato restava sbagliato.
  Causa: `EventBus._handler_failures` è un totale cumulativo per tutta la
  vita del processo, e il watchdog lo usava direttamente per decidere lo
  stato — un solo errore passato bastava a bloccare "degraded" per sempre.
  Aggiunto `EventBus.take_recent_handler_failures()` (finestra dall'ultimo
  controllo, si resetta alla lettura); il watchdog ora la usa al posto del
  totale cumulativo, che resta comunque esposto nell'API come diagnostica.
  Test di regressione aggiunto (`tests/test_watchdog.py`), suite completa
  66/66 e `ruff check` puliti con Python 3.12.13. Non ancora deployato
  sull'add-on live.

## Endpoint principali

- `/api/v1/ha/health`
- `/api/v1/watchdog/health`
- `/api/v1/db/ping`
- `/api/v1/ha/ping`
- `/api/v1/devices`
- `/api/v1/devices/debounced`
- `/api/v1/incidents`
- `/api/v1/notifications`

## Prossimi passi consigliati

1. Dopo deploy controllato, verificare watchdog, DB, WebSocket, EventBus,
   incidenti e notifiche; poi valutare il watchdog Supervisor esterno.
