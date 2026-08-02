# DOMUS Guardian 1.0.4 — Riepilogo tecnico

**Stato:** implementata localmente, non rilasciata.

- Retry PostgreSQL configurabile: 1–5 tentativi, default 3.
- Backoff esponenziale cooperativo: 0–30 s, default 1 s; non blocca EventBus/WebSocket.
- Ping DB e reset pool in thread; reset tentato una sola volta per ciclo. Il suo errore non termina il watchdog.
- Nuove azioni watchdog: `database_pool_reset`, `database_retry_succeeded`.
- TTS/STT e sorgenti DLNA esclusi dagli incidenti availability; gli incidenti storici sono risolti senza cancellazione.
- Nessuna nuova migrazione Alembic e nessuna API rimossa. `config.yaml`, runtime e changelog sono allineati a 1.0.4.

**Validazione nota:** lint riportato verde e 14 test mirati riportati passati. La suite completa resta da eseguire con Python moderno: `.venv` locale è Python 3.9.6, il container usa Python 3.13.
