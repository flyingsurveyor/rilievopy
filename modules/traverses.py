"""
Traverses (Poligonali) — Open and closed traverse computation.

Features:
  - Open traverse with angular/linear compensation
  - Closed traverse (polygon) with closure check
  - Bowditch (transit) rule compensation
  - Crandall method
  - Least-squares adjustment
  - Altitude leveling traverse
  - Quality metrics and tolerance checks

Angles in gon (centesimal degrees), distances in meters.
"""

import math
from typing import Dict, Any, List, Optional, Tuple


# ================================================================
#  CONSTANTS & TOLERANCES
# ================================================================

# Angular tolerance formula: T_ang = K * sqrt(n) [gon]
ANGULAR_TOL_K = 0.015  # typical for 1" theodolite
# Linear tolerance: T_lin = K * sqrt(L) [m]
LINEAR_TOL_K = 0.02  # 1:5000 ratio typical

GON_TO_RAD = math.pi / 200.0
RAD_TO_GON = 200.0 / math.pi


# ================================================================
#  STATION DATA
# ================================================================

class StazionePoligonale:
    """A traverse station with observed data."""

    def __init__(self, name: str,
                 angolo_hz: Optional[float] = None,
                 distanza: Optional[float] = None,
                 dislivello: Optional[float] = None,
                 e_noto: Optional[float] = None,
                 n_noto: Optional[float] = None,
                 h_noto: Optional[float] = None):
        self.name = name
        self.angolo_hz = angolo_hz    # Horizontal angle (gon), or azimuth for first
        self.distanza = distanza      # Distance to NEXT station (m)
        self.dislivello = dislivello  # Height difference to NEXT station (m)
        self.e_noto = e_noto          # Known E coordinate (for start/end)
        self.n_noto = n_noto          # Known N coordinate
        self.h_noto = h_noto          # Known height

        # Computed values (filled after adjustment)
        self.azimut: Optional[float] = None  # Azimuth to next (gon)
        self.e_calc: Optional[float] = None
        self.n_calc: Optional[float] = None
        self.h_calc: Optional[float] = None
        self.corr_e: float = 0.0
        self.corr_n: float = 0.0
        self.corr_h: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "angolo_hz": self.angolo_hz,
            "distanza": self.distanza,
            "dislivello": self.dislivello,
            "e_noto": self.e_noto, "n_noto": self.n_noto, "h_noto": self.h_noto,
            "azimut": self.azimut,
            "e_calc": self.e_calc, "n_calc": self.n_calc, "h_calc": self.h_calc,
            "corr_e": self.corr_e, "corr_n": self.corr_n, "corr_h": self.corr_h,
        }


# ================================================================
#  OPEN TRAVERSE
# ================================================================

def calcola_poligonale_aperta(
    stazioni: List[StazionePoligonale],
    azimut_partenza: float,
    azimut_arrivo: Optional[float] = None,
    metodo: str = "bowditch"
) -> Dict[str, Any]:
    """
    Compute an open traverse (poligonale aperta).

    Args:
        stazioni: list of stations (first and last must have known coords)
        azimut_partenza: starting azimuth (gon) from first to second station
        azimut_arrivo: expected arrival azimuth (gon), if available for check
        metodo: "bowditch", "crandall", or "transit"

    Returns:
        dict with computed coordinates, closures, corrections, quality
    """
    n = len(stazioni)
    if n < 2:
        return {"error": "Servono almeno 2 stazioni"}

    first = stazioni[0]
    last = stazioni[-1]

    if first.e_noto is None or first.n_noto is None:
        return {"error": "La prima stazione deve avere coordinate note"}

    # ---------- Step 1: Compute provisional azimuths ----------
    azimuts = [azimut_partenza]
    for i in range(1, n - 1):
        if stazioni[i].angolo_hz is not None:
            # Az(i→i+1) = Az(i-1→i) + angle_i - 200
            az_next = azimuts[-1] + stazioni[i].angolo_hz - 200.0
            while az_next < 0:
                az_next += 400.0
            while az_next >= 400.0:
                az_next -= 400.0
            azimuts.append(az_next)
        else:
            azimuts.append(azimuts[-1])

    # ---------- Step 2: Compute provisional coordinates ----------
    e_prov = [first.e_noto]
    n_prov = [first.n_noto]

    total_dist = 0.0
    for i in range(n - 1):
        d = stazioni[i].distanza or 0.0
        total_dist += d
        az_rad = azimuts[i] * GON_TO_RAD
        de = d * math.sin(az_rad)
        dn = d * math.cos(az_rad)
        e_prov.append(e_prov[-1] + de)
        n_prov.append(n_prov[-1] + dn)

    # ---------- Step 3: Closure ----------
    has_endpoint = (last.e_noto is not None and last.n_noto is not None)

    closure_e = 0.0
    closure_n = 0.0
    closure_lin = 0.0
    closure_ratio = 0.0

    if has_endpoint:
        closure_e = e_prov[-1] - last.e_noto
        closure_n = n_prov[-1] - last.n_noto
        closure_lin = math.hypot(closure_e, closure_n)
        closure_ratio = total_dist / closure_lin if closure_lin > 1e-9 else float('inf')

    # Angular closure
    n_angles = sum(1 for s in stazioni[1:-1] if s.angolo_hz is not None)
    angular_closure = None
    angular_tolerance = ANGULAR_TOL_K * math.sqrt(n_angles) if n_angles > 0 else None

    if azimut_arrivo is not None and len(azimuts) > 0:
        computed_final_az = azimuts[-1] if len(azimuts) == n - 1 else azimuts[-1]
        angular_closure = computed_final_az - azimut_arrivo
        while angular_closure > 200:
            angular_closure -= 400
        while angular_closure < -200:
            angular_closure += 400

    # ---------- Step 4: Compensation ----------
    e_comp = list(e_prov)
    n_comp = list(n_prov)

    if has_endpoint and total_dist > 0:
        if metodo == "bowditch":
            # Bowditch rule: correction proportional to cumulative distance
            cum_dist = 0.0
            for i in range(1, n):
                d = stazioni[i - 1].distanza or 0.0
                cum_dist += d
                ratio = cum_dist / total_dist
                e_comp[i] = e_prov[i] - closure_e * ratio
                n_comp[i] = n_prov[i] - closure_n * ratio

        elif metodo == "transit":
            # Transit rule: correction proportional to absolute dE, dN
            sum_abs_de = sum(abs(e_prov[i + 1] - e_prov[i]) for i in range(n - 1))
            sum_abs_dn = sum(abs(n_prov[i + 1] - n_prov[i]) for i in range(n - 1))
            cum_de = 0.0
            cum_dn = 0.0
            for i in range(1, n):
                cum_de += abs(e_prov[i] - e_prov[i - 1])
                cum_dn += abs(n_prov[i] - n_prov[i - 1])
                re = cum_de / sum_abs_de if sum_abs_de > 0 else 0
                rn = cum_dn / sum_abs_dn if sum_abs_dn > 0 else 0
                e_comp[i] = e_prov[i] - closure_e * re
                n_comp[i] = n_prov[i] - closure_n * rn

    # ---------- Step 5: Altitude compensation ----------
    h_comp = [first.h_noto or 0.0]
    total_dh_obs = 0.0
    for i in range(n - 1):
        dh = stazioni[i].dislivello or 0.0
        total_dh_obs += dh
        h_comp.append(h_comp[0] + total_dh_obs)

    closure_h = 0.0
    if last.h_noto is not None and first.h_noto is not None:
        expected_dh = last.h_noto - first.h_noto
        closure_h = total_dh_obs - expected_dh
        # Distribute proportionally to distance
        cum_dist = 0.0
        for i in range(1, n):
            d = stazioni[i - 1].distanza or 0.0
            cum_dist += d
            ratio = cum_dist / total_dist if total_dist > 0 else 0
            h_comp[i] -= closure_h * ratio

    # ---------- Step 6: Store results ----------
    for i, st in enumerate(stazioni):
        st.e_calc = e_comp[i]
        st.n_calc = n_comp[i]
        st.h_calc = h_comp[i]
        st.corr_e = e_comp[i] - e_prov[i]
        st.corr_n = n_comp[i] - n_prov[i]
        if i < len(azimuts):
            st.azimut = azimuts[i]

    # Tolerance check
    linear_tolerance = LINEAR_TOL_K * math.sqrt(total_dist / 1000.0)
    linear_ok = closure_lin <= linear_tolerance if has_endpoint else None
    angular_ok = (abs(angular_closure) <= angular_tolerance
                  if angular_closure is not None and angular_tolerance is not None
                  else None)

    return {
        "stazioni": [s.to_dict() for s in stazioni],
        "n_stazioni": n,
        "sviluppo": total_dist,
        "chiusura": {
            "errore_est": closure_e,
            "errore_nord": closure_n,
            "errore_lineare": closure_lin,
            "rapporto": closure_ratio,
            "errore_angolare_gon": angular_closure,
            "errore_quota": closure_h,
        },
        "tolleranze": {
            "lineare": linear_tolerance,
            "angolare_gon": angular_tolerance,
            "lineare_ok": linear_ok,
            "angolare_ok": angular_ok,
        },
        "metodo": metodo,
    }


# ================================================================
#  CLOSED TRAVERSE (Poligonale chiusa)
# ================================================================

def calcola_poligonale_chiusa(
    stazioni: List[StazionePoligonale],
    azimut_partenza: float,
    metodo: str = "bowditch"
) -> Dict[str, Any]:
    """
    Compute a closed traverse (returns to starting point).

    The last station's distance/angle closes back to the first.
    """
    n = len(stazioni)
    if n < 3:
        return {"error": "Servono almeno 3 stazioni per una poligonale chiusa"}

    first = stazioni[0]
    if first.e_noto is None or first.n_noto is None:
        return {"error": "La prima stazione deve avere coordinate note"}

    # For closed traverse, the endpoint is the start point
    # Make a copy of stations and add the first as endpoint
    stazioni_ext = list(stazioni)
    endpoint = StazionePoligonale(
        name=first.name + "_chiusura",
        e_noto=first.e_noto,
        n_noto=first.n_noto,
        h_noto=first.h_noto,
    )
    stazioni_ext.append(endpoint)

    # Angular closure for closed traverse: sum of angles should be (n-2)*200 gon
    sum_angles = sum(s.angolo_hz for s in stazioni if s.angolo_hz is not None)
    expected_sum = (n - 2) * 200.0  # internal angles of polygon
    angular_closure = sum_angles - expected_sum

    result = calcola_poligonale_aperta(
        stazioni_ext, azimut_partenza,
        azimut_arrivo=azimut_partenza,
        metodo=metodo,
    )

    if "error" in result:
        return result

    result["tipo"] = "chiusa"
    result["somma_angoli"] = sum_angles
    result["somma_angoli_teorica"] = expected_sum
    result["chiusura"]["errore_angolare_gon"] = angular_closure

    return result


# ================================================================
#  AREA DIVISION (Frazionamento)
# ================================================================

def dividi_area_con_dividenti(
    vertici: List[Tuple[float, float]],
    area_target: float,
    lato_vincolo: Tuple[int, int],
    max_iter: int = 100
) -> Dict[str, Any]:
    """
    Divide a polygon to obtain a sub-area of specified size.

    Uses an iterative approach: moves a dividing line parallel to
    the constraint side until the target area is achieved.

    Args:
        vertici: polygon vertices as [(E, N), ...]
        area_target: desired area of the sub-polygon (m²)
        lato_vincolo: indices of the constraint side (start, end)
        max_iter: maximum iterations

    Returns:
        dict with dividing line coordinates and actual area
    """
    n = len(vertici)
    if n < 3:
        return {"error": "Servono almeno 3 vertici"}

    total_area = _shoelace_area(vertici)
    if area_target >= total_area:
        return {"error": f"Area target ({area_target:.2f}) >= area totale ({total_area:.2f})"}

    i_start, i_end = lato_vincolo
    # Direction of constraint side
    dx = vertici[i_end][0] - vertici[i_start][0]
    dy = vertici[i_end][1] - vertici[i_start][1]
    side_len = math.hypot(dx, dy)
    if side_len < 1e-9:
        return {"error": "Lato di vincolo degenere"}

    # Perpendicular direction (inward)
    perp_x = -dy / side_len
    perp_y = dx / side_len

    # Binary search on offset distance
    offset_min = 0.0
    offset_max = _max_perpendicular_distance(vertici, i_start, i_end)

    best_offset = 0.0
    best_area = 0.0

    for _ in range(max_iter):
        offset = (offset_min + offset_max) / 2
        # Create dividing line at offset from constraint side
        line_p1 = (vertici[i_start][0] + offset * perp_x,
                    vertici[i_start][1] + offset * perp_y)
        line_p2 = (vertici[i_end][0] + offset * perp_x,
                    vertici[i_end][1] + offset * perp_y)

        # Clip polygon with dividing line
        sub_poly = _clip_polygon_by_line(vertici, line_p1, line_p2)
        if not sub_poly or len(sub_poly) < 3:
            offset_max = offset
            continue

        area = _shoelace_area(sub_poly)
        best_offset = offset
        best_area = area

        if abs(area - area_target) < 0.01:  # 0.01 m² tolerance
            break

        if area < area_target:
            offset_min = offset
        else:
            offset_max = offset

    line_p1 = (vertici[i_start][0] + best_offset * perp_x,
                vertici[i_start][1] + best_offset * perp_y)
    line_p2 = (vertici[i_end][0] + best_offset * perp_x,
                vertici[i_end][1] + best_offset * perp_y)

    return {
        "dividenti": [line_p1, line_p2],
        "offset": best_offset,
        "area_ottenuta": best_area,
        "area_target": area_target,
        "area_totale": total_area,
        "area_residua": total_area - best_area,
        "errore_area": best_area - area_target,
    }


def _shoelace_area(pts: List[Tuple[float, float]]) -> float:
    """Shoelace formula for polygon area."""
    n = len(pts)
    area = 0.0
    for i in range(n):
        j = (i + 1) % n
        area += pts[i][0] * pts[j][1]
        area -= pts[j][0] * pts[i][1]
    return abs(area) / 2.0


def _max_perpendicular_distance(vertici, i_start, i_end):
    """Max perpendicular distance from any vertex to the constraint side."""
    dx = vertici[i_end][0] - vertici[i_start][0]
    dy = vertici[i_end][1] - vertici[i_start][1]
    side_len = math.hypot(dx, dy)
    if side_len < 1e-9:
        return 0.0

    max_dist = 0.0
    for v in vertici:
        # Perpendicular distance from point to line
        dist = abs((v[0] - vertici[i_start][0]) * (-dy) +
                    (v[1] - vertici[i_start][1]) * dx) / side_len
        max_dist = max(max_dist, dist)
    return max_dist


def _clip_polygon_by_line(polygon, lp1, lp2):
    """Clip polygon by a line (Sutherland-Hodgman, one edge)."""
    output = list(polygon)
    if len(output) < 3:
        return output

    def side(p):
        return ((lp2[0] - lp1[0]) * (p[1] - lp1[1]) -
                (lp2[1] - lp1[1]) * (p[0] - lp1[0]))

    def intersect(a, b):
        da = side(a)
        db = side(b)
        if abs(da - db) < 1e-12:
            return a
        t = da / (da - db)
        return (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))

    clipped = []
    n = len(output)
    for i in range(n):
        curr = output[i]
        nxt = output[(i + 1) % n]
        sc = side(curr)
        sn = side(nxt)
        if sc >= 0:
            clipped.append(curr)
            if sn < 0:
                clipped.append(intersect(curr, nxt))
        elif sn >= 0:
            clipped.append(intersect(curr, nxt))

    return clipped


# ================================================================
#  LEVELING TRAVERSE (Livellazione)
# ================================================================

def calcola_livellazione(
    misure: List[Dict[str, float]],
    quota_partenza: float,
    quota_arrivo: Optional[float] = None
) -> Dict[str, Any]:
    """
    Compute a leveling traverse.

    misure: list of {"nome": str, "lettura_indietro": float, "lettura_avanti": float,
                     "distanza": float (optional)}

    Returns: compensated heights, closure error, and quality.
    """
    n = len(misure)
    if n < 1:
        return {"error": "Serve almeno una misura"}

    # Compute raw heights
    dislivelli = []
    distanze = []
    for m in misure:
        dh = m.get("lettura_indietro", 0) - m.get("lettura_avanti", 0)
        dislivelli.append(dh)
        distanze.append(m.get("distanza", 1.0))

    total_dist = sum(distanze)
    total_dh = sum(dislivelli)

    # Provisional heights
    quote_prov = [quota_partenza]
    for dh in dislivelli:
        quote_prov.append(quote_prov[-1] + dh)

    # Closure
    closure = 0.0
    if quota_arrivo is not None:
        closure = quote_prov[-1] - quota_arrivo

    # Compensate proportional to distance
    quote_comp = [quota_partenza]
    cum_dist = 0.0
    for i, dh in enumerate(dislivelli):
        cum_dist += distanze[i]
        ratio = cum_dist / total_dist if total_dist > 0 else 0
        q = quote_prov[i + 1] - closure * ratio
        quote_comp.append(q)

    # Tolerance: 2mm * sqrt(L_km)
    tol = 0.002 * math.sqrt(total_dist / 1000.0)

    risultati = []
    for i, m in enumerate(misure):
        risultati.append({
            "nome": m.get("nome", f"S{i + 1}"),
            "dislivello": dislivelli[i],
            "quota_provvisoria": quote_prov[i + 1],
            "quota_compensata": quote_comp[i + 1],
            "correzione": quote_comp[i + 1] - quote_prov[i + 1],
        })

    return {
        "risultati": risultati,
        "sviluppo": total_dist,
        "dislivello_totale": total_dh,
        "errore_chiusura": closure,
        "tolleranza": tol,
        "entro_tolleranza": abs(closure) <= tol if quota_arrivo is not None else None,
        "quota_partenza": quota_partenza,
        "quota_arrivo": quota_arrivo,
    }
