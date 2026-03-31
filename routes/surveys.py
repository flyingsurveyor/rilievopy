"""
Survey routes: list, create, view, add point, export (GeoJSON/CSV/DXF/GPKG/TXT/Map).
"""

import io
import json
import math
import os
import time
from datetime import datetime
from uuid import uuid4

from flask import Blueprint, abort, make_response, render_template, request, send_from_directory, jsonify

from modules.state import STATE
from modules.utils import sanitize_point_name, robust_avg, robust_avg_stats
from modules.survey import (
    list_survey_ids, load_survey, save_survey, create_survey,
    delete_survey_file, next_point_id, point_feature,
    flatten_point_for_csv, CSV_HEADER, SURVEY_DIR, survey_path, point_from_feature,
    backup_survey, survey_audio_dir
)
from modules.exports import (
    build_dxf_advanced, export_geopackage, format_point_txt
)
from modules.active_survey import get_active_survey_id, set_active_survey_id
from modules.codici_punto import load_codici
from modules import settings as _settings
from modules.session_log import log_event, read_log
from modules.geodesy import WGS84_A

bp = Blueprint('surveys', __name__)

# GNSS data freshness thresholds (seconds)
_GNSS_STALE_WARN_S = 5.0   # age below this → run quality gate checks
_GNSS_STALE_ERROR_S = 10.0  # age above this → reject measurement entirely


def _redirect(url: str):
    r = make_response("", 302)
    r.headers["Location"] = url
    return r


def _json_response(payload, status=200):
    return make_response(json.dumps(payload, ensure_ascii=False), status, {"Content-Type": "application/json; charset=utf-8"})


def _safe_note_filename(note_id: str, point_ref: str, kind: str, ext: str = ".webm") -> str:
    point_ref = sanitize_point_name(point_ref or kind or "nota")[:20] or kind
    ext = (ext or ".webm").lower()
    if not ext.startswith('.'):
        ext = '.' + ext
    allowed = {'.webm', '.wav', '.ogg', '.m4a', '.mp4', '.aac'}
    if ext not in allowed:
        ext = '.webm'
    return f"{note_id}_{point_ref}{ext}"


def _survey_note_sections(svy: dict):
    props = svy.setdefault("properties", {})
    return (
        props.setdefault("voice_notes_session", []),
        props.setdefault("voice_notes_pending", []),
    )


def _build_voice_note_meta(sid: str, *, kind: str, point_ref: str = "", point_name: str = "", codice: str = "",
                           note_text: str = "", duration_s=None, mime: str = "audio/webm",
                           filename: str = "", file_size: int = 0, snap: dict | None = None) -> dict:
    snap = snap or {}
    hp = snap.get("HPPOSLLH", {})
    tpv = snap.get("TPV", {})
    dop = snap.get("DOP", {})
    note_id = datetime.now().strftime("vn_%Y%m%d_%H%M%S_") + uuid4().hex[:8]
    return {
        "id": note_id,
        "kind": kind,
        "survey_id": sid,
        "point_ref": point_ref or "",
        "point_name": point_name or point_ref or "",
        "codice": codice or "",
        "note_text": (note_text or "").strip()[:300],
        "created": datetime.now().isoformat(timespec='seconds'),
        "audio_file": filename,
        "audio_mime": mime or "audio/webm",
        "file_size": int(file_size or 0),
        "duration_s": float(duration_s) if duration_s not in (None, "") else None,
        "transcript": "",
        "transcript_status": "pending",
        "gnss_snapshot": {
            "fix_type": tpv.get("rtk", "none"),
            "mode": tpv.get("mode"),
            "numSV": tpv.get("numSV"),
            "time": tpv.get("time"),
            "lat": hp.get("lat", tpv.get("lat")),
            "lon": hp.get("lon", tpv.get("lon")),
            "altHAE": hp.get("altHAE"),
            "altMSL": hp.get("altMSL", tpv.get("altMSL")),
            "hAcc": hp.get("hAcc", tpv.get("hAcc")),
            "vAcc": hp.get("vAcc", tpv.get("vAcc")),
            "pdop": dop.get("pdop"),
            "hdop": dop.get("hdop"),
            "vdop": dop.get("vdop"),
        },
    }


def _note_audio_abspath(note: dict) -> str | None:
    fn = os.path.basename(note.get("audio_file") or "")
    sid = note.get("survey_id")
    if not fn or not sid:
        return None
    return os.path.join(survey_audio_dir(sid), fn)


def _iter_voice_notes(svy: dict):
    session_notes, pending_notes = _survey_note_sections(svy)
    for note in session_notes:
        yield note, session_notes
    for note in pending_notes:
        yield note, pending_notes
    for feat in svy.get("features", []):
        notes = feat.get("properties", {}).get("voice_notes", [])
        for note in notes:
            yield note, notes


def _find_voice_note(svy: dict, note_id: str):
    for note, container in _iter_voice_notes(svy):
        if note.get("id") == note_id:
            return note, container
    return None, None


def _attach_pending_notes_to_feature(svy: dict, feat: dict, pid: str):
    _, pending_notes = _survey_note_sections(svy)
    matched = []
    remaining = []
    for note in pending_notes:
        if note.get("point_ref") == pid:
            note["point_id"] = pid
            matched.append(note)
        else:
            remaining.append(note)
    if matched:
        feat.setdefault("properties", {}).setdefault("voice_notes", []).extend(matched)
    svy.setdefault("properties", {})["voice_notes_pending"] = remaining


def _note_public_payload(note: dict, point_id: str = "") -> dict:
    payload = {
        "id": note.get("id"),
        "kind": note.get("kind"),
        "point_ref": note.get("point_ref"),
        "point_id": note.get("point_id") or point_id or note.get("point_ref"),
        "point_name": note.get("point_name"),
        "codice": note.get("codice"),
        "note_text": note.get("note_text"),
        "created": note.get("created"),
        "duration_s": note.get("duration_s"),
        "transcript": note.get("transcript", ""),
        "transcript_status": note.get("transcript_status"),
        "file_size": note.get("file_size"),
        "gnss_snapshot": note.get("gnss_snapshot", {}),
    }
    if note.get("id") and note.get("survey_id"):
        payload["audio_url"] = f"/survey/{note['survey_id']}/voice-note/{note['id']}/audio"
    return payload


def _collect_voice_notes_for_view(svy: dict):
    props = svy.setdefault("properties", {})
    session_notes = [_note_public_payload(n) for n in props.get("voice_notes_session", [])]
    pending_notes = [_note_public_payload(n) for n in props.get("voice_notes_pending", [])]
    point_notes = []
    for feat in svy.get("features", []):
        pid = feat.get("id", "")
        point_name = feat.get("properties", {}).get("name", pid)
        codice = feat.get("properties", {}).get("codice", "")
        for note in feat.get("properties", {}).get("voice_notes", []):
            payload = _note_public_payload(note, point_id=pid)
            payload["point_ref"] = payload.get("point_ref") or pid
            payload["point_name"] = payload.get("point_name") or point_name
            payload["codice"] = payload.get("codice") or codice
            point_notes.append(payload)

    def _created_key(item):
        return item.get("created") or ""

    session_notes.sort(key=_created_key)
    pending_notes.sort(key=_created_key)
    point_notes.sort(key=_created_key)
    return session_notes, pending_notes, point_notes


def _delete_note_audio_file(note: dict):
    abspath = _note_audio_abspath(note)
    if abspath and os.path.isfile(abspath):
        try:
            os.remove(abspath)
        except Exception:
            pass


def _tpv_age_seconds(snap: dict):
    """Return age of TPV data in seconds, or None if no timestamp."""
    tpv = snap.get("TPV", {})
    ts_str = tpv.get("time")
    if not ts_str:
        return None
    try:
        ts = datetime.fromisoformat(ts_str)
        return (datetime.now() - ts).total_seconds()
    except Exception:
        return None


# ---------- List ----------
@bp.route("/surveys")
def surveys_list():
    ids = list_survey_ids()
    active_sid = get_active_survey_id()
    active_title = None
    items = []
    if not ids:
        items.append('<div class="card">Nessun rilievo. <a class="btn" href="/survey/new">Crea il primo</a></div>')
    for sid in ids:
        try:
            svy = load_survey(sid)
            title = svy.get("properties", {}).get("title", sid)
            desc = svy.get("properties", {}).get("desc", "")
            npt = len(svy.get("features", []))
            if sid == active_sid:
                active_title = title
            is_active = (sid == active_sid)
            set_btn = (
                f'<button class="btn btn-set-active" data-sid="{sid}">📌 Imposta attivo</button>'
                if not is_active else
                '<span class="active-badge">✅ Attivo</span>'
            )
            items.append(
                f'<div class="card"><div class="kv"><span><b>{title}</b> ({sid})</span><span>{npt} punti</span></div>'
                f'<div class="kv"><span>{desc or "-"}</span>'
                f'<span><a class="btn" href="/survey/{sid}">Apri</a> {set_btn}</span></div></div>'
            )
        except Exception as e:
            items.append(f'<div class="card">Errore con {sid}: {e}</div>')
    return render_template('rtk_surveys_list.html',
                           items="\n".join(items),
                           active_sid=active_sid,
                           active_title=active_title)




# ---------- New survey ----------
@bp.route("/survey/new", methods=["GET", "POST"])
def survey_new():
    if request.method == "GET":
        return render_template('rtk_survey_new.html')
    title = (request.form.get("title") or "").strip()[:40]
    desc = (request.form.get("desc") or "").strip()[:1000]
    sid = create_survey(title, desc)
    set_active_survey_id(sid)
    return _redirect(f"/survey/{sid}")


# ---------- Active survey ----------
@bp.route("/api/survey/<sid>/set_active", methods=["POST"])
def survey_set_active(sid):
    try:
        load_survey(sid)
    except FileNotFoundError:
        return make_response(json.dumps({"ok": False, "error": "not found"}), 404,
                             {"Content-Type": "application/json"})
    set_active_survey_id(sid)
    return make_response(json.dumps({"ok": True, "sid": sid}), 200,
                         {"Content-Type": "application/json"})


@bp.route("/survey/active/point")
def survey_active_point():
    active_sid = get_active_survey_id()
    if not active_sid:
        return _redirect("/surveys")
    return _redirect(f"/survey/{active_sid}/point")



@bp.route("/survey/<sid>")
def survey_view(sid):
    try:
        svy = load_survey(sid)
    except FileNotFoundError:
        abort(404)
    props = svy.get("properties", {})
    active_sid = get_active_survey_id()
    rows = []
    points_for_json = []
    for idx, f in enumerate(svy.get("features", [])):
        p = f.get("properties", {})
        hp = p.get("HPPOSLLH", {})
        dop = p.get("DOP", {})
        rp = p.get("RELPOSNED", {})

        def fnum(x, fmt):
            return "-" if x is None else fmt.format(x)

        point_id = f.get('id', '')
        point_name = p.get('name', '')
        codice = p.get('codice', '')
        points_for_json.append({"id": point_id, "name": point_name})

        samp = p.get("sampling", {})
        sigma_N = samp.get("sigma_N")
        sigma_E = samp.get("sigma_E")
        sigma_U = samp.get("sigma_U")

        def fsig(x):
            return f"±{x:.3f} m" if x is not None else "-"

        rows.append(
            "<tr>"
            f"<td><input type='checkbox' class='area-cb' value='{point_id}'/></td>"
            f"<td>{point_id}</td>"
            f"<td>{point_name}</td>"
            f"<td>{codice}</td>"
            f"<td>{fnum(hp.get('lat'), '{:.9f}')}</td>"
            f"<td>{fnum(hp.get('lon'), '{:.9f}')}</td>"
            f"<td>{fnum(hp.get('altHAE'), '{:.3f}')}</td>"
            f"<td>{fnum(dop.get('pdop'), '{:.2f}')}</td>"
            f"<td>{fnum(dop.get('hdop'), '{:.2f}')}</td>"
            f"<td>{fnum(dop.get('vdop'), '{:.2f}')}</td>"
            f"<td>{fnum(rp.get('N'), '{:.4f}')}</td>"
            f"<td>{fnum(rp.get('E'), '{:.4f}')}</td>"
            f"<td>{fnum(rp.get('D'), '{:.4f}')}</td>"
            f"<td>{fnum(rp.get('baseline'), '{:.4f}')}</td>"
            f"<td>{fsig(sigma_N)}</td>"
            f"<td>{fsig(sigma_E)}</td>"
            f"<td>{fsig(sigma_U)}</td>"
            f"<td><a class='btn' href='/survey/{sid}/point/{point_id}.txt'>TXT</a></td>"
            f"<td><button class='btn-delete-point' onclick=\"confirmDeletePoint('{sid}', {idx}, '{point_name}')\">🗑️</button></td>"
            "</tr>"
        )
    num_points = len(rows)
    num_columns = 19
    session_notes, pending_notes, point_notes = _collect_voice_notes_for_view(svy)
    note_counts = {
        "session": len(session_notes),
        "pending": len(pending_notes),
        "point": len(point_notes),
        "total": len(session_notes) + len(pending_notes) + len(point_notes),
    }
    return render_template('rtk_survey_view.html',
                           sid=props.get("id", sid),
                           title=props.get("title", sid),
                           notes=props.get("desc", ""),
                           rows="\n".join(rows) or f"<tr><td colspan='{num_columns}'>(nessun punto)</td></tr>",
                           points_json=json.dumps(points_for_json),
                           num_points=str(num_points),
                           active_sid=active_sid,
                           voice_session_notes_json=json.dumps(session_notes),
                           voice_pending_notes_json=json.dumps(pending_notes),
                           voice_point_notes_json=json.dumps(point_notes),
                           voice_note_counts=note_counts)


# ---------- Update notes ----------
@bp.route("/survey/<sid>/notes", methods=["POST"])
def survey_update_notes(sid):
    try:
        svy = load_survey(sid)
    except FileNotFoundError:
        abort(404)
    notes = (request.form.get("notes") or "").strip()[:1000]
    svy.setdefault("properties", {})["desc"] = notes
    save_survey(sid, svy)
    return _redirect(f"/survey/{sid}")


# ---------- Add point ----------
@bp.route("/survey/<sid>/point", methods=["GET", "POST"])
def survey_point(sid):
    if request.method == "GET":
        try:
            svy = load_survey(sid)
        except FileNotFoundError:
            abort(404)
        next_pid = next_point_id(svy)
        codici_data = load_codici()
        active_title = svy.get("properties", {}).get("title", sid)
        num_points = len(svy.get("features", []))
        saved = request.args.get("saved", "")
        savedname = request.args.get("savedname", "")
        savedcodice = request.args.get("savedcodice", "")
        props = svy.setdefault("properties", {})
        session_notes = [_note_public_payload(n) for n in props.get("voice_notes_session", [])[-8:]][::-1]
        pending_notes = [_note_public_payload(n) for n in props.get("voice_notes_pending", []) if n.get("point_ref") == next_pid][-8:][::-1]
        return render_template('rtk_survey_point_form.html',
                               sid=sid,
                               next_pid=next_pid,
                               active_title=active_title,
                               num_points=num_points,
                               codici_json=json.dumps(codici_data),
                               saved=saved,
                               savedname=savedname,
                               savedcodice=savedcodice,
                               pending_notes_json=json.dumps(pending_notes),
                               session_notes_json=json.dumps(session_notes))

    name = sanitize_point_name(request.form.get("name", ""))
    desc = (request.form.get("desc", "") or "").strip()[:300]
    codice = (request.form.get("codice", "") or "").strip()[:20]
    dur_form = request.form.get("dur", "").strip()
    force = request.form.get("force", "0") == "1"
    duration = None
    if dur_form:
        try:
            duration = float(dur_form)
        except Exception:
            duration = None
    if duration is None:
        try:
            duration = float(request.args.get("dur", 10.0))
        except Exception:
            duration = 10.0
    try:
        interval = float(request.args.get("step", 0.5))
    except Exception:
        interval = 0.5

    # ---------- Pre-sampling GNSS checks ----------
    if not force:
        pre_snap = STATE.snapshot()
        age = _tpv_age_seconds(pre_snap)

        # Check for stale or missing GNSS data (>_GNSS_STALE_ERROR_S seconds)
        if age is None or age > _GNSS_STALE_ERROR_S:
            try:
                log_event("point_failed", sid, {"reason": "no_gnss_data"})
            except Exception:
                pass
            return make_response(json.dumps({
                "ok": False,
                "error": "no_gnss_data",
                "message": "Nessun dato GNSS ricevuto. Verificare la connessione."
            }), 200, {"Content-Type": "application/json"})

        # Quality gate checks (only when GNSS data is fresh, i.e., age < _GNSS_STALE_WARN_S)
        if age < _GNSS_STALE_WARN_S:
            cfg = _settings.load_settings()
            if cfg.get("rtk_quality_gate", True):
                tpv_pre = pre_snap.get("TPV", {})
                hp_pre = pre_snap.get("HPPOSLLH", {})
                dop_pre = pre_snap.get("DOP", {})
                warnings = []

                rtk_val = tpv_pre.get("rtk", "none")
                if rtk_val == "none":
                    warnings.append(f"Nessun fix RTK (stato: {rtk_val})")

                hacc = hp_pre.get("hAcc")
                max_hacc = cfg.get("max_hacc", 0.05)
                if hacc is not None and hacc > max_hacc:
                    warnings.append(
                        f"Precisione orizzontale elevata: {hacc:.3f} m (soglia: {max_hacc} m)"
                    )

                pdop = dop_pre.get("pdop")
                max_pdop = cfg.get("max_pdop", 3.0)
                if pdop is not None and pdop > max_pdop:
                    warnings.append(f"PDOP elevato: {pdop:.2f} (soglia: {max_pdop})")

                numsv = tpv_pre.get("numSV")
                min_sv = cfg.get("min_sv", 8)
                if numsv is not None and numsv < min_sv:
                    warnings.append(f"Pochi satelliti: {numsv} (minimo: {min_sv})")

                if warnings:
                    return make_response(json.dumps({
                        "ok": False,
                        "quality_warning": True,
                        "warnings": warnings
                    }), 200, {"Content-Type": "application/json"})

    start_ts = datetime.now()
    samples = []
    n_iters = max(1, int(duration / interval))
    for _ in range(n_iters):
        samples.append(STATE.snapshot())
        time.sleep(interval)
    end_ts = datetime.now()

    def collect_key(group, key):
        vals = []
        for s in samples:
            g = s.get(group, {})
            v = g.get(key, None)
            if isinstance(v, (int, float)):
                vals.append(v)
        return robust_avg(vals)

    mode = int(round(collect_key("TPV", "mode") or 0))
    rtk = samples[-1].get("TPV", {}).get("rtk", "-")
    numSV = int(round(collect_key("TPV", "numSV") or 0))

    lat = collect_key("HPPOSLLH", "lat") or collect_key("TPV", "lat")
    lon = collect_key("HPPOSLLH", "lon") or collect_key("TPV", "lon")
    altHAE = collect_key("HPPOSLLH", "altHAE")
    altMSL = collect_key("HPPOSLLH", "altMSL") or collect_key("TPV", "altMSL")
    hAcc = collect_key("HPPOSLLH", "hAcc") or collect_key("TPV", "hAcc")
    vAcc = collect_key("HPPOSLLH", "vAcc") or collect_key("TPV", "vAcc")

    X = collect_key("HPPOSECEF", "X")
    Y = collect_key("HPPOSECEF", "Y")
    Z = collect_key("HPPOSECEF", "Z")
    pAcc = collect_key("HPPOSECEF", "pAcc")

    gdop = collect_key("DOP", "gdop"); pdop = collect_key("DOP", "pdop")
    hdop = collect_key("DOP", "hdop"); vdop = collect_key("DOP", "vdop")
    ndop = collect_key("DOP", "ndop"); edop = collect_key("DOP", "edop")
    tdop = collect_key("DOP", "tdop")

    covNN = collect_key("COV", "covNN"); covEE = collect_key("COV", "covEE")
    covDD = collect_key("COV", "covDD"); covNE = collect_key("COV", "covNE")
    covND = collect_key("COV", "covND"); covED = collect_key("COV", "covED")

    relN = collect_key("RELPOS", "N"); relE = collect_key("RELPOS", "E")
    relD = collect_key("RELPOS", "D")
    relsN = collect_key("RELPOS", "sN"); relsE = collect_key("RELPOS", "sE")
    relsD = collect_key("RELPOS", "sD")
    horiz = baseline = bearing = slope = None
    if relN is not None and relE is not None and relD is not None:
        horiz = math.hypot(relN, relE)
        baseline = math.sqrt(horiz * horiz + relD * relD)
        bearing = math.degrees(math.atan2(relE, relN))
        bearing = bearing + 360 if bearing < 0 else bearing
        slope = math.degrees(math.atan2(-relD, horiz)) if horiz else 0.0

    # ---------- Sigma (std dev) of position samples ----------
    def collect_vals(group, key):
        vals = []
        for s in samples:
            g = s.get(group, {})
            v = g.get(key, None)
            if isinstance(v, (int, float)):
                vals.append(v)
        return vals

    _lat_stats = robust_avg_stats(collect_vals("HPPOSLLH", "lat"))
    _lon_stats = robust_avg_stats(collect_vals("HPPOSLLH", "lon"))
    _alt_stats = robust_avg_stats(collect_vals("HPPOSLLH", "altHAE"))

    _mean_lat_rad = math.radians(lat or 0.0)
    sigma_N = None
    sigma_E = None
    sigma_U = None
    n_kept = _alt_stats.get("n_kept", 0)
    if _lat_stats.get("sigma") is not None:
        sigma_N = _lat_stats["sigma"] * math.radians(1.0) * WGS84_A
    if _lon_stats.get("sigma") is not None:
        sigma_E = _lon_stats["sigma"] * math.radians(1.0) * WGS84_A * math.cos(_mean_lat_rad)
    if _alt_stats.get("sigma") is not None:
        sigma_U = _alt_stats["sigma"]

    stats = {
        "mode": mode, "rtk": rtk, "numSV": numSV,
        "hAcc": hAcc, "vAcc": vAcc, "pAcc": pAcc,
        "gdop": gdop, "pdop": pdop, "hdop": hdop, "vdop": vdop,
        "ndop": ndop, "edop": edop, "tdop": tdop,
        "covNN": covNN, "covEE": covEE, "covDD": covDD,
        "covNE": covNE, "covND": covND, "covED": covED,
        "relN": relN, "relE": relE, "relD": relD,
        "relsN": relsN, "relsE": relsE, "relsD": relsD,
        "baseline": baseline, "horiz": horiz, "bearing": bearing, "slope": slope,
        "sigma_N": sigma_N, "sigma_E": sigma_E, "sigma_U": sigma_U, "n_kept": n_kept,
    }

    try:
        svy = load_survey(sid)
    except FileNotFoundError:
        abort(404)
    pid = next_point_id(svy)
    start_iso = start_ts.isoformat(timespec='seconds')
    end_iso = end_ts.isoformat(timespec='seconds')

    feat = point_feature(
        pid, lat, lon, altHAE, altMSL, X, Y, Z, stats,
        {"name": name, "codice": codice, "desc": desc, "duration": duration,
         "interval": interval, "n_samples": len(samples),
         "start": start_iso, "end": end_iso}
    )
    _attach_pending_notes_to_feature(svy, feat, pid)
    svy.setdefault("features", []).append(feat)
    backup_survey(sid)
    save_survey(sid, svy)

    try:
        log_event("point_saved", sid, {"pid": pid, "codice": codice, "rtk": rtk})
    except Exception:
        pass

    return _redirect(f"/survey/{sid}/point?saved={pid}&savedname={name}&savedcodice={codice}")


# ---------- Voice notes ----------
@bp.route("/api/survey/<sid>/voice-note", methods=["POST"])
def survey_voice_note_upload(sid):
    try:
        svy = load_survey(sid)
    except FileNotFoundError:
        abort(404)

    audio = request.files.get("audio")
    if not audio or not audio.filename:
        return _json_response({"ok": False, "error": "missing_audio", "message": "Nessun file audio ricevuto."}, 400)

    kind = (request.form.get("kind") or "point").strip().lower()
    if kind not in ("point", "session"):
        kind = "point"
    point_ref = sanitize_point_name(request.form.get("point_ref", "") or "")[:20]
    point_name = sanitize_point_name(request.form.get("point_name", "") or point_ref)[:20]
    codice = (request.form.get("codice") or "").strip()[:20]
    note_text = (request.form.get("note_text") or "").strip()[:300]
    duration_s = request.form.get("duration_s")
    mime = (request.form.get("mime") or audio.mimetype or "audio/webm").strip()[:80]

    snap = STATE.snapshot()
    tmp_meta = _build_voice_note_meta(sid, kind=kind, point_ref=point_ref, point_name=point_name,
                                      codice=codice, note_text=note_text, duration_s=duration_s,
                                      mime=mime, filename="", file_size=0, snap=snap)
    ext = os.path.splitext(audio.filename or "")[1] or ('.' + mime.split('/')[-1].split(';')[0] if '/' in mime else '.webm')
    filename = _safe_note_filename(tmp_meta['id'], point_ref or point_name or kind, ext=ext, kind=kind)
    abspath = os.path.join(survey_audio_dir(sid), filename)
    audio.save(abspath)
    file_size = os.path.getsize(abspath) if os.path.exists(abspath) else 0

    note = _build_voice_note_meta(sid, kind=kind, point_ref=point_ref, point_name=point_name,
                                  codice=codice, note_text=note_text, duration_s=duration_s,
                                  mime=mime, filename=filename, file_size=file_size, snap=snap)
    note['id'] = tmp_meta['id']

    session_notes, pending_notes = _survey_note_sections(svy)
    if kind == 'session':
        session_notes.append(note)
    else:
        pending_notes.append(note)

    backup_survey(sid)
    save_survey(sid, svy)
    try:
        log_event("voice_note_saved", sid, {"note_id": note["id"], "kind": kind, "point_ref": point_ref})
    except Exception:
        pass
    return _json_response({"ok": True, "note": _note_public_payload(note)})


@bp.route("/survey/<sid>/voice-note/<note_id>/audio")
def survey_voice_note_audio(sid, note_id):
    try:
        svy = load_survey(sid)
    except FileNotFoundError:
        abort(404)
    note, _ = _find_voice_note(svy, note_id)
    if not note:
        abort(404)
    abspath = _note_audio_abspath(note)
    if not abspath or not os.path.isfile(abspath):
        abort(404)
    return send_from_directory(os.path.dirname(abspath), os.path.basename(abspath), mimetype=note.get('audio_mime') or 'audio/webm')


@bp.route("/api/survey/<sid>/voice-notes")
def survey_voice_notes_list(sid):
    try:
        svy = load_survey(sid)
    except FileNotFoundError:
        abort(404)
    session_notes, pending_notes, point_notes = _collect_voice_notes_for_view(svy)
    return _json_response({
        "ok": True,
        "session_notes": session_notes,
        "pending_notes": pending_notes,
        "point_notes": point_notes,
    })


@bp.route("/api/survey/<sid>/voice-note/<note_id>", methods=["POST"])
def survey_voice_note_update(sid, note_id):
    try:
        svy = load_survey(sid)
    except FileNotFoundError:
        abort(404)
    note, _container = _find_voice_note(svy, note_id)
    if not note:
        return _json_response({"ok": False, "error": "not_found", "message": "Nota non trovata."}, 404)

    payload = request.get_json(silent=True) or {}
    note_text = (request.form.get("note_text") if request.form else None)
    if note_text is None:
        note_text = payload.get("note_text", note.get("note_text", ""))
    note["note_text"] = (note_text or "").strip()[:300]

    if note.get("kind") == "session":
        if (request.form and "point_name" in request.form) or (not request.form and "point_name" in payload):
            note["point_name"] = ((request.form.get("point_name") if request.form else payload.get("point_name")) or "").strip()[:80]
        if (request.form and "codice" in request.form) or (not request.form and "codice" in payload):
            note["codice"] = ((request.form.get("codice") if request.form else payload.get("codice")) or "").strip()[:20]

    backup_survey(sid)
    save_survey(sid, svy)
    try:
        log_event("voice_note_updated", sid, {"note_id": note_id})
    except Exception:
        pass
    return _json_response({"ok": True, "note": _note_public_payload(note, point_id=note.get("point_id") or note.get("point_ref") or "")})


@bp.route("/api/survey/<sid>/voice-note/<note_id>/delete", methods=["POST"])
def survey_voice_note_delete(sid, note_id):
    try:
        svy = load_survey(sid)
    except FileNotFoundError:
        abort(404)
    note, container = _find_voice_note(svy, note_id)
    if not note or container is None:
        return _json_response({"ok": False, "error": "not_found", "message": "Nota non trovata."}, 404)

    _delete_note_audio_file(note)
    container[:] = [n for n in container if n.get("id") != note_id]
    backup_survey(sid)
    save_survey(sid, svy)
    try:
        log_event("voice_note_deleted", sid, {"note_id": note_id})
    except Exception:
        pass
    return _json_response({"ok": True, "deleted_id": note_id})


# ---------- Downloads ----------
@bp.route("/survey/<sid>.geojson")
def survey_download_geojson(sid):
    path = survey_path(sid)
    if not os.path.isfile(path):
        abort(404)
    return send_from_directory(SURVEY_DIR, os.path.basename(path),
                               as_attachment=True, download_name=f"{sid}.geojson")


@bp.route("/survey/<sid>.csv")
def survey_download_csv(sid):
    try:
        svy = load_survey(sid)
    except FileNotFoundError:
        abort(404)
    buf = io.StringIO()
    buf.write("id," + ",".join(CSV_HEADER) + "\n")
    for f in svy.get("features", []):
        row = [f.get("id", "")] + flatten_point_for_csv(f)
        buf.write(",".join(row) + "\n")
    resp = make_response(buf.getvalue())
    resp.headers["Content-Type"] = "text/csv; charset=utf-8"
    resp.headers["Content-Disposition"] = f'attachment; filename="{sid}.csv"'
    return resp


@bp.route("/survey/<sid>/map")
def survey_map(sid):
    try:
        svy = load_survey(sid)
    except FileNotFoundError:
        abort(404)
    if not svy.get("features"):
        return make_response("Nessun punto da visualizzare", 400)
    return render_template('rtk_map.html', sid=sid, geojson=json.dumps(svy))


@bp.route("/survey/<sid>/cad")
def survey_cad(sid):
    """Open the CAD editor pre-loaded with this survey."""
    try:
        svy = load_survey(sid)
    except FileNotFoundError:
        abort(404)
    title = svy.get("properties", {}).get("title", sid)
    return render_template('rtk_survey_cad.html', sid=sid, title=title)


@bp.route("/survey/<sid>.dxf")
def survey_download_dxf(sid):
    try:
        svy = load_survey(sid)
    except FileNotFoundError:
        abort(404)
    try:
        text_height = float(request.args.get("text_height", "0.1"))
        show_precision = request.args.get("precision", "false").lower() in ("true", "1", "yes")
        quota_decimals = int(request.args.get("decimals", "3"))
        dxf_text = build_dxf_advanced(svy, mode="3d", text_height=text_height,
                                      show_precision=show_precision, quota_decimals=quota_decimals)
    except Exception as e:
        return make_response(f"Errore DXF: {e}", 500)
    resp = make_response(dxf_text)
    resp.headers["Content-Type"] = "application/dxf; charset=utf-8"
    resp.headers["Content-Disposition"] = f'attachment; filename="{sid}.dxf"'
    return resp


@bp.route("/survey/<sid>_2d.dxf")
def survey_download_dxf_2d(sid):
    try:
        svy = load_survey(sid)
    except FileNotFoundError:
        abort(404)
    try:
        text_height = float(request.args.get("text_height", "0.1"))
        show_precision = request.args.get("precision", "false").lower() in ("true", "1", "yes")
        quota_decimals = int(request.args.get("decimals", "3"))
        dxf_text = build_dxf_advanced(svy, mode="2d", text_height=text_height,
                                      show_precision=show_precision, quota_decimals=quota_decimals)
    except Exception as e:
        return make_response(f"Errore DXF: {e}", 500)
    resp = make_response(dxf_text)
    resp.headers["Content-Type"] = "application/dxf; charset=utf-8"
    resp.headers["Content-Disposition"] = f'attachment; filename="{sid}_2d.dxf"'
    return resp


@bp.route("/survey/<sid>.gpkg")
def survey_download_geopackage(sid):
    try:
        svy = load_survey(sid)
    except FileNotFoundError:
        abort(404)
    if not svy.get("features"):
        return make_response("Nessun punto nel rilievo", 400)
    filepath = export_geopackage(svy, sid)
    if not filepath:
        return make_response("Errore nella creazione del GeoPackage", 500)
    try:
        with open(filepath, "rb") as fh:
            gpkg_data = fh.read()
        try:
            os.unlink(filepath)
        except Exception:
            pass
        resp = make_response(gpkg_data)
        resp.headers["Content-Type"] = "application/geopackage+sqlite3"
        resp.headers["Content-Disposition"] = f'attachment; filename="{sid}.gpkg"'
        return resp
    except Exception as e:
        return make_response(f"Errore lettura GeoPackage: {e}", 500)


@bp.route("/survey/<sid>/point/<pid>.txt")
def survey_point_txt(sid, pid):
    try:
        svy = load_survey(sid)
    except FileNotFoundError:
        abort(404)
    feat = next((f for f in svy.get("features", []) if f.get("id") == pid), None)
    if not feat:
        abort(404)
    txt = format_point_txt(feat, sid)
    resp = make_response(txt)
    resp.headers["Content-Type"] = "text/plain; charset=utf-8"
    resp.headers["Content-Disposition"] = f'attachment; filename="{sid}-{pid}.txt"'
    return resp


# ---------- Delete ----------
@bp.route("/survey/<sid>/point/<int:point_index>/delete", methods=["POST"])
def delete_point(sid, point_index):
    try:
        svy = load_survey(sid)
    except FileNotFoundError:
        return make_response({"error": "Survey not found"}, 404)
    features = svy.get("features", [])
    if point_index < 0 or point_index >= len(features):
        return make_response({"error": f"Point not found at index {point_index}"}, 404)
    removed = features.pop(point_index)
    for note in removed.get("properties", {}).get("voice_notes", []):
        _delete_note_audio_file(note)
    save_survey(sid, svy)
    return make_response({
        "success": True,
        "message": f"Punto {removed.get('id', '')} eliminato"
    }, 200)


@bp.route("/survey/<sid>/delete", methods=["POST"])
def delete_survey(sid):
    try:
        load_survey(sid)
    except FileNotFoundError:
        return jsonify({"success": False, "error": "Survey not found"}), 404

    data = request.get_json(silent=True) or {}
    confirm = (data.get("confirmation") or "").strip()

    if confirm != "ELIMINA":
        return jsonify({
            "success": False,
            "error": "Conferma non valida. Scrivi 'ELIMINA' per confermare."
        }), 400

    delete_survey_file(sid)
    return jsonify({
        "success": True,
        "message": f"Rilievo {sid} eliminato"
    }), 200


# ---------- Area ----------
@bp.route("/survey/<sid>/area", methods=["POST"])
def survey_calc_area(sid):
    from modules.cogo import calc_area_perimeter
    from modules.geodesy import geodetic_to_ecef, ecef_delta_to_enu

    data = request.get_json()
    if not data:
        return make_response({"error": "No data"}, 400)
    point_ids = data.get("point_ids") or data.get("points", [])
    if len(point_ids) < 3:
        return make_response({"error": "Servono almeno 3 punti"}, 400)

    try:
        svy = load_survey(sid)
    except FileNotFoundError:
        return make_response({"error": "Survey not found"}, 404)

    features = svy.get("features", [])
    ordered_feats = []
    for pid in point_ids:
        f = next((f for f in features if f.get("id") == pid), None)
        if f:
            ordered_feats.append(f)

    if len(ordered_feats) < 3:
        return make_response({"error": "Punti insufficienti con coordinate valide"}, 400)

    # Get reference point
    first_p = ordered_feats[0].get("properties", {}).get("HPPOSLLH", {})
    lat0, lon0 = first_p.get("lat"), first_p.get("lon")
    if lat0 is None or lon0 is None:
        return make_response({"error": "Coordinate primo punto non valide"}, 400)
    alt0 = first_p.get("altMSL") or first_p.get("altHAE", 0.0)
    X0, Y0, Z0 = geodetic_to_ecef(lat0, lon0, alt0)

    enu_points = []
    for f in ordered_feats:
        hp = f.get("properties", {}).get("HPPOSLLH", {})
        lat, lon = hp.get("lat"), hp.get("lon")
        if lat is None or lon is None:
            continue
        alt = hp.get("altMSL") or hp.get("altHAE", 0.0)
        X, Y, Z = geodetic_to_ecef(lat, lon, alt)
        e, n, u = ecef_delta_to_enu(X - X0, Y - Y0, Z - Z0, lat0, lon0)
        enu_points.append((e, n))

    if len(enu_points) < 3:
        return make_response({"error": "Punti insufficienti con coordinate valide"}, 400)

    result = calc_area_perimeter(enu_points)
    return make_response(result, 200)


# ---------- Compat / old routes ----------
@bp.route("/survey", methods=["GET"])
def survey_redirect():
    return _redirect("/surveys")


@bp.route("/files/<path:filename>")
def files_route(filename):
    path = os.path.join(SURVEY_DIR, filename)
    if not os.path.isfile(path):
        abort(404)
    return send_from_directory(SURVEY_DIR, filename, as_attachment=True)


# ---------- Session log ----------
@bp.route("/api/session_log")
def api_session_log():
    try:
        limit = int(request.args.get("limit", 200))
    except (ValueError, TypeError):
        limit = 200
    events = read_log(limit=limit)
    return make_response(json.dumps(events, ensure_ascii=False),
                         200, {"Content-Type": "application/json"})
