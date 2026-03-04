"""
Import points from external files (CSV, GeoJSON, TXT).
"""

import json

from flask import Blueprint, render_template, make_response, request

import modules.state as state_mod
from modules.survey import (
    list_survey_ids, load_survey, save_survey, create_survey,
    next_point_id, point_feature
)

bp = Blueprint('import_points', __name__)


@bp.route("/import", methods=["GET", "POST"])
def import_points():
    if request.method == "GET":
        return render_template('rtk_import.html')

    file = request.files.get("file")
    dest = request.form.get("dest", "stakeout")

    if not file:
        return make_response("Nessun file caricato", 400)

    content = file.read().decode("utf-8", errors="ignore")
    lines = [l.strip() for l in content.split("\n") if l.strip() and not l.strip().startswith("#")]

    points = []
    errors = []

    for i, line in enumerate(lines, 1):
        if "," in line or ";" in line:
            sep = "," if "," in line else ";"
            parts = [p.strip() for p in line.split(sep)]
        else:
            parts = line.split()

        if len(parts) < 4:
            errors.append(f"Riga {i}: formato non valido (serve: nome,lat,lon,quota)")
            continue

        try:
            name = parts[0]
            lat = float(parts[1])
            lon = float(parts[2])
            alt = float(parts[3])

            if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
                errors.append(f"Riga {i}: coordinate fuori range")
                continue

            points.append({"name": name, "lat": lat, "lon": lon, "alt": alt})
        except ValueError as e:
            errors.append(f"Riga {i}: errore parsing - {e}")

    return render_template('rtk_import_preview.html',
                           points=points, errors=errors, dest=dest,
                           points_json=json.dumps(points))


@bp.route("/import/confirm", methods=["POST"])
def import_confirm():
    dest = request.form.get("dest", "stakeout")
    points_json = request.form.get("points", "[]")

    try:
        points = json.loads(points_json)
    except:
        return make_response("Errore parsing dati", 400)

    if dest == "stakeout":
        with state_mod.STAKEOUT_LOCK:
            state_mod.STAKEOUT_TARGETS.extend(points)

        return render_template('rtk_import_done.html',
                               count=len(points), dest='stakeout')

    return make_response("Destinazione non supportata", 400)
