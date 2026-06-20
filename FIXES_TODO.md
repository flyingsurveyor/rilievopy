# RilievoPY — Lista interventi concordati

Generato il 2026-05-10 dopo revisione generale del progetto.

---

## Priorità ALTA

### ✅ 1. Sampling bloccante nel request handler
**File:** `routes/surveys.py:463-466`
**Problema:** `for _ in range(n_iters): time.sleep(interval)` blocca il thread Flask per tutta la durata del campionamento (default 10s). Con Waitress a 4 thread, 3-4 click sul bottone "Salva punto" bloccano il server.
**Fix concordato:** Background thread + poll asincrono.
- POST `/survey/<sid>/point` avvia thread, ritorna subito `{"job_id": "..."}`
- Thread esegue il sampling, scrive risultato in dict `JOBS` in-memory
- Frontend fa poll su `/api/job/<job_id>` ogni 500ms
- Quality gate pre-sampling rimane sincrono (è istantaneo)
- Se il tab viene chiuso durante sampling: campionamento finisce, punto non viene salvato (comportamento accettabile)
- La progress bar nel frontend sarà reale (non solo timer locale)
- **Notifica completamento:** via SSE esistente. Thread scrive `STATE.set("JOB", {id, status, progress, total, result})`. Il form page già ascolta SSE da base.html — legge `JOB.id` e reagisce. Nessun nuovo endpoint di polling.

### ✅ 2. Feedback disconnessione GNSS (rtkino / upstream)
**File:** `modules/ubx_parser.py:182-184`, `templates/base.html`, `modules/state.py`
**Problema:** Quando la connessione TCP con RTKino si perde, l'UI non riceve nessun evento esplicito. Dopo 3s di silenzio scatta `setNoData()`. Nel frattempo i dati visualizzati sono stantii ma non segnalati come tali. L'utente non sa se è un problema di rete locale o del ricevitore.
**Fix concordato:**
- `upstream_loop` scrive `STATE.patch("TPV", _connected=False)` alla disconnessione
- `upstream_loop` scrive `STATE.patch("TPV", _connected=True)` alla (ri)connessione
- Il frontend SSE handler distingue:
  - `_connected: false` → banner "Connessione RTK persa, riconnessione..."
  - silenzio SSE > 3s → "Nessun dato (problema rete locale)"
- **Da implementare:** banner visibile in `base.html`, distinguendo i due casi

---

## Priorità MEDIA

### ✅ 1. NAV-HPPOSLLH sovrascrive TPV in modo fragile
**File:** `modules/ubx_parser.py:148-155`
**Problema:** Dopo SET di HPPOSLLH, il codice fa snapshot di TPV, aggiorna la copia locale e sovrascrive TPV — sovrascrivendo potenzialmente campi di NAV-PVT con valori obsoleti, e aggiornando `time` con now_iso() anche se i dati HPPOSLLH sono del ciclo precedente.
**Fix concordato:** Sostituire pattern snapshot→update→set con `STATE.patch()` atomico:
```python
# PRIMA
tpv = STATE.snapshot().get("TPV", {})
tpv.update({"lat": lat, "lon": lon, "altHAE": h, ...})
STATE.set("TPV", tpv)

# DOPO
STATE.patch("TPV", lat=lat, lon=lon, altHAE=h, altMSL=hmsl,
            hAcc=hAcc, vAcc=vAcc, time=now_iso())
```
Tocca solo i campi HPPOSLLH, non sovrascrive `rtk`, `numSV`, `flags`, `rtcmAge`, ecc.

### ✅ 4. `/api/all_points` senza cache (e lista punti COGO)
**File:** `routes/dashboard.py:50-62`, `routes/cogo.py:38`
**Problema:** Ogni richiesta legge tutti i file GeoJSON di tutti i rilievi dal disco. Uguale per i `<select>` nelle pagine COGO (lista tutti i punti di tutti i rilievi ad ogni apertura pagina).
**Fix concordato:** Cache in-memory invalidata sulle scritture (non TTL).
- Variabile `_all_points_cache = None` in modulo survey o dashboard
- `save_survey()` e funzioni di eliminazione punti chiamano `invalidate_all_points_cache()`
- `/api/all_points` e `list_all_points_options()` usano la stessa cache
- **Vantaggio su Termux:** cache sempre calda, nessun I/O finché non cambia nulla

### ✅ 5. Doppio HTML punti: card mobile + tabella desktop
**File:** `templates/rtk_survey_view.html:69-101`
**Problema:** Server renderizza sempre entrambe le viste. CSS nasconde una delle due. Con 200 punti = 200 card + 200 righe × 19 colonne nel DOM. Pesante su Termux.
**Fix concordato: Opzione A** — tenere solo le card, fare CSS responsive per desktop.
- Eliminare `<div class="table-wrap">` e la tabella completa dal template
- Le card rimangono, CSS le dispone in griglia 2-3 colonne su desktop
- La vista dettagliata con 19 colonne è sostituita dall'export (.xlsx, .geojson)
- **Campi card desktop:** tutti i valori parsati dai messaggi UBX-NAV, organizzati per sezione:
  - **Posizione:** Lat, Lon, altHAE, altMSL
  - **Qualità:** hAcc, vAcc, pAcc, rtk, mode, numSV, rtcmAge, diffSoln
  - **DOP:** PDOP, HDOP, VDOP, GDOP, NDOP, EDOP, TDOP
  - **Sigma (da COV):** σN, σE, σD
  - **RELPOS (vs base):** N, E, D, baseline, bearing, slope
  - **ECEF:** X, Y, Z
  - **Campionamento:** n_samples, duration, interval, IMU tilt max
  - **Meta:** codice, descrizione, timestamp start/end
- Card mobile: rimane compatta come ora (Lat, Lon, HAE, PDOP, σN, σE)
- Implementazione: CSS media query, le sezioni extra sono visibili solo su schermo largo

---

## Priorità BASSA / Miglioramenti UX

### 6. `alert()` come feedback UI
**File:** `templates/rtk_survey_view.html:214-215` e altri
**Problema:** `alert()` blocca il thread JS, fastidioso su mobile Android.
**Fix:** Toast/snackbar non bloccante (div temporaneo con setTimeout auto-hide).
**Nota:** Da fare in una chat dedicata quando si tocca la view.

### ✅ 7. Limite durata registrazione audio note vocali
**File:** `templates/rtk_survey_view.html` (MediaRecorder JS), `routes/surveys.py` (upload)
**Problema:** Nessun limite di durata/dimensione file audio. Una registrazione accidentale lunga può esaurire spazio su sdcard.
**Fix:**
- Stop automatico `mediaRecorder.stop()` dopo **60 secondi** (client-side, con `setTimeout`)
- Mostrare countdown nell'UI quando mancano <10s
- Check server-side su Content-Length prima di salvare (reject se > ~3MB, compatibile con 60s webm)

### 8. ~~SSE heartbeat non adattivo~~ — NON FARE
Il 500ms è corretto e funziona perfettamente. La dashboard mostra i dati UBX-NAV aggiornati in real-time senza problemi. Non cambiare.

### 9. COGO (da trattare in chat dedicata)
- Codice intersecazione duplicato: `routes/cogo.py:309-326` vs `modules/cogo.py:179-196`
- Select con tutti i punti da tutti i rilievi lento su molti punti (collegato al fix #4 cache)
- Altri aspetti UX/calcolo COGO

---

## Note architetturali ricorrenti

- **RilievoPY è monoutente.** Soluzioni semplici (thread singolo, cache in-memory, SSE) sono preferibili. Non over-engineer per multi-client.
- **Platform principale: Termux su Android** (con termux:API per notifiche). Secondario: RPi, PC/Windows.
- **RAM limitata su Termux:** evitare strutture dati che crescono senza limite (cache senza invalidazione, DOM pesante).
- **Connettività campo:** la perdita di connessione col ricevitore è evento normale, l'UI deve gestirla con grazia.
