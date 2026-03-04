"""
Survey routes: list, create, view, add point, export (GeoJSON/CSV/DXF/GPKG/TXT/Map).
"""

import io
import json
import math
import os
import time
from datetime import datetime

from flask import Blueprint, abort, make_response, render_template, request, send_from_directory

from modules.state import STATE
from modules.utils import sanitize_point_name, robust_avg
from modules.survey import (
    list_survey_ids, load_survey, save_survey, create_survey,
    delete_survey_file, next_point_id, point_feature,
    flatten_point_for_csv, SURVEY_DIR, survey_path, point_from_feature
)
from modules.exports import (
    build_dxf_advanced, export_geopackage, format_point_txt
)
from modules.active_survey import get_active_survey_id, set_active_survey_id
from modules.codici_punto import load_codici

bp = Blueprint('surveys', __name__)


def _redirect(url: str):
    r = make_response("", 302)
    r.headers["Location"] = url
    return r


# ---------- List ----------
@bp.route("/surveys")
def surveys_list():
    ids = list_survey_ids()
    active_sid = get_active_survey_id()
    active_title = None
    items = []
    if not ids:
        items.append('<div class="card">Nessun rilievo. <a class="btn" href="/survey/new">Crea il primo</a></div>')
    for sid in ids:
        try:
            svy = load_survey(sid)
            title = svy.get("properties", {}).get("title", sid)
            desc = svy.get("properties", {}).get("desc", "")
            npt = len(svy.get("features", []))
            if sid == active_sid:
                active_title = title
            is_active = (sid == active_sid)
            set_btn = (
                f'<button class="btn btn-set-active" data-sid="{sid}">📌 Imposta attivo</button>'
                if not is_active else
                '<span class="active-badge">✅ Attivo</span>'
            )
            items.append(
                f'<div class="card"><div class="kv"><span><b>{title}</b> ({sid})</span><span>{npt} punti</span></div>'
                f'<div class="kv"><span>{desc or "-"}</span>'
                f'<span><a class="btn" href="/survey/{sid}">Apri</a> {set_btn}</span></div></div>'
            )
        except Exception as e:
            items.append(f'<div class="card">Errore con {sid}: {e}</div>')
    return render_template('rtk_surveys_list.html',
                           items="\n".join(items),
                           active_sid=active_sid,
                           active_title=active_title)




# ---------- New survey ----------
@bp.route("/survey/new", methods=["GET", "POST"])
def survey_new():
    if request.method == "GET":
        return render_template('rtk_survey_new.html')
    title = (request.form.get("title") or "").strip()[:40]
    desc = (request.form.get("desc") or "").strip()[:1000]
    sid = create_survey(title, desc)
    set_active_survey_id(sid)
    return _redirect(f"/survey/{sid}")


# ---------- Active survey ----------
@bp.route("/api/survey/<sid>/set_active", methods=["POST"])
def survey_set_active(sid):
    try:
        load_survey(sid)
    except FileNotFoundError:
        return make_response(json.dumps({"ok": False, "error": "not found"}), 404,
                             {"Content-Type": "application/json"})
    set_active_survey_id(sid)
    return make_response(json.dumps({"ok": True, "sid": sid}), 200,
                         {"Content-Type": "application/json"})


@bp.route("/survey/active/point")
def survey_active_point():
    active_sid = get_active_survey_id()
    if not active_sid:
        return _redirect("/surveys")
    return _redirect(f"/survey/{active_sid}/point")



@bp.route("/survey/<sid>")
def survey_view(sid):
    try:
        svy = load_survey(sid)
    except FileNotFoundError:
        abort(404)
    props = svy.get("properties", {})
    active_sid = get_active_survey_id()
    rows = []
    points_for_json = []
    for idx, f in enumerate(svy.get("features", [])):
        p = f.get("properties", {})
        hp = p.get("HPPOSLLH", {})
        dop = p.get("DOP", {})
        rp = p.get("RELPOSNED", {})

        def fnum(x, fmt):
            return "-" if x is None else fmt.format(x)

        point_id = f.get('id', '')
        point_name = p.get('name', '')
        codice = p.get('codice', '')
        points_for_json.append({"id": point_id, "name": point_name})

        rows.append(
            "<tr>"
            f"<td><input type='checkbox' class='area-cb' value='{point_id}'/></td>"
            f"<td>{point_id}</td>"
            f"<td>{point_name}</td>"
            f"<td>{codice}</td>"
            f"<td>{fnum(hp.get('lat'), '{:.9f}')}</td>"
            f"<td>{fnum(hp.get('lon'), '{:.9f}')}</td>"
            f"<td>{fnum(hp.get('altHAE'), '{:.3f}')}</td>"
            f"<td>{fnum(dop.get('pdop'), '{:.2f}')}</td>"
            f"<td>{fnum(dop.get('hdop'), '{:.2f}')}</td>"
            f"<td>{fnum(dop.get('vdop'), '{:.2f}')}</td>"
            f"<td>{fnum(rp.get('N'), '{:.4f}')}</td>"
            f"<td>{fnum(rp.get('E'), '{:.4f}')}</td>"
            f"<td>{fnum(rp.get('D'), '{:.4f}')}</td>"
            f"<td>{fnum(rp.get('baseline'), '{:.4f}')}</td>"
            f"<td><a class='btn' href='/survey/{sid}/point/{point_id}.txt'>TXT</a></td>"
            f"<td><button class='btn-delete-point' onclick=\"confirmDeletePoint('{sid}', {idx}, '{point_name}')\">🗑️</button></td>"
            "</tr>"
        )
    num_points = len(rows)
    num_columns = 16
    return render_template('rtk_survey_view.html',
                           sid=props.get("id", sid),
                           title=props.get("title", sid),
                           notes=props.get("desc", ""),
                           rows="\n".join(rows) or f"<tr><td colspan='{num_columns}'>(nessun punto)</td></tr>",
                           points_json=json.dumps(points_for_json),
                           num_points=str(num_points),
                           active_sid=active_sid)


# ---------- Update notes ----------
@bp.route("/survey/<sid>/notes", methods=["POST"])
def survey_update_notes(sid):
    try:
        svy = load_survey(sid)
    except FileNotFoundError:
        abort(404)
    notes = (request.form.get("notes") or "").strip()[:1000]
    svy.setdefault("properties", {})["desc"] = notes
    save_survey(sid, svy)
    return _redirect(f"/survey/{sid}")


# ---------- Add point ----------
@bp.route("/survey/<sid>/point", methods=["GET", "POST"])
def survey_point(sid):
    if request.method == "GET":
        try:
            svy = load_survey(sid)
        except FileNotFoundError:
            abort(404)
        next_pid = next_point_id(svy)
        codici_data = load_codici()
        active_title = svy.get("properties", {}).get("title", sid)
        num_points = len(svy.get("features", []))
        saved = request.args.get("saved", "")
        savedname = request.args.get("savedname", "")
        savedcodice = request.args.get("savedcodice", "")
        return render_template('rtk_survey_point_form.html',
                               sid=sid,
                               next_pid=next_pid,
                               active_title=active_title,
                               num_points=num_points,
                               codici_json=json.dumps(codici_data),
                               saved=saved,
                               savedname=savedname,
                               savedcodice=savedcodice)

    name = sanitize_point_name(request.form.get("name", ""))
    desc = (request.form.get("desc", "") or "").strip()[:300]
    codice = (request.form.get("codice", "") or "").strip()[:20]
    dur_form = request.form.get("dur", "").strip()
    duration = None
    if dur_form:
        try:
            duration = float(dur_form)
        except Exception:
            duration = None
    if duration is None:
        try:
            duration = float(request.args.get("dur", 10.0))
        except Exception:
            duration = 10.0
    try:
        interval = float(request.args.get("step", 0.5))
    except Exception:
        interval = 0.5

    start_ts = datetime.now()
    samples = []
    n_iters = max(1, int(duration / interval))
    for _ in range(n_iters):
        samples.append(STATE.snapshot())
        time.sleep(interval)
    end_ts = datetime.now()

    def collect_key(group, key):
        vals = []
        for s in samples:
            g = s.get(group, {})
            v = g.get(key, None)
            if isinstance(v, (int, float)):
                vals.append(v)
        return robust_avg(vals)

    mode = int(round(collect_key("TPV", "mode") or 0))
    rtk = samples[-1].get("TPV", {}).get("rtk", "-")
    numSV = int(round(collect_key("TPV", "numSV") or 0))

    lat = collect_key("HPPOSLLH", "lat") or collect_key("TPV", "lat")
    lon = collect_key("HPPOSLLH", "lon") or collect_key("TPV", "lon")
    altHAE = collect_key("HPPOSLLH", "altHAE")
    altMSL = collect_key("HPPOSLLH", "altMSL") or collect_key("TPV", "altMSL")
    hAcc = collect_key("HPPOSLLH", "hAcc") or collect_key("TPV", "hAcc")
    vAcc = collect_key("HPPOSLLH", "vAcc") or collect_key("TPV", "vAcc")

    X = collect_key("HPPOSECEF", "X")
    Y = collect_key("HPPOSECEF", "Y")
    Z = collect_key("HPPOSECEF", "Z")
    pAcc = collect_key("HPPOSECEF", "pAcc")

    gdop = collect_key("DOP", "gdop"); pdop = collect_key("DOP", "pdop")
    hdop = collect_key("DOP", "hdop"); vdop = collect_key("DOP", "vdop")
    ndop = collect_key("DOP", "ndop"); edop = collect_key("DOP", "edop")
    tdop = collect_key("DOP", "tdop")

    covNN = collect_key("COV", "covNN"); covEE = collect_key("COV", "covEE")
    covDD = collect_key("COV", "covDD"); covNE = collect_key("COV", "covNE")
    covND = collect_key("COV", "covND"); covED = collect_key("COV", "covED")

    relN = collect_key("RELPOS", "N"); relE = collect_key("RELPOS", "E")
    relD = collect_key("RELPOS", "D")
    relsN = collect_key("RELPOS", "sN"); relsE = collect_key("RELPOS", "sE")
    relsD = collect_key("RELPOS", "sD")
    horiz = baseline = bearing = slope = None
    if relN is not None and relE is not None and relD is not None:
        horiz = math.hypot(relN, relE)
        baseline = math.sqrt(horiz * horiz + relD * relD)
        bearing = math.degrees(math.atan2(relE, relN))
        bearing = bearing + 360 if bearing < 0 else bearing
        slope = math.degrees(math.atan2(-relD, horiz)) if horiz else 0.0

    stats = {
        "mode": mode, "rtk": rtk, "numSV": numSV,
        "hAcc": hAcc, "vAcc": vAcc, "pAcc": pAcc,
        "gdop": gdop, "pdop": pdop, "hdop": hdop, "vdop": vdop,
        "ndop": ndop, "edop": edop, "tdop": tdop,
        "covNN": covNN, "covEE": covEE, "covDD": covDD,
        "covNE": covNE, "covND": covND, "covED": covED,
        "relN": relN, "relE": relE, "relD": relD,
        "relsN": relsN, "relsE": relsE, "relsD": relsD,
        "baseline": baseline, "horiz": horiz, "bearing": bearing, "slope": slope
    }

    try:
        svy = load_survey(sid)
    except FileNotFoundError:
        abort(404)
    pid = next_point_id(svy)
    start_iso = start_ts.isoformat(timespec='seconds')
    end_iso = end_ts.isoformat(timespec='seconds')

    feat = point_feature(
        pid, lat, lon, altHAE, altMSL, X, Y, Z, stats,
        {"name": name, "codice": codice, "desc": desc, "duration": duration,
         "interval": interval, "n_samples": len(samples),
         "start": start_iso, "end": end_iso}
    )
    svy.setdefault("features", []).append(feat)
    save_survey(sid, svy)

    return _redirect(f"/survey/{sid}/point?saved={pid}&savedname={name}&savedcodice={codice}")


# ---------- Downloads ----------
@bp.route("/survey/<sid>.geojson")
def survey_download_geojson(sid):
    path = survey_path(sid)
    if not os.path.isfile(path):
        abort(404)
    return send_from_directory(SURVEY_DIR, os.path.basename(path),
                               as_attachment=True, download_name=f"{sid}.geojson")


@bp.route("/survey/<sid>.csv")
def survey_download_csv(sid):
    try:
        svy = load_survey(sid)
    except FileNotFoundError:
        abort(404)
    buf = io.StringIO()
    buf.write("id,name,lat,lon,altHAE,altMSL,X,Y,Z,mode,rtk,numSV,gdop,pdop,hdop,vdop,ndop,edop,tdop,hAcc,vAcc,pAcc,NN,EE,DD,NE,ND,ED,relN,relE,relD,relsN,relsE,relsD,baseline,bearingDeg,slopeDeg\n")
    for f in svy.get("features", []):
        row = [f.get("id", "")] + flatten_point_for_csv(f)
        buf.write(",".join(row) + "\n")
    resp = make_response(buf.getvalue())
    resp.headers["Content-Type"] = "text/csv; charset=utf-8"
    resp.headers["Content-Disposition"] = f'attachment; filename="{sid}.csv"'
    return resp


@bp.route("/survey/<sid>/map")
def survey_map(sid):
    try:
        svy = load_survey(sid)
    except FileNotFoundError:
        abort(404)
    if not svy.get("features"):
        return make_response("Nessun punto da visualizzare", 400)
    return render_template('rtk_map.html', sid=sid, geojson=json.dumps(svy))


@bp.route("/survey/<sid>.dxf")
def survey_download_dxf(sid):
    try:
        svy = load_survey(sid)
    except FileNotFoundError:
        abort(404)
    try:
        text_height = float(request.args.get("text_height", "0.1"))
        show_precision = request.args.get("precision", "false").lower() in ("true", "1", "yes")
        quota_decimals = int(request.args.get("decimals", "3"))
        dxf_text = build_dxf_advanced(svy, mode="3d", text_height=text_height,
                                      show_precision=show_precision, quota_decimals=quota_decimals)
    except Exception as e:
        return make_response(f"Errore DXF: {e}", 500)
    resp = make_response(dxf_text)
    resp.headers["Content-Type"] = "application/dxf; charset=utf-8"
    resp.headers["Content-Disposition"] = f'attachment; filename="{sid}.dxf"'
    return resp


@bp.route("/survey/<sid>_2d.dxf")
def survey_download_dxf_2d(sid):
    try:
        svy = load_survey(sid)
    except FileNotFoundError:
        abort(404)
    try:
        text_height = float(request.args.get("text_height", "0.1"))
        show_precision = request.args.get("precision", "false").lower() in ("true", "1", "yes")
        quota_decimals = int(request.args.get("decimals", "3"))
        dxf_text = build_dxf_advanced(svy, mode="2d", text_height=text_height,
                                      show_precision=show_precision, quota_decimals=quota_decimals)
    except Exception as e:
        return make_response(f"Errore DXF: {e}", 500)
    resp = make_response(dxf_text)
    resp.headers["Content-Type"] = "application/dxf; charset=utf-8"
    resp.headers["Content-Disposition"] = f'attachment; filename="{sid}_2d.dxf"'
    return resp


@bp.route("/survey/<sid>.gpkg")
def survey_download_geopackage(sid):
    try:
        svy = load_survey(sid)
    except FileNotFoundError:
        abort(404)
    if not svy.get("features"):
        return make_response("Nessun punto nel rilievo", 400)
    filepath = export_geopackage(svy, sid)
    if not filepath:
        return make_response("Errore nella creazione del GeoPackage", 500)
    try:
        with open(filepath, "rb") as fh:
            gpkg_data = fh.read()
        try:
            os.unlink(filepath)
        except Exception:
            pass
        resp = make_response(gpkg_data)
        resp.headers["Content-Type"] = "application/geopackage+sqlite3"
        resp.headers["Content-Disposition"] = f'attachment; filename="{sid}.gpkg"'
        return resp
    except Exception as e:
        return make_response(f"Errore lettura GeoPackage: {e}", 500)


@bp.route("/survey/<sid>/point/<pid>.txt")
def survey_point_txt(sid, pid):
    try:
        svy = load_survey(sid)
    except FileNotFoundError:
        abort(404)
    feat = next((f for f in svy.get("features", []) if f.get("id") == pid), None)
    if not feat:
        abort(404)
    txt = format_point_txt(feat, sid)
    resp = make_response(txt)
    resp.headers["Content-Type"] = "text/plain; charset=utf-8"
    resp.headers["Content-Disposition"] = f'attachment; filename="{sid}-{pid}.txt"'
    return resp


# ---------- Delete ----------
@bp.route("/survey/<sid>/point/<int:point_index>/delete", methods=["POST"])
def delete_point(sid, point_index):
    try:
        svy = load_survey(sid)
    except FileNotFoundError:
        return make_response({"error": "Survey not found"}, 404)
    features = svy.get("features", [])
    if point_index < 0 or point_index >= len(features):
        return make_response({"error": f"Point not found at index {point_index}"}, 404)
    removed = features.pop(point_index)
    save_survey(sid, svy)
    return make_response({
        "success": True,
        "message": f"Punto {removed.get('id', '')} eliminato"
    }, 200)


@bp.route("/survey/<sid>/delete", methods=["POST"])
def delete_survey(sid):
    try:
        load_survey(sid)
    except FileNotFoundError:
        return make_response({"error": "Survey not found"}, 404)
    confirm = request.form.get("confirm", "")
    if confirm != "ELIMINA":
        return make_response({"error": "Conferma non valida. Scrivi 'ELIMINA' per confermare."}, 400)
    delete_survey_file(sid)
    return make_response({"success": True, "message": f"Rilievo {sid} eliminato"}, 200)


# ---------- Area ----------
@bp.route("/survey/<sid>/area", methods=["POST"])
def survey_calc_area(sid):
    from modules.cogo import calc_area_perimeter
    from modules.geodesy import geodetic_to_ecef, ecef_delta_to_enu

    data = request.get_json()
    if not data:
        return make_response({"error": "No data"}, 400)
    point_ids = data.get("point_ids", [])
    if len(point_ids) < 3:
        return make_response({"error": "Servono almeno 3 punti"}, 400)

    try:
        svy = load_survey(sid)
    except FileNotFoundError:
        return make_response({"error": "Survey not found"}, 404)

    features = svy.get("features", [])
    ordered_feats = []
    for pid in point_ids:
        f = next((f for f in features if f.get("id") == pid), None)
        if f:
            ordered_feats.append(f)

    if len(ordered_feats) < 3:
        return make_response({"error": "Punti insufficienti con coordinate valide"}, 400)

    # Get reference point
    first_p = ordered_feats[0].get("properties", {}).get("HPPOSLLH", {})
    lat0, lon0 = first_p.get("lat"), first_p.get("lon")
    if lat0 is None or lon0 is None:
        return make_response({"error": "Coordinate primo punto non valide"}, 400)
    alt0 = first_p.get("altMSL") or first_p.get("altHAE", 0.0)
    X0, Y0, Z0 = geodetic_to_ecef(lat0, lon0, alt0)

    enu_points = []
    for f in ordered_feats:
        hp = f.get("properties", {}).get("HPPOSLLH", {})
        lat, lon = hp.get("lat"), hp.get("lon")
        if lat is None or lon is None:
            continue
        alt = hp.get("altMSL") or hp.get("altHAE", 0.0)
        X, Y, Z = geodetic_to_ecef(lat, lon, alt)
        e, n, u = ecef_delta_to_enu(X - X0, Y - Y0, Z - Z0, lat0, lon0)
        enu_points.append((e, n))

    if len(enu_points) < 3:
        return make_response({"error": "Punti insufficienti con coordinate valide"}, 400)

    result = calc_area_perimeter(enu_points)
    return make_response(result, 200)


# ---------- Compat / old routes ----------
@bp.route("/survey", methods=["GET"])
def survey_redirect():
    return _redirect("/surveys")


@bp.route("/files/<path:filename>")
def files_route(filename):
    path = os.path.join(SURVEY_DIR, filename)
    if not os.path.isfile(path):
        abort(404)
    return send_from_directory(SURVEY_DIR, filename, as_attachment=True)
