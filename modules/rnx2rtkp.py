"""
rnx2rtkp Wrapper — verified against rnx2rtkp.c source line by line.

Key design notes from the source code:
  - Config file (-k) is loaded FIRST, then CLI options OVERRIDE it (two-pass)
  - Input files are positional (no flag): rover_obs base_obs nav [nav...]
  - -sys INCLUDES systems (opposite of convbin -y which EXCLUDES)
  - -ts/-te take TWO arguments each (date + time)
  - -r takes THREE arguments (x y z), -l takes THREE (lat lon hgt)
  - -bl takes TWO arguments (baseline stdev)
  - Max 16 input files (MAXFILE, l.24)
"""

import os
import subprocess
from pathlib import Path


class Rnx2rtkpWrapper:
    """Wrapper for RTKLIB rnx2rtkp CLI tool"""

    # Processing modes (rnx2rtkp.c l.51-52, l.139)
    MODES = [
        {'value': 0, 'name': 'single',        'label': 'Single Point'},
        {'value': 1, 'name': 'dgps',          'label': 'DGPS'},
        {'value': 2, 'name': 'kinematic',     'label': 'Kinematic'},
        {'value': 3, 'name': 'static',        'label': 'Static'},
        {'value': 4, 'name': 'static-start',  'label': 'Static-Start'},
        {'value': 5, 'name': 'moving-base',   'label': 'Moving Base'},
        {'value': 6, 'name': 'fixed',         'label': 'Fixed'},
        {'value': 7, 'name': 'ppp-kinematic', 'label': 'PPP Kinematic'},
        {'value': 8, 'name': 'ppp-static',    'label': 'PPP Static'},
        {'value': 9, 'name': 'ppp-fixed',     'label': 'PPP Fixed'},
    ]

    # Solution type: -b/-c flags (rnx2rtkp.c l.159-160)
    SOLUTION_TYPES = [
        {'value': 0, 'name': 'forward',  'label': 'Forward'},
        {'value': 1, 'name': 'backward', 'label': 'Backward',           'flag': '-b'},
        {'value': 2, 'name': 'combined', 'label': 'Forward + Backward', 'flag': '-c'},
    ]

    # AR modes: -i/-h flags (rnx2rtkp.c l.161-162)
    AR_MODES = [
        {'value': 0, 'name': 'off',            'label': 'Off'},
        {'value': 1, 'name': 'continuous',      'label': 'Continuous'},
        {'value': 2, 'name': 'instantaneous',   'label': 'Instantaneous', 'flag': '-i'},
        {'value': 3, 'name': 'fix-and-hold',    'label': 'Fix and Hold',  'flag': '-h'},
    ]

    # GNSS systems — -sys INCLUDES (rnx2rtkp.c l.141-153)
    # Default: GPS + GLONASS if -sys not specified (l.195-196)
    GNSS_SYSTEMS = [
        {'code': 'G', 'name': 'GPS'},
        {'code': 'R', 'name': 'GLONASS'},
        {'code': 'E', 'name': 'Galileo'},
        {'code': 'J', 'name': 'QZSS'},
        {'code': 'C', 'name': 'BeiDou'},
        {'code': 'I', 'name': 'NavIC'},
    ]

    # Frequencies (rnx2rtkp.c l.55, l.140)
    FREQ_OPTIONS = [
        {'value': 1, 'label': 'L1 only'},
        {'value': 2, 'label': 'L1 + L2'},
        {'value': 3, 'label': 'L1 + L2 + L5'},
    ]

    # Output position format (rnx2rtkp.c l.165-167)
    OUTPUT_FORMATS = [
        {'value': 'llh',  'label': 'Lat/Lon/Height'},
        {'value': 'xyz',  'label': 'ECEF X/Y/Z',      'flag': '-e'},
        {'value': 'enu',  'label': 'E/N/U Baseline',   'flag': '-a'},
        {'value': 'nmea', 'label': 'NMEA-0183 GGA',    'flag': '-n'},
    ]

    # Solution status output (rnx2rtkp.c l.74, l.186)
    SOL_STATUS = [
        {'value': 0, 'label': 'Off'},
        {'value': 1, 'label': 'States'},
        {'value': 2, 'label': 'Residuals'},
    ]

    MAXFILE = 16  # rnx2rtkp.c l.24

    def __init__(self, binary_path='rnx2rtkp'):
        self.binary = binary_path

    def is_available(self):
        return os.path.isfile(self.binary) and os.access(self.binary, os.X_OK)

    def get_version(self):
        """--version flag (rnx2rtkp.c l.188-190)"""
        try:
            result = subprocess.run(
                [self.binary, '--version'],
                capture_output=True, text=True, timeout=10
            )
            return (result.stdout + result.stderr).strip() or None
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return None

    def build_command(self, rover_obs, base_obs='', nav_files=None,
                      options=None):
        """
        Build rnx2rtkp command line — all flags verified against source.

        File order: rover_obs base_obs nav1 [nav2 ...] (positional, l.193)
        Config file (-k) placed first so CLI overrides it (l.117-124).
        """
        options = options or {}
        cmd = [self.binary]

        # ── -k config file: FIRST (two-pass design, l.117-124) ──
        config_file = options.get('config_file', '')
        if config_file:
            cmd.extend(['-k', config_file])

        # ── -o output file (l.126) ──
        output_file = options.get('output_file', '')
        if output_file:
            cmd.extend(['-o', output_file])

        # ── -ts: TWO args (l.127-131) ──
        start_time = options.get('start_time', '')
        if start_time:
            parts = start_time.strip().split()
            if len(parts) >= 2:
                cmd.extend(['-ts', parts[0], parts[1]])
            elif len(parts) == 1:
                cmd.extend(['-ts', parts[0], '0:0:0'])

        # ── -te: TWO args (l.132-136) ──
        end_time = options.get('end_time', '')
        if end_time:
            parts = end_time.strip().split()
            if len(parts) >= 2:
                cmd.extend(['-te', parts[0], parts[1]])
            elif len(parts) == 1:
                cmd.extend(['-te', parts[0], '23:59:59'])

        # ── -ti interval (l.137) ──
        interval = options.get('interval', '')
        if interval:
            cmd.extend(['-ti', str(interval)])

        # ── -p mode 0-9 (l.139) ──
        mode = options.get('mode', '')
        if mode != '':
            cmd.extend(['-p', str(mode)])

        # ── -f freq (l.140) ──
        freq = options.get('freq', '')
        if freq:
            cmd.extend(['-f', str(freq)])

        # ── -sys: INCLUDE semantics (l.141-153) ──
        systems = options.get('systems', [])
        if systems:
            if isinstance(systems, list):
                sys_str = ','.join(systems)
            else:
                sys_str = str(systems)
            cmd.extend(['-sys', sys_str])

        # ── -m elevation mask deg (l.155) ──
        elev = options.get('elevation_mask', '')
        if elev != '':
            cmd.extend(['-m', str(elev)])

        # ── -v AR threshold (l.156) ──
        ar_val = options.get('ar_validation', '')
        if ar_val != '':
            cmd.extend(['-v', str(ar_val)])

        # ── Solution type: -b or -c (l.159-160) ──
        sol = options.get('solution_type', '')
        if sol == 'backward':
            cmd.append('-b')
        elif sol == 'combined':
            cmd.append('-c')

        # ── AR mode: -i or -h (l.161-162) ──
        ar = options.get('ar_mode', '')
        if ar == 'instantaneous':
            cmd.append('-i')
        elif ar == 'fix-and-hold':
            cmd.append('-h')

        # ── -bl: TWO args (l.169-171) ──
        bl = options.get('baseline', '')
        if bl:
            if isinstance(bl, (list, tuple)) and len(bl) >= 2:
                cmd.extend(['-bl', str(bl[0]), str(bl[1])])
            elif isinstance(bl, str) and bl.strip():
                parts = bl.strip().replace(',', ' ').split()
                if len(parts) >= 2:
                    cmd.extend(['-bl', parts[0], parts[1]])

        # ── -r: THREE args ECEF (l.172-176) ──
        ref_ecef = options.get('ref_ecef', '')
        if ref_ecef:
            if isinstance(ref_ecef, (list, tuple)) and len(ref_ecef) >= 3:
                cmd.extend(['-r'] + [str(v) for v in ref_ecef[:3]])
            elif isinstance(ref_ecef, str) and ref_ecef.strip():
                parts = ref_ecef.strip().replace(',', ' ').split()
                if len(parts) >= 3:
                    cmd.extend(['-r'] + parts[:3])

        # ── -l: THREE args LLH (l.177-183) — mutually exclusive with -r ──
        ref_llh = options.get('ref_llh', '')
        if ref_llh and not ref_ecef:
            if isinstance(ref_llh, (list, tuple)) and len(ref_llh) >= 3:
                cmd.extend(['-l'] + [str(v) for v in ref_llh[:3]])
            elif isinstance(ref_llh, str) and ref_llh.strip():
                parts = ref_llh.strip().replace(',', ' ').split()
                if len(parts) >= 3:
                    cmd.extend(['-l'] + parts[:3])

        # ── Output format flags ──
        fmt = options.get('output_format', '')
        if fmt == 'xyz':     cmd.append('-e')   # l.165
        elif fmt == 'enu':   cmd.append('-a')   # l.166
        elif fmt == 'nmea':  cmd.append('-n')   # l.167

        if options.get('time_format'):    cmd.append('-t')   # l.163
        if options.get('time_utc'):       cmd.append('-u')   # l.164
        if options.get('degree_format'):  cmd.append('-g')   # l.168

        td = options.get('time_decimals', '')
        if td != '':
            cmd.extend(['-d', str(td)])  # l.158

        sep = options.get('field_sep', '')
        if sep:
            cmd.extend(['-s', sep])  # l.157

        # ── -y solution status (l.186) ──
        ss = options.get('sol_status', '')
        if ss != '' and int(ss) > 0:
            cmd.extend(['-y', str(ss)])

        # ── -x trace (l.187) ──
        tr = options.get('trace', '')
        if tr != '' and int(tr) > 0:
            cmd.extend(['-x', str(tr)])

        # ── --rover / --base (l.184-185) ──
        rn = options.get('rover_name', '')
        if rn: cmd.extend(['--rover', rn])
        bn = options.get('base_name', '')
        if bn: cmd.extend(['--base', bn])

        # ── Input files: positional, LAST (l.193) ──
        if rover_obs:
            cmd.append(rover_obs)
        if base_obs:
            cmd.append(base_obs)
        if nav_files:
            for nf in nav_files:
                if nf:
                    cmd.append(nf)

        return cmd

    def build_command_string(self, rover_obs, base_obs='', nav_files=None,
                             options=None):
        cmd = self.build_command(rover_obs, base_obs, nav_files, options)
        return ' '.join(f'"{c}"' if ' ' in c else c for c in cmd)

    def run(self, rover_obs, base_obs='', nav_files=None, options=None,
            timeout=3600):
        """
        Run rnx2rtkp. Timeout default 3600s — PPK on RPi Zero can be slow.
        """
        options = options or {}
        nav_files = nav_files or []
        cmd = self.build_command(rover_obs, base_obs, nav_files, options)
        output_file = options.get('output_file', '')

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout,
            )

            output_info = None
            if output_file and os.path.isfile(output_file):
                output_info = {
                    'name': os.path.basename(output_file),
                    'path': output_file,
                    'size': os.path.getsize(output_file),
                }

            return {
                'stdout': result.stdout,
                'stderr': result.stderr,
                'returncode': result.returncode,
                'output_file': output_info,
                'command': self.build_command_string(
                    rover_obs, base_obs, nav_files, options),
            }
        except subprocess.TimeoutExpired:
            return {
                'stdout': '',
                'stderr': f'Process timed out after {timeout}s',
                'returncode': -1,
                'output_file': None,
                'command': self.build_command_string(
                    rover_obs, base_obs, nav_files, options),
            }
        except FileNotFoundError:
            return {
                'stdout': '',
                'stderr': f'rnx2rtkp binary not found: {self.binary}',
                'returncode': -1,
                'output_file': None,
                'command': self.build_command_string(
                    rover_obs, base_obs, nav_files, options),
            }

    @staticmethod
    def parse_pos_file(filepath):
        """
        Parse .pos output. Q values: 1=fix, 2=float, 3=sbas, 4=dgps, 5=single, 6=ppp
        """
        header_lines = []
        data = []

        with open(filepath, 'r') as f:
            for line in f:
                line = line.rstrip('\n')
                if line.startswith('%'):
                    header_lines.append(line)
                    continue
                if not line.strip():
                    continue
                parts = line.split()
                if len(parts) < 5:
                    continue
                try:
                    # Time field: "GPST" or "yyyy/mm/dd hh:mm:ss.sss"
                    idx = 2 if len(parts) > 8 and ':' in parts[1] else 1
                    entry = {
                        'time': parts[0] + ' ' + parts[1] if idx == 2 else parts[0],
                        'lat': float(parts[idx]),
                        'lon': float(parts[idx + 1]),
                        'height': float(parts[idx + 2]),
                        'quality': int(parts[idx + 3]),
                    }
                    if len(parts) > idx + 4:
                        entry['ns'] = int(parts[idx + 4])
                    if len(parts) > idx + 7:
                        entry['sdn'] = float(parts[idx + 5])
                        entry['sde'] = float(parts[idx + 6])
                        entry['sdu'] = float(parts[idx + 7])
                    if len(parts) > idx + 12:
                        entry['age'] = float(parts[idx + 11])
                        entry['ratio'] = float(parts[idx + 12])
                    data.append(entry)
                except (ValueError, IndexError):
                    continue

        total = len(data)
        fix_count = sum(1 for d in data if d['quality'] == 1)
        float_count = sum(1 for d in data if d['quality'] == 2)
        single_count = sum(1 for d in data if d['quality'] == 5)

        return {
            'header': header_lines,
            'data': data,
            'summary': {
                'total_epochs': total,
                'fix': fix_count,
                'float': float_count,
                'single': single_count,
                'fix_pct': round(100 * fix_count / total, 1) if total else 0,
                'float_pct': round(100 * float_count / total, 1) if total else 0,
            },
        }
