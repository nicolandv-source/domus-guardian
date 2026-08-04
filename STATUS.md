# DOMUS Guardian — Stato progetto

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
