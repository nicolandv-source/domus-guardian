# DOMUS Guardian — Report operativo

**3 agosto 2026 — branch `feature/guardian-1.0.4-watchdog-recovery`**

## Stato completato

- Branch locale, `HEAD` e `origin/feature/guardian-1.0.4-watchdog-recovery` verificati sul commit iniziale `4972a251d7fef1310f3b338c4ed1051f0c2fe3e2`.
- Mantenuta la versione **1.0.4**; nessuna migrazione Alembic aggiunta.
- Il bootstrap WebSocket legge ora sia entity registry sia device registry Home Assistant prima dello snapshot degli stati.
- La riconciliazione media player usa esclusivamente dati dei registry, mai titoli o testo degli incidenti:
  - MAC normalizzati condivisi creano un cluster fisico forte, anche con `device_id` HA diversi;
  - un media player senza MAC può unirsi a un cluster che possiede un MAC soltanto con due segnali coerenti tra area, modello compatibile (anche per contenimento normalizzato) e nome specifico;
  - modelli incompatibili, sola area e nomi generici non sono sufficienti.
- Lo stato `off` resta operativo. Un incidente viene risolto quando un'altra rappresentazione del suo cluster è operativa; con tutte le rappresentazioni `unavailable`/`unknown` rimane aperto.
- La riconciliazione conserva le righe storiche, è idempotente al doppio avvio e non riapre l'incidente quando il sibling operativo resta nel cluster.

## Copertura verificata

- Samsung/DLNA con MAC condiviso: sibling `on` e sibling `off`.
- Cast Q60D senza MAC associato a Samsung Q60D tramite area e modello specifici.
- Google Nest Hub generico nella stessa area non associato alla TV.
- Modello diverso, stesso nome e area: nessuna correlazione.
- DLNA unico non disponibile e cluster interamente non disponibile: incidente reale mantenuto.
- Doppia riconciliazione senza duplicazioni, con storico risolto preservato.

## Verifiche locali

Eseguite con Python **3.12.13**:

- `ruff check app tests alembic` — superato.
- `PYTHONPATH=. pytest -q` — **58 superati**; unico avviso non bloccante di deprecazione FastAPI/Starlette.
- `bash -n run.sh` — superato.
- `git diff --check` — superato.

## Limiti intenzionali

Non sono stati eseguiti deploy, riavvii o modifiche a Home Assistant, Supervisor, host o PostgreSQL. Restano necessari solo deploy controllato e osservazione live post-rilascio.
