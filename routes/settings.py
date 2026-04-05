"""
Settings page: configure GNSS connection, relay, survey defaults via web UI.
"""

import io
import json
import os
import zipfile
from datetime import datetime

from flask import Blueprint, make_response, render_template, request, jsonify, send_file

from modules import settings as cfg
from modules.connection import CONN
import modules.workspace as workspace

bp = Blueprint('settings', __name__)


def _app_version() -> str:
    """Return git commit hash or 'unknown'."""
    try:
        import subprocess
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        result = subprocess.run(
            ["git", "-C", base, "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=3
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return "unknown"


@bp.route("/settings")
def settings_page():
    s = cfg.load_settings()
    ws = workspace.get_workspace()
    return render_template('rtk_settings.html',
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
        sel_median="selected" if s.get("robust_mode") == "median" else "",
        workspace_dir=ws,
        workspace_default=workspace.default_workspace())


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
        from modules.settings import RTKINO_TCP_PORT
        rtkino_host = saved.get("rtkino_host", "")
        if rtkino_host:
            CONN.restart(
                gnss_host=rtkino_host,
                gnss_port=RTKINO_TCP_PORT,
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
    from modules.settings import RTKINO_TCP_PORT
    rtkino_host = s.get("rtkino_host", "")
    return {
        "gnss_host": rtkino_host,
        "gnss_port": RTKINO_TCP_PORT if rtkino_host else 0,
        "gnss_connected": conn_status["gnss_connected"],
        "relay_active": conn_status["relay_active"],
        "rtkino_host": rtkino_host,
        "rtkino_tcp_port": RTKINO_TCP_PORT,
    }


# ─── Workspace API ────────────────────────────────────────────────────────────

@bp.route("/api/workspace/info")
def api_workspace_info():
    ws = workspace.get_workspace()
    has_data = workspace.workspace_has_data(ws)
    return jsonify({
        "workspace_dir": ws,
        "default_workspace": workspace.default_workspace(),
        "surveys_count": has_data["surveys"],
        "ppk_conf_count": has_data["ppk_conf"],
        "surveys_dir": workspace.surveys_dir(ws),
        "ppk_conf_dir": workspace.ppk_conf_dir(ws),
    })


@bp.route("/api/workspace/init", methods=["POST"])
def api_workspace_init():
    """Create workspace subdirectories under the current (or specified) workspace."""
    data = request.get_json() or {}
    ws = data.get("workspace_dir") or workspace.get_workspace()
    ws = os.path.expanduser(str(ws))
    try:
        workspace.init_workspace(ws)
        return jsonify({"ok": True, "workspace_dir": ws})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.route("/api/workspace/set", methods=["POST"])
def api_workspace_set():
    """Persist a new workspace_dir (does not move data)."""
    data = request.get_json() or {}
    new_ws = (data.get("workspace_dir") or "").strip()
    if not new_ws:
        return jsonify({"ok": False, "error": "workspace_dir is required"}), 400
    new_ws = os.path.expanduser(new_ws)
    cfg.update({"workspace_dir": new_ws})
    return jsonify({"ok": True, "workspace_dir": new_ws})


@bp.route("/api/workspace/check_destination", methods=["POST"])
def api_workspace_check_destination():
    """Return how many files are already in the target workspace."""
    data = request.get_json() or {}
    target = (data.get("workspace_dir") or "").strip()
    if not target:
        return jsonify({"ok": False, "error": "workspace_dir is required"}), 400
    target = os.path.expanduser(target)
    has_data = workspace.workspace_has_data(target)
    return jsonify({
        "ok": True,
        "surveys_count": has_data["surveys"],
        "ppk_conf_count": has_data["ppk_conf"],
    })


@bp.route("/api/workspace/copy", methods=["POST"])
def api_workspace_copy():
    """
    Copy surveys/ and ppk/conf/ from old workspace to new workspace.
    Optionally delete old data after successful copy (delete_old=true).
    Optionally update the active workspace_dir to the new one (set_active=true).
    """
    data = request.get_json() or {}
    src = (data.get("src_workspace") or workspace.get_workspace()).strip()
    dst = (data.get("dst_workspace") or "").strip()
    delete_old = bool(data.get("delete_old", False))
    set_active = bool(data.get("set_active", True))

    if not dst:
        return jsonify({"ok": False, "error": "dst_workspace is required"}), 400

    src = os.path.expanduser(src)
    dst = os.path.expanduser(dst)

    try:
        result = workspace.copy_data_to_workspace(src, dst)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

    deleted = None
    if delete_old:
        try:
            deleted = workspace.delete_workspace_data(src)
        except Exception as e:
            return jsonify({
                "ok": True,
                "copied": result,
                "delete_error": str(e),
            })

    if set_active:
        cfg.update({"workspace_dir": dst})

    return jsonify({
        "ok": True,
        "copied": result,
        "deleted": deleted,
        "workspace_dir": dst if set_active else src,
    })


# ─── Backup / Restore ────────────────────────────────────────────────────────

@bp.route("/api/workspace/backup")
def api_workspace_backup():
    """Stream a ZIP file with surveys/ + ppk/conf/ + manifest.json."""
    ws = workspace.get_workspace()
    surveys = workspace.surveys_dir(ws)
    conf = workspace.ppk_conf_dir(ws)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # manifest
        manifest = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "version": _app_version(),
            "workspace_dir": ws,
        }
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))

        # surveys/
        if os.path.isdir(surveys):
            for root, dirs, files in os.walk(surveys):
                for fname in files:
                    abs_path = os.path.join(root, fname)
                    rel_path = os.path.relpath(abs_path, ws)
                    zf.write(abs_path, rel_path)

        # ppk/conf/
        if os.path.isdir(conf):
            for fname in os.listdir(conf):
                abs_path = os.path.join(conf, fname)
                if os.path.isfile(abs_path):
                    rel_path = os.path.join("ppk", "conf", fname)
                    zf.write(abs_path, rel_path)

    buf.seek(0)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return send_file(
        buf,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"rilievo_gnss_backup_{ts}.zip",
    )


@bp.route("/api/workspace/restore", methods=["POST"])
def api_workspace_restore():
    """
    Restore a backup ZIP into the current workspace.
    Existing files are NOT overwritten unless overwrite=true is passed as a query param.
    """
    if "file" not in request.files:
        return jsonify({"ok": False, "error": "No file uploaded"}), 400

    f = request.files["file"]
    if not f.filename.lower().endswith(".zip"):
        return jsonify({"ok": False, "error": "File must be a .zip"}), 400

    overwrite = request.args.get("overwrite", "false").lower() in ("1", "true", "yes")
    ws = workspace.get_workspace()
    workspace.init_workspace(ws)

    restored = {"surveys": 0, "ppk_conf": 0, "skipped": 0}
    try:
        with zipfile.ZipFile(f.stream, "r") as zf:
            for member in zf.infolist():
                name = member.filename
                if name == "manifest.json" or name.startswith("__MACOSX"):
                    continue
                dest = os.path.join(ws, name)
                if not overwrite and os.path.exists(dest):
                    restored["skipped"] += 1
                    continue
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with zf.open(member) as src, open(dest, "wb") as dst:
                    dst.write(src.read())
                if name.startswith("surveys/"):
                    restored["surveys"] += 1
                elif name.startswith("ppk/conf/"):
                    restored["ppk_conf"] += 1
    except zipfile.BadZipFile:
        return jsonify({"ok": False, "error": "Invalid ZIP file"}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

    return jsonify({"ok": True, "restored": restored})


# ─── mDNS API ─────────────────────────────────────────────────────────────────

@bp.route("/api/mdns/status")
def api_mdns_status():
    """Stato attuale mDNS."""
    from modules import mdns_service
    current = mdns_service.get_current_hostname()
    s = cfg.load_settings()
    return jsonify({
        "hostname": s.get("mdns_hostname", "rilievopy"),
        "active": current is not None,
        "url": f"http://{current}.local/" if current else None,
    })


@bp.route("/api/mdns/save", methods=["POST"])
def api_mdns_save():
    """Salva nuovo hostname mDNS e riavvia il servizio."""
    from modules import mdns_service

    data = request.get_json() or {}
    new_hostname = mdns_service.normalize_hostname(data.get("hostname", ""))

    if not mdns_service.is_valid_hostname(new_hostname):
        return jsonify({
            "ok": False,
            "error": "Hostname non valido (usa solo a-z, 0-9, trattino)",
        }), 400

    # Salva nei settings
    cfg.update({"mdns_hostname": new_hostname})

    # Riavvia mDNS con il nuovo hostname
    s = cfg.load_settings()
    http_port = s.get("http_port", 8000)
    success = mdns_service.start_mdns(new_hostname, http_port)

    return jsonify({
        "ok": success,
        "hostname": new_hostname,
        "url": f"http://{new_hostname}.local/" if success else None,
    })
