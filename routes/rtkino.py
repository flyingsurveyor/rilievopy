"""
routes/rtkino.py
Blueprint Flask per l'integrazione RTKino.

Pagine:
  /rtkino            — Pagina unificata RTKino (IP, BLE, polling, NTRIP, status, comandi)
  /rtkino/surveys    — Gestione e import rilievi
  /rtkino/ntrip      — Redirect a /rtkino#ntrip (mantenuto per compatibilità)

API:
  GET  /api/rtkino/status
  POST /api/rtkino/settings
  POST /api/rtkino/connect
  POST /api/rtkino/command
  GET  /api/rtkino/surveys
  POST /api/rtkino/survey/import
  GET  /api/rtkino/survey/<sid>/points
  POST /api/rtkino/measure
  GET  /api/rtkino/measure/status
  POST /api/rtkino/ntrip/start
  POST /api/rtkino/ntrip/stop
  GET  /api/rtkino/ntrip/status
"""

import logging
import uuid
from datetime import datetime

from flask import Blueprint, jsonify, redirect, render_template, request, url_for

from modules import settings as cfg
from modules.rtkino_manager import RTKINO

logger = logging.getLogger(__name__)

bp = Blueprint("rtkino", __name__)


# ── Pagine HTML ───────────────────────────────────────────────────────────────

@bp.route("/rtkino")
def rtkino_page():
    """Pagina unificata RTKino: IP, BLE, polling, NTRIP, status, comandi rapidi."""
    from modules.settings import RTKINO_TCP_PORT
    s = cfg.load_settings()
    return render_template(
        "rtkino_dashboard.html",
        rtkino_host=s.get("rtkino_host", ""),
        rtkino_tcp_port=RTKINO_TCP_PORT,
        rtkino_polling=s.get("rtkino_polling", False),
        rtkino_poll_interval=s.get("rtkino_poll_interval", 2.0),
        ble_enabled=s.get("ble_enabled", False),
        ble_device_name=s.get("ble_device_name", "RTKino"),
        ble_passkey=s.get("ble_passkey", 123456),
        ble_autoconnect=s.get("ble_autoconnect", False),
        ble_tts=s.get("ble_tts", True),
        ble_tts_lang=s.get("ble_tts_lang", "it"),
        ntrip_host=s.get("rtkino_ntrip_host", ""),
        ntrip_port=s.get("rtkino_ntrip_port", 2101),
        ntrip_mountpoint=s.get("rtkino_ntrip_mountpoint", ""),
        ntrip_user=s.get("rtkino_ntrip_user", ""),
        ntrip_gga_interval=s.get("rtkino_ntrip_gga_interval", 5),
    )


@bp.route("/rtkino/surveys")
def rtkino_surveys():
    s = cfg.load_settings()
    return render_template(
        "rtkino_surveys.html",
        rtkino_host=s.get("rtkino_host", ""),
        rtkino_port=s.get("rtkino_port", 80),
    )


@bp.route("/rtkino/ntrip")
def rtkino_ntrip():
    """Redirect alla pagina unificata RTKino (sezione NTRIP)."""
    return redirect(url_for("rtkino.rtkino_page") + "#ntrip")


# ── API: stato combinato ──────────────────────────────────────────────────────

@bp.route("/api/rtkino/status")
def api_rtkino_status():
    """Stato combinato: connessione, status RTKino, posizione, NTRIP."""
    return jsonify(RTKINO.combined_status())


# ── API: salva impostazioni RTKino ────────────────────────────────────────────

@bp.route("/api/rtkino/settings", methods=["POST"])
def api_rtkino_settings():
    """Salva le impostazioni RTKino (host, BLE, polling).

    Body JSON: {"action": "save" | "save_and_connect", "settings": {...}}
    """
    data = request.get_json() or {}
    s_in = data.get("settings", {})

    changes: dict = {}

    # RTKino host (IP only — ports are fixed: :80 WebUI, :7856 TCP Streamer)
    rtkino_host = (s_in.get("rtkino_host") or "").strip()
    changes["rtkino_host"] = rtkino_host

    # HTTP polling
    changes["rtkino_polling"] = bool(s_in.get("rtkino_polling", False))
    try:
        interval = float(s_in.get("rtkino_poll_interval") or 2.0)
        changes["rtkino_poll_interval"] = max(1.0, min(30.0, interval))
    except (ValueError, TypeError):
        changes["rtkino_poll_interval"] = 2.0

    # BLE settings
    changes["ble_enabled"] = bool(s_in.get("ble_enabled", False))
    changes["ble_device_name"] = (s_in.get("ble_device_name") or "RTKino").strip()
    try:
        changes["ble_passkey"] = int(s_in.get("ble_passkey", 123456))
    except (ValueError, TypeError):
        changes["ble_passkey"] = 123456
    changes["ble_autoconnect"] = bool(s_in.get("ble_autoconnect", False))
    changes["ble_tts"] = bool(s_in.get("ble_tts", True))
    changes["ble_tts_lang"] = (s_in.get("ble_tts_lang") or "it").strip()

    saved = cfg.update(changes)

    action = data.get("action", "save")
    if action == "save_and_connect" and rtkino_host:
        from modules.connection import CONN
        from modules.settings import RTKINO_TCP_PORT
        CONN.restart(
            gnss_host=rtkino_host,
            gnss_port=RTKINO_TCP_PORT,
            relay_enabled=saved.get("relay_enabled", False),
            relay_bind=saved.get("relay_bind", "127.0.0.1"),
            relay_port=saved.get("relay_port", 21100),
            retry=saved.get("retry_interval", 3.0),
        )
        if changes.get("rtkino_polling"):
            RTKINO.stop_polling()
            RTKINO.start_polling()

    return jsonify({"ok": True})


# ── API: comandi rapidi ───────────────────────────────────────────────────────

@bp.route("/api/rtkino/command", methods=["POST"])
def api_rtkino_command():
    """Esegui un comando su RTKino via HTTP API.

    Body JSON: {"cmd": "ntrip_on" | "ntrip_off" | "log_start" | "log_stop" |
                        "switch_rover" | "zed_reset_hot" | "zed_reset_cold"}
    """
    data = request.get_json() or {}
    cmd = data.get("cmd", "")

    api = RTKINO.get_api()
    if not api:
        return jsonify({"ok": False, "error": "RTKino non configurato"}), 400

    result = None
    if cmd == "ntrip_on":
        result = api.ntrip_toggle(True)
    elif cmd == "ntrip_off":
        result = api.ntrip_toggle(False)
    elif cmd == "log_start":
        result = api.logging_start()
    elif cmd == "log_stop":
        result = api.logging_stop()
    elif cmd == "switch_rover":
        result = api.switch_to_rover()
    elif cmd == "zed_reset_hot":
        result = api.zed_reset("hot")
    elif cmd == "zed_reset_cold":
        result = api.zed_reset("cold")
    else:
        return jsonify({"ok": False, "error": f"Comando sconosciuto: {cmd}"}), 400

    if result is None:
        return jsonify({"ok": False, "error": "Nessuna risposta da RTKino"}), 502
    return jsonify({"ok": True, "result": result})


# ── API: rilievi RTKino ───────────────────────────────────────────────────────

@bp.route("/api/rtkino/surveys")
def api_rtkino_surveys():
    """Lista rilievi presenti su RTKino."""
    api = RTKINO.get_api()
    if not api:
        return jsonify({"ok": False, "error": "RTKino non configurato"}), 400
    data = api.pts_list()
    if data is None:
        return jsonify({"ok": False, "error": "Impossibile contattare RTKino"}), 502
    return jsonify({"ok": True, "surveys": data})


@bp.route("/api/rtkino/survey/import", methods=["POST"])
def api_rtkino_survey_import():
    """Importa un rilievo da RTKino salvandolo in locale (workspace surveys/)."""
    data = request.get_json() or {}
    sid = data.get("sid")
    if not sid:
        return jsonify({"ok": False, "error": "sid richiesto"}), 400

    api = RTKINO.get_api()
    if not api:
        return jsonify({"ok": False, "error": "RTKino non configurato"}), 400

    geojson = api.pts_download(sid)
    if geojson is None:
        return jsonify({"ok": False, "error": "Download fallito"}), 502

    # Salva come nuovo rilievo locale
    try:
        import json
        import os
        import modules.workspace as workspace

        ws = workspace.get_workspace()
        surveys_dir = workspace.surveys_dir(ws)
        os.makedirs(surveys_dir, exist_ok=True)

        # Genera un nuovo ID locale per evitare conflitti
        local_id = str(uuid.uuid4())[:8]
        if isinstance(geojson, dict):
            props = geojson.get("properties") or {}
            original_title = props.get("title") or props.get("name") or sid
            # Marca la provenienza
            geojson.setdefault("properties", {})
            geojson["properties"]["imported_from"] = "RTKino"
            geojson["properties"]["original_sid"] = sid
            geojson["properties"]["import_date"] = datetime.utcnow().isoformat() + "Z"
            geojson["properties"]["id"] = local_id
        else:
            original_title = sid

        filename = os.path.join(surveys_dir, f"{local_id}.geojson")
        with open(filename, "w", encoding="utf-8") as fh:
            json.dump(geojson, fh, ensure_ascii=False, indent=2)

        return jsonify({"ok": True, "local_id": local_id, "title": original_title, "file": filename})
    except Exception:
        logger.exception("[rtkino] import survey error")
        return jsonify({"ok": False, "error": "Errore interno durante l'importazione"}), 500


@bp.route("/api/rtkino/survey/<sid>/points")
def api_rtkino_survey_points(sid: str):
    """Punti GeoJSON di un rilievo RTKino."""
    api = RTKINO.get_api()
    if not api:
        return jsonify({"ok": False, "error": "RTKino non configurato"}), 400
    data = api.pts_points(sid)
    if data is None:
        return jsonify({"ok": False, "error": "Impossibile contattare RTKino"}), 502
    return jsonify({"ok": True, "geojson": data})


# ── API: misurazioni punto ────────────────────────────────────────────────────

@bp.route("/api/rtkino/measure", methods=["POST"])
def api_rtkino_measure():
    """Avvia una misurazione punto su RTKino."""
    data = request.get_json() or {}
    name = data.get("name", "")
    codice = data.get("codice", "")
    duration = int(data.get("duration", 10))
    interval = int(data.get("interval", 1))

    if not name:
        return jsonify({"ok": False, "error": "name richiesto"}), 400

    api = RTKINO.get_api()
    if not api:
        return jsonify({"ok": False, "error": "RTKino non configurato"}), 400

    result = api.pts_measure(name=name, codice=codice, duration=duration, interval=interval)
    if result is None:
        return jsonify({"ok": False, "error": "Impossibile contattare RTKino"}), 502
    return jsonify({"ok": True, "result": result})


@bp.route("/api/rtkino/measure/status")
def api_rtkino_measure_status():
    """Stato/progresso della misurazione corrente su RTKino."""
    api = RTKINO.get_api()
    if not api:
        return jsonify({"ok": False, "error": "RTKino non configurato"}), 400
    data = api.pts_measure_status()
    if data is None:
        return jsonify({"ok": False, "error": "Impossibile contattare RTKino"}), 502
    return jsonify({"ok": True, "status": data})


# ── API: NTRIP client locale (per BLE-only) ───────────────────────────────────

@bp.route("/api/rtkino/ntrip/start", methods=["POST"])
def api_ntrip_start():
    """Avvia il client NTRIP locale e salva le impostazioni."""
    data = request.get_json() or {}
    host = (data.get("host") or "").strip()
    mountpoint = (data.get("mountpoint") or "").strip()

    if not host or not mountpoint:
        return jsonify({"ok": False, "error": "host e mountpoint obbligatori"}), 400

    port = int(data.get("port") or 2101)
    user = data.get("user", "")
    password = data.get("password", "")
    gga_interval = float(data.get("gga_interval") or 5.0)

    # Salva nelle settings (senza password in chiaro se vuoi, ma qui la salviamo)
    cfg.update({
        "rtkino_ntrip_host": host,
        "rtkino_ntrip_port": port,
        "rtkino_ntrip_mountpoint": mountpoint,
        "rtkino_ntrip_user": user,
        "rtkino_ntrip_password": password,
        "rtkino_ntrip_gga_interval": gga_interval,
    })

    try:
        RTKINO.start_ntrip(
            host=host, port=port, mountpoint=mountpoint,
            user=user, password=password, gga_interval=gga_interval,
        )
        return jsonify({"ok": True})
    except Exception:
        logger.exception("[rtkino] ntrip start error")
        return jsonify({"ok": False, "error": "Errore durante l'avvio del client NTRIP"}), 500


@bp.route("/api/rtkino/ntrip/stop", methods=["POST"])
def api_ntrip_stop():
    """Ferma il client NTRIP locale."""
    RTKINO.stop_ntrip()
    return jsonify({"ok": True})


@bp.route("/api/rtkino/ntrip/status")
def api_ntrip_status():
    """Stato del client NTRIP locale."""
    return jsonify(RTKINO.ntrip_status())
