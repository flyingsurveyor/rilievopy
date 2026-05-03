"""
routes/track.py
Endpoints Flask per il track recorder.
"""

import os
import re

from flask import Blueprint, jsonify, request, send_file, render_template, abort

from modules.track_recorder import TRACK_RECORDER, TRACKS_DIR

track_bp = Blueprint("track", __name__)

# Characters allowed in track names
_SAFE_NAME_RE = re.compile(r'^[\w\-]+$')


def _resolve_track_path(name, fmt):
    """
    Return a safe, resolved path for a track file within TRACKS_DIR.
    Returns None if the name is invalid or the path escapes TRACKS_DIR.
    """
    # Strip any directory components and restrict to safe characters
    safe_name = os.path.basename(name)
    if not safe_name or not _SAFE_NAME_RE.match(safe_name):
        return None
    # Build path using only the sanitized basename
    path = os.path.join(TRACKS_DIR, safe_name + "." + fmt)
    # Verify the resolved path stays within TRACKS_DIR (guards against symlinks)
    real_path = os.path.realpath(path)
    real_dir = os.path.realpath(TRACKS_DIR)
    if not (real_path == real_dir or real_path.startswith(real_dir + os.sep)):
        return None
    return path


@track_bp.route("/track")
def track_page():
    return render_template("rtk_track.html", active="track")


@track_bp.route("/track/start", methods=["POST"])
def track_start():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip() or None
    interval = float(data.get("interval", 1.0))
    min_fix = int(data.get("min_fix", 3))
    max_hacc_raw = data.get("max_hacc")
    max_hacc = float(max_hacc_raw) if max_hacc_raw not in (None, "") else None
    result = TRACK_RECORDER.start(
        name=name, interval=interval, min_fix=min_fix, max_hacc=max_hacc
    )
    return jsonify(result)


@track_bp.route("/track/stop", methods=["POST"])
def track_stop():
    return jsonify(TRACK_RECORDER.stop())


@track_bp.route("/track/status")
def track_status():
    return jsonify(TRACK_RECORDER.status())


@track_bp.route("/track/list")
def track_list():
    os.makedirs(TRACKS_DIR, exist_ok=True)
    files = sorted(os.listdir(TRACKS_DIR), reverse=True)
    tracks = {}
    for f in files:
        base, ext = os.path.splitext(f)
        ext = ext.lstrip(".")
        if ext not in ("gpx", "csv"):
            continue
        if base not in tracks:
            tracks[base] = {
                "name": base,
                "gpx": False,
                "csv": False,
                "size_bytes": 0,
                "mtime": 0,
            }
        path = os.path.join(TRACKS_DIR, f)
        stat = os.stat(path)
        tracks[base][ext] = True
        tracks[base]["size_bytes"] = max(tracks[base]["size_bytes"], stat.st_size)
        tracks[base]["mtime"] = max(tracks[base]["mtime"], stat.st_mtime)
    return jsonify({"tracks": list(tracks.values())})


@track_bp.route("/track/download/<name>/<fmt>")
def track_download(name, fmt):
    if fmt not in ("gpx", "csv"):
        abort(400, "format must be gpx or csv")
    path = _resolve_track_path(name, fmt)
    if path is None:
        abort(400, "invalid track name")
    if not os.path.isfile(path):
        abort(404)
    safe_name = os.path.basename(name)
    mime = "application/gpx+xml" if fmt == "gpx" else "text/csv"
    return send_file(path, mimetype=mime, as_attachment=True,
                     download_name=f"{safe_name}.{fmt}")


@track_bp.route("/track/delete/<name>", methods=["POST"])
def track_delete(name):
    deleted = []
    for fmt in ("gpx", "csv"):
        p = _resolve_track_path(name, fmt)
        if p is None:
            return jsonify({"ok": False, "error": "invalid track name"}), 400
        if os.path.isfile(p):
            os.remove(p)
            deleted.append(fmt)
    if not deleted:
        return jsonify({"ok": False, "error": "not found"}), 404
    return jsonify({"ok": True, "deleted": deleted})
