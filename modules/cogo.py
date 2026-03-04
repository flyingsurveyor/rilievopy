"""
COGO (Coordinate Geometry) computation functions.
Trilateration, bearing/distance intersections, polar, offset,
perpendicular projection, alignment, Helmert 2D transformation,
area/perimeter calculation.
"""

import math
from typing import Dict, Any, List, Optional, Tuple


# ---------- Helmert thresholds ----------
PPM_CONVERSION = 1_000_000.0
HELMERT_SCALE_WARNING_PPM = 100
HELMERT_SIGMA0_WARNING = 0.050
HELMERT_RESIDUAL_SIGMA_MULTIPLIER = 3


# ---------- Trilateration ----------
def trilaterate_2d(stations: List[Tuple[float, float]],
                   distances: List[float]) -> Dict[str, Any]:
    """
    2D trilateration with least-squares refinement.
    stations: list of (E, N) coordinates
    distances: list of horizontal distances
    """
    n = len(stations)
    if n < 2:
        return {"error": "Servono almeno 2 stazioni"}
    if n != len(distances):
        return {"error": "Numero di stazioni e distanze non corrispondente"}

    e1, n1 = stations[0]
    e2, n2 = stations[1]
    d1, d2 = distances[0], distances[1]

    dx = e2 - e1
    dy = n2 - n1
    baseline = math.hypot(dx, dy)

    if baseline < 1e-6:
        return {"error": "Le prime due stazioni sono coincidenti"}
    if d1 + d2 < baseline or abs(d1 - d2) > baseline:
        return {"error": "Impossibile: le distanze non formano un triangolo valido"}

    a = (d1 * d1 - d2 * d2 + baseline * baseline) / (2 * baseline)
    h_sq = d1 * d1 - a * a
    if h_sq < 0:
        return {"error": "Errore geometrico: h² negativo"}
    h = math.sqrt(h_sq)

    ux = dx / baseline
    uy = dy / baseline
    px = e1 + a * ux
    py = n1 + a * uy
    perp_x = -uy
    perp_y = ux

    sol1 = (px + h * perp_x, py + h * perp_y)
    sol2 = (px - h * perp_x, py - h * perp_y)

    if n == 2:
        return {
            "solutions": [sol1, sol2],
            "ambiguous": True,
            "message": "Due soluzioni possibili (destra/sinistra)"
        }

    # With 3+ stations: use third circle to discriminate
    e3, n3 = stations[2]
    d3 = distances[2]
    dist1_to_p3 = math.hypot(sol1[0] - e3, sol1[1] - n3)
    dist2_to_p3 = math.hypot(sol2[0] - e3, sol2[1] - n3)
    err1 = abs(dist1_to_p3 - d3)
    err2 = abs(dist2_to_p3 - d3)
    initial = sol1 if err1 < err2 else sol2

    # Least-squares refinement (Newton-Gauss)
    e, n_coord = initial
    for _iteration in range(10):
        A = []
        b = []
        for i in range(n):
            ei, ni = stations[i]
            di = distances[i]
            calc_dist = math.hypot(e - ei, n_coord - ni)
            if calc_dist < 1e-9:
                continue
            residual = calc_dist - di
            de = (e - ei) / calc_dist
            dn = (n_coord - ni) / calc_dist
            A.append([de, dn])
            b.append(-residual)
        if len(A) < 2:
            break
        try:
            AtA_00 = sum(row[0] * row[0] for row in A)
            AtA_01 = sum(row[0] * row[1] for row in A)
            AtA_11 = sum(row[1] * row[1] for row in A)
            Atb_0 = sum(A[i][0] * b[i] for i in range(len(A)))
            Atb_1 = sum(A[i][1] * b[i] for i in range(len(A)))
            det = AtA_00 * AtA_11 - AtA_01 * AtA_01
            if abs(det) < 1e-12:
                break
            delta_e = (AtA_11 * Atb_0 - AtA_01 * Atb_1) / det
            delta_n = (-AtA_01 * Atb_0 + AtA_00 * Atb_1) / det
            e += delta_e
            n_coord += delta_n
            if abs(delta_e) < 1e-6 and abs(delta_n) < 1e-6:
                break
        except (ZeroDivisionError, ValueError):
            break

    # Final residuals
    residuals = []
    for i in range(n):
        ei, ni = stations[i]
        di = distances[i]
        calc_dist = math.hypot(e - ei, n_coord - ni)
        residuals.append(calc_dist - di)
    rms = math.sqrt(sum(r * r for r in residuals) / len(residuals))

    if rms < 0.010:
        quality, quality_color = "excellent", "green"
    elif rms < 0.025:
        quality, quality_color = "good", "yellow"
    elif rms < 0.050:
        quality, quality_color = "acceptable", "orange"
    else:
        quality, quality_color = "check", "red"

    return {
        "solution": (e, n_coord),
        "residuals": residuals,
        "rms": rms,
        "quality": quality,
        "quality_color": quality_color,
        "ambiguous": False
    }


# ---------- Angle conversions ----------
def gon_to_radians(gon: float) -> float:
    """400 gon = 2π radians."""
    return gon * math.pi / 200.0


def radians_to_gon(rad: float) -> float:
    """2π radians = 400 gon."""
    return rad * 200.0 / math.pi


def gon_to_dms(gon: float) -> str:
    """Convert gon (centesimal degrees) to sexagesimal DMS string."""
    degrees = gon * 360.0 / 400.0
    sign = "-" if degrees < 0 else ""
    degrees = abs(degrees)
    d = int(degrees)
    m_frac = (degrees - d) * 60.0
    m = int(m_frac)
    s = (m_frac - m) * 60.0
    return f'{sign}{d}° {m}\' {s:.2f}"'


# ---------- Bearing ----------
def calculate_bearing_gon(e1: float, n1: float, e2: float, n2: float) -> float:
    """Bearing from point 1 to point 2 in gon (0 = North, clockwise)."""
    de = e2 - e1
    dn = n2 - n1
    bearing_rad = math.atan2(de, dn)
    bearing_gon = radians_to_gon(bearing_rad)
    while bearing_gon < 0:
        bearing_gon += 400.0
    while bearing_gon >= 400.0:
        bearing_gon -= 400.0
    return bearing_gon


# ---------- Bearing–Bearing Intersection ----------
def bearing_bearing_intersection(e1: float, n1: float, az1: float,
                                 e2: float, n2: float, az2: float) -> Optional[Tuple[float, float]]:
    """
    Intersection from two points and two bearings (degrees from North).
    Returns (E, N) or None if parallel.
    """
    a1 = math.radians(az1)
    a2 = math.radians(az2)
    dx1, dy1 = math.sin(a1), math.cos(a1)
    dx2, dy2 = math.sin(a2), math.cos(a2)
    cross = dx1 * dy2 - dy1 * dx2
    if abs(cross) < 1e-9:
        return None
    de = e2 - e1
    dn = n2 - n1
    t1 = (de * dy2 - dn * dx2) / cross
    return e1 + t1 * dx1, n1 + t1 * dy1


# ---------- Polar ----------
def polar_to_point(e0: float, n0: float, azimuth_deg: float,
                   distance: float) -> Tuple[float, float]:
    """Point from polar coordinates (azimuth from North, horizontal distance)."""
    az_rad = math.radians(azimuth_deg)
    return e0 + distance * math.sin(az_rad), n0 + distance * math.cos(az_rad)


# ---------- Point Offset from Line ----------
def point_offset_from_line(eA: float, nA: float, eB: float, nB: float,
                           eP: float, nP: float) -> Dict[str, float]:
    """Station and offset of point P from line A→B."""
    dx = eB - eA
    dy = nB - nA
    line_length = math.hypot(dx, dy)
    if line_length < 1e-9:
        return {"error": "Points A and B are coincident"}
    ux = dx / line_length
    uy = dy / line_length
    dpx = eP - eA
    dpy = nP - nA
    station = dpx * ux + dpy * uy
    offset = dpx * uy - dpy * ux
    return {"station": station, "offset": offset, "line_length": line_length}


# ---------- Distance–Distance Intersection ----------
def distance_distance_intersection(e1: float, n1: float, d1: float,
                                   e2: float, n2: float, d2: float) -> Optional[List[Tuple[float, float]]]:
    """Intersection from two points and two distances. Returns 0–2 solutions."""
    dx = e2 - e1
    dy = n2 - n1
    baseline = math.hypot(dx, dy)
    if baseline < 1e-6:
        return None
    if d1 + d2 < baseline or abs(d1 - d2) > baseline:
        return []
    a = (d1 * d1 - d2 * d2 + baseline * baseline) / (2 * baseline)
    h_sq = d1 * d1 - a * a
    if h_sq < 0:
        return []
    h = math.sqrt(h_sq)
    ux = dx / baseline
    uy = dy / baseline
    px = e1 + a * ux
    py = n1 + a * uy
    if h < 1e-9:
        return [(px, py)]
    perp_x, perp_y = -uy, ux
    return [(px + h * perp_x, py + h * perp_y),
            (px - h * perp_x, py - h * perp_y)]


# ---------- Perpendicular Foot ----------
def perpendicular_foot_on_line(eA: float, nA: float, hA: float,
                               eB: float, nB: float, hB: float,
                               eP: float, nP: float, hP: float) -> Dict[str, Any]:
    """Foot of perpendicular from P to line A→B with altitude interpolation."""
    dx = eB - eA
    dy = nB - nA
    line_length = math.hypot(dx, dy)
    if line_length < 1e-9:
        return {"error": "Points A and B are coincident"}

    ux = dx / line_length
    uy = dy / line_length
    dpx = eP - eA
    dpy = nP - nA
    station = dpx * ux + dpy * uy
    offset = dpx * uy - dpy * ux
    foot_e = eA + station * ux
    foot_n = nA + station * uy
    distance_to_line = abs(offset)

    if station < 0:
        interpolated_alt = hA
    elif station > line_length:
        interpolated_alt = hB
    else:
        t = station / line_length
        interpolated_alt = hA + t * (hB - hA)

    return {
        "foot_e": foot_e,
        "foot_n": foot_n,
        "station": station,
        "offset": offset,
        "distance_to_line": distance_to_line,
        "interpolated_alt": interpolated_alt,
        "delta_h": hP - interpolated_alt,
        "line_length": line_length,
        "out_of_alignment": (station < 0) or (station > line_length)
    }


# ---------- Matrix inversion (4×4) ----------
def invert_4x4_symmetric(A: List[List[float]]) -> Optional[List[List[float]]]:
    """Invert a 4×4 matrix via Gaussian elimination with partial pivoting."""
    n = 4
    aug = [row[:] + [0.0] * n for row in A]
    for i in range(n):
        aug[i][n + i] = 1.0

    for col in range(n):
        max_row = col
        for row in range(col + 1, n):
            if abs(aug[row][col]) > abs(aug[max_row][col]):
                max_row = row
        aug[col], aug[max_row] = aug[max_row], aug[col]
        if abs(aug[col][col]) < 1e-12:
            return None
        for row in range(col + 1, n):
            factor = aug[row][col] / aug[col][col]
            for j in range(col, 2 * n):
                aug[row][j] -= factor * aug[col][j]

    for col in range(n - 1, -1, -1):
        pivot = aug[col][col]
        for j in range(2 * n):
            aug[col][j] /= pivot
        for row in range(col):
            factor = aug[row][col]
            for j in range(2 * n):
                aug[row][j] -= factor * aug[col][j]

    return [[aug[i][n + j] for j in range(n)] for i in range(n)]


# ---------- Helmert 2D ----------
def helmert_2d_transform(source_points: List[Tuple[float, float]],
                         target_points: List[Tuple[float, float]]) -> Dict[str, Any]:
    """
    4-parameter 2D Helmert similarity transformation (least squares).
    Model: E' = a·E − b·N + tE  /  N' = b·E + a·N + tN
    """
    n = len(source_points)
    if n < 2:
        return {"error": "Servono almeno 2 coppie di punti omologhi"}
    if n != len(target_points):
        return {"error": "Numero di punti sorgente e destinazione diverso"}

    A = []
    b_obs = []
    for i in range(n):
        Ei, Ni = source_points[i]
        Epi, Npi = target_points[i]
        A.append([Ei, -Ni, 1.0, 0.0])
        b_obs.append(Epi)
        A.append([Ni, Ei, 0.0, 1.0])
        b_obs.append(Npi)

    AtA = [[0.0] * 4 for _ in range(4)]
    for i in range(4):
        for j in range(4):
            AtA[i][j] = sum(A[k][i] * A[k][j] for k in range(len(A)))
    Atb = [sum(A[k][i] * b_obs[k] for k in range(len(A))) for i in range(4)]

    AtA_inv = invert_4x4_symmetric(AtA)
    if AtA_inv is None:
        return {"error": "Matrice singolare - impossibile invertire (punti collineari?)"}

    params = [sum(AtA_inv[i][j] * Atb[j] for j in range(4)) for i in range(4)]
    a, b_param, tE, tN = params

    scale = math.sqrt(a * a + b_param * b_param)
    rotation_rad = math.atan2(b_param, a)
    rotation_gon = radians_to_gon(rotation_rad)
    while rotation_gon < 0:
        rotation_gon += 400.0
    while rotation_gon >= 400.0:
        rotation_gon -= 400.0

    scale_ppm = (scale - 1.0) * PPM_CONVERSION
    rotation_dms = gon_to_dms(rotation_gon)

    residuals = []
    v_squared_sum = 0.0
    for i in range(n):
        Ei, Ni = source_points[i]
        Epi_obs, Npi_obs = target_points[i]
        Epi_calc = a * Ei - b_param * Ni + tE
        Npi_calc = b_param * Ei + a * Ni + tN
        vE = Epi_calc - Epi_obs
        vN = Npi_calc - Npi_obs
        v_abs = math.sqrt(vE * vE + vN * vN)
        residuals.append({"vE": vE, "vN": vN, "v_abs": v_abs})
        v_squared_sum += vE * vE + vN * vN

    redundancy = 2 * n - 4
    sigma0 = math.sqrt(v_squared_sum / redundancy) if redundancy > 0 else 0.0
    rmse = math.sqrt(v_squared_sum / n)

    warnings = []
    if n < 3:
        warnings.append("Meno di 3 coppie - nessuna ridondanza, soluzione esatta")
    if abs(scale_ppm) > HELMERT_SCALE_WARNING_PPM:
        warnings.append(f"Scala devia da 1 di più di {HELMERT_SCALE_WARNING_PPM} ppm ({scale_ppm:.1f} ppm)")
    if sigma0 > HELMERT_SIGMA0_WARNING and redundancy > 0:
        warnings.append(f"σ₀ elevato ({sigma0:.3f} m) - controllare i punti")

    for res in residuals:
        res["flagged"] = (redundancy > 0 and sigma0 > 0
                          and res["v_abs"] > HELMERT_RESIDUAL_SIGMA_MULTIPLIER * sigma0)

    return {
        "parameters": {
            "a": a, "b": b_param, "tE": tE, "tN": tN,
            "scale": scale, "scale_ppm": scale_ppm,
            "rotation_rad": rotation_rad, "rotation_gon": rotation_gon,
            "rotation_dms": rotation_dms
        },
        "diagnostics": {
            "n_points": n, "redundancy": redundancy,
            "sigma0": sigma0, "rmse": rmse, "warnings": warnings
        },
        "residuals": residuals
    }


def apply_helmert_2d(e: float, n: float,
                     a: float, b: float, tE: float, tN: float) -> Tuple[float, float]:
    """Apply 2D Helmert transformation to a point."""
    return a * e - b * n + tE, b * e + a * n + tN


# ---------- Area and Perimeter ----------
def calc_area_perimeter(points: List[Tuple[float, float]]) -> Dict:
    """
    Calculate area (Shoelace) and perimeter from (E, N) list.
    Minimum 3 points.
    """
    n = len(points)
    if n < 3:
        return {"error": "Servono almeno 3 punti", "area": 0.0, "perimeter": 0.0}

    # Shoelace formula
    area = 0.0
    for i in range(n):
        j = (i + 1) % n
        area += points[i][0] * points[j][1]
        area -= points[j][0] * points[i][1]
    area = abs(area) / 2.0

    # Perimeter
    perimeter = 0.0
    for i in range(n):
        j = (i + 1) % n
        perimeter += math.hypot(points[j][0] - points[i][0],
                                points[j][1] - points[i][1])

    return {"area": area, "perimeter": perimeter}
