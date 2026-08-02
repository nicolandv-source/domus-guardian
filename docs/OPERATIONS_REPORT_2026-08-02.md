# DOMUS Guardian — Report operativo finale

**2 agosto 2026 — branch `feature/guardian-1.0.4-watchdog-recovery`**

## Stato verificato

- Worktree iniziale 1.0.4: 9 file, `+261/-29`; `git diff --check` pulito.
- Versione 1.0.4 presente in manifest e runtime; nuovi test per watchdog e riconciliazione.
- Virtualenv locale Python 3.9.6 non idoneo alla suite completa; Dockerfile Python 3.13 slim.

## Non eseguito intenzionalmente

Deploy/riavvii Home Assistant/PostgreSQL, verifiche live, pubblicazione Notion e push remoto.

## Pendenti

1. Suite completa su Python compatibile.
2. Review diff e commit descrittivo.
3. Verifica autenticazione GitHub e conferma utente prima del push.
4. Deploy controllato e verifica watchdog, DB, WebSocket, EventBus, incidenti e notifiche.
