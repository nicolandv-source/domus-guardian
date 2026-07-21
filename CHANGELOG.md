# Changelog

## 0.3.0

- Aggiunto client WebSocket Home Assistant con handshake e riconnessione.
- Aggiunto lifecycle FastAPI con cancellazione pulita del task WebSocket.
- Aggiunti EventBus, DTO, adapter, repository e DeviceService.
- Aggiunto upsert dei dispositivi da eventi `state_changed`.
- Aggiunta apertura e risoluzione degli incidenti di disponibilità.
- Aggiunte API per dispositivi e incidenti.
- Aggiunti test automatici per WebSocket, mapping, persistenza e lifecycle.

## 0.2.0

- Aggiunti PostgreSQL, SQLAlchemy e Alembic.
- Aggiunte tabelle `devices` e `incidents`.
- Aggiunti endpoint database e health dinamico.

## 0.1.0

- Struttura iniziale Home Assistant App.
- API FastAPI e connessione REST al Supervisor.
