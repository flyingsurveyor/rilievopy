"""
Dashboard and SSE streaming routes.
"""

import json
import time

from flask import Blueprint, Response, render_template, jsonify

from modules.state import STATE
from modules.survey import get_all_points_features

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
                try:
                    from modules.alert_monitor import ALERTS
                    pending = ALERTS.pop_pending_audio()
                    if pending:
                        snap["alerts"] = pending
                except Exception:
                    pass
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
    return jsonify({"type": "FeatureCollection", "features": get_all_points_features()})


