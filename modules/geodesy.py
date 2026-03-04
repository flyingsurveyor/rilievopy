"""
Geodetic coordinate transformations.
WGS84 constants, ECEF/ENU/Geodetic conversions.
"""

import math
from typing import Tuple

# ---------- WGS84 Constants ----------
WGS84_A = 6378137.0
WGS84_F = 1 / 298.257223563
WGS84_E2 = WGS84_F * (2 - WGS84_F)


def geodetic_to_ecef(lat_deg: float, lon_deg: float, h: float) -> Tuple[float, float, float]:
    """Convert WGS84 geodetic (lat, lon, height) to ECEF (X, Y, Z)."""
    lat = math.radians(lat_deg)
    lon = math.radians(lon_deg)
    s = math.sin(lat)
    c = math.cos(lat)
    N = WGS84_A / math.sqrt(1 - WGS84_E2 * s * s)
    X = (N + h) * c * math.cos(lon)
    Y = (N + h) * c * math.sin(lon)
    Z = (N * (1 - WGS84_E2) + h) * s
    return X, Y, Z


def ecef_delta_to_enu(dX: float, dY: float, dZ: float,
                      lat0_deg: float, lon0_deg: float) -> Tuple[float, float, float]:
    """Convert ECEF delta to ENU at reference point."""
    phi = math.radians(lat0_deg)
    lam = math.radians(lon0_deg)
    sp, cp = math.sin(phi), math.cos(phi)
    sl, cl = math.sin(lam), math.cos(lam)
    e = -sl * dX + cl * dY
    n = -sp * cl * dX - sp * sl * dY + cp * dZ
    u = cp * cl * dX + cp * sl * dY + sp * dZ
    return e, n, u


def enu_to_geodetic(e: float, n: float, u: float,
                    lat0_deg: float, lon0_deg: float, alt0: float) -> Tuple[float, float, float]:
    """Convert ENU to WGS84 geodetic coordinates using ECEF as intermediate."""
    X0, Y0, Z0 = geodetic_to_ecef(lat0_deg, lon0_deg, alt0)

    phi = math.radians(lat0_deg)
    lam = math.radians(lon0_deg)
    sp, cp = math.sin(phi), math.cos(phi)
    sl, cl = math.sin(lam), math.cos(lam)

    dX = -sl * e - sp * cl * n + cp * cl * u
    dY = cl * e - sp * sl * n + cp * sl * u
    dZ = cp * n + sp * u

    X = X0 + dX
    Y = Y0 + dY
    Z = Z0 + dZ

    # ECEF to geodetic — Bowring iterative
    p = math.sqrt(X * X + Y * Y)
    lon = math.atan2(Y, X)
    lat = math.atan2(Z, p * (1 - WGS84_E2))

    for _ in range(5):
        N = WGS84_A / math.sqrt(1 - WGS84_E2 * math.sin(lat) ** 2)
        lat_new = math.atan2(Z + WGS84_E2 * N * math.sin(lat), p)
        if abs(lat_new - lat) < 1e-12:
            break
        lat = lat_new

    N = WGS84_A / math.sqrt(1 - WGS84_E2 * math.sin(lat) ** 2)
    h = p / math.cos(lat) - N

    return math.degrees(lat), math.degrees(lon), h
