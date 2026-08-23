# Changelog

## 1.0.4 - unreleased

- Abilitato il watchdog Supervisor sull'add-on: riavvio automatico in caso di
  crash del container, senza intervento manuale.
- Il watchdog PostgreSQL effettua fino a tre tentativi con backoff esponenziale
  cooperativo: le attese non bloccano EventBus, WebSocket o loop async.
- Anche un errore durante il reset prudente del pool SQLAlchemy viene gestito:
  il watchdog continua il ciclo successivo e può recuperare da un errore DB
  transitorio senza riavviare servizi.
- Gli incidenti availability storici di TTS/STT e DLNA vengono riconciliati come
  risolti, senza cancellare lo storico; queste sorgenti non aprono nuovi
  incidenti.

## 1.0.3 - unreleased

- Corretto il debounce availability: un recupero prima della scadenza annulla
  in modo deterministico la transizione offline pendente.
- Aggiunta riconciliazione all'avvio e periodica degli incidenti availability
  rispetto agli stati HA persistiti, senza cancellare lo storico.
- Health e API incidenti ora usano gli incidenti persistiti riconciliati; le API
  supportano filtri `status`, `severity`, `limit` e `offset`.
- Le notifiche originate da endpoint sincroni sono programmate sul loop
  applicativo noto, senza errori di event loop assente.
- Le entità helper senza device nel registro Home Assistant sono escluse dal
  monitoraggio availability; i gruppi UI non diventano dispositivi fisici.

## 1.0.2

- Inviato il bearer token anche nell'upgrade WebSocket verso il proxy
  Supervisor, mantenendo il normale handshake `auth` di Home Assistant.

## 1.0.1

- Aggiunte finestre di manutenzione/assenza per `device_id`, con motivo,
  inizio, scadenza opzionale e API per attivazione, elenco e disattivazione.
- Durante una finestra attiva DOMUS esclude il dispositivo da health, incidenti
  e notifiche; gli incidenti aperti vengono risolti in modo controllato.
- Ripristinato automaticamente il monitoraggio alla scadenza o disattivazione.
- Escluse le entità di servizio TTS/STT dal monitoraggio di disponibilità.

## 1.0.0

- Allineata la versione dell'App, delle API e della configurazione di runtime.
- Resi resilienti i worker di debounce e retry notifiche: un errore transitorio
  viene registrato e non interrompe il monitoraggio in background.
- Corretta la dashboard web: quando le API non sono raggiungibili non mostra più
  uno stato falsamente "Online" o un watchdog sano.
- Estesa la suite di regressione per worker e dashboard; 29 test automatici.

## 0.7.0

- Aggiunto watchdog interno per DB, WebSocket, EventBus, loop async e memoria.
- Aggiunto endpoint `/api/v1/watchdog/health` e `watchdog_status` nell'health.
- Aggiunto reset prudente del pool database e riconnessione WebSocket su stale.

## 0.6.0

- Aggiunte notifiche persistenti Home Assistant per incidenti critici e importanti.
- Aggiunti record di consegna, deduplica, cooldown e retry limitato.
- Aggiunte API per consultare le notifiche.

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
