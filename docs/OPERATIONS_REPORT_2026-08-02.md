# DOMUS Guardian — Report operativo finale

**2 agosto 2026 — branch `feature/guardian-1.0.4-watchdog-recovery`**

## Stato verificato

- Worktree iniziale 1.0.4: 9 file, `+261/-29`; `git diff --check` pulito.
- Regressione corretta: `run.sh` ora esporta `APP_VERSION="1.0.4"`, coerente con manifest e runtime.
- Con Python 3.12.13: `ruff check app tests alembic` verde e suite completa `pytest` con 47 test passati; un avviso di deprecazione non bloccante.
- Build container non eseguita: Docker non è disponibile nell'ambiente di validazione.

## Non eseguito intenzionalmente

Deploy/riavvii Home Assistant/PostgreSQL, verifiche live e pubblicazione Notion.

## Pendenti

1. Deploy controllato e verifica watchdog, DB, WebSocket, EventBus, incidenti e notifiche.
