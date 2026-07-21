# DOMUS Guardian — Stato progetto

## Release locale: 0.7.0

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

## Verifica operativa più recente

- Database PostgreSQL raggiungibile.
- WebSocket connesso e in ricezione eventi.
- Endpoint health operativo con score pesato `99` e stato `healthy`.
- Endpoint watchdog operativo con stato `healthy`.
- Test automatici: `24 passed`.
- Watchdog del Supervisor mantenuto disattivato fino a ulteriore periodo di
  osservazione.

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

1. Osservare il watchdog per alcuni giorni con il watchdog del Supervisor
   ancora disattivato.
2. Verificare una notifica di apertura e di risoluzione di un incidente senza
   alterare dispositivi fisici critici.
3. Quando la stabilità è confermata, valutare l'attivazione del watchdog del
   Supervisor come livello di recupero esterno.
