"""
PPK routes blueprint.
All post-processing routes: convbin, RINEX QC, rnx2rtkp, posview, files, uploads.
Adapted from rtklib-web server.py.
"""

import os
import json
import subprocess
import shutil
import threading
import time
import uuid
from collections import deque
from datetime import datetime
from pathlib import Path

from flask import (
    Blueprint, render_template, request, jsonify,
    send_file, redirect, url_for
)
from werkzeug.utils import secure_filename

from modules.ppk_config import PPKConfig as Cfg
from modules.rinex_parser import RinexObsParser
from modules.convbin import ConvbinWrapper
from modules.rnx2rtkp import Rnx2rtkpWrapper
from modules.conf_manager import (
    CONF_SCHEMA, NAVSYS_BITS, SECTIONS, parse_conf, write_conf,
    get_defaults, validate_conf, merge_conf, bitmask_to_navsys
)
from modules.pos_parser import (
    parse_pos, decimate_for_charts, Q_LABELS, Q_COLORS,
    compute_session_stats, weighted_mean_station,
)
from modules import settings as _settings
from modules.rtkino_manager import RTKINO

bp = Blueprint('ppk', __name__)

# Async job tracking: {job_id: {status, started_at, ...}}
running_processes = {}
running_processes_lock = threading.Lock()

# Ring-buffer size for live log tail
_LOG_RING = 50
# Completed job TTL in seconds (1 hour)
_JOB_TTL = 3600


# ─── Error handlers ──────────────────────────

@bp.errorhandler(413)
def handle_too_large(e):
    return jsonify({'error': 'File too large — maximum size is 2 GB'}), 413


@bp.errorhandler(408)
def handle_timeout(e):
    return jsonify({'error': 'Request timeout — file upload took too long'}), 408


# ─── Utilities ────────────────────────────────

def _is_within_dir(path, directory):
    """Check that 'path' is safely within 'directory', avoiding prefix attacks."""
    real_path = os.path.realpath(path)
    real_dir = os.path.realpath(directory)
    return real_path == real_dir or real_path.startswith(real_dir + os.sep)

def list_files(directory, extensions):
    files = []
    if not os.path.isdir(directory):
        return files
    for f in sorted(os.listdir(directory)):
        full = os.path.join(directory, f)
        if os.path.isfile(full):
            ext = os.path.splitext(f)[1].lower()
            if ext in extensions or not extensions:
                files.append({
                    'name': f, 'path': full,
                    'size': format_size(os.path.getsize(full)),
                    'modified': datetime.fromtimestamp(
                        os.path.getmtime(full)).strftime('%Y-%m-%d %H:%M'),
                })
    return files


def format_size(size_bytes):
    for unit in ('B', 'KB', 'MB', 'GB'):
        if size_bytes < 1024:
            return f'{size_bytes:.1f} {unit}'
        size_bytes /= 1024
    return f'{size_bytes:.1f} TB'


# ─── PPK Dashboard ───────────────────────────

@bp.route('/ppk/home')
def ppk_index():
    # PPK Home page removed — redirect permanently to first useful page
    return redirect(url_for('ppk.convbin_page'))


@bp.route('/api/ppk/stats')
def api_stats():
    def count_files(d, exts=None):
        if not os.path.isdir(d):
            return 0
        c = 0
        for f in os.listdir(d):
            if os.path.isfile(os.path.join(d, f)):
                if exts is None or os.path.splitext(f)[1].lower() in exts:
                    c += 1
        return c

    convbin_ok = os.path.isfile(Cfg.CONVBIN_BIN) and os.access(Cfg.CONVBIN_BIN, os.X_OK)
    rnx2rtkp_ok = os.path.isfile(Cfg.RNX2RTKP_BIN) and os.access(Cfg.RNX2RTKP_BIN, os.X_OK)

    return jsonify({
        'uploads': count_files(Cfg.UPLOAD_DIR),
        'rinex': count_files(Cfg.RINEX_DIR),
        'results': count_files(Cfg.RESULTS_DIR),
        'configs': count_files(Cfg.CONF_DIR, ('.conf',)),
        'convbin_ok': convbin_ok,
        'rnx2rtkp_ok': rnx2rtkp_ok,
    })


@bp.route('/api/ppk/tools')
def api_tools():
    tools = {}
    for name, path in [('convbin', Cfg.CONVBIN_BIN),
                        ('rnx2rtkp', Cfg.RNX2RTKP_BIN),
                        ('str2str', Cfg.STR2STR_BIN),
                        ('pos2kml', Cfg.POS2KML_BIN)]:
        found = os.path.isfile(path) and os.access(path, os.X_OK)
        in_path = shutil.which(name)
        tools[name] = {
            'configured_path': path,
            'available': found,
            'in_system_path': in_path,
        }
        if found:
            try:
                r = subprocess.run([path, '--version'], capture_output=True, text=True, timeout=5)
                tools[name]['version'] = (r.stdout + r.stderr).strip()[:100]
            except Exception:
                tools[name]['version'] = None

    return jsonify(tools)


# ─── File Explorer ────────────────────────────

@bp.route('/files')
@bp.route('/files/<path:subpath>')
def file_explorer(subpath=''):
    base_dirs = {
        'uploads': Cfg.UPLOAD_DIR,
        'rinex': Cfg.RINEX_DIR,
        'results': Cfg.RESULTS_DIR,
        'pos': Cfg.POS_DIR,
        'conf': Cfg.CONF_DIR,
        'antex': Cfg.ANTEX_DIR,
    }

    if subpath:
        parts = subpath.split('/', 1)
        base_key = parts[0]
        rel_path = parts[1].lstrip('/') if len(parts) > 1 else ''
    else:
        base_key = None
        rel_path = ''

    if base_key and base_key in base_dirs:
        browse_path = os.path.join(base_dirs[base_key], rel_path)
        if not _is_within_dir(browse_path, base_dirs[base_key]):
            return "Access denied", 403
    else:
        entries = []
        for key, path in base_dirs.items():
            try:
                count = sum(1 for f in Path(path).rglob('*') if f.is_file()) if Path(path).is_dir() else 0
            except OSError:
                count = 0
            entries.append({
                'name': key, 'is_dir': True,
                'size': f'{count} files', 'modified': '', 'path': key,
            })
        s = _settings.load_settings()
        rtkino_configured = bool(s.get('rtkino_host', ''))
        return render_template('files.html', active='files', entries=entries,
                               current_path='', breadcrumbs=[],
                               rtkino_configured=rtkino_configured)

    if not os.path.exists(browse_path):
        return "Not found", 404

    entries = []
    try:
        for item in sorted(os.listdir(browse_path)):
            full = os.path.join(browse_path, item)
            stat = os.stat(full)
            is_dir = os.path.isdir(full)
            rel = '/'.join(filter(None, [rel_path, item]))
            entry_path = '/'.join(filter(None, [base_key, rel]))
            entries.append({
                'name': item, 'is_dir': is_dir,
                'size': format_size(stat.st_size) if not is_dir else '',
                'modified': datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M'),
                'path': entry_path, 'full_path': full,
            })
    except PermissionError:
        pass

    entries.sort(key=lambda e: (not e['is_dir'], e['name'].lower()))

    breadcrumbs = [{'name': 'files', 'path': ''}]
    accum = ''
    for part in subpath.split('/'):
        if part:
            accum = f'{accum}/{part}'.strip('/')
            breadcrumbs.append({'name': part, 'path': accum})

    s = _settings.load_settings()
    rtkino_configured = bool(s.get('rtkino_host', ''))

    return render_template('files.html', active='files', entries=entries,
                           current_path=subpath, breadcrumbs=breadcrumbs,
                           rtkino_configured=rtkino_configured)


# ─── RTKino UBX import (PPK uploads) ─────────

@bp.route('/api/ppk/rtkino/files')
def api_ppk_rtkino_files():
    """List UBX files available on RTKino SD card."""
    api = RTKINO.get_api()
    if not api:
        return jsonify({'ok': False, 'error': 'RTKino non configurato'}), 400
    data = api.gnss_list_files()
    if data is None:
        return jsonify({'ok': False, 'error': 'Impossibile contattare RTKino'}), 502
    files = data.get('files', []) if isinstance(data, dict) else []
    # Annotate which files already exist in uploads
    existing = set(os.listdir(Cfg.UPLOAD_DIR)) if os.path.isdir(Cfg.UPLOAD_DIR) else set()
    for f in files:
        f['already_imported'] = f.get('name', '') in existing
    return jsonify({'ok': True, 'files': files})


@bp.route('/api/ppk/rtkino/import', methods=['POST'])
def api_ppk_rtkino_import():
    """Import a UBX file from RTKino into the PPK uploads folder."""
    data = request.get_json() or {}
    filename = data.get('filename', '')
    if not filename:
        return jsonify({'ok': False, 'error': 'filename richiesto'}), 400

    safe_name = secure_filename(filename)
    if not safe_name:
        return jsonify({'ok': False, 'error': 'filename non valido'}), 400

    api = RTKINO.get_api()
    if not api:
        return jsonify({'ok': False, 'error': 'RTKino non configurato'}), 400

    os.makedirs(Cfg.UPLOAD_DIR, exist_ok=True)
    dest_path = os.path.join(Cfg.UPLOAD_DIR, safe_name)

    # Skip download if file already exists
    if os.path.exists(dest_path):
        return jsonify({
            'ok': True,
            'filename': safe_name,
            'size': os.path.getsize(dest_path),
            'path': dest_path,
            'skipped': True,
        })

    # Stream directly to disk to handle large files efficiently
    size = api.gnss_download_file_to_path(filename, dest_path)
    if size is None:
        # Clean up partial file if it was created
        if os.path.exists(dest_path):
            try:
                os.remove(dest_path)
            except OSError:
                pass
        return jsonify({'ok': False, 'error': 'Download fallito da RTKino'}), 502

    return jsonify({
        'ok': True,
        'filename': safe_name,
        'size': size,
        'path': dest_path,
        'skipped': False,
    })

@bp.route('/api/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400

    f = request.files['file']
    if f.filename == '':
        return jsonify({'error': 'No file selected'}), 400

    ext = f.filename.rsplit('.', 1)[-1].lower() if '.' in f.filename else ''

    raw_exts = ('ubx', 'raw', 'rtcm', 'rtcm2', 'rtcm3', 'nov', 'oem', 'ubx2',
                'sbp', 'stq', 'jps', 'bnx', 'binex', 'rt17', 'sbf', 'unc')
    rinex_exts = ('obs', 'nav', 'rnx', 'gnav', 'hnav', 'qnav', 'lnav', 'cnav',
                  'inav', 'sp3', 'eph', 'clk', 'sbs', 'snx')
    conf_exts = ('conf', 'cfg')
    pos_exts = ('pos',)
    result_exts = ('stat', 'kml', 'kmz', 'gpx')
    antex_exts = ('atx', 'antex')

    explicit_dir = request.form.get('target_dir', '')
    dir_map = {'pos': Cfg.POS_DIR, 'conf': Cfg.CONF_DIR,
               'antex': Cfg.ANTEX_DIR, 'rinex': Cfg.RINEX_DIR,
               'uploads': Cfg.UPLOAD_DIR, 'results': Cfg.RESULTS_DIR}

    if explicit_dir in dir_map:
        target_dir = dir_map[explicit_dir]
    elif ext in raw_exts:
        target_dir = Cfg.UPLOAD_DIR
    elif ext in rinex_exts:
        target_dir = Cfg.RINEX_DIR
    elif ext in conf_exts:
        target_dir = Cfg.CONF_DIR
    elif ext in antex_exts:
        target_dir = Cfg.ANTEX_DIR
    elif ext in pos_exts:
        target_dir = Cfg.POS_DIR
    elif ext in result_exts:
        target_dir = Cfg.RESULTS_DIR
    elif len(ext) == 3 and ext[2] in ('o', 'n', 'g', 'h', 'l', 'p', 'c'):
        target_dir = Cfg.RINEX_DIR
    else:
        target_dir = Cfg.UPLOAD_DIR

    os.makedirs(target_dir, exist_ok=True)
    safe_name = secure_filename(f.filename)
    if not safe_name:
        return jsonify({'error': 'Invalid filename — only alphanumeric characters, dots, dashes and underscores are allowed'}), 400
    filepath = os.path.join(target_dir, safe_name)
    real_filepath = os.path.realpath(filepath)
    if not _is_within_dir(filepath, target_dir):
        return jsonify({'error': 'Access denied'}), 403
    f.save(filepath)

    return jsonify({
        'success': True, 'filename': safe_name,
        'path': filepath, 'size': os.path.getsize(filepath)
    })


@bp.route('/api/delete', methods=['POST'])
def delete_file():
    data = request.get_json()
    filepath = data.get('path', '')
    if not filepath:
        return jsonify({'error': 'No path provided'}), 400

    real_path = os.path.realpath(filepath)
    allowed = any(
        _is_within_dir(filepath, d)
        for d in [Cfg.UPLOAD_DIR, Cfg.RINEX_DIR, Cfg.RESULTS_DIR,
                  Cfg.POS_DIR, Cfg.CONF_DIR, Cfg.ANTEX_DIR]
    )
    if not allowed:
        return jsonify({'error': 'Access denied'}), 403
    if not os.path.isfile(filepath):
        return jsonify({'error': 'File not found'}), 404

    os.remove(filepath)
    return jsonify({'success': True})


@bp.route('/api/download')
def download_file():
    filepath = request.args.get('path', '')
    if not filepath:
        return "No path", 400

    real_path = os.path.realpath(filepath)
    allowed = any(
        _is_within_dir(filepath, d)
        for d in [Cfg.UPLOAD_DIR, Cfg.RINEX_DIR, Cfg.RESULTS_DIR,
                  Cfg.POS_DIR, Cfg.CONF_DIR, Cfg.ANTEX_DIR]
    )
    if not allowed or not os.path.isfile(filepath):
        return "Not found", 404

    return send_file(filepath, as_attachment=True)


@bp.route('/api/files/download')
def api_download_file():
    return download_file()


# ─── Async Job Helpers ────────────────────────

def _cleanup_old_jobs():
    """Remove completed job entries older than _JOB_TTL seconds."""
    now = time.time()
    with running_processes_lock:
        to_delete = [
            jid for jid, j in running_processes.items()
            if j.get('status') in ('done', 'error') and
            now - j.get('finished_at', now) > _JOB_TTL
        ]
        for jid in to_delete:
            del running_processes[jid]


def _run_convbin_thread(job_id, cmd, output_dir, existing_mtimes, start_ts, cmd_str):
    """Worker thread for async convbin execution (Gap 9/10)."""
    ring = running_processes[job_id]['log']
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, cwd=output_dir,
        )
        stdout_lines = []
        for line in proc.stdout:
            stripped = line.rstrip('\n')
            stdout_lines.append(line)
            ring.append(stripped)
            with running_processes_lock:
                running_processes[job_id]['log_list'] = list(ring)
        proc.wait()
        stdout_text = ''.join(stdout_lines)

        new_files = []
        if os.path.isdir(output_dir):
            for f in sorted(os.listdir(output_dir)):
                fp = os.path.join(output_dir, f)
                if not os.path.isfile(fp):
                    continue
                mtime = os.path.getmtime(fp)
                if f not in existing_mtimes or mtime >= start_ts:
                    new_files.append({'name': f, 'path': fp, 'size': os.path.getsize(fp)})

        with running_processes_lock:
            running_processes[job_id].update({
                'status': 'done' if proc.returncode == 0 else 'error',
                'returncode': proc.returncode,
                'stdout': stdout_text,
                'output_files': new_files,
                'finished_at': time.time(),
                'log_list': list(ring),
            })
    except Exception as exc:
        with running_processes_lock:
            running_processes[job_id].update({
                'status': 'error', 'returncode': -1,
                'stdout': '', 'output_files': [],
                'finished_at': time.time(),
                'error': str(exc),
                'log_list': list(ring),
            })
    finally:
        _cleanup_old_jobs()


def _run_rnx2rtkp_thread(job_id, cmd, output_file, tmp_conf, cmd_str):
    """Worker thread for async rnx2rtkp execution (Gap 9/10)."""
    ring = running_processes[job_id]['log']
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True,
        )
        stdout_lines = []
        for line in proc.stdout:
            stripped = line.rstrip('\n')
            stdout_lines.append(line)
            ring.append(stripped)
            with running_processes_lock:
                running_processes[job_id]['log_list'] = list(ring)
        proc.wait()
        stdout_text = ''.join(stdout_lines)

        try:
            os.remove(tmp_conf)
        except OSError:
            pass

        output_info = None
        if output_file and os.path.isfile(output_file):
            output_info = {
                'name': os.path.basename(output_file),
                'path': output_file,
                'size': os.path.getsize(output_file),
            }

        with running_processes_lock:
            running_processes[job_id].update({
                'status': 'done' if proc.returncode == 0 else 'error',
                'returncode': proc.returncode,
                'stdout': stdout_text,
                'stderr': '',
                'output_file': output_info,
                'finished_at': time.time(),
                'log_list': list(ring),
            })
    except Exception as exc:
        try:
            os.remove(tmp_conf)
        except OSError:
            pass
        with running_processes_lock:
            running_processes[job_id].update({
                'status': 'error', 'returncode': -1,
                'stdout': '', 'stderr': str(exc),
                'output_file': None,
                'finished_at': time.time(),
                'error': str(exc),
                'log_list': list(ring),
            })
    finally:
        _cleanup_old_jobs()


# ─── Convbin ─────────────────────────────────

@bp.route('/convbin')
def convbin_page():
    raw_files = list_files(Cfg.UPLOAD_DIR,
                           ('.ubx', '.gps', '.sbp', '.bin', '.stq', '.jps',
                            '.bnx', '.binex', '.rt17', '.sbf', '.unc',
                            '.rtcm2', '.rtcm3', '.raw'))
    wrapper = ConvbinWrapper(Cfg.CONVBIN_BIN)
    return render_template('convbin.html', active='convbin', raw_files=raw_files,
                           tool_available=wrapper.is_available(),
                           binary_path=Cfg.CONVBIN_BIN,
                           project_dir=os.path.dirname(os.path.abspath(__file__)))


@bp.route('/api/convbin/run', methods=['POST'])
def run_convbin():
    data = request.get_json()
    wrapper = ConvbinWrapper(Cfg.CONVBIN_BIN)

    input_file = data.get('input_file', '')
    if not os.path.isfile(input_file):
        return jsonify({'error': f'Input file not found: {input_file}'}), 400

    # Validate input_file is within allowed upload directory
    if not _is_within_dir(input_file, Cfg.UPLOAD_DIR):
        return jsonify({'error': 'Access denied: input file outside uploads directory'}), 403

    output_dir_raw = data.get('output_dir', 'rinex')
    if output_dir_raw in ('rinex', '', None):
        output_dir = Cfg.RINEX_DIR
    elif output_dir_raw == 'same':
        output_dir = os.path.dirname(input_file) or Cfg.RINEX_DIR
    else:
        # For custom output dirs, restrict to allowed data directories
        allowed_out = [Cfg.RINEX_DIR, Cfg.UPLOAD_DIR, Cfg.RESULTS_DIR, Cfg.POS_DIR]
        if any(_is_within_dir(output_dir_raw, d) for d in allowed_out):
            output_dir = output_dir_raw
        else:
            output_dir = Cfg.RINEX_DIR  # fall back to safe default
    os.makedirs(output_dir, exist_ok=True)

    options = {
        'format': data.get('format', 'ubx'),
        'rinex_ver': data.get('rinex_ver', '3.04'),
        'freq': data.get('freq', '5'),
        'receiver_opts': data.get('receiver_opts', ''),
        'interval': data.get('interval', ''),
        'epoch_tol': data.get('epoch_tol', ''),
        'span': data.get('span', ''),
        'start_time': data.get('start_time', ''),
        'end_time': data.get('end_time', ''),
        'rtcm_time': data.get('rtcm_time', ''),
        'marker_name': data.get('marker_name', ''),
        'marker_number': data.get('marker_number', ''),
        'marker_type': data.get('marker_type', ''),
        'observer': data.get('observer', ''),
        'receiver': data.get('receiver', ''),
        'antenna': data.get('antenna', ''),
        'approx_pos': data.get('approx_pos', ''),
        'antenna_delta': data.get('antenna_delta', ''),
        'comment': data.get('comment', ''),
        'enabled_systems': data.get('enabled_systems', []),
        'exclude_sats': data.get('exclude_sats', []),
        'signal_mask': data.get('signal_mask', ''),
        'signal_nomask': data.get('signal_nomask', ''),
        'half_cyc': data.get('half_cyc', False),
        'include_doppler': data.get('include_doppler', True),
        'include_snr': data.get('include_snr', True),
        'include_iono': data.get('include_iono', False),
        'include_time': data.get('include_time', False),
        'include_leaps': data.get('include_leaps', False),
        'station_id': data.get('station_id', ''),
        'trace': data.get('trace', 0),
        'output_dir': output_dir,
    }

    # Build command upfront for the job record
    cmd = wrapper.build_command(input_file, options)
    cmd_str = wrapper.build_command_string(input_file, options)

    # Snapshot existing files to detect new outputs (use resolved path)
    resolved_output_dir = os.path.realpath(output_dir)
    start_ts = time.time()
    existing_mtimes = {}
    if os.path.isdir(resolved_output_dir):
        for f in os.listdir(resolved_output_dir):
            fp = os.path.join(resolved_output_dir, f)
            if os.path.isfile(fp):
                existing_mtimes[f] = os.path.getmtime(fp)

    job_id = uuid.uuid4().hex
    with running_processes_lock:
        running_processes[job_id] = {
            'type': 'convbin',
            'status': 'running',
            'started_at': start_ts,
            'command': cmd_str,
            'input_file': input_file,
            'log': deque(maxlen=_LOG_RING),
            'log_list': [],
        }

    t = threading.Thread(
        target=_run_convbin_thread,
        args=(job_id, cmd, resolved_output_dir, existing_mtimes, start_ts, cmd_str),
        daemon=True,
    )
    t.start()

    return jsonify({'job_id': job_id, 'command': cmd_str})


@bp.route('/api/convbin/job/<job_id>')
def convbin_job_status(job_id):
    with running_processes_lock:
        job = running_processes.get(job_id)
    if job is None:
        return jsonify({'error': 'Job not found'}), 404
    status = job.get('status', 'unknown')
    resp = {
        'job_id': job_id,
        'type': job.get('type', 'convbin'),
        'status': status,
        'command': job.get('command', ''),
        'started_at': job.get('started_at'),
        'log': job.get('log_list', []),
    }
    if status != 'running':
        resp['returncode'] = job.get('returncode', -1)
        resp['output'] = job.get('stdout', '')
        resp['output_files'] = job.get('output_files', [])
        resp['success'] = job.get('returncode', -1) == 0
        resp['finished_at'] = job.get('finished_at')
    return jsonify(resp)


@bp.route('/api/convbin/formats')
def convbin_formats():
    return jsonify(ConvbinWrapper.INPUT_FORMATS)


# ─── RINEX QC ────────────────────────────────

@bp.route('/rinex')
def rinex_page():
    obs_files = list_files(Cfg.RINEX_DIR, ('.obs', '.rnx', '.OBS', '.RNX'))
    for f in os.listdir(Cfg.RINEX_DIR):
        full = os.path.join(Cfg.RINEX_DIR, f)
        if os.path.isfile(full) and len(f) > 3:
            ext = f[-3:].lower()
            if ext.endswith('o') and ext[:-1].isdigit():
                entry = {
                    'name': f, 'path': full,
                    'size': format_size(os.path.getsize(full)),
                    'modified': datetime.fromtimestamp(
                        os.path.getmtime(full)).strftime('%Y-%m-%d %H:%M'),
                }
                if not any(e['path'] == full for e in obs_files):
                    obs_files.append(entry)

    return render_template('rinex.html', active='rinex', obs_files=obs_files)


@bp.route('/api/rinex/parse', methods=['POST'])
def parse_rinex():
    data = request.get_json()
    filepath = data.get('file', '')
    if not os.path.isfile(filepath):
        return jsonify({'error': 'File not found'}), 404

    try:
        parser = RinexObsParser(filepath)
        header = parser.parse_header()
        obs_data = parser.parse_observations(
            max_epochs=int(data.get('max_epochs', 0)) or None,
            decimate=int(data.get('decimate', 1))
        )
        return jsonify({'success': True, 'header': header, 'observations': obs_data})
    except Exception as e:
        import traceback
        return jsonify({'error': str(e), 'trace': traceback.format_exc()}), 500


@bp.route('/api/rinex/snr', methods=['POST'])
def rinex_snr():
    data = request.get_json()
    filepath = data.get('file', '')
    satellites = data.get('satellites', [])
    if not os.path.isfile(filepath):
        return jsonify({'error': 'File not found'}), 404

    try:
        parser = RinexObsParser(filepath)
        parser.parse_header()
        snr_data = parser.get_snr_data(
            satellites=satellites or None,
            decimate=int(data.get('decimate', 1))
        )
        return jsonify({'success': True, 'snr': snr_data})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ─── PPK Processing (rnx2rtkp) ───────────────

def _list_obs_files():
    obs_files = list_files(Cfg.RINEX_DIR, ('.obs', '.rnx', '.OBS', '.RNX'))
    for f in os.listdir(Cfg.RINEX_DIR):
        full = os.path.join(Cfg.RINEX_DIR, f)
        if os.path.isfile(full) and len(f) > 3:
            ext = f[-3:].lower()
            if ext.endswith('o') and ext[:-1].isdigit():
                entry = {
                    'name': f, 'path': full,
                    'size': format_size(os.path.getsize(full)),
                    'modified': datetime.fromtimestamp(
                        os.path.getmtime(full)).strftime('%Y-%m-%d %H:%M'),
                }
                if not any(e['path'] == full for e in obs_files):
                    obs_files.append(entry)
    return obs_files


def _list_nav_files():
    nav_files = list_files(Cfg.RINEX_DIR,
                           ('.nav', '.gnav', '.hnav', '.qnav', '.lnav',
                            '.cnav', '.inav', '.mnav', '.sp3', '.eph', '.clk', '.rnx'))
    for f in os.listdir(Cfg.RINEX_DIR):
        full = os.path.join(Cfg.RINEX_DIR, f)
        if os.path.isfile(full) and len(f) > 3:
            ext = f[-3:].lower()
            if ext.endswith(('n', 'g', 'h', 'l', 'p', 'q', 'c')) and ext[:-1].isdigit():
                entry = {
                    'name': f, 'path': full,
                    'size': format_size(os.path.getsize(full)),
                    'modified': datetime.fromtimestamp(
                        os.path.getmtime(full)).strftime('%Y-%m-%d %H:%M'),
                }
                if not any(e['path'] == full for e in nav_files):
                    nav_files.append(entry)
    return nav_files


def _list_conf_names():
    names = set()
    for d in [Cfg.CONF_DIR, Cfg.DEFAULT_CONF_DIR]:
        if os.path.isdir(d):
            for f in os.listdir(d):
                if f.endswith(('.conf', '.cfg')):
                    names.add(f)
    return sorted(names)


def _resolve_conf_path(name):
    for d in [Cfg.CONF_DIR, Cfg.DEFAULT_CONF_DIR]:
        p = os.path.join(d, name)
        if os.path.isfile(p):
            return p
    return None


@bp.route('/ppk')
def ppk_page():
    wrapper = Rnx2rtkpWrapper(Cfg.RNX2RTKP_BIN)
    conf_values = get_defaults()

    return render_template(
        'rnx2rtkp.html',
        active='ppk',
        obs_files=_list_obs_files(),
        nav_files=_list_nav_files(),
        conf_files=_list_conf_names(),
        current_conf='',
        conf_values=conf_values,
        schema=CONF_SCHEMA,
        sections=SECTIONS,
        navsys_bits=NAVSYS_BITS,
        modes=Rnx2rtkpWrapper.MODES,
        tool_available=wrapper.is_available(),
        tool_version=wrapper.get_version(),
        binary_path=Cfg.RNX2RTKP_BIN,
        project_dir=os.path.dirname(os.path.abspath(__file__)),
    )


@bp.route('/api/ppk/obs_files')
def api_obs_files():
    return jsonify({'files': _list_obs_files()})


@bp.route('/api/ppk/nav_files')
def api_nav_files():
    return jsonify({'files': _list_nav_files()})


@bp.route('/api/rinex/obs_files')
def api_rinex_obs_files():
    """Alias for obs_files — used by convbin/rinex tabs for cross-tab refresh."""
    return jsonify({'files': _list_obs_files()})


@bp.route('/api/convbin/raw_files')
def api_convbin_raw_files():
    """List raw receiver files available for convbin."""
    raw_files = list_files(Cfg.UPLOAD_DIR,
                           ('.ubx', '.gps', '.sbp', '.bin', '.stq', '.jps',
                            '.bnx', '.binex', '.rt17', '.sbf', '.unc',
                            '.rtcm2', '.rtcm3', '.raw'))
    return jsonify({'files': raw_files})


@bp.route('/api/ppk/file_counts')
def api_file_counts():
    """Cheap directory scan — lets other tabs detect new files without full reload."""
    def _count(d, exts=None):
        if not os.path.isdir(d):
            return 0
        return sum(
            1 for f in os.listdir(d)
            if os.path.isfile(os.path.join(d, f)) and
            (exts is None or os.path.splitext(f)[1].lower() in exts)
        )
    return jsonify({
        'upload_count': _count(Cfg.UPLOAD_DIR),
        'rinex_count': _count(Cfg.RINEX_DIR),
        'pos_count': _count(Cfg.POS_DIR, ('.pos',)),
    })


# ─── Conf API ────────────────────────────────

@bp.route('/api/rnx2rtkp/conf/defaults')
def conf_defaults():
    return jsonify(get_defaults())


@bp.route('/api/rnx2rtkp/conf/load')
def conf_load():
    name = request.args.get('name', '')
    if not name:
        return jsonify({'error': 'No name provided'}), 400

    path = _resolve_conf_path(name)
    if not path:
        return jsonify({'error': f'Config not found: {name}'}), 404

    try:
        values = parse_conf(path)
        return jsonify({'values': values, 'filename': name})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/rnx2rtkp/conf/save', methods=['POST'])
def conf_save():
    data = request.get_json()
    name = data.get('name', '').strip()
    values = data.get('values', {})

    if not name:
        return jsonify({'error': 'No name provided'}), 400

    if not name.endswith('.conf'):
        name += '.conf'

    safe_name = ''.join(c for c in name if c.isalnum() or c in '._-')
    if not safe_name.endswith('.conf'):
        safe_name += '.conf'

    filepath = os.path.join(Cfg.CONF_DIR, safe_name)

    try:
        config = get_defaults()
        config = merge_conf(config, values)
        config['_header'] = f'# rtkpost options ({datetime.now().strftime("%Y/%m/%d %H:%M:%S")})'

        errors = validate_conf(config)
        if errors:
            return jsonify({'warnings': [f'{k}: {e}' for k, e in errors]})

        write_conf(config, filepath)
        return jsonify({'success': True, 'filename': safe_name, 'path': filepath})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/rnx2rtkp/conf/upload', methods=['POST'])
def conf_upload():
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400

    f = request.files['file']
    if not f.filename:
        return jsonify({'error': 'No file selected'}), 400

    ext = f.filename.rsplit('.', 1)[-1].lower() if '.' in f.filename else ''
    if ext not in ('conf', 'cfg'):
        return jsonify({'error': 'Only .conf/.cfg files accepted'}), 400

    filepath = os.path.join(Cfg.CONF_DIR, f.filename)
    f.save(filepath)

    return jsonify({'success': True, 'filename': f.filename})


@bp.route('/api/rnx2rtkp/conf/list')
def conf_list():
    return jsonify({'files': _list_conf_names()})


# ─── Processing API ──────────────────────────

@bp.route('/api/rnx2rtkp/preview', methods=['POST'])
def preview_rnx2rtkp():
    data = request.get_json()
    wrapper = Rnx2rtkpWrapper(Cfg.RNX2RTKP_BIN)

    rover_obs = data.get('rover_obs', '')
    base_obs = data.get('base_obs', '')
    nav_files = data.get('nav_files', [])
    conf_values = data.get('conf', {})

    base_name = os.path.splitext(os.path.basename(rover_obs))[0] if rover_obs else 'output'
    output_file = os.path.join(Cfg.POS_DIR, f'{base_name}.pos')

    tmp_conf = os.path.join(Cfg.POS_DIR, f'{base_name}.conf')

    options = {
        'config_file': tmp_conf,
        'output_file': output_file,
        'time_format': True,
    }

    cmd_str = wrapper.build_command_string(rover_obs, base_obs, nav_files, options)
    return jsonify({'command': cmd_str})


@bp.route('/api/rnx2rtkp/process', methods=['POST'])
def run_rnx2rtkp():
    data = request.get_json()
    wrapper = Rnx2rtkpWrapper(Cfg.RNX2RTKP_BIN)

    rover_obs = data.get('rover_obs', '')
    base_obs = data.get('base_obs', '')
    nav_files = data.get('nav_files', [])
    conf_values = data.get('conf', {})
    overrides = data.get('overrides', {})

    if not rover_obs or not os.path.isfile(rover_obs):
        return jsonify({'error': 'Rover observation file not found'}), 400

    # Validate rover_obs is within allowed directories
    _allowed_dirs = [Cfg.RINEX_DIR, Cfg.UPLOAD_DIR]
    if not any(_is_within_dir(rover_obs, d) for d in _allowed_dirs):
        return jsonify({'error': 'Access denied: rover observation file is outside allowed directories'}), 403

    # Gap 8: validate nav file existence and access before launching subprocess
    if nav_files:
        invalid_nav = [p for p in nav_files
                       if not any(_is_within_dir(p, d) for d in _allowed_dirs)]
        if invalid_nav:
            return jsonify({'error': 'Access denied: navigation file outside allowed directories'}), 403
        # Resolve nav paths to avoid taint-tracking concerns
        nav_files = [os.path.realpath(p) for p in nav_files]
        missing_nav = [p for p in nav_files if not os.path.isfile(p)]
        if missing_nav:
            return jsonify({
                'error': 'Navigation file(s) not found: ' + ', '.join(
                    os.path.basename(p) for p in missing_nav)
            }), 400

    base_name = secure_filename(os.path.splitext(os.path.basename(rover_obs))[0]) or 'output'
    timestamp = datetime.now().strftime('%H%M%S')
    output_file = os.path.join(Cfg.POS_DIR, f'{base_name}_{timestamp}.pos')
    os.makedirs(Cfg.POS_DIR, exist_ok=True)

    tmp_conf = os.path.join(Cfg.POS_DIR, f'.tmp_{base_name}_{timestamp}.conf')
    try:
        full_conf = get_defaults()
        full_conf = merge_conf(full_conf, conf_values)
        full_conf['_header'] = '# rtkpost options (auto-generated)'
        write_conf(full_conf, tmp_conf)
    except Exception as e:
        return jsonify({'error': f'Failed to write config: {e}'}), 500

    options = {
        'config_file': tmp_conf,
        'output_file': output_file,
        'time_format': True,
    }

    if overrides.get('mode'):
        options['mode'] = overrides['mode']
    if overrides.get('solution_type'):
        options['solution_type'] = overrides['solution_type']
    if overrides.get('ar_mode'):
        options['ar_mode'] = overrides['ar_mode']
    if overrides.get('trace'):
        options['trace'] = overrides['trace']

    # Build command for the job record
    cmd = wrapper.build_command(rover_obs, base_obs, nav_files, options)
    cmd_str = wrapper.build_command_string(rover_obs, base_obs, nav_files, options)

    job_id = uuid.uuid4().hex
    with running_processes_lock:
        running_processes[job_id] = {
            'type': 'rnx2rtkp',
            'status': 'running',
            'started_at': time.time(),
            'command': cmd_str,
            'rover_obs': rover_obs,
            'log': deque(maxlen=_LOG_RING),
            'log_list': [],
        }

    t = threading.Thread(
        target=_run_rnx2rtkp_thread,
        args=(job_id, cmd, output_file, tmp_conf, cmd_str),
        daemon=True,
    )
    t.start()

    return jsonify({'job_id': job_id, 'command': cmd_str})


@bp.route('/api/rnx2rtkp/job/<job_id>')
def rnx2rtkp_job_status(job_id):
    with running_processes_lock:
        job = running_processes.get(job_id)
    if job is None:
        return jsonify({'error': 'Job not found'}), 404
    status = job.get('status', 'unknown')
    resp = {
        'job_id': job_id,
        'type': job.get('type', 'rnx2rtkp'),
        'status': status,
        'command': job.get('command', ''),
        'started_at': job.get('started_at'),
        'log': job.get('log_list', []),
    }
    if status != 'running':
        resp['returncode'] = job.get('returncode', -1)
        resp['stdout'] = job.get('stdout', '')
        resp['stderr'] = job.get('stderr', '')
        resp['output_file'] = job.get('output_file')
        resp['finished_at'] = job.get('finished_at')
    return jsonify(resp)


@bp.route('/api/ppk/jobs')
def list_jobs():
    """List all running and recently completed jobs (useful for recovery after reconnect)."""
    with running_processes_lock:
        jobs = []
        for jid, j in running_processes.items():
            jobs.append({
                'job_id': jid,
                'type': j.get('type', 'unknown'),
                'status': j.get('status', 'unknown'),
                'started_at': j.get('started_at'),
                'finished_at': j.get('finished_at'),
                'command': j.get('command', ''),
            })
    return jsonify({'jobs': jobs})



@bp.route('/api/rnx2rtkp/results')
def rnx2rtkp_results():
    pos_path = request.args.get('path', '')
    if not pos_path or not os.path.isfile(pos_path):
        return jsonify({'error': 'File not found'}), 404

    try:
        parsed = Rnx2rtkpWrapper.parse_pos_file(pos_path)

        qualities = [d['quality'] for d in parsed['data']]
        max_points = 2000
        if len(qualities) > max_points:
            step = len(qualities) // max_points
            qualities = qualities[::step]

        return jsonify({
            'summary': parsed['summary'],
            'quality_timeline': qualities,
            'header': parsed['header'][:10],
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ─── Antex Upload ────────────────────────────

@bp.route('/api/antex/upload', methods=['POST'])
def upload_antex():
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400

    f = request.files['file']
    if not f.filename:
        return jsonify({'error': 'No file selected'}), 400

    filepath = os.path.join(Cfg.ANTEX_DIR, f.filename)
    f.save(filepath)
    return jsonify({'success': True, 'filename': f.filename, 'path': filepath})


@bp.route('/api/antex/list')
def list_antex():
    files = list_files(Cfg.ANTEX_DIR, ('.atx', '.antex', '.ATX'))
    return jsonify({'files': files})


# ─── Position Viewer ─────────────────────────

@bp.route('/posview')
def posview_page():
    pos_files = list_files(Cfg.POS_DIR, ('.pos',))
    pos_files += list_files(Cfg.RESULTS_DIR, ('.pos',))
    seen = set()
    unique = []
    for f in pos_files:
        if f['name'] not in seen:
            seen.add(f['name'])
            unique.append(f)

    selected = request.args.get('file', '')

    return render_template(
        'posview.html',
        active='posview',
        pos_files=unique,
        selected_file=selected,
        q_labels=Q_LABELS,
        q_colors=Q_COLORS,
    )


@bp.route('/api/pos/parse', methods=['POST'])
def api_pos_parse():
    data = request.get_json()
    filepath = data.get('filepath', '')

    if not filepath or not os.path.isfile(filepath):
        return jsonify({'error': 'File not found'}), 404

    real = os.path.realpath(filepath)
    allowed = [os.path.realpath(Cfg.POS_DIR),
               os.path.realpath(Cfg.RESULTS_DIR)]
    if not any(real.startswith(d) for d in allowed):
        return jsonify({'error': 'Access denied'}), 403

    try:
        result = parse_pos(filepath)
        charts = decimate_for_charts(result['data'], max_points=3000)

        return jsonify({
            'filename': os.path.basename(filepath),
            'header': result['header'],
            'summary': result['summary'],
            'ref_pos': result['ref_pos'],
            'time_span': result['time_span'],
            'format': result['format'],
            'charts': charts,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/pos/list')
def api_pos_list():
    pos_files = list_files(Cfg.POS_DIR, ('.pos',))
    pos_files += list_files(Cfg.RESULTS_DIR, ('.pos',))
    seen = set()
    unique = []
    for f in pos_files:
        if f['name'] not in seen:
            seen.add(f['name'])
            unique.append(f)
    return jsonify({'files': unique})


@bp.route('/api/pos/epochs', methods=['POST'])
def api_pos_epochs():
    """Return all epochs (full data, not decimated) for a .pos file."""
    data = request.get_json()
    filepath = data.get('filepath', '')

    if not filepath or not os.path.isfile(filepath):
        return jsonify({'error': 'File not found'}), 404

    real = os.path.realpath(filepath)
    allowed = [os.path.realpath(Cfg.POS_DIR),
               os.path.realpath(Cfg.RESULTS_DIR)]
    if not any(real.startswith(d) for d in allowed):
        return jsonify({'error': 'Access denied'}), 403

    try:
        result = parse_pos(filepath)
        epochs = result['data']
        session_stats = compute_session_stats(epochs)
        # Serialise epoch list for transfer
        epoch_list = []
        for i, e in enumerate(epochs):
            epoch_list.append({
                'i': i,
                'ts': e.get('time_str', ''),
                'lat': e['lat'],
                'lon': e['lon'],
                'h': e['height'],
                'q': e.get('quality', 0),
                'ns': e.get('ns', 0),
                'ratio': e.get('ratio', 0.0),
                'sdn': e.get('sdn', 0.0),
                'sde': e.get('sde', 0.0),
                'sdu': e.get('sdu', 0.0),
            })
        return jsonify({
            'ok': True,
            'filepath': filepath,
            'filename': os.path.basename(filepath),
            'epochs': epoch_list,
            'session_stats': session_stats,
        })
    except Exception as exc:
        return jsonify({'error': str(exc)}), 500


@bp.route('/ppk/station', methods=['POST'])
def ppk_compute_station():
    """
    Receive a list of epoch indices and a .pos filepath,
    compute weighted mean station, return as JSON.
    Body: {"pos_file": "...", "epoch_indices": [12, 13, ...]}
    """
    body = request.get_json() or {}
    pos_file = body.get('pos_file', '')
    indices = body.get('epoch_indices', [])

    if not pos_file or not os.path.isfile(pos_file):
        return jsonify({'ok': False, 'error': 'File not found'}), 404

    real = os.path.realpath(pos_file)
    allowed = [os.path.realpath(Cfg.POS_DIR),
               os.path.realpath(Cfg.RESULTS_DIR)]
    if not any(real.startswith(d) for d in allowed):
        return jsonify({'ok': False, 'error': 'Access denied'}), 403

    if not indices:
        return jsonify({'ok': False, 'error': 'No epoch indices provided'}), 400

    try:
        result = parse_pos(pos_file)
        epochs = result['data']
        selected = [epochs[i] for i in indices if 0 <= i < len(epochs)]
        if not selected:
            return jsonify({'ok': False, 'error': 'No valid epochs selected'}), 400

        station = weighted_mean_station(selected)
        return jsonify({'ok': True, 'station': station})
    except Exception as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 500


@bp.route('/ppk/import_station', methods=['POST'])
def ppk_import_station():
    """
    Import a PPK station into the active survey as a GeoJSON point.
    Body: {"station": {...}, "name": "PPK_001"}
    """
    from modules.active_survey import get_active_survey_id
    from modules.survey import load_survey, save_survey, backup_survey
    from modules.utils import now_iso

    body = request.get_json() or {}
    station = body.get('station', {})
    name = body.get('name', '').strip()

    if not station:
        return jsonify({'ok': False, 'error': 'No station data provided'}), 400

    sid = get_active_survey_id()
    if not sid:
        return jsonify({'ok': False, 'error': 'no_active_survey'})

    try:
        svy = load_survey(sid)
    except FileNotFoundError:
        return jsonify({'ok': False, 'error': 'no_active_survey'})

    # Count existing PPK points to auto-generate name
    features = svy.get('features', [])
    ppk_count = sum(
        1 for f in features
        if f.get('properties', {}).get('source') == 'ppk'
    )

    if not name:
        name = f'PPK_{ppk_count + 1:03d}'

    pid = name  # Use name as ID for uniqueness

    feature = {
        'type': 'Feature',
        'id': pid,
        'geometry': {
            'type': 'Point',
            'coordinates': [station['lon'], station['lat'], station['h']],
        },
        'properties': {
            'name': name,
            'source': 'ppk',
            'codice': '',
            'note': '',
            'lat': station['lat'],
            'lon': station['lon'],
            'h': station['h'],
            'sigma_N': station.get('sigma_N'),
            'sigma_E': station.get('sigma_E'),
            'sigma_U': station.get('sigma_U'),
            'n_epochs': station.get('n_epochs'),
            'ratio_mean': station.get('ratio_mean'),
            'ratio_max': station.get('ratio_max'),
            'ns_mean': station.get('ns_mean'),
            't_start': station.get('t_start'),
            't_end': station.get('t_end'),
            'ts': now_iso(),
        },
    }

    backup_survey(sid)
    svy.setdefault('features', []).append(feature)
    save_survey(sid, svy)

    return jsonify({'ok': True, 'pid': pid, 'name': name})
