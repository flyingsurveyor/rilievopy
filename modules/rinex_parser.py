"""
RINEX Observation File Parser

Parses RINEX 2.x and 3.x observation files for quality analysis.
Extracts satellite visibility, SNR, cycle slip indicators, and
basic multipath estimates.

Designed to be memory-efficient for Raspberry Pi Zero 2W.
"""

import re
import os
from datetime import datetime, timedelta
from collections import defaultdict


class RinexObsParser:
    """Parser for RINEX observation files (v2.x and v3.x)"""

    # GNSS system identifiers
    SYS_CODES = {
        'G': 'GPS',
        'R': 'GLONASS',
        'E': 'Galileo',
        'J': 'QZSS',
        'C': 'BeiDou',
        'S': 'SBAS',
        'I': 'IRNSS',
    }

    def __init__(self, filepath):
        self.filepath = filepath
        self.version = None
        self.version_major = 2
        self.header = {}
        self.obs_types = {}  # per-system obs types (v3) or global (v2)
        self._header_end = 0
        self._file_size = os.path.getsize(filepath)

    def parse_header(self):
        """Parse RINEX header and return metadata dict"""
        header = {
            'version': '',
            'type': '',
            'marker_name': '',
            'marker_number': '',
            'receiver': '',
            'receiver_type': '',
            'antenna': '',
            'antenna_type': '',
            'approx_pos': {'x': 0, 'y': 0, 'z': 0},
            'antenna_delta': {'h': 0, 'e': 0, 'n': 0},
            'obs_types': {},
            'interval': None,
            'first_obs': None,
            'last_obs': None,
            'num_satellites': 0,
            'systems': [],
            'file_size': self._file_size,
            'file_name': os.path.basename(self.filepath),
        }

        obs_types_lines = {}
        current_sys = 'G'

        with open(self.filepath, 'r', errors='replace') as f:
            line_num = 0
            while True:
                pos = f.tell()          # position before reading line
                line = f.readline()
                if not line:
                    break               # EOF
                line_num += 1

                label = line[60:].strip() if len(line) > 60 else ''

                if label == 'END OF HEADER':
                    self._header_end = f.tell()
                    break

                if label == 'RINEX VERSION / TYPE':
                    header['version'] = line[:20].strip()
                    header['type'] = line[20:40].strip()
                    try:
                        self.version = float(header['version'][:4])
                        self.version_major = int(self.version)
                    except ValueError:
                        self.version = 2.0
                        self.version_major = 2

                elif label == 'MARKER NAME':
                    header['marker_name'] = line[:60].strip()

                elif label == 'MARKER NUMBER':
                    header['marker_number'] = line[:60].strip()

                elif label == 'REC # / TYPE / VERS':
                    header['receiver'] = line[:20].strip()
                    header['receiver_type'] = line[20:40].strip()

                elif label == 'ANT # / TYPE':
                    header['antenna'] = line[:20].strip()
                    header['antenna_type'] = line[20:40].strip()

                elif label == 'APPROX POSITION XYZ':
                    parts = line[:60].split()
                    if len(parts) >= 3:
                        header['approx_pos'] = {
                            'x': float(parts[0]),
                            'y': float(parts[1]),
                            'z': float(parts[2]),
                        }

                elif label == 'ANTENNA: DELTA H/E/N':
                    parts = line[:60].split()
                    if len(parts) >= 3:
                        header['antenna_delta'] = {
                            'h': float(parts[0]),
                            'e': float(parts[1]),
                            'n': float(parts[2]),
                        }

                elif label == 'INTERVAL':
                    try:
                        header['interval'] = float(line[:10].strip())
                    except ValueError:
                        pass

                elif label == 'TIME OF FIRST OBS':
                    header['first_obs'] = self._parse_header_time(line)

                elif label == 'TIME OF LAST OBS':
                    header['last_obs'] = self._parse_header_time(line)

                elif 'SYS / # / OBS TYPES' in label:
                    # RINEX 3.x
                    sys_code = line[0].strip()
                    if sys_code and sys_code in self.SYS_CODES:
                        current_sys = sys_code
                        num = int(line[3:6].strip())
                        types = line[7:60].split()
                        obs_types_lines[current_sys] = types
                    elif current_sys:
                        # continuation line
                        types = line[7:60].split()
                        obs_types_lines.setdefault(current_sys, []).extend(types)

                elif label == '# / TYPES OF OBSERV':
                    # RINEX 2.x
                    parts = line[:60].split()
                    if parts and parts[0].isdigit():
                        num = int(parts[0])
                        types = parts[1:]
                        obs_types_lines['G'] = types
                    else:
                        # continuation
                        types = line[:60].split()
                        obs_types_lines.setdefault('G', []).extend(types)

        # Store obs types
        self.obs_types = obs_types_lines
        header['obs_types'] = obs_types_lines
        header['systems'] = list(obs_types_lines.keys())

        self.header = header
        return header

    def parse_observations(self, max_epochs=None, decimate=1):
        """
        Parse observation data for satellite visibility and overview.

        Returns a dict with:
        - epochs: list of epoch timestamps (ISO strings)
        - satellites: dict of {prn: {system, visibility[], snr_summary}}
        - summary: overall statistics
        """
        if not self.header:
            self.parse_header()

        epochs = []
        sat_data = defaultdict(lambda: {
            'system': '',
            'epochs_present': [],
            'snr_values': [],
            'snr_l1': [],
            'snr_l2': [],
            'has_phase_l1': [],
            'has_phase_l2': [],
            'cycle_slips': [],
        })

        epoch_count = 0
        total_obs = 0

        # Find SNR and phase column indices
        snr_cols = self._find_obs_columns(['S1', 'S1C', 'S1W', 'S1X'], 'snr_l1')
        snr_cols_l2 = self._find_obs_columns(['S2', 'S2C', 'S2W', 'S2X', 'S2L'], 'snr_l2')
        phase_cols_l1 = self._find_obs_columns(['L1', 'L1C', 'L1W', 'L1X'], 'phase_l1')
        phase_cols_l2 = self._find_obs_columns(['L2', 'L2C', 'L2W', 'L2X', 'L2L'], 'phase_l2')

        with open(self.filepath, 'r', errors='replace') as f:
            # Skip header
            for line in f:
                if 'END OF HEADER' in line:
                    break

            if self.version_major >= 3:
                self._parse_v3_observations(
                    f, epochs, sat_data, snr_cols, snr_cols_l2,
                    phase_cols_l1, phase_cols_l2,
                    max_epochs, decimate
                )
            else:
                self._parse_v2_observations(
                    f, epochs, sat_data, snr_cols, snr_cols_l2,
                    phase_cols_l1, phase_cols_l2,
                    max_epochs, decimate
                )

        # Build summary
        sat_summary = {}
        all_systems = set()
        for prn, data in sat_data.items():
            sys_code = prn[0] if prn[0].isalpha() else 'G'
            all_systems.add(sys_code)
            snr_vals = [v for v in data['snr_l1'] if v > 0]
            snr_vals_l2 = [v for v in data['snr_l2'] if v > 0]

            sat_summary[prn] = {
                'system': self.SYS_CODES.get(sys_code, 'Unknown'),
                'system_code': sys_code,
                'num_epochs': len(data['epochs_present']),
                'epoch_indices': data['epochs_present'],
                'snr_l1_mean': round(sum(snr_vals) / len(snr_vals), 1) if snr_vals else 0,
                'snr_l1_min': round(min(snr_vals), 1) if snr_vals else 0,
                'snr_l1_max': round(max(snr_vals), 1) if snr_vals else 0,
                'snr_l2_mean': round(sum(snr_vals_l2) / len(snr_vals_l2), 1) if snr_vals_l2 else 0,
                'has_l2': len(snr_vals_l2) > 0,
                'cycle_slips': data['cycle_slips'],
                'num_cycle_slips': len(data['cycle_slips']),
                'snr_l1_series': data['snr_l1'],
                'snr_l2_series': data['snr_l2'],
            }

        # Determine duration
        duration_sec = 0
        if len(epochs) >= 2:
            try:
                t0 = datetime.fromisoformat(epochs[0])
                t1 = datetime.fromisoformat(epochs[-1])
                duration_sec = (t1 - t0).total_seconds()
            except (ValueError, TypeError):
                pass

        interval = None
        if len(epochs) >= 2:
            try:
                t0 = datetime.fromisoformat(epochs[0])
                t1 = datetime.fromisoformat(epochs[1])
                interval = (t1 - t0).total_seconds()
            except (ValueError, TypeError):
                pass

        summary = {
            'num_epochs': len(epochs),
            'num_satellites': len(sat_summary),
            'systems': sorted(all_systems),
            'system_names': [self.SYS_CODES.get(s, s) for s in sorted(all_systems)],
            'duration_seconds': duration_sec,
            'duration_human': self._format_duration(duration_sec),
            'interval': interval or self.header.get('interval'),
            'first_epoch': epochs[0] if epochs else None,
            'last_epoch': epochs[-1] if epochs else None,
            'sats_per_system': {},
        }

        for sys_code in all_systems:
            sats = [p for p in sat_summary if sat_summary[p]['system_code'] == sys_code]
            summary['sats_per_system'][sys_code] = {
                'name': self.SYS_CODES.get(sys_code, sys_code),
                'count': len(sats),
                'satellites': sorted(sats),
            }

        return {
            'epochs': epochs,
            'satellites': sat_summary,
            'summary': summary,
        }

    def get_snr_data(self, satellites=None, decimate=1):
        """Get detailed SNR time series for specific satellites"""
        if not self.header:
            self.parse_header()

        # Re-parse but only extract SNR
        snr_result = {}
        # This is a simplified version; for efficiency, we could
        # cache the full parse and extract from it
        obs = self.parse_observations(decimate=decimate)

        for prn, data in obs['satellites'].items():
            if satellites and prn not in satellites:
                continue
            snr_result[prn] = {
                'epochs': [obs['epochs'][i] for i in data['epoch_indices']
                           if i < len(obs['epochs'])],
                'snr_l1': data['snr_l1_series'],
                'snr_l2': data['snr_l2_series'],
            }

        return snr_result

    # ─────────────────────────────────────────
    # Internal parsing methods
    # ─────────────────────────────────────────

    def _parse_v3_observations(self, f, epochs, sat_data, snr_l1_cols,
                                snr_l2_cols, phase_l1_cols, phase_l2_cols,
                                max_epochs, decimate):
        """Parse RINEX 3.x observation records"""
        epoch_idx = 0
        kept_idx = 0

        for line in f:
            if not line.startswith('>'):
                continue

            # Epoch header: > YYYY MM DD HH MM SS.SSSSSSS  flag  num_sats
            try:
                year = int(line[2:6])
                month = int(line[7:9])
                day = int(line[10:12])
                hour = int(line[13:15])
                minute = int(line[16:18])
                sec = float(line[19:29])
                flag = int(line[29:32].strip())
                num_sats = int(line[32:35].strip())
            except (ValueError, IndexError):
                continue

            if flag > 1:  # skip special events
                for _ in range(num_sats):
                    next(f, None)
                continue

            epoch_idx += 1
            if decimate > 1 and (epoch_idx - 1) % decimate != 0:
                for _ in range(num_sats):
                    next(f, None)
                continue

            if max_epochs and kept_idx >= max_epochs:
                break

            int_sec = int(sec)
            microsec = int((sec - int_sec) * 1_000_000)
            try:
                epoch_dt = datetime(year, month, day, hour, minute, int_sec, microsec)
            except ValueError:
                for _ in range(num_sats):
                    next(f, None)
                continue

            epoch_str = epoch_dt.isoformat()
            epochs.append(epoch_str)

            # Read satellite observation lines
            for _ in range(num_sats):
                obs_line = next(f, None)
                if obs_line is None:
                    break

                prn = obs_line[0:3].strip()
                if not prn:
                    continue

                sys_code = prn[0] if prn[0].isalpha() else 'G'
                sat_data[prn]['system'] = sys_code
                sat_data[prn]['epochs_present'].append(kept_idx)

                # Parse observation values (each field is 16 chars: 14.3f + LLI + signal strength)
                obs_values = obs_line[3:]
                sys_types = self.obs_types.get(sys_code, self.obs_types.get('G', []))

                snr_l1 = 0.0
                snr_l2 = 0.0

                for col_info in snr_l1_cols.get(sys_code, []):
                    val = self._extract_obs_value(obs_values, col_info['index'])
                    if val > 0:
                        snr_l1 = val
                        break

                for col_info in snr_l2_cols.get(sys_code, []):
                    val = self._extract_obs_value(obs_values, col_info['index'])
                    if val > 0:
                        snr_l2 = val
                        break

                # Check for cycle slip (LLI flag bit 0)
                for col_info in phase_l1_cols.get(sys_code, []):
                    lli = self._extract_lli(obs_values, col_info['index'])
                    if lli & 1:
                        sat_data[prn]['cycle_slips'].append(kept_idx)
                        break

                sat_data[prn]['snr_l1'].append(snr_l1)
                sat_data[prn]['snr_l2'].append(snr_l2)

            kept_idx += 1

    def _parse_v2_observations(self, f, epochs, sat_data, snr_l1_cols,
                                snr_l2_cols, phase_l1_cols, phase_l2_cols,
                                max_epochs, decimate):
        """Parse RINEX 2.x observation records"""
        epoch_idx = 0
        kept_idx = 0
        obs_types_list = self.obs_types.get('G', [])

        for line in f:
            # Epoch header in v2: starts with space and year
            if len(line) < 32:
                continue

            try:
                # V2 format: _YY_MM_DD_HH_MM_SS.SSSSSSS__flag_num
                yy = int(line[1:3])
                month = int(line[4:6])
                day = int(line[7:9])
                hour = int(line[10:12])
                minute = int(line[13:15])
                sec = float(line[15:26])
                flag = int(line[26:29].strip())
                num_sats = int(line[29:32].strip())
            except (ValueError, IndexError):
                continue

            year = yy + (2000 if yy < 80 else 1900)

            if flag > 1:
                # skip non-observation records
                lines_to_skip = num_sats
                for _ in range(lines_to_skip):
                    next(f, None)
                continue

            epoch_idx += 1
            if decimate > 1 and (epoch_idx - 1) % decimate != 0:
                n_lines = num_sats * ((len(obs_types_list) - 1) // 5 + 1)
                for _ in range(n_lines):
                    next(f, None)
                continue

            if max_epochs and kept_idx >= max_epochs:
                break

            int_sec = int(sec)
            microsec = int((sec - int_sec) * 1_000_000)
            try:
                epoch_dt = datetime(year, month, day, hour, minute, int_sec, microsec)
            except ValueError:
                continue

            epoch_str = epoch_dt.isoformat()
            epochs.append(epoch_str)

            # Read satellite PRNs from epoch header (max 12 per line)
            sat_prns = []
            prn_str = line[32:68] if len(line) > 32 else ''
            for i in range(0, min(num_sats * 3, len(prn_str)), 3):
                prn = prn_str[i:i+3].strip()
                if prn:
                    if prn[0].isdigit():
                        prn = 'G' + prn.zfill(2)[-2:]
                    sat_prns.append(prn)

            # Continuation lines for PRNs (if > 12 sats)
            while len(sat_prns) < num_sats:
                cont_line = next(f, '')
                prn_str = cont_line[32:68] if len(cont_line) > 32 else ''
                for i in range(0, min((num_sats - len(sat_prns)) * 3, len(prn_str)), 3):
                    prn = prn_str[i:i+3].strip()
                    if prn:
                        if prn[0].isdigit():
                            prn = 'G' + prn.zfill(2)[-2:]
                        sat_prns.append(prn)

            # Read observation data for each satellite
            obs_per_line = 5
            lines_per_sat = (len(obs_types_list) - 1) // obs_per_line + 1

            for sat_idx, prn in enumerate(sat_prns):
                obs_values_str = ''
                for li in range(lines_per_sat):
                    obs_line = next(f, '')
                    obs_values_str += obs_line.rstrip('\n').ljust(80)

                sys_code = prn[0] if prn[0].isalpha() else 'G'
                sat_data[prn]['system'] = sys_code
                sat_data[prn]['epochs_present'].append(kept_idx)

                # Extract values
                snr_l1 = 0.0
                snr_l2 = 0.0

                for i, obs_type in enumerate(obs_types_list):
                    start = i * 16
                    field = obs_values_str[start:start+16] if start+16 <= len(obs_values_str) else ''

                    if obs_type in ('S1', 'S1C'):
                        try:
                            snr_l1 = float(field[:14].strip()) if field[:14].strip() else 0
                        except ValueError:
                            pass
                    elif obs_type in ('S2', 'S2C', 'S2W', 'S2L'):
                        try:
                            snr_l2 = float(field[:14].strip()) if field[:14].strip() else 0
                        except ValueError:
                            pass
                    elif obs_type in ('L1', 'L1C'):
                        # Check LLI
                        try:
                            lli_char = field[14] if len(field) > 14 else ' '
                            lli = int(lli_char) if lli_char.strip() else 0
                            if lli & 1:
                                sat_data[prn]['cycle_slips'].append(kept_idx)
                        except (ValueError, IndexError):
                            pass

                sat_data[prn]['snr_l1'].append(snr_l1)
                sat_data[prn]['snr_l2'].append(snr_l2)

            kept_idx += 1

    def _find_obs_columns(self, type_names, role):
        """Find column indices for given observation type names per system"""
        result = {}
        for sys_code, types in self.obs_types.items():
            cols = []
            for idx, t in enumerate(types):
                if t in type_names:
                    cols.append({'index': idx, 'type': t})
            if cols:
                result[sys_code] = cols
        return result

    def _extract_obs_value(self, obs_str, col_index):
        """Extract observation value from a V3 observation line"""
        start = col_index * 16
        end = start + 14
        if end > len(obs_str):
            return 0.0
        field = obs_str[start:end].strip()
        try:
            return float(field) if field else 0.0
        except ValueError:
            return 0.0

    def _extract_lli(self, obs_str, col_index):
        """Extract LLI flag from observation"""
        pos = col_index * 16 + 14
        if pos >= len(obs_str):
            return 0
        try:
            c = obs_str[pos]
            return int(c) if c.strip() else 0
        except (ValueError, IndexError):
            return 0

    def _parse_header_time(self, line):
        """Parse time from header line"""
        try:
            parts = line[:60].split()
            if len(parts) >= 6:
                year = int(parts[0])
                month = int(parts[1])
                day = int(parts[2])
                hour = int(parts[3])
                minute = int(parts[4])
                sec = float(parts[5])
                int_sec = int(sec)
                return datetime(year, month, day, hour, minute, int_sec).isoformat()
        except (ValueError, IndexError):
            pass
        return None

    @staticmethod
    def _format_duration(seconds):
        """Format duration in seconds to human-readable string"""
        if seconds <= 0:
            return '0s'
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        parts = []
        if hours:
            parts.append(f'{hours}h')
        if minutes:
            parts.append(f'{minutes}m')
        if secs or not parts:
            parts.append(f'{secs}s')
        return ' '.join(parts)
