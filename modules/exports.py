"""
Export functions for survey data: DXF (2D/3D) and GeoPackage.
"""

import math
import os
import struct
import tempfile
from typing import Dict, Any, Optional, Tuple, List

from .geodesy import geodetic_to_ecef, ecef_delta_to_enu
from .utils import now_iso


# ---------- DXF Export ----------
def _dxf_escape_text(s: str) -> str:
    if s is None:
        return ""
    s = str(s)
    s = s.replace("\n", " ").replace("\r", " ")
    return s


def _feature_to_llh(feat: Dict[str, Any]) -> Optional[Tuple[float, float, float]]:
    """Extract lat, lon, altMSL from feature."""
    p = feat.get("properties", {})
    hp = p.get("HPPOSLLH", {})
    lat = hp.get("lat")
    lon = hp.get("lon")
    alt = hp.get("altMSL") or hp.get("altHAE", 0.0)
    if lat is None or lon is None:
        return None
    return float(lat), float(lon), float(alt)


def build_dxf_advanced(svy: Dict[str, Any], mode: str = "3d",
                       text_height: float = 0.1,
                       show_precision: bool = False,
                       quota_decimals: int = 3) -> str:
    """
    Generate DXF with local ENU coordinates.
    mode: "2d" or "3d"
    First point = (0, 0, altitude) for 3D, (0, 0, 0) for 2D.
    """
    feats = svy.get("features", [])
    if not feats:
        return ""

    first_llh = _feature_to_llh(feats[0])
    if not first_llh:
        return ""
    lat0, lon0, alt0 = first_llh
    X0, Y0, Z0 = geodetic_to_ecef(lat0, lon0, alt0)

    out = []
    def w(code, value):
        out.append(f"{code}\n{value}")

    # Header
    w(0, "SECTION"); w(2, "HEADER")
    w(9, "$INSUNITS"); w(70, "6")
    w(0, "ENDSEC")

    # Layers
    w(0, "SECTION"); w(2, "TABLES")
    w(0, "TABLE"); w(2, "LAYER"); w(70, "4")
    w(0, "LAYER"); w(2, "PUNTI"); w(70, "0"); w(62, "7")
    w(0, "LAYER"); w(2, "ETICHETTE"); w(70, "0"); w(62, "3")
    w(0, "LAYER"); w(2, "QUOTE"); w(70, "0"); w(62, "5")
    w(0, "LAYER"); w(2, "PRECISIONE"); w(70, "0"); w(62, "1")
    w(0, "ENDTAB")
    w(0, "ENDSEC")

    w(0, "SECTION"); w(2, "BLOCKS"); w(0, "ENDSEC")

    # Entities
    w(0, "SECTION"); w(2, "ENTITIES")

    for f in feats:
        p = f.get("properties", {})
        hp = p.get("HPPOSLLH", {})
        llh = _feature_to_llh(f)
        if not llh:
            continue
        lat, lon, alt = llh

        X, Y, Z = geodetic_to_ecef(lat, lon, alt)
        dX, dY, dZ = X - X0, Y - Y0, Z - Z0
        e, n, u = ecef_delta_to_enu(dX, dY, dZ, lat0, lon0)

        x_local, y_local = e, n
        z_local = 0.0 if mode == "2d" else alt
        name = _dxf_escape_text(p.get("name", f.get("id", "")))
        text_offset_x = 0.5

        # POINT
        w(0, "POINT"); w(8, "PUNTI"); w(62, "7")
        w(10, f"{x_local:.4f}"); w(20, f"{y_local:.4f}"); w(30, f"{z_local:.4f}")

        # Label
        w(0, "TEXT"); w(8, "ETICHETTE"); w(62, "3")
        w(10, f"{x_local + text_offset_x:.4f}"); w(20, f"{y_local:.4f}"); w(30, f"{z_local + 0.2:.4f}")
        w(40, f"{text_height}")
        w(1, name if name else "")

        # Altitude
        quota_str = f"{alt:.{quota_decimals}f}"
        w(0, "TEXT"); w(8, "QUOTE"); w(62, "5")
        w(10, f"{x_local + text_offset_x:.4f}"); w(20, f"{y_local:.4f}"); w(30, f"{z_local - 0.2:.4f}")
        w(40, f"{text_height * 0.8}")
        w(1, quota_str)

        # Precision circle
        if show_precision:
            hAcc = hp.get("hAcc")
            if hAcc and hAcc > 0:
                w(0, "CIRCLE"); w(8, "PRECISIONE"); w(62, "1")
                w(10, f"{x_local:.4f}"); w(20, f"{y_local:.4f}"); w(30, f"{z_local:.4f}")
                w(40, f"{hAcc:.4f}")

    w(0, "ENDSEC")
    w(0, "EOF")
    return "\n".join(out) + "\n"


def build_dxf_from_survey(svy: Dict[str, Any]) -> str:
    """Backward compatibility wrapper — 3D DXF."""
    return build_dxf_advanced(svy, mode="3d")


# ---------- GeoPackage Export ----------
GPKG_APPLICATION_ID = 0x47504B47
GPKG_WGS84_SRS_ID = 4326
GPKG_WGS84_DEFINITION = (
    'GEOGCS["WGS 84",DATUM["WGS_1984",SPHEROID["WGS 84",6378137,298.257223563]],'
    'PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433]]'
)


def create_wkb_point_z(lon: float, lat: float, alt: float) -> bytes:
    """WKB Point Z (3D) geometry in little-endian."""
    return b'\x01' + struct.pack('<I', 1001) + struct.pack('<ddd', lon, lat, alt)


def create_gpkg_geometry(lon: float, lat: float, alt: float,
                         srs_id: int = 4326) -> bytes:
    """GeoPackage Binary Header + WKB."""
    gpkg_header = b'GP' + b'\x00' + b'\x01' + struct.pack('<i', srs_id)
    return gpkg_header + create_wkb_point_z(lon, lat, alt)


def export_geopackage_sqlite(svy: Dict[str, Any], filepath: str) -> bool:
    """Create GeoPackage using pure sqlite3 (no geopandas required)."""
    import sqlite3

    features = svy.get("features", [])
    if not features:
        return False

    points = []
    lons, lats = [], []

    for f in features:
        p = f.get("properties", {})
        hp = p.get("HPPOSLLH", {})
        tpv = p.get("TPV", {})
        dop = p.get("DOP", {})
        lat = hp.get("lat")
        lon = hp.get("lon")
        if lat is None or lon is None:
            continue
        lons.append(lon)
        lats.append(lat)
        altHAE = hp.get("altHAE")
        altMSL = hp.get("altMSL")
        alt = altMSL if altMSL is not None else (altHAE if altHAE is not None else 0.0)
        points.append({
            "name": p.get("name", ""), "lat": lat, "lon": lon,
            "altHAE": altHAE, "altMSL": altMSL,
            "hAcc": hp.get("hAcc"), "vAcc": hp.get("vAcc"),
            "pAcc": p.get("HPPOSECEF", {}).get("pAcc"),
            "pdop": dop.get("pdop"), "hdop": dop.get("hdop"),
            "vdop": dop.get("vdop"), "gdop": dop.get("gdop"),
            "rtk": tpv.get("rtk"), "numSV": tpv.get("numSV"),
            "timestamp": p.get("timestamp", ""),
            "geom": create_gpkg_geometry(lon, lat, alt, GPKG_WGS84_SRS_ID)
        })

    if not points:
        return False

    min_x, max_x = min(lons), max(lons)
    min_y, max_y = min(lats), max(lats)

    conn = sqlite3.connect(filepath)
    c = conn.cursor()
    c.execute(f"PRAGMA application_id = {GPKG_APPLICATION_ID}")
    c.execute("PRAGMA user_version = 10300")

    c.execute("""CREATE TABLE gpkg_spatial_ref_sys (
        srs_name TEXT NOT NULL, srs_id INTEGER NOT NULL PRIMARY KEY,
        organization TEXT NOT NULL, organization_coordsys_id INTEGER NOT NULL,
        definition TEXT NOT NULL, description TEXT)""")

    c.execute("INSERT INTO gpkg_spatial_ref_sys VALUES (?,?,?,?,?,?)",
              ('WGS 84 geodetic', GPKG_WGS84_SRS_ID, 'EPSG', GPKG_WGS84_SRS_ID,
               GPKG_WGS84_DEFINITION, 'longitude/latitude WGS 84'))

    c.execute("""CREATE TABLE gpkg_contents (
        table_name TEXT NOT NULL PRIMARY KEY, data_type TEXT NOT NULL,
        identifier TEXT UNIQUE, description TEXT DEFAULT '',
        last_change DATETIME NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
        min_x DOUBLE, min_y DOUBLE, max_x DOUBLE, max_y DOUBLE,
        srs_id INTEGER,
        CONSTRAINT fk_gc_r_srs_id FOREIGN KEY (srs_id) REFERENCES gpkg_spatial_ref_sys(srs_id))""")

    c.execute("INSERT INTO gpkg_contents (table_name,data_type,identifier,description,min_x,min_y,max_x,max_y,srs_id) VALUES (?,?,?,?,?,?,?,?,?)",
              ("punti", "features", "punti", "Punti rilievo GNSS", min_x, min_y, max_x, max_y, GPKG_WGS84_SRS_ID))

    c.execute("""CREATE TABLE gpkg_geometry_columns (
        table_name TEXT NOT NULL, column_name TEXT NOT NULL,
        geometry_type_name TEXT NOT NULL, srs_id INTEGER NOT NULL,
        z TINYINT NOT NULL, m TINYINT NOT NULL,
        CONSTRAINT pk_geom_cols PRIMARY KEY (table_name, column_name),
        CONSTRAINT fk_gc_tn FOREIGN KEY (table_name) REFERENCES gpkg_contents(table_name),
        CONSTRAINT fk_gc_srs FOREIGN KEY (srs_id) REFERENCES gpkg_spatial_ref_sys(srs_id))""")

    c.execute("INSERT INTO gpkg_geometry_columns VALUES (?,?,?,?,?,?)",
              ("punti", "geom", "POINT", GPKG_WGS84_SRS_ID, 1, 0))

    c.execute("""CREATE TABLE punti (
        fid INTEGER PRIMARY KEY AUTOINCREMENT, geom BLOB,
        name TEXT, lat REAL, lon REAL, altHAE REAL, altMSL REAL,
        hAcc REAL, vAcc REAL, pAcc REAL, pdop REAL, hdop REAL,
        vdop REAL, gdop REAL, rtk TEXT, numSV INTEGER, timestamp TEXT)""")

    c.executemany("""INSERT INTO punti (geom,name,lat,lon,altHAE,altMSL,hAcc,vAcc,pAcc,pdop,hdop,vdop,gdop,rtk,numSV,timestamp)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                  [(pt["geom"], pt["name"], pt["lat"], pt["lon"], pt["altHAE"], pt["altMSL"],
                    pt["hAcc"], pt["vAcc"], pt["pAcc"], pt["pdop"], pt["hdop"], pt["vdop"],
                    pt["gdop"], pt["rtk"], pt["numSV"], pt["timestamp"]) for pt in points])

    conn.commit()
    conn.close()
    return True


def export_geopackage(svy: Dict[str, Any], sid: str) -> Optional[str]:
    """
    Export survey to GeoPackage. Tries geopandas first, falls back to pure sqlite3.
    Returns filepath on success, None on failure.
    """
    filepath = None

    try:
        import geopandas as gpd
        from shapely.geometry import Point

        data = []
        geometries = []
        for f in svy.get("features", []):
            p = f.get("properties", {})
            hp = p.get("HPPOSLLH", {})
            tpv = p.get("TPV", {})
            dop = p.get("DOP", {})
            lat, lon = hp.get("lat"), hp.get("lon")
            if lat is None or lon is None:
                continue
            geometries.append(Point(lon, lat))
            data.append({
                "name": p.get("name", ""), "lat": lat, "lon": lon,
                "altHAE": hp.get("altHAE"), "altMSL": hp.get("altMSL"),
                "hAcc": hp.get("hAcc"), "vAcc": hp.get("vAcc"),
                "pAcc": p.get("HPPOSECEF", {}).get("pAcc"),
                "pdop": dop.get("pdop"), "hdop": dop.get("hdop"),
                "vdop": dop.get("vdop"), "gdop": dop.get("gdop"),
                "rtk": tpv.get("rtk"), "numSV": tpv.get("numSV"),
                "timestamp": p.get("timestamp", "")
            })

        gdf = gpd.GeoDataFrame(data, geometry=geometries, crs="EPSG:4326")
        with tempfile.NamedTemporaryFile(suffix=".gpkg", delete=False) as tmp:
            filepath = tmp.name
        gdf.to_file(filepath, driver="GPKG", layer=sid)
        return filepath

    except Exception as e:
        print(f"# {now_iso()} [gpkg] geopandas fallito: {e}, uso SQLite fallback")
        try:
            with tempfile.NamedTemporaryFile(suffix=".gpkg", delete=False) as tmp:
                filepath = tmp.name
            if export_geopackage_sqlite(svy, filepath):
                return filepath
        except Exception as e2:
            print(f"# {now_iso()} [gpkg] sqlite fallback failed: {e2}")

    return None


def format_point_txt(feat: Dict[str, Any], sid: str) -> str:
    """Format a single survey point as detailed text report."""
    p = feat.get("properties", {})
    pid = feat.get("id", "?")
    name = p.get("name", pid)
    desc = p.get("desc", "") or "-"
    samp = p.get("sampling", {})
    start_iso = samp.get("start_iso") or "-"
    end_iso = samp.get("end_iso") or "-"
    duration = samp.get("duration_s", 10.0)
    n_samples = samp.get("n_samples", 0)

    tpv = p.get("TPV", {})
    hp = p.get("HPPOSLLH", {})
    ecef = p.get("HPPOSECEF", {})
    dop = p.get("DOP", {})
    cov = p.get("COV", {})
    rp = p.get("RELPOSNED", {})

    def _fmt(x, fmt):
        try:
            return fmt.format(x if x is not None else float('nan'))
        except Exception:
            return str(x)

    NN, EE, DD = cov.get("NN"), cov.get("EE"), cov.get("DD")
    _sigN = None if NN is None or NN < 0 else math.sqrt(NN)
    _sigE = None if EE is None or EE < 0 else math.sqrt(EE)
    _sigD = None if DD is None or DD < 0 else math.sqrt(DD)
    _rSigN = rp.get("sN") if rp.get("sN") is not None else _sigN
    _rSigE = rp.get("sE") if rp.get("sE") is not None else _sigE
    _rSigD = rp.get("sD") if rp.get("sD") is not None else _sigD

    lines = [
        f"Punto: {name}",
        f"Descrizione: {desc}",
        f"Rilievo: {start_iso} → {end_iso}  (durata {duration:.0f}s, campioni {n_samples})",
        "",
        f"TPV: mode={tpv.get('mode', '')}  RTK={tpv.get('rtk', '')}  SV={tpv.get('numSV', '')}",
        "",
        "HPPOSLLH (media):",
        f"  lat = {_fmt(hp.get('lat'), '{:.9f}')}  lon = {_fmt(hp.get('lon'), '{:.9f}')}",
        f"  altHAE = {_fmt(hp.get('altHAE'), '{:.3f}')} m   altMSL = {_fmt(hp.get('altMSL'), '{:.3f}')} m",
        f"  hAcc = {_fmt(hp.get('hAcc'), '{:.3f}')} m   vAcc = {_fmt(hp.get('vAcc'), '{:.3f}')} m",
        "",
        "HPPOSECEF (media):",
        f"  X = {_fmt(ecef.get('X'), '{:.4f}')} m",
        f"  Y = {_fmt(ecef.get('Y'), '{:.4f}')} m",
        f"  Z = {_fmt(ecef.get('Z'), '{:.4f}')} m",
        f"  pAcc = {_fmt(ecef.get('pAcc'), '{:.4f}')} m",
        "",
        "DOP (media):",
        f"  GDOP={_fmt(dop.get('gdop'), '{:.2f}')}  PDOP={_fmt(dop.get('pdop'), '{:.2f}')}  HDOP={_fmt(dop.get('hdop'), '{:.2f}')}  VDOP={_fmt(dop.get('vdop'), '{:.2f}')}",
        f"  NDOP={_fmt(dop.get('ndop'), '{:.2f}')}  EDOP={_fmt(dop.get('edop'), '{:.2f}')}  TDOP={_fmt(dop.get('tdop'), '{:.2f}')}",
        "",
        "COV Pos (media):",
        f"  σN={_fmt(_sigN, '{:.3f}')} m  σE={_fmt(_sigE, '{:.3f}')} m  σD={_fmt(_sigD, '{:.3f}')} m",
        f"  NN={_fmt(NN, '{:.4e}')}  EE={_fmt(EE, '{:.4e}')}  DD={_fmt(DD, '{:.4e}')} (m²)",
        f"  NE={_fmt(cov.get('NE'), '{:.4e}')}  ND={_fmt(cov.get('ND'), '{:.4e}')}  ED={_fmt(cov.get('ED'), '{:.4e}')} (m²)",
        "",
        "RELPOSNED (media):",
        f"  N={_fmt(rp.get('N'), '{:.4f}')} m  E={_fmt(rp.get('E'), '{:.4f}')} m  D={_fmt(rp.get('D'), '{:.4f}')} m",
        f"  σN={_fmt(_rSigN, '{:.3f}')} m  σE={_fmt(_rSigE, '{:.3f}')} m  σD={_fmt(_rSigD, '{:.3f}')} m",
        f"  |baseline|={_fmt(rp.get('baseline'), '{:.4f}')} m  horiz={_fmt(rp.get('horiz'), '{:.4f}')} m  bearing={_fmt(rp.get('bearingDeg'), '{:.1f}')}°  slope={_fmt(rp.get('slopeDeg'), '{:.1f}')}°",
    ]
    return "\n".join(lines) + "\n"
