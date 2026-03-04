"""
Dashboard and SSE streaming routes.
"""

import json
import time

from flask import Blueprint, Response, render_template, jsonify

from modules.state import STATE
from modules.survey import list_survey_ids, load_survey

bp = Blueprint('dashboard', __name__)


@bp.route("/")
def index():
    return render_template('rtk_dashboard.html')


@bp.route("/events")
def sse():
    def gen():
        while True:
            snap = STATE.snapshot()
            try:
                yield "data: " + json.dumps(snap) + "\n\n"
            except GeneratorExit:
                # Client disconnected (browser navigated away, tab closed, etc.)
                return
            time.sleep(0.5)

    resp = Response(gen(), mimetype="text/event-stream")
    resp.headers['Cache-Control']     = 'no-cache'
    resp.headers['X-Accel-Buffering'] = 'no'
    return resp


@bp.route("/api/all_points")
def all_points():
    """Return all survey points as a GeoJSON FeatureCollection for the dashboard mini-map."""
    features = []
    for sid in list_survey_ids():
        try:
            svy = load_survey(sid)
            survey_title = svy.get("properties", {}).get("title", sid)
            for f in svy.get("features", []):
                feat = dict(f)
                props = dict(feat.get("properties", {}))
                props["_survey_id"] = sid
                props["_survey_title"] = survey_title
                feat["properties"] = props
                features.append(feat)
        except Exception:
            pass
    return jsonify({"type": "FeatureCollection", "features": features})


