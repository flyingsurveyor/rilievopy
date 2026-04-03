"""
routes/rtkino.py
Blueprint Flask per l'integrazione RTKino.

Pagine:
  /rtkino            — Pagina RTKino (IP, stato, comandi rapidi)
  /rtkino/surveys    — Gestione e import rilievi

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
"""

import logging
import uuid
from datetime import datetime

from flask import Blueprint, jsonify, render_template, request

from modules import settings as cfg
from modules.rtkino_manager import RTKINO

logger = logging.getLogger(__name__)

bp = Blueprint("rtkino", __name__)


# ── Pagine HTML ───────────────────────────────────────────────────────────────

@bp.route("/rtkino")
def rtkino_page():
    """Pagina RTKino: IP, stato connessione, comandi rapidi."""
    from modules.settings import RTKINO_TCP_PORT
    s = cfg.load_settings()
    return render_template(
        "rtkino_dashboard.html",
        rtkino_host=s.get("rtkino_host", ""),
        rtkino_tcp_port=RTKINO_TCP_PORT,
    )


@bp.route("/rtkino/surveys")
def rtkino_surveys():
    from modules.settings import RTKINO_WEBUI_PORT
    s = cfg.load_settings()
    return render_template(
        "rtkino_surveys.html",
        rtkino_host=s.get("rtkino_host", ""),
        rtkino_port=RTKINO_WEBUI_PORT,
    )

# ── API: stato combinato ──────────────────────────────────────────────────────

@bp.route("/api/rtkino/status")
def api_rtkino_status():
    """Stato combinato: connessione e configurazione RTKino."""
    return jsonify(RTKINO.combined_status())


# ── API: salva impostazioni RTKino ────────────────────────────────────────────

@bp.route("/api/rtkino/settings", methods=["POST"])
def api_rtkino_settings():
    """Salva le impostazioni RTKino (solo host IP).

    Body JSON: {"action": "save" | "save_and_connect", "settings": {"rtkino_host": ...}}
    """
    data = request.get_json() or {}
    s_in = data.get("settings", {})

    # RTKino host (IP only — ports are fixed: :80 WebUI, :7856 TCP Streamer)
    rtkino_host = (s_in.get("rtkino_host") or "").strip()
    saved = cfg.update({"rtkino_host": rtkino_host})

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
