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

  USB OTG (ZED-F9P direct via termux-usb + libusb):
  GET  /api/usb/devices     — list available USB devices
  POST /api/usb/connect     — connect to USB OTG device
  POST /api/usb/disconnect  — disconnect USB OTG
  GET  /api/usb/status      — USB OTG connection status
  POST /api/usb/permission  — request Android USB permission
  POST /api/usb/compile     — compile the C helper binary
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


# ── API: USB OTG (ZED-F9P direct) ────────────────────────────────────────────

@bp.route("/api/usb/devices")
def api_usb_devices():
    """List available USB devices via termux-usb -l."""
    from modules.usb_otg import is_usb_otg_available, list_usb_devices
    if not is_usb_otg_available():
        return jsonify({"ok": False, "error": "termux-usb non disponibile (solo Android/Termux)",
                        "available": False, "devices": []})
    devices = list_usb_devices()
    if devices is None:
        return jsonify({"ok": False, "error": "Impossibile ottenere la lista dei device USB",
                        "available": True, "devices": []})
    return jsonify({"ok": True, "available": True, "devices": devices})


@bp.route("/api/usb/status")
def api_usb_status():
    """USB OTG connection status."""
    from modules.connection import CONN
    from modules.usb_otg import is_usb_otg_available, usb_reader_compiled
    s = cfg.load_settings()
    conn_status = CONN.status()
    return jsonify({
        "ok": True,
        "otg_available": is_usb_otg_available(),
        "reader_compiled": usb_reader_compiled(),
        "gnss_source": s.get("gnss_source", "tcp"),
        "configured_device": s.get("usb_otg_device", ""),
        "connected": conn_status["gnss_connected"] and conn_status["source_type"] == "usb_otg",
        "active_device": conn_status.get("usb_device", ""),
    })


@bp.route("/api/usb/permission", methods=["POST"])
def api_usb_permission():
    """Request Android USB permission for a device (shows system dialog)."""
    from modules.usb_otg import is_usb_otg_available, request_usb_permission
    if not is_usb_otg_available():
        return jsonify({"ok": False, "error": "termux-usb non disponibile"}), 400
    data = request.get_json() or {}
    device = (data.get("device") or "").strip()
    if not device:
        return jsonify({"ok": False, "error": "device richiesto"}), 400
    ok = request_usb_permission(device)
    return jsonify({"ok": ok, "device": device})


@bp.route("/api/usb/compile", methods=["POST"])
def api_usb_compile():
    """Compile the C USB OTG reader helper (tools/usb_otg_reader.c)."""
    from modules.usb_otg import compile_usb_reader, usb_reader_compiled
    if usb_reader_compiled():
        return jsonify({"ok": True, "message": "Già compilato", "compiled": True})
    ok, message = compile_usb_reader()
    return jsonify({"ok": ok, "message": message, "compiled": ok})


@bp.route("/api/usb/connect", methods=["POST"])
def api_usb_connect():
    """Connect to a USB OTG device (ZED-F9P)."""
    from modules.connection import CONN
    from modules.usb_otg import is_usb_otg_available, usb_reader_compiled
    if not is_usb_otg_available():
        return jsonify({"ok": False, "error": "termux-usb non disponibile (solo Android/Termux)"}), 400

    data = request.get_json() or {}
    device = (data.get("device") or "").strip()
    if not device:
        return jsonify({"ok": False, "error": "device richiesto"}), 400

    if not usb_reader_compiled():
        from modules.usb_otg import compile_usb_reader as _compile
        ok, msg = _compile()
        if not ok:
            return jsonify({"ok": False, "error": f"Compilazione fallita: {msg}"}), 500

    # Save settings
    s = cfg.update({"gnss_source": "usb_otg", "usb_otg_device": device})

    # Start connection
    CONN.start_usb_otg(
        device=device,
        relay_enabled=s.get("relay_enabled", False),
        relay_bind=s.get("relay_bind", "127.0.0.1"),
        relay_port=s.get("relay_port", 21100),
        retry=s.get("retry_interval", 3.0),
    )
    return jsonify({"ok": True, "device": device})


@bp.route("/api/usb/disconnect", methods=["POST"])
def api_usb_disconnect():
    """Disconnect USB OTG and reset gnss_source to tcp."""
    from modules.connection import CONN
    CONN.stop()
    cfg.update({"gnss_source": "tcp"})
    return jsonify({"ok": True})
