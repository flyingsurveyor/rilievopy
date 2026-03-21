"""
Routes for topographic computation tools:
  - DTM / contour lines / volumes / profiles
  - Traverses (poligonali) and leveling
  - Area division (frazionamento)
  - CAD web editor with save/load/export
"""

import json
import math
import os
import tempfile
from datetime import datetime

from flask import Blueprint, render_template, request, jsonify, make_response

from modules.dtm import (
    TIN, extract_contours, volume_between_surfaces,
    extract_profile, extract_cross_sections, tin_statistics,
)
from modules.traverses import (
    StazionePoligonale, calcola_poligonale_aperta,
    calcola_poligonale_chiusa, dividi_area_con_dividenti,
    calcola_livellazione,
)
from modules.survey import load_survey, list_survey_ids, SURVEY_DIR

bp = Blueprint('topo_tools', __name__)

CAD_DIR = os.path.join(SURVEY_DIR, "cad_projects")
os.makedirs(CAD_DIR, exist_ok=True)


def _survey_options():
    surveys = []
    for sid in list_survey_ids():
        try:
            svy = load_survey(sid)
            surveys.append({"sid": sid,
                "title": svy.get("properties", {}).get("title", sid),
                "n_points": len(svy.get("features", []))})
        except Exception:
            pass
    return surveys


def _all_survey_points():
    """All points from all surveys for dropdown selection."""
    all_pts = []
    for sid in list_survey_ids():
        try:
            svy = load_survey(sid)
            title = svy.get("properties", {}).get("title", sid)
            for feat in svy.get("features", []):
                props = feat.get("properties", {})
                hp = props.get("HPPOSLLH", {})
                lat, lon = hp.get("lat"), hp.get("lon")
                if lat is None or lon is None:
                    continue
                all_pts.append({
                    "sid": sid, "survey": title, "pid": feat.get("id", ""),
                    "name": props.get("name", ""),
                    "lat": lat, "lon": lon,
                    "alt": hp.get("altMSL") or hp.get("altHAE", 0) or 0,
                    "label": f"{title} / {props.get('name', feat.get('id', ''))}",
                })
        except Exception:
            pass
    return all_pts


def _survey_to_local(sid):
    svy = load_survey(sid)
    features = svy.get("features", [])
    if not features:
        return [], None
    first_hp = features[0].get("properties", {}).get("HPPOSLLH", {})
    olat, olon = first_hp.get("lat", 0), first_hp.get("lon", 0)
    points = []
    for feat in features:
        props = feat.get("properties", {})
        hp = props.get("HPPOSLLH", {})
        lat, lon = hp.get("lat"), hp.get("lon")
        alt = hp.get("altMSL") or hp.get("altHAE", 0) or 0
        if lat is None or lon is None:
            continue
        x = (lon - olon) * 111320 * math.cos(math.radians(olat))
        y = (lat - olat) * 110540
        points.append({"id": feat.get("id", ""), "name": props.get("name", ""),
            "code": props.get("codice", ""),
            "x": round(x, 3), "y": round(y, 3), "z": round(float(alt), 3)})
    return points, {"lat": olat, "lon": olon}


# ================================================================
#  DTM
# ================================================================

@bp.route("/dtm")
def dtm_page():
    return render_template("rtk_dtm.html", surveys=_survey_options())

def _build_tin(sid):
    svy = load_survey(sid)
    features = svy.get("features", [])
    if len(features) < 3:
        raise ValueError("Servono almeno 3 punti per un DTM")
    first_hp = features[0].get("properties", {}).get("HPPOSLLH", {})
    origin = (first_hp.get("lat", 0), first_hp.get("lon", 0))
    tin = TIN()
    tin.add_points_from_features(features, use_local=True, origin=origin)
    tin.build()
    return tin, origin

@bp.route("/api/dtm/build", methods=["POST"])
def api_dtm_build():
    data = request.get_json()
    try:
        tin, origin = _build_tin(data["survey_id"])
        stats = tin_statistics(tin)
        tri_data = [{"v0":{"x":tin.points[t.i0].x,"y":tin.points[t.i0].y,"z":tin.points[t.i0].z},
                     "v1":{"x":tin.points[t.i1].x,"y":tin.points[t.i1].y,"z":tin.points[t.i1].z},
                     "v2":{"x":tin.points[t.i2].x,"y":tin.points[t.i2].y,"z":tin.points[t.i2].z}} for t in tin.triangles]
        pts_data = [{"x":p.x,"y":p.y,"z":p.z,"name":p.name} for p in tin.points]
        # Auto contours
        contour_data = None
        ci = data.get("contour_interval")
        if ci:
            contours = extract_contours(tin, interval=float(ci))
            contour_data = {f"{z:.2f}": [[{"x":p[0],"y":p[1]} for p in pl] for pl in pls] for z, pls in contours.items()}
        return jsonify({"statistics":stats,"triangles":tri_data,"points":pts_data,
                        "origin":{"lat":origin[0],"lon":origin[1]},"contours":contour_data})
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@bp.route("/api/dtm/contours", methods=["POST"])
def api_dtm_contours():
    data = request.get_json()
    try:
        tin, _ = _build_tin(data["survey_id"])
        interval = float(data.get("interval", 1.0))
        contours = extract_contours(tin, interval=interval)
        cd = {f"{z:.2f}": [[{"x":p[0],"y":p[1]} for p in pl] for pl in pls] for z, pls in contours.items()}
        return jsonify({"contours": cd, "interval": interval, "n_levels": len(cd)})
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@bp.route("/api/dtm/volume", methods=["POST"])
def api_dtm_volume():
    data = request.get_json()
    try:
        tin, _ = _build_tin(data["survey_id"])
        return jsonify(volume_between_surfaces(tin, float(data["reference_z"])))
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@bp.route("/api/dtm/profile", methods=["POST"])
def api_dtm_profile():
    data = request.get_json()
    try:
        tin, _ = _build_tin(data["survey_id"])
        start = (float(data["start_x"]), float(data["start_y"]))
        end = (float(data["end_x"]), float(data["end_y"]))
        step = float(data.get("step", 1.0))
        profile = extract_profile(tin, start, end, step)
        return jsonify({"profile": profile, "length": math.hypot(end[0]-start[0], end[1]-start[1])})
    except Exception as e:
        return jsonify({"error": str(e)}), 400

# ================================================================
#  TRAVERSES
# ================================================================

@bp.route("/traverses")
def traverses_page():
    return render_template("rtk_traverses.html", surveys=_survey_options(), all_points_json=json.dumps(_all_survey_points()))

@bp.route("/api/traverses/compute", methods=["POST"])
def api_traverses_compute():
    data = request.get_json()
    try:
        tipo = data.get("tipo", "aperta")
        az_start = float(data["azimut_partenza"])
        az_end = data.get("azimut_arrivo")
        if az_end is not None: az_end = float(az_end)
        metodo = data.get("metodo", "bowditch")
        stazioni = [StazionePoligonale(name=s["name"], angolo_hz=s.get("angolo_hz"),
            distanza=s.get("distanza"), dislivello=s.get("dislivello"),
            e_noto=s.get("e_noto"), n_noto=s.get("n_noto"), h_noto=s.get("h_noto")) for s in data["stazioni"]]
        result = calcola_poligonale_chiusa(stazioni, az_start, metodo) if tipo == "chiusa" else calcola_poligonale_aperta(stazioni, az_start, az_end, metodo)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@bp.route("/api/traverses/leveling", methods=["POST"])
def api_traverses_leveling():
    data = request.get_json()
    try:
        q_arr = data.get("quota_arrivo")
        return jsonify(calcola_livellazione(data["misure"], float(data["quota_partenza"]), float(q_arr) if q_arr is not None else None))
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@bp.route("/api/traverses/export_csv", methods=["POST"])
def api_traverses_export_csv():
    data = request.get_json()
    try:
        lines = ["Nome;E;N;H;Azimut;Corr_E;Corr_N"]
        for s in data.get("stazioni", []):
            lines.append(";".join([s.get("name",""),
                f"{s['e_calc']:.3f}" if s.get("e_calc") is not None else "",
                f"{s['n_calc']:.3f}" if s.get("n_calc") is not None else "",
                f"{s['h_calc']:.3f}" if s.get("h_calc") is not None else "",
                f"{s['azimut']:.4f}" if s.get("azimut") is not None else "",
                f"{s['corr_e']:.4f}", f"{s['corr_n']:.4f}"]))
        resp = make_response("\n".join(lines) + "\n")
        resp.headers["Content-Type"] = "text/csv; charset=utf-8"
        resp.headers["Content-Disposition"] = "attachment; filename=poligonale.csv"
        return resp
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@bp.route("/api/area/divide", methods=["POST"])
def api_area_divide():
    data = request.get_json()
    try:
        return jsonify(dividi_area_con_dividenti(
            [(v[0],v[1]) for v in data["vertici"]], float(data["area_target"]),
            (int(data["lato_vincolo"][0]), int(data["lato_vincolo"][1]))))
    except Exception as e:
        return jsonify({"error": str(e)}), 400

# ================================================================
#  CAD
# ================================================================

@bp.route("/cad")
def cad_page():
    return render_template("rtk_cad.html", surveys=_survey_options())

@bp.route("/api/cad/load_survey", methods=["POST"])
def api_cad_load_survey():
    data = request.get_json()
    try:
        sid = data["survey_id"]
        points, origin = _survey_to_local(sid)
        if not points:
            return jsonify({"error": "Rilievo vuoto"}), 400
        svy = load_survey(sid)
        return jsonify({"points": points, "origin": origin, "survey_id": sid,
            "title": svy.get("properties", {}).get("title", sid)})
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@bp.route("/api/cad/save", methods=["POST"])
def api_cad_save():
    data = request.get_json()
    try:
        name = data.get("name", "").strip()
        if not name:
            name = datetime.now().strftime("CAD_%Y%m%d_%H%M%S")
        safe = "".join(c for c in name if c.isalnum() or c in "_-")[:40] or "project"
        filepath = os.path.join(CAD_DIR, f"{safe}.json")
        project = {"name": name, "saved": datetime.now().isoformat(timespec="seconds"),
            "survey_id": data.get("survey_id", ""), "origin": data.get("origin"),
            "points": data.get("points", []), "entities": data.get("entities", []),
            "camera": data.get("camera"), "layers": data.get("layers")}
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(project, f, ensure_ascii=False, indent=2)
        return jsonify({"ok": True, "name": safe})
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@bp.route("/api/cad/load/<name>")
def api_cad_load(name):
    filepath = os.path.join(CAD_DIR, f"{name}.json")
    if not os.path.exists(filepath):
        return jsonify({"error": "Progetto non trovato"}), 404
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return jsonify(json.load(f))
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@bp.route("/api/cad/list_projects")
def api_cad_list_projects():
    projects = []
    for fn in sorted(os.listdir(CAD_DIR)):
        if fn.endswith(".json"):
            try:
                with open(os.path.join(CAD_DIR, fn), "r") as f:
                    d = json.load(f)
                projects.append({"name": fn[:-5], "title": d.get("name", fn[:-5]),
                    "saved": d.get("saved", ""), "n_points": len(d.get("points", [])),
                    "n_entities": len(d.get("entities", []))})
            except Exception:
                pass
    return jsonify({"projects": projects})

@bp.route("/api/cad/export_dxf", methods=["POST"])
def api_cad_export_dxf():
    data = request.get_json()
    try:
        points = data.get("points", [])
        entities = data.get("entities", [])
        out = []
        def w(c, v): out.append(f"{c}\n{v}")
        w(0,"SECTION"); w(2,"HEADER"); w(9,"$INSUNITS"); w(70,"6"); w(0,"ENDSEC")
        layers = set(["PUNTI","ETICHETTE","QUOTE","DISEGNO"])
        for p in points:
            if p.get("layer"): layers.add(p["layer"])
        for e in entities:
            if e.get("layer"): layers.add(e["layer"])
        colors = {"PUNTI":"7","ETICHETTE":"3","QUOTE":"5","DISEGNO":"4","FABBRICATI":"1","RECINZIONI":"6","VIABILITA":"8"}
        w(0,"SECTION"); w(2,"TABLES"); w(0,"TABLE"); w(2,"LAYER"); w(70,str(len(layers)))
        for ln in sorted(layers):
            w(0,"LAYER"); w(2,ln); w(70,"0"); w(62,colors.get(ln,"7"))
        w(0,"ENDTAB"); w(0,"ENDSEC"); w(0,"SECTION"); w(2,"BLOCKS"); w(0,"ENDSEC")
        w(0,"SECTION"); w(2,"ENTITIES")
        for p in points:
            ly = p.get("layer","PUNTI")
            w(0,"POINT"); w(8,ly); w(10,f"{p['x']:.4f}"); w(20,f"{p['y']:.4f}"); w(30,f"{p.get('z',0):.4f}")
            if p.get("name"):
                w(0,"TEXT"); w(8,"ETICHETTE"); w(10,f"{p['x']+.5:.4f}"); w(20,f"{p['y']:.4f}"); w(30,f"{p.get('z',0)+.2:.4f}"); w(40,"0.15"); w(1,p["name"])
            w(0,"TEXT"); w(8,"QUOTE"); w(10,f"{p['x']+.5:.4f}"); w(20,f"{p['y']-.3:.4f}"); w(30,f"{p.get('z',0):.4f}"); w(40,"0.12"); w(1,f"{p.get('z',0):.2f}")
        for ent in entities:
            ep = ent.get("pts", [])
            if len(ep) < 2: continue
            ly = ent.get("layer", "DISEGNO")
            if ent.get("type") == "line" and len(ep) >= 2:
                w(0,"LINE"); w(8,ly); w(10,f"{ep[0]['x']:.4f}"); w(20,f"{ep[0]['y']:.4f}"); w(30,"0")
                w(11,f"{ep[1]['x']:.4f}"); w(21,f"{ep[1]['y']:.4f}"); w(31,"0")
            else:
                w(0,"LWPOLYLINE"); w(8,ly); w(90,str(len(ep))); w(70,"1" if ent.get("type")=="polygon" else "0")
                for pt in ep: w(10,f"{pt['x']:.4f}"); w(20,f"{pt['y']:.4f}")
        w(0,"ENDSEC"); w(0,"EOF")
        resp = make_response("\n".join(out) + "\n")
        resp.headers["Content-Type"] = "application/dxf"
        resp.headers["Content-Disposition"] = "attachment; filename=cad_export.dxf"
        return resp
    except Exception as e:
        return jsonify({"error": str(e)}), 400
