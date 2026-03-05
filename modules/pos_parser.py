"""
RTKLIB .pos File Parser

Parses solution files produced by rnx2rtkp.
Extracts all fields, computes ENU relative to reference,
decimates data for chart display.

.pos format (with -t flag):
  %  GPST          latitude(deg) longitude(deg)  height(m)   Q  ns   sdn(m)   sde(m)   sdu(m)  sdne(m)  sdeu(m)  sdun(m) age(s)  ratio
  2024/01/15 10:00:00.000  45.12345678  11.12345678   123.456  1  12   0.0023   0.0019   0.0045   0.0001   0.0002   0.0001   0.50   999.9

Q values: 1=fix, 2=float, 3=sbas, 4=dgps, 5=single, 6=ppp
"""

import os
import math
from datetime import datetime

# Quality labels matching rtkplot
Q_LABELS = {1: 'Fix', 2: 'Float', 3: 'SBAS', 4: 'DGPS', 5: 'Single', 6: 'PPP'}
Q_COLORS = {1: '#2ecc71', 2: '#f39c12', 3: '#3498db', 4: '#3498db', 5: '#e74c3c', 6: '#9b59b6'}


def parse_pos(filepath, max_points=None):
    """
    Parse an RTKLIB .pos file.

    Returns dict with:
      header:    list of header comment lines
      data:      list of epoch dicts (all fields)
      summary:   statistics (fix%, float%, totals, etc.)
      ref_pos:   reference position (mean of fix solutions)
      time_span: {start, end, duration_s}
    """
    if not os.path.isfile(filepath):
        raise FileNotFoundError(f'File not found: {filepath}')

    header_lines = []
    data = []
    format_hint = None  # 'llh' or 'xyz' or 'enu'

    with open(filepath, 'r') as f:
        for line in f:
            line = line.rstrip('\n')

            # Header lines start with %
            if line.startswith('%'):
                header_lines.append(line)
                # Detect format from header
                low = line.lower()
                if 'latitude' in low and 'longitude' in low:
                    format_hint = 'llh'
                elif 'x-ecef' in low:
                    format_hint = 'xyz'
                elif 'e-baseline' in low:
                    format_hint = 'enu'
                continue

            if not line.strip():
                continue

            parts = line.split()
            if len(parts) < 5:
                continue

            try:
                entry = _parse_line(parts)
                if entry:
                    data.append(entry)
            except (ValueError, IndexError):
                continue

    if not data:
        return {
            'header': header_lines,
            'data': [],
            'summary': _empty_summary(),
            'ref_pos': None,
            'time_span': None,
            'format': format_hint or 'llh',
        }

    # Reference position: mean of fix solutions (Q=1), or all if no fix
    ref_pos = _compute_reference(data)

    # Compute ENU for each epoch relative to reference
    if ref_pos and format_hint != 'enu':
        for d in data:
            e, n, u = _llh_to_enu(
                d['lat'], d['lon'], d['height'],
                ref_pos['lat'], ref_pos['lon'], ref_pos['height']
            )
            d['e'] = e
            d['n'] = n
            d['u'] = u

    # Time span
    time_span = None
    if len(data) >= 2 and data[0].get('time_str') and data[-1].get('time_str'):
        time_span = {
            'start': data[0]['time_str'],
            'end': data[-1]['time_str'],
            'epochs': len(data),
        }
        # Try to compute duration
        try:
            t0 = _parse_time(data[0]['time_str'])
            t1 = _parse_time(data[-1]['time_str'])
            if t0 and t1:
                time_span['duration_s'] = (t1 - t0).total_seconds()
                time_span['duration_str'] = _format_duration(time_span['duration_s'])
                # Interval
                if len(data) > 1:
                    time_span['interval_s'] = round(time_span['duration_s'] / (len(data) - 1), 3)
        except Exception:
            pass

    # Summary statistics
    summary = _compute_summary(data)

    return {
        'header': header_lines,
        'data': data,
        'summary': summary,
        'ref_pos': ref_pos,
        'time_span': time_span,
        'format': format_hint or 'llh',
    }


def decimate_for_charts(data, max_points=3000):
    """
    Decimate data for chart display. Keeps all quality transitions.
    Returns dict of arrays for efficient JSON transfer.
    """
    n = len(data)
    if n == 0:
        return _empty_chart_data()

    step = max(1, n // max_points)

    # Extract arrays (more efficient than sending array of objects)
    times = []
    lats = []
    lons = []
    heights = []
    qualities = []
    ns_list = []
    sdn_list = []
    sde_list = []
    sdu_list = []
    age_list = []
    ratio_list = []
    e_list = []
    n_list = []
    u_list = []

    for i in range(0, n, step):
        d = data[i]
        times.append(d.get('time_str', ''))
        lats.append(d.get('lat', 0))
        lons.append(d.get('lon', 0))
        heights.append(d.get('height', 0))
        qualities.append(d.get('quality', 0))
        ns_list.append(d.get('ns', 0))
        sdn_list.append(d.get('sdn', 0))
        sde_list.append(d.get('sde', 0))
        sdu_list.append(d.get('sdu', 0))
        age_list.append(d.get('age', 0))
        ratio_list.append(d.get('ratio', 0))
        e_list.append(round(d.get('e', 0), 4))
        n_list.append(round(d.get('n', 0), 4))
        u_list.append(round(d.get('u', 0), 4))

    return {
        'time': times,
        'lat': lats,
        'lon': lons,
        'height': heights,
        'quality': qualities,
        'ns': ns_list,
        'sdn': sdn_list,
        'sde': sde_list,
        'sdu': sdu_list,
        'age': age_list,
        'ratio': ratio_list,
        'e': e_list,
        'n': n_list,
        'u': u_list,
        'total_epochs': len(data),
        'displayed_epochs': len(times),
        'decimation': step,
    }


# ═══════════════════════════════════════════════════════
# Internal helpers
# ═══════════════════════════════════════════════════════

def _parse_line(parts):
    """Parse a single .pos data line into a dict"""
    # Detect time format: "yyyy/mm/dd hh:mm:ss.sss" (2 fields) or "wwww ssss.sss" (1-2)
    # Key: if second field contains ':', it's hh:mm:ss → date+time = 2 fields
    if len(parts) > 8 and ':' in parts[1]:
        # Date + time format (from -t flag)
        time_str = parts[0] + ' ' + parts[1]
        idx = 2
    elif len(parts) > 7:
        # GPS week + TOW or single time field
        time_str = parts[0]
        idx = 1
    else:
        return None

    entry = {
        'time_str': time_str,
        'lat': float(parts[idx]),
        'lon': float(parts[idx + 1]),
        'height': float(parts[idx + 2]),
        'quality': int(parts[idx + 3]),
    }

    # Optional fields
    if len(parts) > idx + 4:
        entry['ns'] = int(parts[idx + 4])
    else:
        entry['ns'] = 0

    if len(parts) > idx + 7:
        entry['sdn'] = float(parts[idx + 5])
        entry['sde'] = float(parts[idx + 6])
        entry['sdu'] = float(parts[idx + 7])
    else:
        entry['sdn'] = entry['sde'] = entry['sdu'] = 0.0

    if len(parts) > idx + 10:
        entry['sdne'] = float(parts[idx + 8])
        entry['sdeu'] = float(parts[idx + 9])
        entry['sdun'] = float(parts[idx + 10])
    else:
        entry['sdne'] = entry['sdeu'] = entry['sdun'] = 0.0

    if len(parts) > idx + 11:
        entry['age'] = float(parts[idx + 11])
    else:
        entry['age'] = 0.0

    if len(parts) > idx + 12:
        entry['ratio'] = float(parts[idx + 12])
    else:
        entry['ratio'] = 0.0

    return entry


def _compute_reference(data):
    """Compute reference position from mean of fix solutions"""
    fix_data = [d for d in data if d['quality'] == 1]
    if not fix_data:
        fix_data = data  # Use all if no fix

    if not fix_data:
        return None

    n = len(fix_data)
    ref_lat = sum(d['lat'] for d in fix_data) / n
    ref_lon = sum(d['lon'] for d in fix_data) / n
    ref_hgt = sum(d['height'] for d in fix_data) / n

    return {
        'lat': ref_lat,
        'lon': ref_lon,
        'height': ref_hgt,
        'from_fix': len([d for d in data if d['quality'] == 1]) > 0,
        'n_averaged': n,
    }


def _llh_to_enu(lat, lon, hgt, lat0, lon0, hgt0):
    """
    Convert LLH to local ENU relative to reference point.
    Simple approximation good for short baselines (< 100km).
    """
    DEG2RAD = math.pi / 180.0
    a = 6378137.0  # WGS84 semi-major axis

    lat_r = lat0 * DEG2RAD
    dlat = (lat - lat0) * DEG2RAD
    dlon = (lon - lon0) * DEG2RAD
    dhgt = hgt - hgt0

    # Meridional and prime vertical radii
    e2 = 0.00669437999014
    sin_lat = math.sin(lat_r)
    N = a / math.sqrt(1 - e2 * sin_lat * sin_lat)
    M = a * (1 - e2) / ((1 - e2 * sin_lat * sin_lat) ** 1.5)

    east = dlon * N * math.cos(lat_r)
    north = dlat * M
    up = dhgt

    return round(east, 4), round(north, 4), round(up, 4)


def _compute_summary(data):
    """Compute statistics from parsed data"""
    n = len(data)
    if n == 0:
        return _empty_summary()

    q_counts = {}
    for d in data:
        q = d['quality']
        q_counts[q] = q_counts.get(q, 0) + 1

    fix = q_counts.get(1, 0)
    flt = q_counts.get(2, 0)
    single = q_counts.get(5, 0)
    ppp = q_counts.get(6, 0)

    # Position precision (from fix solutions)
    fix_data = [d for d in data if d['quality'] == 1]
    precision = {}
    if fix_data:
        sdn_vals = [d['sdn'] for d in fix_data if d['sdn'] > 0]
        sde_vals = [d['sde'] for d in fix_data if d['sde'] > 0]
        sdu_vals = [d['sdu'] for d in fix_data if d['sdu'] > 0]
        if sdn_vals:
            precision['mean_sdn'] = round(sum(sdn_vals) / len(sdn_vals), 4)
            precision['max_sdn'] = round(max(sdn_vals), 4)
        if sde_vals:
            precision['mean_sde'] = round(sum(sde_vals) / len(sde_vals), 4)
            precision['max_sde'] = round(max(sde_vals), 4)
        if sdu_vals:
            precision['mean_sdu'] = round(sum(sdu_vals) / len(sdu_vals), 4)
            precision['max_sdu'] = round(max(sdu_vals), 4)

    # ENU stats (from fix solutions)
    enu_stats = {}
    if fix_data and 'e' in fix_data[0]:
        e_vals = [d['e'] for d in fix_data]
        n_vals = [d['n'] for d in fix_data]
        u_vals = [d['u'] for d in fix_data]
        if e_vals:
            enu_stats['e_std'] = round(_std(e_vals), 4)
            enu_stats['n_std'] = round(_std(n_vals), 4)
            enu_stats['u_std'] = round(_std(u_vals), 4)
            enu_stats['h_rms'] = round(math.sqrt(
                _std(e_vals)**2 + _std(n_vals)**2), 4)

    # Satellite stats
    ns_vals = [d['ns'] for d in data if d['ns'] > 0]
    ns_stats = {}
    if ns_vals:
        ns_stats['mean'] = round(sum(ns_vals) / len(ns_vals), 1)
        ns_stats['min'] = min(ns_vals)
        ns_stats['max'] = max(ns_vals)

    # Ratio stats
    ratio_vals = [d['ratio'] for d in data if d['ratio'] > 0]
    ratio_stats = {}
    if ratio_vals:
        ratio_stats['mean'] = round(sum(ratio_vals) / len(ratio_vals), 1)
        ratio_stats['max'] = round(max(ratio_vals), 1)

    return {
        'total_epochs': n,
        'fix': fix,
        'float': flt,
        'single': single,
        'ppp': ppp,
        'fix_pct': round(100 * fix / n, 1),
        'float_pct': round(100 * flt / n, 1),
        'single_pct': round(100 * single / n, 1),
        'q_counts': q_counts,
        'precision': precision,
        'enu_stats': enu_stats,
        'ns_stats': ns_stats,
        'ratio_stats': ratio_stats,
    }


def _empty_summary():
    return {
        'total_epochs': 0, 'fix': 0, 'float': 0, 'single': 0, 'ppp': 0,
        'fix_pct': 0, 'float_pct': 0, 'single_pct': 0,
        'q_counts': {}, 'precision': {}, 'enu_stats': {},
        'ns_stats': {}, 'ratio_stats': {},
    }


def _empty_chart_data():
    return {
        'time': [], 'lat': [], 'lon': [], 'height': [],
        'quality': [], 'ns': [], 'sdn': [], 'sde': [], 'sdu': [],
        'age': [], 'ratio': [], 'e': [], 'n': [], 'u': [],
        'total_epochs': 0, 'displayed_epochs': 0, 'decimation': 1,
    }


def _std(vals):
    """Standard deviation"""
    if len(vals) < 2:
        return 0.0
    mean = sum(vals) / len(vals)
    return math.sqrt(sum((v - mean) ** 2 for v in vals) / (len(vals) - 1))


def _parse_time(time_str):
    """Parse time string to datetime"""
    for fmt in ('%Y/%m/%d %H:%M:%S.%f', '%Y/%m/%d %H:%M:%S'):
        try:
            return datetime.strptime(time_str.strip(), fmt)
        except ValueError:
            continue
    return None


def _format_duration(seconds):
    """Format duration in seconds to human readable"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if h > 0:
        return f'{h}h {m}m {s}s'
    elif m > 0:
        return f'{m}m {s}s'
    return f'{s}s'


# ═══════════════════════════════════════════════════════
# PPK Analyser functions
# ═══════════════════════════════════════════════════════

def compute_session_stats(epochs: list) -> dict:
    """
    Compute session-level statistics from a list of epoch dicts.

    Returns dict with fix count/%, ratio stats, satellite stats, sigma means.
    Only stdlib Python — no numpy/pandas.
    """
    n = len(epochs)
    if n == 0:
        return {
            'total_epochs': 0, 'fix_epochs': 0, 'fix_pct': 0.0,
            'ratio_mean': 0.0, 'ratio_max': 0.0,
            'ns_mean': 0.0, 'sdu_mean': 0.0, 'sdn_mean': 0.0, 'sde_mean': 0.0,
        }

    fix_epochs = [e for e in epochs if e.get('quality') == 1]
    n_fix = len(fix_epochs)

    ratio_vals = [e['ratio'] for e in fix_epochs if e.get('ratio', 0) > 0]
    ns_vals = [e['ns'] for e in epochs if e.get('ns', 0) > 0]
    sdu_vals = [e['sdu'] for e in fix_epochs if e.get('sdu', 0) > 0]
    sdn_vals = [e['sdn'] for e in fix_epochs if e.get('sdn', 0) > 0]
    sde_vals = [e['sde'] for e in fix_epochs if e.get('sde', 0) > 0]

    def _mean(vals):
        return round(sum(vals) / len(vals), 4) if vals else 0.0

    return {
        'total_epochs': n,
        'fix_epochs': n_fix,
        'fix_pct': round(100.0 * n_fix / n, 1),
        'ratio_mean': _mean(ratio_vals),
        'ratio_max': round(max(ratio_vals), 1) if ratio_vals else 0.0,
        'ns_mean': _mean(ns_vals),
        'sdu_mean': _mean(sdu_vals),
        'sdn_mean': _mean(sdn_vals),
        'sde_mean': _mean(sde_vals),
    }


def weighted_mean_station(epochs: list) -> dict:
    """
    Compute weighted mean position from a list of epoch dicts.

    Weight = 1/σ² for each coordinate independently.
    σ floor = 0.0001 m to avoid division by zero with malformed data.
    Propagated sigma of mean: σ_mean = sqrt(1 / Σ(1/σ_i²))

    Returns dict with lat, lon, h, sigma_N, sigma_E, sigma_U,
    n_epochs, ratio_mean, ratio_max, ns_mean, t_start, t_end.
    """
    _SIGMA_FLOOR = 0.0001

    if not epochs:
        return {}

    sum_w_lat = sum_w_lon = sum_w_h = 0.0
    sum_wlat = sum_wlon = sum_wh = 0.0

    for e in epochs:
        sdn = max(e.get('sdn', 0) or 0, _SIGMA_FLOOR)
        sde = max(e.get('sde', 0) or 0, _SIGMA_FLOOR)
        sdu = max(e.get('sdu', 0) or 0, _SIGMA_FLOOR)

        wn = 1.0 / (sdn * sdn)
        we = 1.0 / (sde * sde)
        wh = 1.0 / (sdu * sdu)

        sum_w_lat += wn
        sum_wlat += wn * e['lat']

        sum_w_lon += we
        sum_wlon += we * e['lon']

        sum_w_h += wh
        sum_wh += wh * e['height']

    lat_mean = sum_wlat / sum_w_lat
    lon_mean = sum_wlon / sum_w_lon
    h_mean = sum_wh / sum_w_h

    sigma_N = math.sqrt(1.0 / sum_w_lat)
    sigma_E = math.sqrt(1.0 / sum_w_lon)
    sigma_U = math.sqrt(1.0 / sum_w_h)

    ratio_vals = [e['ratio'] for e in epochs if e.get('ratio', 0) > 0]
    ns_vals = [e['ns'] for e in epochs if e.get('ns', 0) > 0]

    def _mean(vals):
        return round(sum(vals) / len(vals), 2) if vals else 0.0

    t_start = epochs[0].get('time_str', '')
    t_end = epochs[-1].get('time_str', '')

    return {
        'lat': lat_mean,
        'lon': lon_mean,
        'h': h_mean,
        'sigma_N': round(sigma_N, 4),
        'sigma_E': round(sigma_E, 4),
        'sigma_U': round(sigma_U, 4),
        'n_epochs': len(epochs),
        'ratio_mean': _mean(ratio_vals),
        'ratio_max': round(max(ratio_vals), 1) if ratio_vals else 0.0,
        'ns_mean': _mean(ns_vals),
        't_start': t_start,
        't_end': t_end,
    }
