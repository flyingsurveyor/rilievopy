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
SURVEY_DIR = os.path.abspath(os.path.join(os.getcwd(), "surveys"))
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
    return os.path.join(SURVEY_DIR, f"{sid}{SURVEY_EXT}")


# ---------- CRUD ----------
def list_survey_ids() -> List[str]:
    ids = []
    try:
        for fn in os.listdir(SURVEY_DIR):
            if fn.endswith(SURVEY_EXT):
                ids.append(fn[:-len(SURVEY_EXT)])
    except FileNotFoundError:
        pass
    ids.sort(key=lambda sid: os.path.getmtime(survey_path(sid)), reverse=True)
    return ids


def load_survey(sid: str) -> Dict[str, Any]:
    path = survey_path(sid)
    with SURVEY_LOCK:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)


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
            "name": meta.get("name") or pid,
            "codice": meta.get("codice", ""),
            "desc": meta.get("desc") or "",
            "timestamp": now_iso(),
            "TPV": {"mode": stats.get("mode"), "rtk": stats.get("rtk"), "numSV": stats.get("numSV")},
            "HPPOSLLH": {"lat": lat, "lon": lon, "altHAE": altHAE, "altMSL": altMSL,
                         "hAcc": stats.get("hAcc"), "vAcc": stats.get("vAcc")},
            "HPPOSECEF": {"X": X, "Y": Y, "Z": Z, "pAcc": stats.get("pAcc")},
            "DOP": {"gdop": stats.get("gdop"), "pdop": stats.get("pdop"),
                    "hdop": stats.get("hdop"), "vdop": stats.get("vdop"),
                    "ndop": stats.get("ndop"), "edop": stats.get("edop"),
                    "tdop": stats.get("tdop")},
            "COV": {"NN": stats.get("covNN"), "EE": stats.get("covEE"), "DD": stats.get("covDD"),
                    "NE": stats.get("covNE"), "ND": stats.get("covND"), "ED": stats.get("covED")},
            "RELPOSNED": {"N": stats.get("relN"), "E": stats.get("relE"), "D": stats.get("relD"),
                          "sN": stats.get("relsN"), "sE": stats.get("relsE"), "sD": stats.get("relsD"),
                          "baseline": stats.get("baseline"), "horiz": stats.get("horiz"),
                          "bearingDeg": stats.get("bearing"), "slopeDeg": stats.get("slope")},
            "sampling": {
                "start_iso": meta.get("start"),
                "end_iso": meta.get("end"),
                "duration_s": meta.get("duration", 10.0),
                "interval_s": meta.get("interval", 0.5),
                "n_samples": meta.get("n_samples", 0),
                "sigma_N": stats.get("sigma_N"),
                "sigma_E": stats.get("sigma_E"),
                "sigma_U": stats.get("sigma_U"),
                "n_kept": stats.get("n_kept"),
            }
        }
    }




# ---------- Media / voice notes ----------
def survey_media_dir(sid: str) -> str:
    return os.path.join(SURVEY_DIR, "media", sid)


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
    "name", "codice", "lat", "lon", "altHAE", "altMSL",
    "X", "Y", "Z", "mode", "rtk", "numSV",
    "gdop", "pdop", "hdop", "vdop", "ndop", "edop", "tdop",
    "hAcc", "vAcc", "pAcc",
    "covNN", "covEE", "covDD", "covNE", "covND", "covED",
    "relN", "relE", "relD", "relsN", "relsE", "relsD",
    "baseline", "bearing", "slope",
    "sigma_N", "sigma_E", "sigma_U", "n_kept"
]


def flatten_point_for_csv(feat: Dict[str, Any]) -> List[str]:
    p = feat["properties"]
    hp = p.get("HPPOSLLH", {})
    ecef = p.get("HPPOSECEF", {})
    dop = p.get("DOP", {})
    cov = p.get("COV", {})
    rp = p.get("RELPOSNED", {})
    samp = p.get("sampling", {})

    def f(x, fmt):
        try:
            return fmt.format(x if x is not None else float("nan"))
        except Exception:
            return ""

    return [
        p.get("name", ""),
        p.get("codice", ""),
        f(hp.get("lat"), "{:.9f}"),
        f(hp.get("lon"), "{:.9f}"),
        f(hp.get("altHAE"), "{:.3f}"),
        f(hp.get("altMSL"), "{:.3f}"),
        f(ecef.get("X"), "{:.4f}"),
        f(ecef.get("Y"), "{:.4f}"),
        f(ecef.get("Z"), "{:.4f}"),
        str(p.get("TPV", {}).get("mode", "")),
        p.get("TPV", {}).get("rtk", ""),
        f(p.get("TPV", {}).get("numSV", 0), "{}"),
        f(dop.get("gdop"), "{:.2f}"),
        f(dop.get("pdop"), "{:.2f}"),
        f(dop.get("hdop"), "{:.2f}"),
        f(dop.get("vdop"), "{:.2f}"),
        f(dop.get("ndop"), "{:.2f}"),
        f(dop.get("edop"), "{:.2f}"),
        f(dop.get("tdop"), "{:.2f}"),
        f(hp.get("hAcc"), "{:.3f}"),
        f(hp.get("vAcc"), "{:.3f}"),
        f(ecef.get("pAcc"), "{:.3f}"),
        f(cov.get("NN"), "{:.4e}"),
        f(cov.get("EE"), "{:.4e}"),
        f(cov.get("DD"), "{:.4e}"),
        f(cov.get("NE"), "{:.4e}"),
        f(cov.get("ND"), "{:.4e}"),
        f(cov.get("ED"), "{:.4e}"),
        f(rp.get("N"), "{:.4f}"),
        f(rp.get("E"), "{:.4f}"),
        f(rp.get("D"), "{:.4f}"),
        f(rp.get("sN"), "{:.3f}"),
        f(rp.get("sE"), "{:.3f}"),
        f(rp.get("sD"), "{:.3f}"),
        f(rp.get("baseline"), "{:.4f}"),
        f(rp.get("bearingDeg"), "{:.2f}"),
        f(rp.get("slopeDeg"), "{:.2f}"),
        f(samp.get("sigma_N"), "{:.4f}"),
        f(samp.get("sigma_E"), "{:.4f}"),
        f(samp.get("sigma_U"), "{:.4f}"),
        f(samp.get("n_kept"), "{}"),
    ]


# ---------- Utility for compare / stakeout ----------
def point_from_feature(f: Dict[str, Any]) -> Dict[str, Any]:
    """Extract point info from a GeoJSON feature."""
    p = f.get("properties", {})
    hp = p.get("HPPOSLLH", {})
    ecef = p.get("HPPOSECEF", {})
    return {
        "name": p.get("name", f.get("id")),
        "lat": hp.get("lat"), "lon": hp.get("lon"),
        "altHAE": hp.get("altHAE"), "altMSL": hp.get("altMSL"),
        "X": ecef.get("X"), "Y": ecef.get("Y"), "Z": ecef.get("Z"),
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
