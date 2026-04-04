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
    lat = p.get("lat")
    lon = p.get("lon")
    alt = p.get("alt_msl") or p.get("alt_hae", 0.0)
    if lat is None or lon is None:
        return None
    return float(lat), float(lon), float(alt)


def _point_layer_name(p: Dict[str, Any]) -> str:
    """Return the layer name for a point based on its codice (or 'PUNTI' as fallback)."""
    return (p.get("codice") or "").strip() or "PUNTI"


def build_dxf_advanced(svy: Dict[str, Any], mode: str = "3d",
                       text_height: float = 0.1,
                       show_precision: bool = False,
                       quota_decimals: int = 3,
                       layer_by_code: bool = True) -> str:
    """
    Generate DXF with local ENU coordinates.
    mode: "2d" or "3d"
    First point = (0, 0, altitude) for 3D, (0, 0, 0) for 2D.
    layer_by_code: if True, each point goes on a layer named after its codice.
    """
    feats = svy.get("features", [])
    if not feats:
        return ""

    first_llh = _feature_to_llh(feats[0])
    if not first_llh:
        return ""
    lat0, lon0, alt0 = first_llh
    X0, Y0, Z0 = geodetic_to_ecef(lat0, lon0, alt0)

    # Collect unique point-layer codes when layer_by_code is enabled
    ACI_PALETTE = [7, 1, 2, 3, 4, 5, 6]  # white, red, yellow, green, cyan, blue, magenta
    if layer_by_code:
        unique_codes: List[str] = []
        for f in feats:
            cod = _point_layer_name(f.get("properties", {}))
            if cod not in unique_codes:
                unique_codes.append(cod)
        if "PUNTI" not in unique_codes:
            unique_codes.insert(0, "PUNTI")
    else:
        unique_codes = ["PUNTI"]

    out = []
    def w(code, value):
        out.append(f"{code}\n{value}")

    # Header
    w(0, "SECTION"); w(2, "HEADER")
    w(9, "$INSUNITS"); w(70, "6")
    w(0, "ENDSEC")

    # Layers
    fixed_layers = ["ETICHETTE", "QUOTE", "PRECISIONE"]
    fixed_colors = [3, 5, 1]
    all_layers = list(unique_codes) + fixed_layers
    total_layers = len(all_layers)

    w(0, "SECTION"); w(2, "TABLES")
    w(0, "TABLE"); w(2, "LAYER"); w(70, str(total_layers))

    for idx, lname in enumerate(unique_codes):
        color = ACI_PALETTE[idx % len(ACI_PALETTE)]
        w(0, "LAYER"); w(2, lname); w(70, "0"); w(62, str(color))

    for lname, color in zip(fixed_layers, fixed_colors):
        w(0, "LAYER"); w(2, lname); w(70, "0"); w(62, str(color))

    w(0, "ENDTAB")
    w(0, "ENDSEC")

    w(0, "SECTION"); w(2, "BLOCKS"); w(0, "ENDSEC")

    # Entities
    w(0, "SECTION"); w(2, "ENTITIES")

    for f in feats:
        p = f.get("properties", {})
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

        # Determine point layer
        if layer_by_code:
            pt_layer = _point_layer_name(p)
        else:
            pt_layer = "PUNTI"

        # POINT
        w(0, "POINT"); w(8, pt_layer); w(62, "7")
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
            hAcc = p.get("h_acc")
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
        lat = p.get("lat")
        lon = p.get("lon")
        if lat is None or lon is None:
            continue
        lons.append(lon)
        lats.append(lat)
        alt_hae = p.get("alt_hae")
        alt_msl = p.get("alt_msl")
        alt = alt_msl if alt_msl is not None else (alt_hae if alt_hae is not None else 0.0)
        points.append({
            "name": p.get("name", ""),
            "codice": p.get("codice", ""),
            "desc": p.get("desc", ""),
            "timestamp": p.get("timestamp", ""),
            "lat": lat, "lon": lon,
            "alt_hae": alt_hae, "alt_msl": alt_msl,
            "h_acc": p.get("h_acc"), "v_acc": p.get("v_acc"),
            "ecef_x": p.get("ecef_x"), "ecef_y": p.get("ecef_y"),
            "ecef_z": p.get("ecef_z"), "p_acc": p.get("p_acc"),
            "rtk": p.get("rtk"), "gnss_mode": p.get("gnss_mode"),
            "num_sv": p.get("num_sv"),
            "pdop": p.get("pdop"), "hdop": p.get("hdop"),
            "vdop": p.get("vdop"), "gdop": p.get("gdop"),
            "ndop": p.get("ndop"), "edop": p.get("edop"),
            "tdop": p.get("tdop"),
            "cov_nn": p.get("cov_nn"), "cov_ee": p.get("cov_ee"),
            "cov_dd": p.get("cov_dd"), "cov_ne": p.get("cov_ne"),
            "cov_nd": p.get("cov_nd"), "cov_ed": p.get("cov_ed"),
            "rel_n": p.get("rel_n"), "rel_e": p.get("rel_e"),
            "rel_d": p.get("rel_d"), "rel_sn": p.get("rel_sn"),
            "rel_se": p.get("rel_se"), "rel_sd": p.get("rel_sd"),
            "baseline": p.get("baseline"), "horiz": p.get("horiz"),
            "bearing_deg": p.get("bearing_deg"), "slope_deg": p.get("slope_deg"),
            "sigma_n": p.get("sigma_n"), "sigma_e": p.get("sigma_e"),
            "sigma_u": p.get("sigma_u"),
            "n_samples": p.get("n_samples", 0), "n_kept": p.get("n_kept"),
            "duration_s": p.get("duration_s"), "interval_s": p.get("interval_s"),
            "start_time": p.get("start_time"), "end_time": p.get("end_time"),
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
              ("punti", "features", "punti", "Punti rilievo RilievoPY", min_x, min_y, max_x, max_y, GPKG_WGS84_SRS_ID))

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
        name TEXT, codice TEXT, desc TEXT, timestamp TEXT,
        lat REAL, lon REAL, alt_hae REAL, alt_msl REAL,
        h_acc REAL, v_acc REAL, p_acc REAL,
        ecef_x REAL, ecef_y REAL, ecef_z REAL,
        rtk TEXT, gnss_mode INTEGER, num_sv INTEGER,
        pdop REAL, hdop REAL, vdop REAL, gdop REAL,
        ndop REAL, edop REAL, tdop REAL,
        cov_nn REAL, cov_ee REAL, cov_dd REAL,
        cov_ne REAL, cov_nd REAL, cov_ed REAL,
        rel_n REAL, rel_e REAL, rel_d REAL,
        rel_sn REAL, rel_se REAL, rel_sd REAL,
        baseline REAL, horiz REAL, bearing_deg REAL, slope_deg REAL,
        sigma_n REAL, sigma_e REAL, sigma_u REAL,
        n_samples INTEGER, n_kept INTEGER,
        duration_s REAL, interval_s REAL,
        start_time TEXT, end_time TEXT)""")

    c.executemany(
        """INSERT INTO punti (geom,name,codice,desc,timestamp,
            lat,lon,alt_hae,alt_msl,h_acc,v_acc,p_acc,
            ecef_x,ecef_y,ecef_z,rtk,gnss_mode,num_sv,
            pdop,hdop,vdop,gdop,ndop,edop,tdop,
            cov_nn,cov_ee,cov_dd,cov_ne,cov_nd,cov_ed,
            rel_n,rel_e,rel_d,rel_sn,rel_se,rel_sd,
            baseline,horiz,bearing_deg,slope_deg,
            sigma_n,sigma_e,sigma_u,n_samples,n_kept,
            duration_s,interval_s,start_time,end_time)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,
                ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        [(pt["geom"], pt["name"], pt["codice"], pt["desc"], pt["timestamp"],
          pt["lat"], pt["lon"], pt["alt_hae"], pt["alt_msl"],
          pt["h_acc"], pt["v_acc"], pt["p_acc"],
          pt["ecef_x"], pt["ecef_y"], pt["ecef_z"],
          pt["rtk"], pt["gnss_mode"], pt["num_sv"],
          pt["pdop"], pt["hdop"], pt["vdop"], pt["gdop"],
          pt["ndop"], pt["edop"], pt["tdop"],
          pt["cov_nn"], pt["cov_ee"], pt["cov_dd"],
          pt["cov_ne"], pt["cov_nd"], pt["cov_ed"],
          pt["rel_n"], pt["rel_e"], pt["rel_d"],
          pt["rel_sn"], pt["rel_se"], pt["rel_sd"],
          pt["baseline"], pt["horiz"], pt["bearing_deg"], pt["slope_deg"],
          pt["sigma_n"], pt["sigma_e"], pt["sigma_u"],
          pt["n_samples"], pt["n_kept"],
          pt["duration_s"], pt["interval_s"],
          pt["start_time"], pt["end_time"]) for pt in points])

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
            lat, lon = p.get("lat"), p.get("lon")
            if lat is None or lon is None:
                continue
            geometries.append(Point(lon, lat))
            data.append({
                "name": p.get("name", ""),
                "codice": p.get("codice", ""),
                "desc": p.get("desc", ""),
                "timestamp": p.get("timestamp", ""),
                "lat": lat, "lon": lon,
                "alt_hae": p.get("alt_hae"), "alt_msl": p.get("alt_msl"),
                "h_acc": p.get("h_acc"), "v_acc": p.get("v_acc"),
                "p_acc": p.get("p_acc"),
                "ecef_x": p.get("ecef_x"), "ecef_y": p.get("ecef_y"),
                "ecef_z": p.get("ecef_z"),
                "rtk": p.get("rtk"), "gnss_mode": p.get("gnss_mode"),
                "num_sv": p.get("num_sv"),
                "pdop": p.get("pdop"), "hdop": p.get("hdop"),
                "vdop": p.get("vdop"), "gdop": p.get("gdop"),
                "ndop": p.get("ndop"), "edop": p.get("edop"),
                "tdop": p.get("tdop"),
                "cov_nn": p.get("cov_nn"), "cov_ee": p.get("cov_ee"),
                "cov_dd": p.get("cov_dd"), "cov_ne": p.get("cov_ne"),
                "cov_nd": p.get("cov_nd"), "cov_ed": p.get("cov_ed"),
                "rel_n": p.get("rel_n"), "rel_e": p.get("rel_e"),
                "rel_d": p.get("rel_d"), "rel_sn": p.get("rel_sn"),
                "rel_se": p.get("rel_se"), "rel_sd": p.get("rel_sd"),
                "baseline": p.get("baseline"), "horiz": p.get("horiz"),
                "bearing_deg": p.get("bearing_deg"), "slope_deg": p.get("slope_deg"),
                "sigma_n": p.get("sigma_n"), "sigma_e": p.get("sigma_e"),
                "sigma_u": p.get("sigma_u"),
                "n_samples": p.get("n_samples", 0), "n_kept": p.get("n_kept"),
                "duration_s": p.get("duration_s"), "interval_s": p.get("interval_s"),
                "start_time": p.get("start_time"), "end_time": p.get("end_time"),
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
    start_iso = p.get("start_time") or "-"
    end_iso = p.get("end_time") or "-"
    duration = p.get("duration_s", 10.0)
    n_samples = p.get("n_samples", 0)

    def _fmt(x, fmt):
        try:
            if x is None:
                return ""
            return fmt.format(float(x))
        except Exception:
            return str(x)

    cov_nn = p.get("cov_nn")
    cov_ee = p.get("cov_ee")
    cov_dd = p.get("cov_dd")
    _sigN = None if cov_nn is None or cov_nn < 0 else math.sqrt(cov_nn)
    _sigE = None if cov_ee is None or cov_ee < 0 else math.sqrt(cov_ee)
    _sigD = None if cov_dd is None or cov_dd < 0 else math.sqrt(cov_dd)
    _rSigN = p.get("rel_sn") if p.get("rel_sn") is not None else _sigN
    _rSigE = p.get("rel_se") if p.get("rel_se") is not None else _sigE
    _rSigD = p.get("rel_sd") if p.get("rel_sd") is not None else _sigD

    lines = [
        f"Punto: {name}",
        f"Descrizione: {desc}",
        f"Rilievo: {start_iso} → {end_iso}  (durata {duration:.0f}s, campioni {n_samples})",
        "",
        f"TPV: mode={p.get('gnss_mode', '')}  RTK={p.get('rtk', '')}  SV={p.get('num_sv', '')}",
        "",
        "HPPOSLLH (media):",
        f"  lat = {_fmt(p.get('lat'), '{:.9f}')}  lon = {_fmt(p.get('lon'), '{:.9f}')}",
        f"  altHAE = {_fmt(p.get('alt_hae'), '{:.3f}')} m   altMSL = {_fmt(p.get('alt_msl'), '{:.3f}')} m",
        f"  hAcc = {_fmt(p.get('h_acc'), '{:.3f}')} m   vAcc = {_fmt(p.get('v_acc'), '{:.3f}')} m",
        "",
        "HPPOSECEF (media):",
        f"  X = {_fmt(p.get('ecef_x'), '{:.4f}')} m",
        f"  Y = {_fmt(p.get('ecef_y'), '{:.4f}')} m",
        f"  Z = {_fmt(p.get('ecef_z'), '{:.4f}')} m",
        f"  pAcc = {_fmt(p.get('p_acc'), '{:.4f}')} m",
        "",
        "DOP (media):",
        f"  GDOP={_fmt(p.get('gdop'), '{:.2f}')}  PDOP={_fmt(p.get('pdop'), '{:.2f}')}  HDOP={_fmt(p.get('hdop'), '{:.2f}')}  VDOP={_fmt(p.get('vdop'), '{:.2f}')}",
        f"  NDOP={_fmt(p.get('ndop'), '{:.2f}')}  EDOP={_fmt(p.get('edop'), '{:.2f}')}  TDOP={_fmt(p.get('tdop'), '{:.2f}')}",
        "",
        "COV Pos (media):",
        f"  σN={_fmt(_sigN, '{:.3f}')} m  σE={_fmt(_sigE, '{:.3f}')} m  σD={_fmt(_sigD, '{:.3f}')} m",
        f"  NN={_fmt(cov_nn, '{:.4e}')}  EE={_fmt(cov_ee, '{:.4e}')}  DD={_fmt(cov_dd, '{:.4e}')} (m²)",
        f"  NE={_fmt(p.get('cov_ne'), '{:.4e}')}  ND={_fmt(p.get('cov_nd'), '{:.4e}')}  ED={_fmt(p.get('cov_ed'), '{:.4e}')} (m²)",
        "",
        "RELPOSNED (media):",
        f"  N={_fmt(p.get('rel_n'), '{:.4f}')} m  E={_fmt(p.get('rel_e'), '{:.4f}')} m  D={_fmt(p.get('rel_d'), '{:.4f}')} m",
        f"  σN={_fmt(_rSigN, '{:.3f}')} m  σE={_fmt(_rSigE, '{:.3f}')} m  σD={_fmt(_rSigD, '{:.3f}')} m",
        f"  |baseline|={_fmt(p.get('baseline'), '{:.4f}')} m  horiz={_fmt(p.get('horiz'), '{:.4f}')} m  bearing={_fmt(p.get('bearing_deg'), '{:.1f}')}°  slope={_fmt(p.get('slope_deg'), '{:.1f}')}°",
    ]
    return "\n".join(lines) + "\n"
