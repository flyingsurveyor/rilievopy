"""
Survey storage — GeoJSON-based multi-point survey management.
"""

import os
import re
import json
import shutil
import threading
from uuid import uuid4
from typing import Dict, Any, List, Optional, Tuple

from .utils import now_iso

# ---------- Configuration ----------
# SURVEY_DIR is now workspace-aware.  Use get_survey_dir() at call time.
# The module-level SURVEY_DIR is kept for backward compatibility but is
# re-evaluated on every access via the property-like helper below.

def _legacy_survey_dir() -> str:
    """Fallback: project-root surveys/ (used only when workspace is unavailable)."""
    return os.path.abspath(os.path.join(os.getcwd(), "surveys"))


def get_survey_dir() -> str:
    """Return the active workspace surveys directory, creating it if needed."""
    try:
        from .workspace import surveys_dir
        d = surveys_dir()
    except Exception:
        d = _legacy_survey_dir()
    os.makedirs(d, exist_ok=True)
    return d


# Module-level SURVEY_DIR kept for compatibility; callers that cached this
# value at import time will get the legacy path — all internal code now uses
# get_survey_dir() directly.
SURVEY_DIR = _legacy_survey_dir()
os.makedirs(SURVEY_DIR, exist_ok=True)

SURVEY_LOCK = threading.Lock()
SURVEY_EXT = ".geojson"


# ---------- Helpers ----------
def sanitize_survey_id(s: str) -> str:
    from datetime import datetime
    s = (s or "").strip()
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"[^A-Za-z0-9_\-]", "", s)
    return s[:32] or datetime.now().strftime("R%Y%m%d_%H%M%S")


def survey_path(sid: str) -> str:
    return os.path.join(get_survey_dir(), f"{sid}{SURVEY_EXT}")


# ---------- CRUD ----------
def list_survey_ids() -> List[str]:
    ids = []
    survey_dir = get_survey_dir()
    try:
        for fn in os.listdir(survey_dir):
            if fn.endswith(SURVEY_EXT):
                ids.append(fn[:-len(SURVEY_EXT)])
    except FileNotFoundError:
        pass
    ids.sort(key=lambda sid: os.path.getmtime(survey_path(sid)), reverse=True)
    return ids


def _normalize_feature(feat: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert a feature with legacy nested properties (HPPOSLLH, TPV, DOP, etc.)
    to the new flat structure. Returns the feature unchanged if already flat.
    """
    p = feat.get("properties", {})
    # Detect legacy structure: presence of nested sub-dicts
    if "HPPOSLLH" not in p and "TPV" not in p:
        return feat  # already flat or empty

    hp = p.get("HPPOSLLH", {})
    ecef = p.get("HPPOSECEF", {})
    tpv = p.get("TPV", {})
    dop = p.get("DOP", {})
    cov = p.get("COV", {})
    rp = p.get("RELPOSNED", {})
    samp = p.get("sampling", {})

    flat = {
        # identification
        "name":        p.get("name", ""),
        "codice":      p.get("codice", ""),
        "desc":        p.get("desc", ""),
        "timestamp":   p.get("timestamp", ""),
        # coordinates
        "lat":         hp.get("lat"),
        "lon":         hp.get("lon"),
        "alt_hae":     hp.get("altHAE"),
        "alt_msl":     hp.get("altMSL"),
        "h_acc":       hp.get("hAcc"),
        "v_acc":       hp.get("vAcc"),
        # ECEF
        "ecef_x":      ecef.get("X"),
        "ecef_y":      ecef.get("Y"),
        "ecef_z":      ecef.get("Z"),
        "p_acc":       ecef.get("pAcc"),
        # GNSS quality
        "rtk":         tpv.get("rtk"),
        "gnss_mode":   tpv.get("mode"),
        "num_sv":      tpv.get("numSV"),
        # DOP
        "pdop":        dop.get("pdop"),
        "hdop":        dop.get("hdop"),
        "vdop":        dop.get("vdop"),
        "gdop":        dop.get("gdop"),
        "ndop":        dop.get("ndop"),
        "edop":        dop.get("edop"),
        "tdop":        dop.get("tdop"),
        # covariance
        "cov_nn":      cov.get("NN"),
        "cov_ee":      cov.get("EE"),
        "cov_dd":      cov.get("DD"),
        "cov_ne":      cov.get("NE"),
        "cov_nd":      cov.get("ND"),
        "cov_ed":      cov.get("ED"),
        # relative position (baseline)
        "rel_n":       rp.get("N"),
        "rel_e":       rp.get("E"),
        "rel_d":       rp.get("D"),
        "rel_sn":      rp.get("sN"),
        "rel_se":      rp.get("sE"),
        "rel_sd":      rp.get("sD"),
        "baseline":    rp.get("baseline"),
        "horiz":       rp.get("horiz"),
        "bearing_deg": rp.get("bearingDeg"),
        "slope_deg":   rp.get("slopeDeg"),
        # sampling statistics
        "sigma_n":     samp.get("sigma_N"),
        "sigma_e":     samp.get("sigma_E"),
        "sigma_u":     samp.get("sigma_U"),
        "n_samples":   samp.get("n_samples", 0),
        "n_kept":      samp.get("n_kept"),
        "duration_s":  samp.get("duration_s", 10.0),
        "interval_s":  samp.get("interval_s", 0.5),
        "start_time":  samp.get("start_iso"),
        "end_time":    samp.get("end_iso"),
    }
    # Preserve any extra keys not in the legacy sub-dicts (e.g. voice_notes)
    for k, v in p.items():
        if k not in ("HPPOSLLH", "HPPOSECEF", "TPV", "DOP", "COV", "RELPOSNED", "sampling"):
            if k not in flat:
                flat[k] = v
    feat = dict(feat)
    feat["properties"] = flat
    return feat


def load_survey(sid: str) -> Dict[str, Any]:
    path = survey_path(sid)
    with SURVEY_LOCK:
        with open(path, "r", encoding="utf-8") as fh:
            svy = json.load(fh)
    # Normalize legacy nested features to flat structure on load
    if "features" in svy:
        svy["features"] = [_normalize_feature(f) for f in svy["features"]]
    return svy


def save_survey(sid: str, obj: Dict[str, Any]):
    path = survey_path(sid)
    tmp = path + ".tmp"
    with SURVEY_LOCK:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)


def create_survey(title: str, desc: str) -> str:
    from datetime import datetime
    sid = sanitize_survey_id(title) or datetime.now().strftime("R%Y%m%d_%H%M%S")
    path = survey_path(sid)
    if os.path.exists(path):
        sid = sid + "-" + datetime.now().strftime("%H%M%S")
    obj = {
        "type": "FeatureCollection",
        "name": title,
        "properties": {
            "id": sid,
            "title": title,
            "desc": (desc or "").strip()[:1000],
            "created": now_iso(),
            "app": "gnss.py",
            "version": "multi-pt-2"
        },
        "features": []
    }
    save_survey(sid, obj)
    return sid


def delete_survey_file(sid: str) -> bool:
    path = survey_path(sid)
    removed = False
    if os.path.exists(path):
        os.remove(path)
        removed = True
    media_dir = survey_media_dir(sid)
    if os.path.isdir(media_dir):
        shutil.rmtree(media_dir, ignore_errors=True)
        removed = True or removed
    return removed


def backup_survey(sid: str):
    """Create a .bak copy of the survey file before modification."""
    src = survey_path(sid)
    bak = src + ".bak"
    if os.path.exists(src):
        shutil.copy2(src, bak)


def next_point_id(svy: Dict[str, Any]) -> str:
    n = 1 + len(svy.get("features", []))
    return f"P{n:03d}"


# ---------- Point feature creation ----------
def point_feature(pid: str,
                  lat: Optional[float], lon: Optional[float],
                  altHAE: Optional[float], altMSL: Optional[float],
                  X: Optional[float], Y: Optional[float], Z: Optional[float],
                  stats: Dict[str, Any], meta: Dict[str, Any]) -> Dict[str, Any]:
    if lat is not None and lon is not None and altHAE is not None:
        geom = {"type": "Point", "coordinates": [lon, lat, altHAE]}
    else:
        geom = {"type": "Point", "coordinates": [(lon or 0.0), (lat or 0.0)]}
    return {
        "type": "Feature",
        "id": pid,
        "geometry": geom,
        "properties": {
            # identification
            "name":        meta.get("name") or pid,
            "codice":      meta.get("codice", ""),
            "desc":        meta.get("desc") or "",
            "timestamp":   now_iso(),
            # coordinates (float, full precision)
            "lat":         lat,
            "lon":         lon,
            "alt_hae":     altHAE,
            "alt_msl":     altMSL,
            "h_acc":       stats.get("hAcc"),
            "v_acc":       stats.get("vAcc"),
            # ECEF
            "ecef_x":      X,
            "ecef_y":      Y,
            "ecef_z":      Z,
            "p_acc":       stats.get("pAcc"),
            # GNSS quality
            "rtk":         stats.get("rtk"),
            "gnss_mode":   stats.get("mode"),
            "num_sv":      stats.get("numSV"),
            # DOP
            "pdop":        stats.get("pdop"),
            "hdop":        stats.get("hdop"),
            "vdop":        stats.get("vdop"),
            "gdop":        stats.get("gdop"),
            "ndop":        stats.get("ndop"),
            "edop":        stats.get("edop"),
            "tdop":        stats.get("tdop"),
            # covariance
            "cov_nn":      stats.get("covNN"),
            "cov_ee":      stats.get("covEE"),
            "cov_dd":      stats.get("covDD"),
            "cov_ne":      stats.get("covNE"),
            "cov_nd":      stats.get("covND"),
            "cov_ed":      stats.get("covED"),
            # relative position (baseline)
            "rel_n":       stats.get("relN"),
            "rel_e":       stats.get("relE"),
            "rel_d":       stats.get("relD"),
            "rel_sn":      stats.get("relsN"),
            "rel_se":      stats.get("relsE"),
            "rel_sd":      stats.get("relsD"),
            "baseline":    stats.get("baseline"),
            "horiz":       stats.get("horiz"),
            "bearing_deg": stats.get("bearing"),
            "slope_deg":   stats.get("slope"),
            # sampling statistics
            "sigma_n":     stats.get("sigma_N"),
            "sigma_e":     stats.get("sigma_E"),
            "sigma_u":     stats.get("sigma_U"),
            "n_samples":   meta.get("n_samples", 0),
            "n_kept":      stats.get("n_kept"),
            "duration_s":  meta.get("duration", 10.0),
            "interval_s":  meta.get("interval", 0.5),
            "start_time":  meta.get("start"),
            "end_time":    meta.get("end"),
        }
    }




# ---------- Media / voice notes ----------
def survey_media_dir(sid: str) -> str:
    return os.path.join(get_survey_dir(), "media", sid)


def survey_audio_dir(sid: str) -> str:
    path = os.path.join(survey_media_dir(sid), "audio")
    os.makedirs(path, exist_ok=True)
    return path


def note_file_url(sid: str, filename: str) -> str:
    return f"/survey/{sid}/media/audio/{filename}"


def _survey_note_lists(svy: Dict[str, Any]):
    props = svy.setdefault("properties", {})
    yield props.setdefault("voice_notes_session", [])
    yield props.setdefault("voice_notes_pending", [])
    for feat in svy.get("features", []):
        yield feat.setdefault("properties", {}).setdefault("voice_notes", [])


def find_voice_note(svy: Dict[str, Any], note_id: str):
    props = svy.setdefault("properties", {})
    for name in ("voice_notes_session", "voice_notes_pending"):
        notes = props.setdefault(name, [])
        for idx, note in enumerate(notes):
            if note.get("id") == note_id:
                return name, notes, idx, note, None
    for feat in svy.get("features", []):
        notes = feat.setdefault("properties", {}).setdefault("voice_notes", [])
        for idx, note in enumerate(notes):
            if note.get("id") == note_id:
                return "feature", notes, idx, note, feat
    return None, None, None, None, None


def cleanup_note_file(sid: str, note: Dict[str, Any]):
    filename = note.get("audio_filename") or os.path.basename(note.get("audio_file", ""))
    if not filename:
        return
    path = os.path.join(survey_audio_dir(sid), filename)
    try:
        if os.path.isfile(path):
            os.remove(path)
    except Exception:
        pass


def remove_note_by_id(sid: str, svy: Dict[str, Any], note_id: str) -> bool:
    kind, notes, idx, note, feat = find_voice_note(svy, note_id)
    if note is None:
        return False
    cleanup_note_file(sid, note)
    notes.pop(idx)
    return True


def move_pending_notes_to_feature(svy: Dict[str, Any], feat: Dict[str, Any], name: str, codice: str):
    props = svy.setdefault("properties", {})
    pending = props.setdefault("voice_notes_pending", [])
    remaining = []
    attached = feat.setdefault("properties", {}).setdefault("voice_notes", [])
    for note in pending:
        if note.get("point_name") == name and (not codice or note.get("point_code") in (None, "", codice)):
            note["point_id"] = feat.get("id")
            note["point_name"] = feat.get("properties", {}).get("name", name)
            note["point_code"] = feat.get("properties", {}).get("codice", codice)
            note["kind"] = "point"
            attached.append(note)
        else:
            remaining.append(note)
    props["voice_notes_pending"] = remaining


def build_voice_notes_index(sid: str, svy: Dict[str, Any]) -> Dict[str, Any]:
    props = svy.setdefault("properties", {})
    session = list(props.get("voice_notes_session", []))
    pending = list(props.get("voice_notes_pending", []))
    point = []
    for feat in svy.get("features", []):
        fp = feat.get("properties", {})
        for note in fp.get("voice_notes", []) or []:
            item = dict(note)
            item.setdefault("point_id", feat.get("id"))
            item.setdefault("point_name", fp.get("name", feat.get("id")))
            item.setdefault("point_code", fp.get("codice", ""))
            point.append(item)
    for group in (session, pending, point):
        for note in group:
            if note.get("audio_filename") and not note.get("audio_url"):
                note["audio_url"] = note_file_url(sid, note["audio_filename"])
    return {"session": session, "pending": pending, "point": point}

# ---------- CSV flatten ----------
CSV_HEADER = [
    "name", "codice", "desc", "timestamp",
    "lat", "lon", "alt_hae", "alt_msl",
    "ecef_x", "ecef_y", "ecef_z",
    "gnss_mode", "rtk", "num_sv",
    "gdop", "pdop", "hdop", "vdop", "ndop", "edop", "tdop",
    "h_acc", "v_acc", "p_acc",
    "cov_nn", "cov_ee", "cov_dd", "cov_ne", "cov_nd", "cov_ed",
    "rel_n", "rel_e", "rel_d", "rel_sn", "rel_se", "rel_sd",
    "baseline", "horiz", "bearing_deg", "slope_deg",
    "sigma_n", "sigma_e", "sigma_u", "n_kept", "n_samples",
    "duration_s", "interval_s", "start_time", "end_time",
]


def flatten_point_for_csv(feat: Dict[str, Any]) -> List[str]:
    p = feat["properties"]

    def f(x, fmt):
        try:
            return fmt.format(x if x is not None else float("nan"))
        except Exception:
            return ""

    return [
        p.get("name", ""),
        p.get("codice", ""),
        p.get("desc", ""),
        p.get("timestamp", ""),
        f(p.get("lat"), "{:.9f}"),
        f(p.get("lon"), "{:.9f}"),
        f(p.get("alt_hae"), "{:.3f}"),
        f(p.get("alt_msl"), "{:.3f}"),
        f(p.get("ecef_x"), "{:.4f}"),
        f(p.get("ecef_y"), "{:.4f}"),
        f(p.get("ecef_z"), "{:.4f}"),
        str(p.get("gnss_mode", "")),
        p.get("rtk", "") or "",
        f(p.get("num_sv", 0), "{}"),
        f(p.get("gdop"), "{:.2f}"),
        f(p.get("pdop"), "{:.2f}"),
        f(p.get("hdop"), "{:.2f}"),
        f(p.get("vdop"), "{:.2f}"),
        f(p.get("ndop"), "{:.2f}"),
        f(p.get("edop"), "{:.2f}"),
        f(p.get("tdop"), "{:.2f}"),
        f(p.get("h_acc"), "{:.3f}"),
        f(p.get("v_acc"), "{:.3f}"),
        f(p.get("p_acc"), "{:.3f}"),
        f(p.get("cov_nn"), "{:.4e}"),
        f(p.get("cov_ee"), "{:.4e}"),
        f(p.get("cov_dd"), "{:.4e}"),
        f(p.get("cov_ne"), "{:.4e}"),
        f(p.get("cov_nd"), "{:.4e}"),
        f(p.get("cov_ed"), "{:.4e}"),
        f(p.get("rel_n"), "{:.4f}"),
        f(p.get("rel_e"), "{:.4f}"),
        f(p.get("rel_d"), "{:.4f}"),
        f(p.get("rel_sn"), "{:.3f}"),
        f(p.get("rel_se"), "{:.3f}"),
        f(p.get("rel_sd"), "{:.3f}"),
        f(p.get("baseline"), "{:.4f}"),
        f(p.get("horiz"), "{:.4f}"),
        f(p.get("bearing_deg"), "{:.2f}"),
        f(p.get("slope_deg"), "{:.2f}"),
        f(p.get("sigma_n"), "{:.4f}"),
        f(p.get("sigma_e"), "{:.4f}"),
        f(p.get("sigma_u"), "{:.4f}"),
        f(p.get("n_kept"), "{}"),
        f(p.get("n_samples", 0), "{}"),
        f(p.get("duration_s"), "{:.1f}"),
        f(p.get("interval_s"), "{:.2f}"),
        p.get("start_time", "") or "",
        p.get("end_time", "") or "",
    ]


# ---------- Utility for compare / stakeout ----------
def point_from_feature(f: Dict[str, Any]) -> Dict[str, Any]:
    """Extract point info from a GeoJSON feature."""
    p = f.get("properties", {})
    return {
        "name": p.get("name", f.get("id")),
        "lat": p.get("lat"), "lon": p.get("lon"),
        "altHAE": p.get("alt_hae"), "altMSL": p.get("alt_msl"),
        "X": p.get("ecef_x"), "Y": p.get("ecef_y"), "Z": p.get("ecef_z"),
        "start": None, "end": None
    }


def list_all_points_options() -> List[Tuple[str, str]]:
    """List all points across all surveys as (label, value) tuples."""
    opts = []
    for sid in list_survey_ids():
        try:
            svy = load_survey(sid)
            for f in svy.get("features", []):
                label = f'{sid} / {f.get("id", "")} / {f.get("properties", {}).get("name", "")}'
                val = f'{sid}|{f.get("id", "")}'
                opts.append((label, val))
        except Exception:
            pass
    return opts
