# Changelog

## 0.5.0

- Aggiunti profili health pesati (`critical`, `important`, `optional`).
- Aggiunta classificazione configurabile per dominio, device class e nome.
- Health score calcolato sul peso dei dispositivi stabilizzati offline.
- Aggiunto campo `device_class` al catalogo dispositivi.
- Arricchito endpoint diagnostico dei dispositivi stabilizzati.

## 0.4.0

- Raggruppamento delle entità per dispositivo fisico tramite il registro entità
  Home Assistant, con fallback a `entity_id`.
- Debounce configurabile per i cambi di disponibilità (`45` secondi predefiniti).
- Incidenti e health calcolati solo sugli stati stabilizzati.
- Aggiunto bootstrap degli stati correnti Home Assistant al collegamento WebSocket.
- Aggiunto endpoint diagnostico `/api/v1/devices/debounced`.

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
