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
from datetime import datetime
from pathlib import Path

from flask import (
    Blueprint, render_template, request, jsonify,
    send_file, redirect, url_for
)

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

bp = Blueprint('ppk', __name__)

running_processes = {}


# ─── Utilities ────────────────────────────────

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
        rel_path = parts[1] if len(parts) > 1 else ''
    else:
        base_key = None
        rel_path = ''

    if base_key and base_key in base_dirs:
        browse_path = os.path.join(base_dirs[base_key], rel_path)
        if not os.path.realpath(browse_path).startswith(os.path.realpath(base_dirs[base_key])):
            return "Access denied", 403
    else:
        entries = []
        for key, path in base_dirs.items():
            count = sum(1 for f in Path(path).rglob('*') if f.is_file())
            entries.append({
                'name': key, 'is_dir': True,
                'size': f'{count} files', 'modified': '', 'path': key,
            })
        return render_template('files.html', active='files', entries=entries,
                               current_path='', breadcrumbs=[])

    if not os.path.exists(browse_path):
        return "Not found", 404

    entries = []
    try:
        for item in sorted(os.listdir(browse_path)):
            full = os.path.join(browse_path, item)
            stat = os.stat(full)
            is_dir = os.path.isdir(full)
            entry_path = f'{base_key}/{rel_path}/{item}'.strip('/')
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

    return render_template('files.html', active='files', entries=entries,
                           current_path=subpath, breadcrumbs=breadcrumbs)


# ─── Upload / Delete / Download ──────────────

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
    filepath = os.path.join(target_dir, f.filename)
    f.save(filepath)

    return jsonify({
        'success': True, 'filename': f.filename,
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
        real_path.startswith(os.path.realpath(d))
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
        real_path.startswith(os.path.realpath(d))
        for d in [Cfg.UPLOAD_DIR, Cfg.RINEX_DIR, Cfg.RESULTS_DIR,
                  Cfg.POS_DIR, Cfg.CONF_DIR, Cfg.ANTEX_DIR]
    )
    if not allowed or not os.path.isfile(filepath):
        return "Not found", 404

    return send_file(filepath, as_attachment=True)


@bp.route('/api/files/download')
def api_download_file():
    return download_file()


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

    output_dir_raw = data.get('output_dir', 'rinex')
    if output_dir_raw in ('rinex', '', None):
        output_dir = Cfg.RINEX_DIR
    elif output_dir_raw == 'same':
        output_dir = os.path.dirname(input_file) or Cfg.RINEX_DIR
    else:
        output_dir = output_dir_raw
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

    try:
        result = wrapper.run(input_file, options)
        return jsonify({
            'success': result.get('returncode', -1) == 0,
            'command': result.get('command', ''),
            'output': result.get('stdout', ''),
            'errors': result.get('stderr', ''),
            'output_files': result.get('output_files', []),
            'returncode': result.get('returncode', 0),
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


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

    base_name = os.path.splitext(os.path.basename(rover_obs))[0]
    timestamp = datetime.now().strftime('%H%M%S')
    output_file = os.path.join(Cfg.POS_DIR, f'{base_name}_{timestamp}.pos')
    os.makedirs(Cfg.POS_DIR, exist_ok=True)

    tmp_conf = os.path.join(Cfg.POS_DIR, f'.tmp_{base_name}.conf')
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

    try:
        result = wrapper.run(rover_obs, base_obs, nav_files, options, timeout=7200)

        try:
            os.remove(tmp_conf)
        except OSError:
            pass

        return jsonify({
            'command': result.get('command', ''),
            'stdout': result.get('stdout', ''),
            'stderr': result.get('stderr', ''),
            'returncode': result.get('returncode', -1),
            'output_file': result.get('output_file'),
        })

    except Exception as e:
        import traceback
        return jsonify({'error': str(e), 'trace': traceback.format_exc()}), 500


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
