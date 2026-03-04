"""
Convbin Wrapper

Wraps the RTKLIB convbin CLI tool for converting raw receiver
data to RINEX format. All options verified against convbin.c source.

Reference: RTKLIBExplorer demo5 convbin.c
"""

import os
import subprocess
from pathlib import Path


class ConvbinWrapper:
    """Wrapper for RTKLIB convbin CLI tool"""

    # ─────────────────────────────────────────
    # Formats: verified against convbin.c l.100-113, l.566-608
    # ─────────────────────────────────────────
    INPUT_FORMATS = [
        {'value': 'ubx',     'label': 'u-blox LEA-4T/5T/6T/7T/M8T/F9', 'extensions': ['.ubx']},
        {'value': 'nov',     'label': 'NovAtel OEM4/V/6/7, OEMStar',    'extensions': ['.gps']},
        {'value': 'sbp',     'label': 'Swift Navigation SBP',           'extensions': ['.sbp']},
        {'value': 'hemis',   'label': 'Hemisphere Eclipse/Crescent',    'extensions': ['.bin']},
        {'value': 'stq',     'label': 'SkyTraq S1315F',                 'extensions': ['.stq']},
        {'value': 'javad',   'label': 'Javad GREIS',                    'extensions': ['.jps']},
        {'value': 'nvs',     'label': 'NVS NV08C BINR',                 'extensions': ['.nvs']},
        {'value': 'binex',   'label': 'BINEX',                          'extensions': ['.bnx', '.binex']},
        {'value': 'rt17',    'label': 'Trimble RT17',                   'extensions': ['.rt17']},
        {'value': 'sbf',     'label': 'Septentrio SBF',                 'extensions': ['.sbf']},
        {'value': 'unicore', 'label': 'Unicore',                        'extensions': ['.unc']},
        {'value': 'rtcm2',   'label': 'RTCM 2',                         'extensions': ['.rtcm2']},
        {'value': 'rtcm3',   'label': 'RTCM 3',                         'extensions': ['.rtcm3']},
        {'value': 'rinex',   'label': 'RINEX',                          'extensions': ['.obs', '.rnx', '.nav']},
    ]

    # Auto-detect format from extension (from convbin.c l.586-608)
    EXT_TO_FORMAT = {
        '.rtcm2': 'rtcm2', '.rtcm3': 'rtcm3', '.gps': 'nov',
        '.ubx': 'ubx', '.sbp': 'sbp', '.bin': 'hemis',
        '.stq': 'stq', '.jps': 'javad', '.bnx': 'binex',
        '.binex': 'binex', '.rt17': 'rt17', '.sbf': 'sbf',
        '.unc': 'unicore', '.obs': 'rinex', '.rnx': 'rinex',
        '.nav': 'rinex',
    }

    # RINEX versions supported
    RINEX_VERSIONS = ['2.10', '2.11', '2.12', '3.00', '3.02', '3.03', '3.04']

    # GNSS systems — convbin uses -y to EXCLUDE systems (convbin.c l.517-526)
    # Default: all enabled. Use -y G to exclude GPS, -y R to exclude GLONASS, etc.
    GNSS_SYSTEMS = [
        {'code': 'G', 'name': 'GPS',     'flag': 'SYS_GPS'},
        {'code': 'R', 'name': 'GLONASS', 'flag': 'SYS_GLO'},
        {'code': 'E', 'name': 'Galileo', 'flag': 'SYS_GAL'},
        {'code': 'J', 'name': 'QZSS',    'flag': 'SYS_QZS'},
        {'code': 'S', 'name': 'SBAS',    'flag': 'SYS_SBS'},
        {'code': 'C', 'name': 'BeiDou',  'flag': 'SYS_CMP'},
        {'code': 'I', 'name': 'NavIC',   'flag': 'SYS_IRN'},
    ]

    ALL_SYS_CODES = {s['code'] for s in GNSS_SYSTEMS}

    # Frequencies: -f N where N=1..5 (convbin.c l.556-560)
    # L1, L2, L3(L5), L4(L6), L5(L7) — default is 5 (all)
    FREQ_OPTIONS = [
        {'value': '1', 'label': 'L1 only'},
        {'value': '2', 'label': 'L1 + L2'},
        {'value': '3', 'label': 'L1 + L2 + L5'},
        {'value': '4', 'label': 'L1 + L2 + L5 + L6'},
        {'value': '5', 'label': 'All (L1-L7)'},
    ]

    # 9 output files (convbin.c l.57, l.533-541)
    # -o obs, -n nav, -g gnav, -h hnav, -q qnav, -l lnav, -b cnav, -i inav, -s sbas
    OUTPUT_FILES = [
        {'flag': '-o', 'suffix': '.obs', 'label': 'OBS (observation)'},
        {'flag': '-n', 'suffix': '.nav', 'label': 'NAV (GPS/mixed navigation)'},
        {'flag': '-g', 'suffix': '.gnav', 'label': 'GNAV (GLONASS navigation)'},
        {'flag': '-h', 'suffix': '.hnav', 'label': 'HNAV (geostationary navigation)'},
        {'flag': '-q', 'suffix': '.qnav', 'label': 'QNAV (QZSS navigation)'},
        {'flag': '-l', 'suffix': '.lnav', 'label': 'LNAV (Galileo navigation)'},
        {'flag': '-b', 'suffix': '.cnav', 'label': 'CNAV (BeiDou navigation)'},
        {'flag': '-i', 'suffix': '.inav', 'label': 'INAV (NavIC navigation)'},
        {'flag': '-s', 'suffix': '.sbs', 'label': 'SBAS messages'},
    ]

    def __init__(self, binary_path='convbin'):
        self.binary = binary_path

    def is_available(self):
        """Check if convbin binary exists and is executable"""
        return os.path.isfile(self.binary) and os.access(self.binary, os.X_OK)

    def get_version(self):
        """Get convbin version string (--version flag, convbin.c l.545-548)"""
        try:
            result = subprocess.run(
                [self.binary, '--version'],
                capture_output=True, text=True, timeout=10
            )
            output = (result.stdout + result.stderr).strip()
            return output if output else None
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return None

    def detect_format(self, filepath):
        """Auto-detect input format from file extension (convbin.c l.583-608)"""
        ext = os.path.splitext(filepath)[1].lower()
        if ext in self.EXT_TO_FORMAT:
            return self.EXT_TO_FORMAT[ext]
        # Check for RINEX numeric extensions like .24o, .23n
        if len(ext) == 4 and ext[1:3].isdigit():
            suffix = ext[3].lower()
            if suffix in ('o',):
                return 'rinex'
            if suffix in ('n',):
                return 'rinex'
        return None

    def build_command(self, input_file, options=None):
        """
        Build convbin command line from options dict.
        All flags verified against convbin.c cmdopts() function.

        Parameters in options dict:
        -----------------------------------------------------------
        Time control:
          start_time     -ts y/m/d h:m:s    start time
          end_time       -te y/m/d h:m:s    end time
          rtcm_time      -tr y/m/d h:m:s    approx time for RTCM
          interval       -ti tint           obs interval (s)
          epoch_tol      -tt ttol           epoch tolerance (s) [0.005]
          span           -span hours        time span (h)

        Format:
          format         -r format          input format string
          rinex_ver      -v ver             RINEX version [3.04]
          freq           -f N               number of frequencies [5]
          receiver_opts  -ro opt            receiver-specific options

        RINEX header:
          comment        -hc comment        header comment
          marker_name    -hm marker         marker name
          marker_number  -hn markno         marker number
          marker_type    -ht marktype       marker type
          observer       -ho obs/agency     observer and agency (/-separated)
          receiver       -hr num/type/ver   receiver info (/-separated)
          antenna        -ha num/type       antenna info (/-separated)
          approx_pos     -hp x/y/z          approx position (/-separated)
          antenna_delta  -hd h/e/n          antenna delta (/-separated)

        Output control:
          include_doppler  -od              include Doppler [on by default]
          include_snr      -os              include SNR [on by default]
          include_iono     -oi              iono correction in nav header
          include_time     -ot              time correction in nav header
          include_leaps    -ol              leap seconds in nav header

        Filtering:
          enabled_systems  list             systems to KEEP (e.g. ['G','R','E','C'])
                                            — convbin excludes the rest via -y
          exclude_sats     list             satellites to exclude (e.g. ['G03','R15'])
          signal_mask      str              signal mask string
          signal_nomask    str              signal no-mask string
          half_cyc         bool             half-cycle ambiguity correction

        Output files:
          output_dir       -d dir           output directory
          station_id       -c staid         RINEX file name convention
          obs_file         -o file          explicit OBS output file
          nav_file         -n file          explicit NAV output file
          gnav_file        -g file          explicit GNAV output file
          hnav_file        -h file          explicit HNAV output file
          qnav_file        -q file          explicit QNAV output file
          lnav_file        -l file          explicit LNAV output file
          cnav_file        -b file          explicit CNAV output file
          inav_file        -i file          explicit INAV output file
          sbas_file        -s file          explicit SBAS output file

        Debug:
          trace            -trace level     trace output level
        -----------------------------------------------------------
        """
        options = options or {}
        cmd = [self.binary]

        # -- Input format (-r) --
        fmt = options.get('format', '')
        if fmt:
            cmd.extend(['-r', fmt])

        # -- RINEX version (-v) --
        rinex_ver = options.get('rinex_ver', '')
        if rinex_ver:
            cmd.extend(['-v', rinex_ver])

        # -- Frequencies (-f) --
        freq = options.get('freq', '')
        if freq:
            cmd.extend(['-f', str(freq)])

        # -- Receiver options (-ro) --
        ro = options.get('receiver_opts', '')
        if ro:
            cmd.extend(['-ro', ro])

        # -- Time control --
        start_time = options.get('start_time', '')
        if start_time:
            cmd.extend(['-ts', start_time])

        end_time = options.get('end_time', '')
        if end_time:
            cmd.extend(['-te', end_time])

        rtcm_time = options.get('rtcm_time', '')
        if rtcm_time:
            cmd.extend(['-tr', rtcm_time])

        interval = options.get('interval', '')
        if interval:
            cmd.extend(['-ti', str(interval)])

        epoch_tol = options.get('epoch_tol', '')
        if epoch_tol:
            cmd.extend(['-tt', str(epoch_tol)])

        span = options.get('span', '')
        if span:
            cmd.extend(['-span', str(span)])

        # -- RINEX header fields --
        comment = options.get('comment', '')
        if comment:
            cmd.extend(['-hc', comment])

        marker_name = options.get('marker_name', '')
        if marker_name:
            cmd.extend(['-hm', marker_name])

        marker_number = options.get('marker_number', '')
        if marker_number:
            cmd.extend(['-hn', marker_number])

        marker_type = options.get('marker_type', '')
        if marker_type:
            cmd.extend(['-ht', marker_type])

        observer = options.get('observer', '')
        if observer:
            cmd.extend(['-ho', observer])

        receiver = options.get('receiver', '')
        if receiver:
            cmd.extend(['-hr', receiver])

        antenna = options.get('antenna', '')
        if antenna:
            cmd.extend(['-ha', antenna])

        approx_pos = options.get('approx_pos', '')
        if approx_pos:
            cmd.extend(['-hp', approx_pos])

        antenna_delta = options.get('antenna_delta', '')
        if antenna_delta:
            cmd.extend(['-hd', antenna_delta])

        # -- Output content flags --
        if options.get('include_doppler', True):
            cmd.append('-od')
        if options.get('include_snr', True):
            cmd.append('-os')
        if options.get('include_iono', False):
            cmd.append('-oi')
        if options.get('include_time', False):
            cmd.append('-ot')
        if options.get('include_leaps', False):
            cmd.append('-ol')

        # -- Half-cycle correction (-halfc) --
        if options.get('half_cyc', False):
            cmd.append('-halfc')

        # -- System exclusion (-y): convbin EXCLUDES with -y --
        # UI sends list of enabled systems; we exclude what's NOT enabled
        enabled_systems = options.get('enabled_systems', [])
        if enabled_systems:
            for sys_info in self.GNSS_SYSTEMS:
                code = sys_info['code']
                if code not in enabled_systems:
                    cmd.extend(['-y', code])

        # -- Exclude specific satellites (-x) --
        exclude_sats = options.get('exclude_sats', [])
        for sat in exclude_sats:
            cmd.extend(['-x', sat])

        # -- Signal mask (-mask / -nomask) --
        signal_mask = options.get('signal_mask', '')
        if signal_mask:
            cmd.extend(['-mask', signal_mask])

        signal_nomask = options.get('signal_nomask', '')
        if signal_nomask:
            cmd.extend(['-nomask', signal_nomask])

        # -- Output directory (-d) --
        output_dir = options.get('output_dir', '')
        if output_dir:
            cmd.extend(['-d', output_dir])

        # -- Station ID for RINEX naming (-c) --
        station_id = options.get('station_id', '')
        if station_id:
            cmd.extend(['-c', station_id])

        # -- Explicit output files --
        for key, flag in [
            ('obs_file', '-o'), ('nav_file', '-n'), ('gnav_file', '-g'),
            ('hnav_file', '-h'), ('qnav_file', '-q'), ('lnav_file', '-l'),
            ('cnav_file', '-b'), ('inav_file', '-i'), ('sbas_file', '-s'),
        ]:
            val = options.get(key, '')
            if val:
                cmd.extend([flag, val])

        # -- Trace level --
        trace = options.get('trace', 0)
        if trace and int(trace) > 0:
            cmd.extend(['-trace', str(trace)])

        # -- Input file (must be last, convbin.c l.551) --
        cmd.append(input_file)

        return cmd

    def build_command_string(self, input_file, options=None):
        """Return command as a single string for display"""
        cmd = self.build_command(input_file, options)
        parts = []
        for c in cmd:
            if ' ' in c:
                parts.append(f'"{c}"')
            else:
                parts.append(c)
        return ' '.join(parts)

    def run(self, input_file, options=None, timeout=600):
        """
        Run convbin with given options.

        Returns dict with stdout, stderr, returncode, and output file list.
        Timeout default 600s (10 min) — generous for RPi Zero 2W.
        """
        options = options or {}
        cmd = self.build_command(input_file, options)

        # Determine where output files will appear
        output_dir = options.get('output_dir', '')
        if not output_dir:
            output_dir = os.path.dirname(input_file) or '.'

        # List files before to detect new ones
        existing = set()
        if os.path.isdir(output_dir):
            existing = set(os.listdir(output_dir))

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )

            # Find new output files
            new_files = []
            if os.path.isdir(output_dir):
                current = set(os.listdir(output_dir))
                new = current - existing
                for f in sorted(new):
                    full_path = os.path.join(output_dir, f)
                    if os.path.isfile(full_path):
                        new_files.append({
                            'name': f,
                            'path': full_path,
                            'size': os.path.getsize(full_path),
                        })

            return {
                'stdout': result.stdout,
                'stderr': result.stderr,
                'returncode': result.returncode,
                'output_files': new_files,
                'command': self.build_command_string(input_file, options),
            }

        except subprocess.TimeoutExpired:
            return {
                'stdout': '',
                'stderr': f'Process timed out after {timeout}s',
                'returncode': -1,
                'output_files': [],
                'command': self.build_command_string(input_file, options),
            }
        except FileNotFoundError:
            return {
                'stdout': '',
                'stderr': f'convbin binary not found: {self.binary}',
                'returncode': -1,
                'output_files': [],
                'command': self.build_command_string(input_file, options),
            }
