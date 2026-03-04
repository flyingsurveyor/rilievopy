"""
Stakeout (picchettamento) routes: set target, SSE stream with ENU deltas.
"""

import json
import math
import time

from flask import Blueprint, Response, render_template, make_response, request

import modules.state as state_mod
from modules.geodesy import geodetic_to_ecef, ecef_delta_to_enu
from modules.survey import list_all_points_options, load_survey, point_from_feature

bp = Blueprint('stakeout', __name__)


@bp.route("/stakeout", methods=["GET"])
def stakeout_page():
    opts = list_all_points_options()
    options = "\n".join([f'<option value="{v}">{l}</option>' for (l, v) in opts])
    return render_template('rtk_stakeout.html', options=options)


@bp.route("/stakeout/target", methods=["POST"])
def stakeout_set_target():
    data = request.get_json()
    if not data:
        return make_response({"error": "No data"}, 400)

    target_info = data.get("target")
    threshold = data.get("threshold", 0.05)
    if not target_info:
        return make_response({"error": "No target"}, 400)

    if "lat" in target_info and "lon" in target_info:
        target = {
            "name": target_info.get("name", "Manual"),
            "lat": float(target_info["lat"]),
            "lon": float(target_info["lon"]),
            "alt": float(target_info.get("alt", 0.0)),
            "threshold": threshold
        }
    else:
        sid = target_info.get("sid")
        pid = target_info.get("pid")
        if not sid or not pid:
            return make_response({"error": "Invalid target"}, 400)
        try:
            svy = load_survey(sid)
            feat = next((f for f in svy.get("features", []) if f.get("id") == pid), None)
            if not feat:
                return make_response({"error": "Point not found"}, 404)
            pt = point_from_feature(feat)
            target = {
                "name": pt.get("name", pid),
                "lat": pt["lat"],
                "lon": pt["lon"],
                "alt": pt.get("altMSL") or pt.get("altHAE", 0.0),
                "threshold": threshold
            }
        except Exception as e:
            return make_response({"error": str(e)}, 500)

    with state_mod.STAKEOUT_LOCK:
        state_mod.STAKEOUT_CURRENT = target

    return make_response({"ok": True}, 200)


@bp.route("/stakeout/events")
def stakeout_events():
    def gen():
        while True:
            with state_mod.STAKEOUT_LOCK:
                target = state_mod.STAKEOUT_CURRENT

            if not target:
                yield "data: " + json.dumps({"error": "No target set"}) + "\n\n"
                time.sleep(1.0)
                continue

            snap = state_mod.STATE.snapshot()
            tpv = snap.get("TPV", {})
            hp = snap.get("HPPOSLLH", {})
            lat = hp.get("lat") or tpv.get("lat")
            lon = hp.get("lon") or tpv.get("lon")
            alt = hp.get("altMSL") or tpv.get("altMSL") or hp.get("altHAE", 0.0)

            if lat is None or lon is None:
                yield "data: " + json.dumps({"error": "No position fix"}) + "\n\n"
                time.sleep(1.0)
                continue

            try:
                X_c, Y_c, Z_c = geodetic_to_ecef(lat, lon, alt)
                X_t, Y_t, Z_t = geodetic_to_ecef(target["lat"], target["lon"], target["alt"])
                dX, dY, dZ = X_t - X_c, Y_t - Y_c, Z_t - Z_c
                e, n, u = ecef_delta_to_enu(dX, dY, dZ, lat, lon)
                dist2d = math.hypot(e, n)
                dist3d = math.sqrt(dist2d * dist2d + u * u)
                yield "data: " + json.dumps({
                    "target_name": target.get("name", ""),
                    "deltaN": n, "deltaE": e, "deltaU": u,
                    "dist2d": dist2d, "dist3d": dist3d
                }) + "\n\n"
            except Exception as exc:
                yield "data: " + json.dumps({"error": str(exc)}) + "\n\n"

            time.sleep(1.0)

    return Response(gen(), mimetype="text/event-stream")
