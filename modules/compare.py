"""
Compare two survey points: compute ENU deltas, distances, bearing.
"""

import math
from typing import Dict, Any

from .geodesy import geodetic_to_ecef, ecef_delta_to_enu


def compare_points(A: Dict[str, Any], B: Dict[str, Any]) -> Dict[str, Any]:
    """Compare two points, returning ENU deltas, horizontal/3D distance, bearing."""

    def ecef_from(d):
        X, Y, Z = d.get("X"), d.get("Y"), d.get("Z")
        if X is not None and Y is not None and Z is not None:
            return X, Y, Z
        lat, lon = d.get("lat"), d.get("lon")
        h = d.get("altHAE")
        if h is None:
            h = d.get("altMSL", 0.0)
        if lat is None or lon is None:
            return None
        return geodetic_to_ecef(lat, lon, h)

    ecefA = ecef_from(A)
    ecefB = ecef_from(B)
    latA, lonA = A.get("lat"), A.get("lon")

    if not ecefA or not ecefB or latA is None or lonA is None:
        return {"ok": False, "err": "Dati insufficienti (mancano ECEF o lat/lon del primo punto)."}

    dX = ecefB[0] - ecefA[0]
    dY = ecefB[1] - ecefA[1]
    dZ = ecefB[2] - ecefA[2]
    e, n, u = ecef_delta_to_enu(dX, dY, dZ, latA, lonA)
    horiz = math.hypot(e, n)
    dist3d = math.sqrt(horiz * horiz + u * u)
    bearing = math.degrees(math.atan2(e, n))
    bearing += 360.0 if bearing < 0 else 0

    dh = None
    if A.get("altMSL") is not None and B.get("altMSL") is not None:
        dh = B["altMSL"] - A["altMSL"]
    elif A.get("altHAE") is not None and B.get("altHAE") is not None:
        dh = B["altHAE"] - A["altHAE"]

    return {
        "ok": True,
        "ΔE": e, "ΔN": n, "ΔU": u,
        "horiz": horiz, "dist3D": dist3d,
        "bearing": bearing, "Δquota": dh,
        "A": A, "B": B
    }
