"""
Settings page: configure GNSS connection, relay, survey defaults via web UI.
"""

import json

from flask import Blueprint, make_response, render_template, request

from modules import settings as cfg
from modules.connection import CONN

bp = Blueprint('settings', __name__)




@bp.route("/settings")
def settings_page():
    s = cfg.load_settings()
    return render_template('rtk_settings.html',
        gnss_host=s.get("gnss_host", ""),
        gnss_port=str(s.get("gnss_port", 1234)),
        gnss_autoconnect_checked="checked" if s.get("gnss_autoconnect", True) else "",
        retry_interval=str(s.get("retry_interval", 3.0)),
        relay_enabled_checked="checked" if s.get("relay_enabled", True) else "",
        relay_bind=s.get("relay_bind", "127.0.0.1"),
        relay_port=str(s.get("relay_port", 21100)),
        default_sample_duration=str(s.get("default_sample_duration", 10.0)),
        default_sample_interval=str(s.get("default_sample_interval", 0.5)),
        robust_sigma=str(s.get("robust_sigma", 2.0)),
        http_bind=s.get("http_bind", "0.0.0.0"),
        http_port=str(s.get("http_port", 8000)),
        sel_sigma="selected" if s.get("robust_mode") == "sigma" else "",
        sel_trim="selected" if s.get("robust_mode") == "trim" else "",
        sel_median="selected" if s.get("robust_mode") == "median" else "")


@bp.route("/api/settings", methods=["POST"])
def api_settings():
    data = request.get_json()
    if not data:
        return {"ok": False, "error": "No data"}, 400

    action = data.get("action", "save")

    if action == "reset":
        cfg.reset_to_defaults()
        return {"ok": True}

    new_settings = data.get("settings", {})
    if not new_settings:
        return {"ok": False, "error": "No settings"}, 400

    # Validate
    port = cfg.validate_port(new_settings.get("gnss_port", 1234))
    if port is None:
        return {"ok": False, "error": "Porta GNSS non valida"}, 400
    new_settings["gnss_port"] = port

    rport = cfg.validate_port(new_settings.get("relay_port", 21100))
    if rport is None:
        return {"ok": False, "error": "Porta relay non valida"}, 400
    new_settings["relay_port"] = rport

    hport = cfg.validate_port(new_settings.get("http_port", 8000))
    if hport is None:
        return {"ok": False, "error": "Porta HTTP non valida"}, 400
    new_settings["http_port"] = hport

    # Save
    saved = cfg.update(new_settings)

    # Apply robust mode to utils module
    from modules import utils
    utils.ROBUST_MODE = saved.get("robust_mode", "sigma")
    utils.ROBUST_SIGMA = saved.get("robust_sigma", 2.0)
    utils.ROBUST_TRIM_Q = saved.get("robust_trim_q", 0.10)

    if action == "save_and_connect":
        CONN.restart(
            gnss_host=saved.get("gnss_host", ""),
            gnss_port=saved.get("gnss_port", 1234),
            relay_enabled=saved.get("relay_enabled", False),
            relay_bind=saved.get("relay_bind", "127.0.0.1"),
            relay_port=saved.get("relay_port", 21100),
            retry=saved.get("retry_interval", 3.0),
        )

    return {"ok": True}


@bp.route("/api/settings/status")
def api_settings_status():
    s = cfg.load_settings()
    conn_status = CONN.status()
    return {
        "gnss_host": s.get("gnss_host", ""),
        "gnss_port": s.get("gnss_port", 0),
        "gnss_connected": conn_status["gnss_connected"],
        "relay_active": conn_status["relay_active"],
    }
