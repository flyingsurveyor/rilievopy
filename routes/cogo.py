"""
COGO (Coordinate Geometry) route handlers.
Trilateration, bearing intersection, polar, offset,
perpendicular projection, multi-point alignment, Helmert 2D transformation.
"""

import math
import json

from flask import Blueprint, make_response, render_template, request

from modules.geodesy import geodetic_to_ecef, ecef_delta_to_enu, enu_to_geodetic
from modules.cogo import (
    trilaterate_2d, gon_to_radians, radians_to_gon,
    calculate_bearing_gon, bearing_bearing_intersection,
    polar_to_point, point_offset_from_line,
    perpendicular_foot_on_line, gon_to_dms, helmert_2d_transform, apply_helmert_2d
)
from modules.exports import _feature_to_llh
from modules.survey import (
    list_all_points_options, list_survey_ids, load_survey, save_survey,
    next_point_id, point_feature
)

bp = Blueprint('cogo', __name__)

# ---------- COGO routes ----------
@bp.route("/cogo", methods=["GET"])
def cogo_hub():
    """COGO hub page"""
    return render_template('rtk_cogo_hub.html')

@bp.route("/cogo/trilateration", methods=["GET"])
def cogo_trilateration():
    """Trilateration page"""
    opts = list_all_points_options()
    options = "\n".join([f'<option value="{v}">{l}</option>' for (l,v) in opts])
    
    # Survey options for saving
    survey_opts = []
    for sid in list_survey_ids():
        survey_opts.append(f'<option value="{sid}">{sid}</option>')
    survey_options = "\n".join(survey_opts)
    
    return render_template('rtk_cogo_trilateration.html', options=options, survey_options=survey_options)

@bp.route("/cogo/trilateration/calc", methods=["POST"])
def cogo_trilateration_calc():
    """Calculate trilateration"""
    data = request.get_json()
    if not data:
        return make_response({"error": "No data"}, 400)
    
    stations_data = data.get("stations", [])
    distances = data.get("distances", [])
    solution_index = data.get("solution_index")
    
    if len(stations_data) < 2:
        return make_response({"error": "Servono almeno 2 stazioni"}, 400)
    
    # Load station points and convert to ENU
    stations_llh = []
    for st in stations_data:
        try:
            svy = load_survey(st["sid"])
            feat = next((f for f in svy.get("features", []) if f.get("id") == st["pid"]), None)
            if not feat:
                return make_response({"error": f"Punto {st['pid']} non trovato"}, 404)
            
            llh = _feature_to_llh(feat)
            if not llh:
                return make_response({"error": f"Coordinate punto {st['pid']} non valide"}, 400)
            
            stations_llh.append(llh)
        except FileNotFoundError:
            return make_response({"error": f"Rilievo {st['sid']} non trovato"}, 404)
    
    # Use first station as ENU origin
    lat0, lon0, alt0 = stations_llh[0]
    X0, Y0, Z0 = geodetic_to_ecef(lat0, lon0, alt0)
    
    # Convert all stations to ENU
    stations_enu = []
    for lat, lon, alt in stations_llh:
        X, Y, Z = geodetic_to_ecef(lat, lon, alt)
        dX, dY, dZ = X - X0, Y - Y0, Z - Z0
        e, n, u = ecef_delta_to_enu(dX, dY, dZ, lat0, lon0)
        stations_enu.append((e, n))
    
    # Calculate trilateration
    result = trilaterate_2d(stations_enu, distances)
    
    if result.get("error"):
        return make_response(result, 400)
    
    if result.get("ambiguous"):
        # Two solutions - return both with geodetic coordinates
        solutions = []
        for sol in result["solutions"]:
            e, n = sol
            # Average altitude from stations
            avg_alt = sum(llh[2] for llh in stations_llh) / len(stations_llh)
            lat, lon, alt = enu_to_geodetic(e, n, 0, lat0, lon0, alt0)
            solutions.append({
                "e": e,
                "n": n,
                "lat": lat,
                "lon": lon,
                "alt": avg_alt
            })
        
        return make_response({
            "ambiguous": True,
            "solutions": solutions,
            "stations_enu": [{"e": e, "n": n} for e, n in stations_enu],
            "message": result["message"]
        }, 200)
    
    e, n = result["solution"]
    
    # Average altitude from stations
    avg_alt = sum(llh[2] for llh in stations_llh) / len(stations_llh)
    
    # Convert to geodetic
    lat, lon, alt = enu_to_geodetic(e, n, 0, lat0, lon0, alt0)
    
    return make_response({
        "e": e,
        "n": n,
        "lat": lat,
        "lon": lon,
        "alt": avg_alt,
        "residuals": result.get("residuals", []),
        "rms": result.get("rms"),
        "quality": result.get("quality"),
        "quality_color": result.get("quality_color")
    }, 200)

@bp.route("/cogo/trilateration/save", methods=["POST"])
def cogo_trilateration_save():
    """Save trilateration point to survey"""
    data = request.get_json()
    if not data:
        return make_response({"error": "No data"}, 400)
    
    survey_id = data.get("survey_id")
    point_name = sanitize_point_name(data.get("point_name", "SP001"))
    solution = data.get("solution")
    
    if not survey_id or not solution:
        return make_response({"error": "Dati mancanti"}, 400)
    
    try:
        svy = load_survey(survey_id)
    except FileNotFoundError:
        return make_response({"error": "Rilievo non trovato"}, 404)
    
    # Create point feature
    pid = next_point_id(svy)
    
    feat = point_feature(
        pid=pid,
        lat=solution["lat"],
        lon=solution["lon"],
        altHAE=solution["alt"],
        altMSL=solution["alt"],
        X=None, Y=None, Z=None,
        stats={
            "mode": 3,
            "rtk": "trilateration",
            "numSV": None,
            "hAcc": solution.get("rms"),
            "vAcc": None,
            "pAcc": None,
            "gdop": None, "pdop": None, "hdop": None, "vdop": None,
            "ndop": None, "edop": None, "tdop": None,
            "covNN": None, "covEE": None, "covDD": None,
            "covNE": None, "covND": None, "covED": None,
            "relN": None, "relE": None, "relD": None,
            "relsN": None, "relsE": None, "relsD": None,
            "baseline": None, "horiz": None, "bearing": None, "slope": None
        },
        meta={
            "name": point_name,
            "desc": "Punto calcolato con trilaterazione COGO",
            "start": now_iso(),
            "end": now_iso(),
            "duration": 0,
            "interval": 0,
            "n_samples": 0
        }
    )
    
    # Add COGO metadata
    feat["properties"]["origin"] = "trilateration"
    feat["properties"]["cogo_method"] = "trilateration"
    feat["properties"]["trilateration"] = {
        "e_enu": solution["e"],
        "n_enu": solution["n"],
        "residuals": solution.get("residuals", []),
        "rms": solution.get("rms"),
        "quality": solution.get("quality")
    }
    
    # Override TPV.mode to COGO
    feat["properties"]["TPV"]["mode"] = "COGO"
    
    svy["features"].append(feat)
    save_survey(survey_id, svy)
    
    return make_response({"success": True, "point_id": pid}, 200)

@bp.route("/cogo/bearing-intersection", methods=["GET"])
def cogo_bearing_intersection():
    """Bearing-bearing intersection page"""
    opts = list_all_points_options()
    options = "\n".join([f'<option value="{v}">{l}</option>' for (l,v) in opts])
    
    survey_opts = []
    for sid in list_survey_ids():
        survey_opts.append(f'<option value="{sid}">{sid}</option>')
    survey_options = "\n".join(survey_opts)
    
    return render_template('rtk_cogo_bearing.html', options=options, survey_options=survey_options)

@bp.route("/cogo/bearing-intersection/calc", methods=["POST"])
def cogo_bearing_intersection_calc():
    """Calculate bearing-bearing intersection with total station Hz angles in gon"""
    data = request.get_json()
    if not data:
        return make_response({"error": "No data"}, 400)
    
    point1 = data.get("point1")
    point2 = data.get("point2")
    hz1_ref = data.get("hz1_ref")
    hz1_target = data.get("hz1_target")
    hz2_ref = data.get("hz2_ref")
    hz2_target = data.get("hz2_target")
    
    # Check required fields
    if not point1 or not point2:
        return make_response({"error": "Punti mancanti"}, 400)
    if hz1_ref is None or hz1_target is None or hz2_ref is None or hz2_target is None:
        return make_response({"error": "Angoli Hz mancanti"}, 400)
    
    # Optional elevation data
    v1 = data.get("v1")
    hs1 = data.get("hs1")
    ht1 = data.get("ht1")
    v2 = data.get("v2")
    hs2 = data.get("hs2")
    ht2 = data.get("ht2")
    
    # Load points
    try:
        svy1 = load_survey(point1["sid"])
        feat1 = next((f for f in svy1.get("features", []) if f.get("id") == point1["pid"]), None)
        if not feat1:
            return make_response({"error": f"Punto 1 non trovato"}, 404)
        
        svy2 = load_survey(point2["sid"])
        feat2 = next((f for f in svy2.get("features", []) if f.get("id") == point2["pid"]), None)
        if not feat2:
            return make_response({"error": f"Punto 2 non trovato"}, 404)
        
        llh1 = _feature_to_llh(feat1)
        llh2 = _feature_to_llh(feat2)
        
        if not llh1 or not llh2:
            return make_response({"error": "Coordinate non valide"}, 400)
    except FileNotFoundError as e:
        return make_response({"error": str(e)}, 404)
    
    # Use first point as ENU origin
    lat0, lon0, alt0 = llh1
    X0, Y0, Z0 = geodetic_to_ecef(lat0, lon0, alt0)
    
    # Convert point 2 to ENU
    lat2, lon2, alt2 = llh2
    X2, Y2, Z2 = geodetic_to_ecef(lat2, lon2, alt2)
    dX, dY, dZ = X2 - X0, Y2 - Y0, Z2 - Z0
    e2, n2, u2 = ecef_delta_to_enu(dX, dY, dZ, lat0, lon0)
    
    # Point 1 is at origin
    e1, n1 = 0.0, 0.0
    
    # Calculate bearing from P1 to P2 in gon
    bearing_p1_to_p2_gon = calculate_bearing_gon(e1, n1, e2, n2)
    
    # Calculate angle at station 1: α₁ = Hz_target - Hz_ref (clockwise/destrorso)
    alpha1_gon = hz1_target - hz1_ref
    
    # Bearing from P1 to unknown point S: bearing_p1_to_p2 + alpha1
    bearing_p1_to_s_gon = bearing_p1_to_p2_gon + alpha1_gon
    
    # Calculate bearing from P2 to P1 in gon
    bearing_p2_to_p1_gon = calculate_bearing_gon(e2, n2, e1, n1)
    
    # Calculate angle at station 2: α₂ = Hz_target - Hz_ref
    alpha2_gon = hz2_target - hz2_ref
    
    # Bearing from P2 to unknown point S: bearing_p2_to_p1 + alpha2
    bearing_p2_to_s_gon = bearing_p2_to_p1_gon + alpha2_gon
    
    # Convert bearings from gon to radians for intersection calculation
    bearing1_rad = gon_to_radians(bearing_p1_to_s_gon)
    bearing2_rad = gon_to_radians(bearing_p2_to_s_gon)
    
    # Calculate intersection using direction vectors
    dx1 = math.sin(bearing1_rad)
    dy1 = math.cos(bearing1_rad)
    dx2 = math.sin(bearing2_rad)
    dy2 = math.cos(bearing2_rad)
    
    # Check if parallel (cross product ~ 0)
    cross = dx1 * dy2 - dy1 * dx2
    if abs(cross) < 1e-9:
        return make_response({"error": "Direzioni parallele - nessuna intersezione"}, 400)
    
    # Solve: P1 + t1*dir1 = P2 + t2*dir2
    de = e2 - e1
    dn = n2 - n1
    
    t1 = (de * dy2 - dn * dx2) / cross
    
    e = e1 + t1 * dx1
    n = n1 + t1 * dy1
    
    # Calculate horizontal distances from stations to intersection point
    dist1 = math.sqrt((e - e1)**2 + (n - n1)**2)
    dist2 = math.sqrt((e - e2)**2 + (n - n2)**2)
    
    # Calculate elevation
    elevation_info = None
    alt = (alt0 + alt2) / 2  # Default: average of station altitudes
    
    # If we have zenith angle data from both stations, calculate elevation
    if v1 is not None and hs1 is not None and ht1 is not None and \
       v2 is not None and hs2 is not None and ht2 is not None:
        
        # Calculate elevation from station 1
        # Zenith angle: 0 gon = zenith, 100 gon = horizontal, 200 gon = nadir
        # Angle from horizontal = V - 100 gon
        # dislivello = d_horizontal * tan(angle_from_horizontal)
        angle_from_horiz_1_rad = gon_to_radians(v1 - 100.0)
        dislivello1 = dist1 * math.tan(angle_from_horiz_1_rad)
        alt_from_st1 = alt0 + hs1 + dislivello1 - ht1
        
        # Calculate elevation from station 2
        angle_from_horiz_2_rad = gon_to_radians(v2 - 100.0)
        dislivello2 = dist2 * math.tan(angle_from_horiz_2_rad)
        alt_from_st2 = alt2 + hs2 + dislivello2 - ht2
        
        # Use average and report residual
        alt = (alt_from_st1 + alt_from_st2) / 2
        residual = alt_from_st1 - alt_from_st2
        
        elevation_info = {
            "alt_from_st1": alt_from_st1,
            "alt_from_st2": alt_from_st2,
            "residual": residual
        }
    elif v1 is not None and hs1 is not None and ht1 is not None:
        # Only station 1 has elevation data
        angle_from_horiz_1_rad = gon_to_radians(v1 - 100.0)
        dislivello1 = dist1 * math.tan(angle_from_horiz_1_rad)
        alt = alt0 + hs1 + dislivello1 - ht1
    elif v2 is not None and hs2 is not None and ht2 is not None:
        # Only station 2 has elevation data
        angle_from_horiz_2_rad = gon_to_radians(v2 - 100.0)
        dislivello2 = dist2 * math.tan(angle_from_horiz_2_rad)
        alt = alt2 + hs2 + dislivello2 - ht2
    
    # Convert to geodetic
    lat, lon, alt_result = enu_to_geodetic(e, n, 0, lat0, lon0, alt0)
    
    result = {
        "e": e,
        "n": n,
        "lat": lat,
        "lon": lon,
        "alt": alt
    }
    
    if elevation_info:
        result["elevation_info"] = elevation_info
    
    return make_response(result, 200)

@bp.route("/cogo/bearing-intersection/save", methods=["POST"])
def cogo_bearing_intersection_save():
    """Save bearing intersection point"""
    data = request.get_json()
    if not data:
        return make_response({"error": "No data"}, 400)
    
    survey_id = data.get("survey_id")
    point_name = sanitize_point_name(data.get("point_name", "INT001"))
    solution = data.get("solution")
    
    if not survey_id or not solution:
        return make_response({"error": "Dati mancanti"}, 400)
    
    try:
        svy = load_survey(survey_id)
    except FileNotFoundError:
        return make_response({"error": "Rilievo non trovato"}, 404)
    
    pid = next_point_id(svy)
    
    feat = point_feature(
        pid=pid,
        lat=solution["lat"],
        lon=solution["lon"],
        altHAE=solution["alt"],
        altMSL=solution["alt"],
        X=None, Y=None, Z=None,
        stats={"mode": 3, "rtk": "cogo-bearing-intersection", "numSV": None,
               "hAcc": None, "vAcc": None, "pAcc": None,
               "gdop": None, "pdop": None, "hdop": None, "vdop": None,
               "ndop": None, "edop": None, "tdop": None,
               "covNN": None, "covEE": None, "covDD": None,
               "covNE": None, "covND": None, "covED": None,
               "relN": None, "relE": None, "relD": None,
               "relsN": None, "relsE": None, "relsD": None,
               "baseline": None, "horiz": None, "bearing": None, "slope": None},
        meta={"name": point_name, "desc": "Punto calcolato con intersezione diretta COGO",
              "start": now_iso(), "end": now_iso(), "duration": 0, "interval": 0, "n_samples": 0}
    )
    
    feat["properties"]["origin"] = "bearing-intersection"
    feat["properties"]["cogo_method"] = "bearing-intersection"
    feat["properties"]["TPV"]["mode"] = "COGO"
    
    svy["features"].append(feat)
    save_survey(survey_id, svy)
    
    return make_response({"success": True, "point_id": pid}, 200)

@bp.route("/cogo/polar", methods=["GET"])
def cogo_polar():
    """Polar point page"""
    opts = list_all_points_options()
    options = "\n".join([f'<option value="{v}">{l}</option>' for (l,v) in opts])
    
    survey_opts = []
    for sid in list_survey_ids():
        survey_opts.append(f'<option value="{sid}">{sid}</option>')
    survey_options = "\n".join(survey_opts)
    
    return render_template('rtk_cogo_polar.html', options=options, survey_options=survey_options)

@bp.route("/cogo/polar/calc", methods=["POST"])
def cogo_polar_calc():
    """
    Calcola punto polare da stazione totale con orientamento su punto GNSS.
    Accetta sia distanza inclinata che distanza orizzontale.

    Parametri:
      station:    {sid, pid}
      ref_point:  {sid, pid}
      hz_ref:     lettura Hz al punto di orientamento (gon)
      hz_target:  lettura Hz al punto incognito (gon)
      dist:       distanza misurata (m) — inclinata o orizzontale secondo dist_type
      dist_type:  "inclined" | "horizontal"
      zenith:     angolo zenitale (gon, 100=orizz.) — obbligatorio se inclined,
                  opzionale se horizontal (serve solo per calcolare quota)
      hs:         altezza strumento (m)
      ht:         altezza prisma (m)

    Calcolo quota:
      - dist inclinata  → d_h = dist*sin(V), dz = dist*cos(V)
      - dist orizzontale con zenitale → dz = d_h * cos(V)/sin(V)  [= d_h * cot(V)]
      - dist orizzontale senza zenitale → quota stazione (dz=0, avviso nel risultato)
    """
    try:
        data = request.get_json()
        if not data:
            return make_response({"error": "No data"}, 400)

        station    = data.get("station")
        ref_point  = data.get("ref_point")
        hz_ref     = data.get("hz_ref")
        hz_target  = data.get("hz_target")
        dist       = data.get("dist")
        dist_type  = data.get("dist_type", "inclined")   # "inclined" | "horizontal"
        zenith     = data.get("zenith")                  # gon, opzionale se horizontal
        hs         = data.get("hs")
        ht         = data.get("ht")

        if not station or not ref_point:
            return make_response({"error": "Seleziona stazione e punto di orientamento"}, 400)
        if hz_ref is None or hz_target is None:
            return make_response({"error": "Inserisci le letture angolari Hz"}, 400)
        if dist is None or dist <= 0:
            return make_response({"error": "Distanza non valida"}, 400)
        if dist_type == "inclined" and zenith is None:
            return make_response({"error": "Per distanza inclinata l'angolo zenitale e' obbligatorio"}, 400)
        if hs is None or ht is None:
            return make_response({"error": "Altezza strumento e prisma obbligatorie"}, 400)

        # Carica i due punti GNSS
        svy_st  = load_survey(station["sid"])
        feat_st = next((f for f in svy_st.get("features", []) if f.get("id") == station["pid"]), None)
        if not feat_st:
            return make_response({"error": f"Punto stazione non trovato (pid={station['pid']})"}, 404)

        svy_ref  = load_survey(ref_point["sid"])
        feat_ref = next((f for f in svy_ref.get("features", []) if f.get("id") == ref_point["pid"]), None)
        if not feat_ref:
            return make_response({"error": f"Punto orientamento non trovato (pid={ref_point['pid']})"}, 404)

        llh_st  = _feature_to_llh(feat_st)
        llh_ref = _feature_to_llh(feat_ref)
        if not llh_st:
            return make_response({"error": "Coordinate punto stazione non valide"}, 400)
        if not llh_ref:
            return make_response({"error": "Coordinate punto orientamento non valide"}, 400)

        lat0, lon0, alt0 = llh_st

        # Converte il punto di orientamento in ENU (origine alla stazione)
        X0,   Y0,   Z0   = geodetic_to_ecef(lat0, lon0, alt0)
        Xref, Yref, Zref = geodetic_to_ecef(llh_ref[0], llh_ref[1], llh_ref[2])
        eRef, nRef, _    = ecef_delta_to_enu(Xref-X0, Yref-Y0, Zref-Z0, lat0, lon0)

        # Azimut geodetico stazione→orientamento (gon, 0=Nord, destrorso)
        az_to_ref_gon = calculate_bearing_gon(0.0, 0.0, eRef, nRef)

        # Angolo relativo destrorso al punto incognito
        delta_hz = hz_target - hz_ref

        # Azimut stazione→punto incognito
        az_to_target_gon = (az_to_ref_gon + delta_hz) % 400.0
        az_rad = gon_to_radians(az_to_target_gon)

        # ── Calcolo distanza orizzontale, dislivello e distanza inclinata ──────
        quota_note = None
        d_inclined_out = None

        if dist_type == "inclined":
            # Distanza inclinata: V obbligatorio
            zenith_rad   = gon_to_radians(zenith)
            d_horizontal = dist * math.sin(zenith_rad)
            dz           = dist * math.cos(zenith_rad)   # positivo se V<100 (verso l'alto)
            d_inclined_out = dist

        else:
            # Distanza orizzontale
            d_horizontal = dist
            if zenith is not None:
                # Zenitale fornito → calcola dislivello: dz = d_h * cos(V)/sin(V)
                zenith_rad = gon_to_radians(zenith)
                sin_v = math.sin(zenith_rad)
                if abs(sin_v) < 1e-9:
                    return make_response({"error": "Angolo zenitale 0 o 200 gon non valido con distanza orizzontale"}, 400)
                dz = d_horizontal * math.cos(zenith_rad) / sin_v
                # Distanza inclinata equivalente per info
                d_inclined_out = math.sqrt(d_horizontal**2 + dz**2)
            else:
                # Nessun zenitale: quota non calcolabile, uso quota stazione
                dz = 0.0
                quota_note = "Zenitale non fornito: quota = quota stazione (dz=0)"

        # Quota punto incognito
        alt_target = alt0 + hs + dz - ht

        # Coordinate ENU del punto incognito
        e_target = d_horizontal * math.sin(az_rad)
        n_target = d_horizontal * math.cos(az_rad)

        # Converti in WGS84
        lat_t, lon_t, _ = enu_to_geodetic(e_target, n_target, 0, lat0, lon0, alt0)

        result = {
            "lat": lat_t, "lon": lon_t, "alt": alt_target,
            "e": e_target, "n": n_target,
            "d_horizontal": d_horizontal,
            "dz": dz,
            "bearing_gon": az_to_target_gon,
            "az_to_ref_gon": az_to_ref_gon,
            "delta_hz": delta_hz % 400.0,
            "dist_type": dist_type,
        }
        if d_inclined_out is not None:
            result["d_inclined"] = d_inclined_out
        if quota_note:
            result["quota_note"] = quota_note

        return make_response(result, 200)

    except FileNotFoundError as ex:
        return make_response({"error": f"Rilievo non trovato: {ex}"}, 404)
    except Exception as ex:
        import traceback; traceback.print_exc()
        return make_response({"error": f"Errore interno: {ex}"}, 500)


@bp.route("/cogo/polar/save", methods=["POST"])
def cogo_polar_save():
    """Save polar point"""
    data = request.get_json()
    if not data:
        return make_response({"error": "No data"}, 400)
    
    survey_id = data.get("survey_id")
    point_name = sanitize_point_name(data.get("point_name", "POL001"))
    solution = data.get("solution")
    
    if not survey_id or not solution:
        return make_response({"error": "Dati mancanti"}, 400)
    
    try:
        svy = load_survey(survey_id)
    except FileNotFoundError:
        return make_response({"error": "Rilievo non trovato"}, 404)
    
    pid = next_point_id(svy)
    
    feat = point_feature(
        pid=pid,
        lat=solution["lat"],
        lon=solution["lon"],
        altHAE=solution["alt"],
        altMSL=solution["alt"],
        X=None, Y=None, Z=None,
        stats={"mode": 3, "rtk": "cogo-polar", "numSV": None,
               "hAcc": None, "vAcc": None, "pAcc": None,
               "gdop": None, "pdop": None, "hdop": None, "vdop": None,
               "ndop": None, "edop": None, "tdop": None,
               "covNN": None, "covEE": None, "covDD": None,
               "covNE": None, "covND": None, "covED": None,
               "relN": None, "relE": None, "relD": None,
               "relsN": None, "relsE": None, "relsD": None,
               "baseline": None, "horiz": None, "bearing": None, "slope": None},
        meta={"name": point_name, "desc": "Punto polare stazione totale COGO",
              "start": now_iso(), "end": now_iso(), "duration": 0, "interval": 0, "n_samples": 0}
    )
    
    feat["properties"]["origin"] = "polar"
    feat["properties"]["cogo_method"] = "polar"
    feat["properties"]["TPV"]["mode"] = "COGO"
    
    svy["features"].append(feat)
    save_survey(survey_id, svy)
    
    return make_response({"success": True, "point_id": pid}, 200)

@bp.route("/cogo/offset", methods=["GET"])
def cogo_offset():
    """Offset from alignment page"""
    opts = list_all_points_options()
    options = "\n".join([f'<option value="{v}">{l}</option>' for (l,v) in opts])
    
    return render_template('rtk_cogo_offset.html', options=options)

@bp.route("/cogo/offset/calc", methods=["POST"])
def cogo_offset_calc():
    """Calculate offset from alignment"""
    data = request.get_json()
    if not data:
        return make_response({"error": "No data"}, 400)
    
    pointA = data.get("pointA")
    pointB = data.get("pointB")
    pointP = data.get("pointP")
    
    if not pointA or not pointB or not pointP:
        return make_response({"error": "Seleziona tutti i punti"}, 400)
    
    # Load all three points
    try:
        svyA = load_survey(pointA["sid"])
        featA = next((f for f in svyA.get("features", []) if f.get("id") == pointA["pid"]), None)
        if not featA:
            return make_response({"error": "Punto A non trovato"}, 404)
        
        svyB = load_survey(pointB["sid"])
        featB = next((f for f in svyB.get("features", []) if f.get("id") == pointB["pid"]), None)
        if not featB:
            return make_response({"error": "Punto B non trovato"}, 404)
        
        svyP = load_survey(pointP["sid"])
        featP = next((f for f in svyP.get("features", []) if f.get("id") == pointP["pid"]), None)
        if not featP:
            return make_response({"error": "Punto P non trovato"}, 404)
        
        llhA = _feature_to_llh(featA)
        llhB = _feature_to_llh(featB)
        llhP = _feature_to_llh(featP)
        
        if not llhA or not llhB or not llhP:
            return make_response({"error": "Coordinate non valide"}, 400)
    except FileNotFoundError as e:
        return make_response({"error": str(e)}, 404)
    
    # Use point A as ENU origin
    lat0, lon0, alt0 = llhA
    X0, Y0, Z0 = geodetic_to_ecef(lat0, lon0, alt0)
    
    # Convert B and P to ENU
    points_llh = [llhB, llhP]
    points_enu = []
    
    for lat, lon, alt in points_llh:
        X, Y, Z = geodetic_to_ecef(lat, lon, alt)
        dX, dY, dZ = X - X0, Y - Y0, Z - Z0
        e, n, u = ecef_delta_to_enu(dX, dY, dZ, lat0, lon0)
        points_enu.append((e, n))
    
    eB, nB = points_enu[0]
    eP, nP = points_enu[1]
    eA, nA = 0.0, 0.0  # A is at origin
    
    # Calculate offset
    result = point_offset_from_line(eA, nA, eB, nB, eP, nP)
    
    if result.get("error"):
        return make_response(result, 400)
    
    return make_response(result, 200)

# ---------- COGO: Perpendicular Projection ----------
@bp.route("/cogo/perpendicular", methods=["GET"])
def cogo_perpendicular():
    """Perpendicular foot projection page"""
    opts = list_all_points_options()
    options = "\n".join([f'<option value="{v}">{l}</option>' for (l,v) in opts])
    
    survey_opts = []
    for sid in list_survey_ids():
        survey_opts.append(f'<option value="{sid}">{sid}</option>')
    survey_options = "\n".join(survey_opts)
    
    return render_template('rtk_cogo_perpendicular.html', options=options, survey_options=survey_options)

@bp.route("/cogo/perpendicular/calc", methods=["POST"])
def cogo_perpendicular_calc():
    """Calculate perpendicular foot projection"""
    data = request.get_json()
    if not data:
        return make_response({"error": "No data"}, 400)
    
    pointA = data.get("pointA")
    pointB = data.get("pointB")
    pointP = data.get("pointP")
    
    if not pointA or not pointB or not pointP:
        return make_response({"error": "Seleziona tutti i punti"}, 400)
    
    # Load all three points
    try:
        svyA = load_survey(pointA["sid"])
        featA = next((f for f in svyA.get("features", []) if f.get("id") == pointA["pid"]), None)
        if not featA:
            return make_response({"error": "Punto A non trovato"}, 404)
        
        svyB = load_survey(pointB["sid"])
        featB = next((f for f in svyB.get("features", []) if str(f.get("id")) == str(pointB["pid"])), None)
        if not featB:
            return make_response({"error": "Punto B non trovato"}, 404)
        
        svyP = load_survey(pointP["sid"])
        featP = next((f for f in svyP.get("features", []) if str(f.get("id")) == str(pointP["pid"])), None)
        if not featP:
            return make_response({"error": "Punto P non trovato"}, 404)
    except Exception as e:
        return make_response({"error": str(e)}, 500)    
        
    # Extract coordinates
    llhA = _feature_to_llh(featA)
    llhB = _feature_to_llh(featB)
    llhP = _feature_to_llh(featP)
    
    if not llhA or not llhB or not llhP:
        return make_response({"error": "Coordinate non valide"}, 400)
    
    lat0, lon0, alt0 = llhA
    
    # Convert to ECEF
    X0, Y0, Z0 = geodetic_to_ecef(lat0, lon0, alt0)
    XB, YB, ZB = geodetic_to_ecef(llhB[0], llhB[1], llhB[2])
    XP, YP, ZP = geodetic_to_ecef(llhP[0], llhP[1], llhP[2])
    
    # Convert to ENU (origin at A)
    dXB, dYB, dZB = XB - X0, YB - Y0, ZB - Z0
    dXP, dYP, dZP = XP - X0, YP - Y0, ZP - Z0
    
    eA, nA, uA = 0.0, 0.0, 0.0  # A is origin
    eB, nB, uB = ecef_delta_to_enu(dXB, dYB, dZB, lat0, lon0)
    eP, nP, uP = ecef_delta_to_enu(dXP, dYP, dZP, lat0, lon0)
    
    # Calculate perpendicular foot
    result = perpendicular_foot_on_line(eA, nA, alt0, eB, nB, llhB[2], eP, nP, llhP[2])
    
    if result.get("error"):
        return make_response(result, 400)
    
    # Convert foot back to WGS84
    foot_lat, foot_lon, foot_alt = enu_to_geodetic(result["foot_e"], result["foot_n"], 0, lat0, lon0, alt0)
    
    # Determine offset side
    offset_side = "DX" if result["offset"] >= 0 else "SX"
    
    return make_response({
        "foot": {
            "e": result["foot_e"],
            "n": result["foot_n"],
            "lat": foot_lat,
            "lon": foot_lon,
            "alt": result["interpolated_alt"]
        },
        "station": result["station"],
        "offset": result["offset"],
        "offset_side": offset_side,
        "distance_to_line": result["distance_to_line"],
        "delta_h": result["delta_h"],
        "interpolated_alt": result["interpolated_alt"],
        "line_length": result["line_length"],
        "out_of_alignment": result["out_of_alignment"]
    }, 200)

@bp.route("/cogo/perpendicular/save", methods=["POST"])
def cogo_perpendicular_save():
    """Save perpendicular foot point"""
    data = request.get_json()
    if not data:
        return make_response({"error": "No data"}, 400)
    
    survey_id = data.get("survey_id")
    point_name = sanitize_point_name(data.get("point_name", "PERP001"))
    foot = data.get("foot")
    
    if not survey_id or not foot:
        return make_response({"error": "Dati mancanti"}, 400)
    
    try:
        svy = load_survey(survey_id)
    except FileNotFoundError:
        return make_response({"error": "Rilievo non trovato"}, 404)
    
    pid = next_point_id(svy)
    
    feat = point_feature(
        pid=pid,
        lat=foot["lat"],
        lon=foot["lon"],
        altHAE=foot["alt"],
        altMSL=foot["alt"],
        X=None, Y=None, Z=None,
        stats={"mode": 3, "rtk": "cogo-perpendicular", "numSV": None,
               "hAcc": None, "vAcc": None, "pAcc": None,
               "gdop": None, "pdop": None, "hdop": None, "vdop": None,
               "ndop": None, "edop": None, "tdop": None,
               "covNN": None, "covEE": None, "covDD": None,
               "covNE": None, "covND": None, "covED": None,
               "relN": None, "relE": None, "relD": None,
               "relsN": None, "relsE": None, "relsD": None,
               "baseline": None, "horiz": None, "bearing": None, "slope": None},
        meta={"name": point_name, "desc": "Piede perpendicolare calcolato con COGO",
              "start": now_iso(), "end": now_iso(), "duration": 0, "interval": 0, "n_samples": 0}
    )
    
    feat["properties"]["origin"] = "perpendicular"
    feat["properties"]["cogo_method"] = "perpendicular"
    feat["properties"]["TPV"]["mode"] = "COGO"
    
    svy["features"].append(feat)
    save_survey(survey_id, svy)
    
    return make_response({"success": True, "point_id": pid}, 200)

# ---------- COGO: Multi-point Alignment ----------
@bp.route("/cogo/alignment", methods=["GET"])
def cogo_alignment():
    """Multi-point alignment analysis page"""
    opts = list_all_points_options()
    options = "\n".join([f'<option value="{v}">{l}</option>' for (l,v) in opts])
    
    return render_template('rtk_cogo_alignment.html', options=options)

@bp.route("/cogo/alignment/calc", methods=["POST"])
def cogo_alignment_calc():
    """Calculate alignment report for multiple points"""
    data = request.get_json()
    if not data:
        return make_response({"error": "No data"}, 400)
    
    pointA = data.get("pointA")
    pointB = data.get("pointB")
    points = data.get("points", [])
    
    if not pointA or not pointB:
        return make_response({"error": "Seleziona i punti dell'allineamento A e B"}, 400)
    
    if not points:
        return make_response({"error": "Seleziona almeno un punto da analizzare"}, 400)
    
    try:
        # Load alignment points
        svyA = load_survey(pointA["sid"])
        featA = next((f for f in svyA.get("features", []) if f.get("id") == pointA["pid"]), None)
        if not featA:
            return make_response({"error": "Punto A non trovato"}, 404)
        
        svyB = load_survey(pointB["sid"])
        featB = next((f for f in svyB.get("features", []) if f.get("id") == pointB["pid"]), None)
        if not featB:
            return make_response({"error": "Punto B non trovato"}, 404)
        
        llhA = _feature_to_llh(featA)
        llhB = _feature_to_llh(featB)
        
        if not llhA or not llhB:
            return make_response({"error": "Coordinate allineamento non valide"}, 400)
        
        # Use A as ENU origin
        lat0, lon0, alt0 = llhA
        X0, Y0, Z0 = geodetic_to_ecef(lat0, lon0, alt0)
        XB, YB, ZB = geodetic_to_ecef(llhB[0], llhB[1], llhB[2])
        
        eA, nA, hA = 0.0, 0.0, alt0
        eB, nB, uB = ecef_delta_to_enu(XB - X0, YB - Y0, ZB - Z0, lat0, lon0)
        hB = llhB[2]
        
        # Calculate alignment bearing
        bearing_gon = calculate_bearing_gon(eA, nA, eB, nB)
        line_length = math.hypot(eB - eA, nB - nA)
        
        # Process each point
        results = []
        for pt in points:
            try:
                svy = load_survey(pt["sid"])
                feat = next((f for f in svy.get("features", []) if f.get("id") == pt["pid"]), None)
                if not feat:
                    continue
                
                llh = _feature_to_llh(feat)
                if not llh:
                    continue
                
                # Convert to ENU
                X, Y, Z = geodetic_to_ecef(llh[0], llh[1], llh[2])
                eP, nP, uP = ecef_delta_to_enu(X - X0, Y - Y0, Z - Z0, lat0, lon0)
                
                # Calculate offset
                offset_result = point_offset_from_line(eA, nA, eB, nB, eP, nP)
                
                if offset_result.get("error"):
                    continue
                
                station = offset_result["station"]
                offset = offset_result["offset"]
                
                # Interpolate altitude
                if station < 0:
                    interp_alt = hA
                elif station > line_length:
                    interp_alt = hB
                else:
                    t = station / line_length
                    interp_alt = hA + t * (hB - hA)
                
                delta_h = llh[2] - interp_alt
                side = "DX" if offset >= 0 else "SX"
                out_of_alignment = (station < 0) or (station > line_length)
                
                point_name = feat.get("properties", {}).get("name", pt["pid"])
                
                results.append({
                    "name": point_name,
                    "station": station,
                    "offset": abs(offset),
                    "side": side,
                    "delta_h": delta_h,
                    "out_of_alignment": out_of_alignment
                })
            except Exception:
                continue
        
        # Sort by station
        results.sort(key=lambda x: x["station"])
        
        nameA = featA.get("properties", {}).get("name", pointA["pid"])
        nameB = featB.get("properties", {}).get("name", pointB["pid"])
        
        return make_response({
            "alignment": {
                "from": nameA,
                "to": nameB,
                "length": line_length,
                "bearing_gon": bearing_gon
            },
            "points": results
        }, 200)
        
    except FileNotFoundError as e:
        return make_response({"error": f"Rilievo non trovato: {e}"}, 404)
    except Exception as e:
        return make_response({"error": str(e)}, 500)

# ---------- COGO: Helmert 2D Transformation ----------
@bp.route("/cogo/helmert", methods=["GET"])
def cogo_helmert():
    """Helmert 2D transformation page"""
    opts = list_all_points_options()
    options = "\n".join([f'<option value="{v}">{l}</option>' for (l,v) in opts])
    
    survey_opts = []
    for sid in list_survey_ids():
        survey_opts.append(f'<option value="{sid}">{sid}</option>')
    survey_options = "\n".join(survey_opts)
    
    return render_template('rtk_cogo_helmert.html', options=options, survey_options=survey_options)

@bp.route("/cogo/helmert/calc", methods=["POST"])
def cogo_helmert_calc():
    """Calculate Helmert transformation parameters and optionally transform points"""
    data = request.get_json()
    if not data:
        return make_response({"error": "No data"}, 400)
    
    pairs = data.get("pairs", [])
    transform_points = data.get("transform_points", [])
    
    if len(pairs) < 2:
        return make_response({"error": "Servono almeno 2 coppie di punti omologhi"}, 400)
    
    try:
        # Load all pairs and convert to ENU
        source_points = []
        target_points = []
        pair_names = []
        
        # Use first source point as origin for source system
        first_src = pairs[0]["source"]
        svySrc0 = load_survey(first_src["sid"])
        featSrc0 = next((f for f in svySrc0.get("features", []) if f.get("id") == first_src["pid"]), None)
        if not featSrc0:
            return make_response({"error": "Primo punto sorgente non trovato"}, 404)
        
        llhSrc0 = _feature_to_llh(featSrc0)
        if not llhSrc0:
            return make_response({"error": "Coordinate primo punto sorgente non valide"}, 400)
        
        lat0_src, lon0_src, alt0_src = llhSrc0
        X0_src, Y0_src, Z0_src = geodetic_to_ecef(lat0_src, lon0_src, alt0_src)
        
        # Use first target point as origin for target system
        first_tgt = pairs[0]["target"]
        svyTgt0 = load_survey(first_tgt["sid"])
        featTgt0 = next((f for f in svyTgt0.get("features", []) if f.get("id") == first_tgt["pid"]), None)
        if not featTgt0:
            return make_response({"error": "Primo punto destinazione non trovato"}, 404)
        
        llhTgt0 = _feature_to_llh(featTgt0)
        if not llhTgt0:
            return make_response({"error": "Coordinate primo punto destinazione non valide"}, 400)
        
        lat0_tgt, lon0_tgt, alt0_tgt = llhTgt0
        X0_tgt, Y0_tgt, Z0_tgt = geodetic_to_ecef(lat0_tgt, lon0_tgt, alt0_tgt)
        
        # Convert all pairs to ENU
        for pair in pairs:
            # Source point
            svySrc = load_survey(pair["source"]["sid"])
            featSrc = next((f for f in svySrc.get("features", []) if f.get("id") == pair["source"]["pid"]), None)
            if not featSrc:
                continue
            
            llhSrc = _feature_to_llh(featSrc)
            if not llhSrc:
                continue
            
            XSrc, YSrc, ZSrc = geodetic_to_ecef(llhSrc[0], llhSrc[1], llhSrc[2])
            eSrc, nSrc, uSrc = ecef_delta_to_enu(XSrc - X0_src, YSrc - Y0_src, ZSrc - Z0_src, lat0_src, lon0_src)
            
            # Target point
            svyTgt = load_survey(pair["target"]["sid"])
            featTgt = next((f for f in svyTgt.get("features", []) if f.get("id") == pair["target"]["pid"]), None)
            if not featTgt:
                continue
            
            llhTgt = _feature_to_llh(featTgt)
            if not llhTgt:
                continue
            
            XTgt, YTgt, ZTgt = geodetic_to_ecef(llhTgt[0], llhTgt[1], llhTgt[2])
            eTgt, nTgt, uTgt = ecef_delta_to_enu(XTgt - X0_tgt, YTgt - Y0_tgt, ZTgt - Z0_tgt, lat0_tgt, lon0_tgt)
            
            source_points.append((eSrc, nSrc))
            target_points.append((eTgt, nTgt))
            
            nameSrc = featSrc.get("properties", {}).get("name", pair["source"]["pid"])
            pair_names.append(nameSrc)
        
        if len(source_points) < 2:
            return make_response({"error": "Impossibile caricare almeno 2 coppie valide"}, 400)
        
        # Calculate Helmert transformation
        helmert_result = helmert_2d_transform(source_points, target_points)
        
        if helmert_result.get("error"):
            return make_response(helmert_result, 400)
        
        # Add names to residuals
        for i, res in enumerate(helmert_result["residuals"]):
            if i < len(pair_names):
                res["name"] = pair_names[i]
            else:
                res["name"] = f"Point_{i+1}"
        
        # Transform additional points if requested
        transformed_points = []
        if transform_points:
            params = helmert_result["parameters"]
            a, b, tE, tN = params["a"], params["b"], params["tE"], params["tN"]
            
            for pt in transform_points:
                try:
                    svySrc = load_survey(pt["sid"])
                    featSrc = next((f for f in svySrc.get("features", []) if f.get("id") == pt["pid"]), None)
                    if not featSrc:
                        continue
                    
                    llhSrc = _feature_to_llh(featSrc)
                    if not llhSrc:
                        continue
                    
                    # Convert to source ENU
                    XSrc, YSrc, ZSrc = geodetic_to_ecef(llhSrc[0], llhSrc[1], llhSrc[2])
                    eSrc, nSrc, uSrc = ecef_delta_to_enu(XSrc - X0_src, YSrc - Y0_src, ZSrc - Z0_src, lat0_src, lon0_src)
                    
                    # Apply Helmert transformation
                    eTgt, nTgt = apply_helmert_2d(eSrc, nSrc, a, b, tE, tN)
                    
                    # Convert back to WGS84 using target origin
                    latTgt, lonTgt, altTgt = enu_to_geodetic(eTgt, nTgt, 0, lat0_tgt, lon0_tgt, alt0_tgt)
                    
                    # Use source altitude (no vertical transformation)
                    altTgt = llhSrc[2]
                    
                    nameSrc = featSrc.get("properties", {}).get("name", pt["pid"])
                    
                    transformed_points.append({
                        "name": nameSrc,
                        "lat_src": llhSrc[0],
                        "lon_src": llhSrc[1],
                        "lat_dst": latTgt,
                        "lon_dst": lonTgt,
                        "alt": altTgt
                    })
                except Exception:
                    continue
        
        return make_response({
            "parameters": helmert_result["parameters"],
            "diagnostics": helmert_result["diagnostics"],
            "residuals": helmert_result["residuals"],
            "transformed_points": transformed_points
        }, 200)
        
    except FileNotFoundError as e:
        return make_response({"error": f"Rilievo non trovato: {e}"}, 404)
    except Exception as e:
        return make_response({"error": str(e)}, 500)

@bp.route("/cogo/helmert/save", methods=["POST"])
def cogo_helmert_save():
    """Save Helmert transformed points"""
    data = request.get_json()
    if not data:
        return make_response({"error": "No data"}, 400)
    
    survey_id = data.get("survey_id")
    points = data.get("points", [])
    
    if not survey_id or not points:
        return make_response({"error": "Dati mancanti"}, 400)
    
    try:
        svy = load_survey(survey_id)
    except FileNotFoundError:
        return make_response({"error": "Rilievo non trovato"}, 404)
    
    saved_ids = []
    
    for pt in points:
        pid = next_point_id(svy)
        point_name = sanitize_point_name(pt.get("name", f"HELM{pid}"))
        
        feat = point_feature(
            pid=pid,
            lat=pt["lat_dst"],
            lon=pt["lon_dst"],
            altHAE=pt["alt"],
            altMSL=pt["alt"],
            X=None, Y=None, Z=None,
            stats={"mode": 3, "rtk": "cogo-helmert", "numSV": None,
                   "hAcc": None, "vAcc": None, "pAcc": None,
                   "gdop": None, "pdop": None, "hdop": None, "vdop": None,
                   "ndop": None, "edop": None, "tdop": None,
                   "covNN": None, "covEE": None, "covDD": None,
                   "covNE": None, "covND": None, "covED": None,
                   "relN": None, "relE": None, "relD": None,
                   "relsN": None, "relsE": None, "relsD": None,
                   "baseline": None, "horiz": None, "bearing": None, "slope": None},
            meta={"name": point_name, "desc": "Punto trasformato con Helmert 2D COGO",
                  "start": now_iso(), "end": now_iso(), "duration": 0, "interval": 0, "n_samples": 0}
        )
        
        feat["properties"]["origin"] = "helmert"
        feat["properties"]["cogo_method"] = "helmert"
        feat["properties"]["TPV"]["mode"] = "COGO"
        
        svy["features"].append(feat)
        saved_ids.append(pid)
    
    save_survey(survey_id, svy)
    
    return make_response({"success": True, "point_ids": saved_ids}, 200)
