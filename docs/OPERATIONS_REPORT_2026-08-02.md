# DOMUS Guardian — Report operativo finale

**2 agosto 2026 — branch `feature/guardian-1.0.4-watchdog-recovery`**

## Stato verificato

- Worktree iniziale 1.0.4: 9 file, `+261/-29`; `git diff --check` pulito.
- Regressione corretta: `run.sh` ora esporta `APP_VERSION="1.0.4"`, coerente con manifest e runtime.
- Test live 1.0.4: l'avvio era sano, ma sette incidenti availability storici TTS/STT/DLNA (ID 1582, 1449, 829, 828, 827, 826 e 822) sono rimasti `open`; il deploy è stato annullato senza modifiche a Home Assistant o PostgreSQL.
- Correzione DLNA: un incidente `dlna_dmr` viene risolto soltanto se il registro entità associa lo stesso `device_id` a un altro `media_player` non-DLNA in stato disponibile/operativo. Le TV con sola entità DLNA, o con sibling non-DLNA non disponibile, restano incidenti reali. La riconciliazione è idempotente e non cancella lo storico.
- Con Python 3.12.13: `ruff check app tests alembic`, `PYTHONPATH=. pytest -q` (51 test), `bash -n run.sh` e `git diff --check` superati; resta un avviso di deprecazione FastAPI/Starlette non bloccante.
- Build container non eseguita: Docker non è disponibile nell'ambiente di validazione.

## Non eseguito intenzionalmente

Deploy/riavvii Home Assistant/PostgreSQL, verifiche live e pubblicazione Notion.

## Pendenti

1. Deploy controllato e verifica watchdog, DB, WebSocket, EventBus, incidenti e notifiche.
