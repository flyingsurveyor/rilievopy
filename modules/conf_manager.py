"""
RTKLIB Configuration Manager

Defines ALL configuration options for rnx2rtkp processing.
Each option has: key, section, label, type, values/range, default, unit, help.

Verified against: f9p_ppk.conf (demo5 b34h) and rtklib.h option definitions.

Sections:
  pos1-*   Position Setting 1 (mode, frequency, corrections, systems)
  pos2-*   Position Setting 2 (AR, thresholds, constraints)
  out-*    Output settings
  stats-*  Statistics / error model
  ant1-*   Rover antenna
  ant2-*   Base antenna
  misc-*   Miscellaneous
  file-*   File paths
"""

import os
import re
from pathlib import Path

# ═══════════════════════════════════════════════════════════════
# COMPLETE OPTION SCHEMA — ALL 128 options
# ═══════════════════════════════════════════════════════════════
# type: 'enum' | 'number' | 'text' | 'bool' | 'bitmask'
# For enum: 'values' is list of {value, label}
# For number: 'min', 'max', 'step' (optional)
# For bool: stored as on/off in conf

CONF_SCHEMA = [
    # ───────────────────────────────────────
    # pos1-* : Position Setting 1
    # ───────────────────────────────────────
    {
        'key': 'pos1-posmode', 'section': 'pos1', 'label': 'Positioning Mode',
        'type': 'enum', 'default': 'kinematic',
        'values': [
            {'value': 'single',       'label': 'Single'},
            {'value': 'dgps',         'label': 'DGPS'},
            {'value': 'kinematic',    'label': 'Kinematic'},
            {'value': 'static',       'label': 'Static'},
            {'value': 'static-start', 'label': 'Static-Start'},
            {'value': 'movingbase',   'label': 'Moving Base'},
            {'value': 'fixed',        'label': 'Fixed'},
            {'value': 'ppp-kine',     'label': 'PPP Kinematic'},
            {'value': 'ppp-static',   'label': 'PPP Static'},
            {'value': 'ppp-fixed',    'label': 'PPP Fixed'},
        ],
        'help': 'Processing mode (0:single .. 9:ppp-fixed)',
    },
    {
        'key': 'pos1-frequency', 'section': 'pos1', 'label': 'Frequencies',
        'type': 'enum', 'default': 'l1+l2',
        'values': [
            {'value': 'l1',         'label': 'L1'},
            {'value': 'l1+l2',     'label': 'L1+L2'},
            {'value': 'l1+l2+l5', 'label': 'L1+L2+L5'},
            {'value': 'l1+l2+l5+l6', 'label': 'L1+L2+L5+L6'},
        ],
        'help': 'Number of carrier frequencies',
    },
    {
        'key': 'pos1-soltype', 'section': 'pos1', 'label': 'Solution Type',
        'type': 'enum', 'default': 'combined',
        'values': [
            {'value': 'forward',               'label': 'Forward'},
            {'value': 'backward',              'label': 'Backward'},
            {'value': 'combined',              'label': 'Combined'},
            {'value': 'combined-nophasereset', 'label': 'Combined (no phase reset)'},
        ],
        'help': 'Filter direction',
    },
    {
        'key': 'pos1-elmask', 'section': 'pos1', 'label': 'Elevation Mask',
        'type': 'number', 'default': '15', 'unit': 'deg',
        'min': 0, 'max': 90, 'step': 1,
        'help': 'Elevation mask angle in degrees',
    },
    {
        'key': 'pos1-snrmask_r', 'section': 'pos1', 'label': 'SNR Mask (Rover)',
        'type': 'bool', 'default': 'off',
        'help': 'Enable SNR mask for rover',
    },
    {
        'key': 'pos1-snrmask_b', 'section': 'pos1', 'label': 'SNR Mask (Base)',
        'type': 'bool', 'default': 'off',
        'help': 'Enable SNR mask for base',
    },
    {
        'key': 'pos1-snrmask_L1', 'section': 'pos1', 'label': 'SNR Mask L1',
        'type': 'text', 'default': '35,35,35,35,35,35,35,35,35',
        'help': 'SNR mask values for L1 (9 elevation ranges, dB·Hz)',
    },
    {
        'key': 'pos1-snrmask_L2', 'section': 'pos1', 'label': 'SNR Mask L2',
        'type': 'text', 'default': '35,35,35,35,35,35,35,35,35',
        'help': 'SNR mask values for L2 (9 elevation ranges, dB·Hz)',
    },
    {
        'key': 'pos1-snrmask_L5', 'section': 'pos1', 'label': 'SNR Mask L5',
        'type': 'text', 'default': '35,35,35,35,35,35,35,35,35',
        'help': 'SNR mask values for L5 (9 elevation ranges, dB·Hz)',
    },
    {
        'key': 'pos1-dynamics', 'section': 'pos1', 'label': 'Dynamics Model',
        'type': 'bool', 'default': 'on',
        'help': 'Receiver dynamics model',
    },
    {
        'key': 'pos1-tidecorr', 'section': 'pos1', 'label': 'Tide Correction',
        'type': 'enum', 'default': 'off',
        'values': [
            {'value': 'off', 'label': 'Off'},
            {'value': 'on',  'label': 'Solid Earth Tide'},
            {'value': 'otl', 'label': 'Solid + Ocean Tide Loading'},
        ],
        'help': 'Earth tide correction',
    },
    {
        'key': 'pos1-ionoopt', 'section': 'pos1', 'label': 'Ionosphere Correction',
        'type': 'enum', 'default': 'brdc',
        'values': [
            {'value': 'off',       'label': 'Off'},
            {'value': 'brdc',      'label': 'Broadcast'},
            {'value': 'sbas',      'label': 'SBAS'},
            {'value': 'dual-freq', 'label': 'Dual-Frequency'},
            {'value': 'est-stec',  'label': 'Estimate STEC'},
            {'value': 'ionex-tec', 'label': 'IONEX TEC'},
            {'value': 'qzs-brdc',  'label': 'QZSS Broadcast'},
        ],
        'help': 'Ionospheric correction model',
    },
    {
        'key': 'pos1-tropopt', 'section': 'pos1', 'label': 'Troposphere Correction',
        'type': 'enum', 'default': 'saas',
        'values': [
            {'value': 'off',         'label': 'Off'},
            {'value': 'saas',        'label': 'Saastamoinen'},
            {'value': 'sbas',        'label': 'SBAS'},
            {'value': 'est-ztd',     'label': 'Estimate ZTD'},
            {'value': 'est-ztdgrad', 'label': 'Estimate ZTD+Gradient'},
        ],
        'help': 'Tropospheric correction model',
    },
    {
        'key': 'pos1-sateph', 'section': 'pos1', 'label': 'Satellite Ephemeris',
        'type': 'enum', 'default': 'brdc',
        'values': [
            {'value': 'brdc',        'label': 'Broadcast'},
            {'value': 'precise',     'label': 'Precise (SP3)'},
            {'value': 'brdc+sbas',   'label': 'Broadcast + SBAS'},
            {'value': 'brdc+ssrapc', 'label': 'Broadcast + SSR APC'},
            {'value': 'brdc+ssrcom', 'label': 'Broadcast + SSR CoM'},
        ],
        'help': 'Satellite ephemeris source',
    },
    {
        'key': 'pos1-posopt1', 'section': 'pos1', 'label': 'Satellite PCV',
        'type': 'bool', 'default': 'off',
        'help': 'Apply satellite antenna phase center variation (needs ANTEX)',
    },
    {
        'key': 'pos1-posopt2', 'section': 'pos1', 'label': 'Receiver PCV',
        'type': 'bool', 'default': 'off',
        'help': 'Apply receiver antenna phase center variation (needs ANTEX)',
    },
    {
        'key': 'pos1-posopt3', 'section': 'pos1', 'label': 'Phase Windup',
        'type': 'enum', 'default': 'off',
        'values': [
            {'value': 'off',     'label': 'Off'},
            {'value': 'on',      'label': 'On'},
            {'value': 'precise', 'label': 'Precise'},
        ],
        'help': 'Phase windup correction',
    },
    {
        'key': 'pos1-posopt4', 'section': 'pos1', 'label': 'Reject Eclipsing Sats',
        'type': 'bool', 'default': 'off',
        'help': 'Reject eclipsing GPS satellites',
    },
    {
        'key': 'pos1-posopt5', 'section': 'pos1', 'label': 'RAIM FDE',
        'type': 'bool', 'default': 'off',
        'help': 'Receiver Autonomous Integrity Monitoring (fault detection)',
    },
    {
        'key': 'pos1-posopt6', 'section': 'pos1', 'label': 'Multi-Freq Phase Align',
        'type': 'bool', 'default': 'off',
        'help': 'Multi-frequency phase alignment correction',
    },
    {
        'key': 'pos1-exclsats', 'section': 'pos1', 'label': 'Excluded Satellites',
        'type': 'text', 'default': '',
        'help': 'Excluded satellite PRNs (e.g. C01 C02 G10)',
    },
    {
        'key': 'pos1-navsys', 'section': 'pos1', 'label': 'Navigation Systems',
        'type': 'bitmask', 'default': '45',
        'values': [
            {'value': 1,  'label': 'GPS',    'code': 'G'},
            {'value': 2,  'label': 'SBAS',   'code': 'S'},
            {'value': 4,  'label': 'GLONASS', 'code': 'R'},
            {'value': 8,  'label': 'Galileo', 'code': 'E'},
            {'value': 16, 'label': 'QZSS',   'code': 'J'},
            {'value': 32, 'label': 'BeiDou', 'code': 'C'},
            {'value': 64, 'label': 'NavIC',  'code': 'I'},
        ],
        'help': 'Bitmask: 1=GPS, 2=SBAS, 4=GLO, 8=GAL, 16=QZS, 32=BDS, 64=NavIC',
    },

    # ───────────────────────────────────────
    # pos2-* : Position Setting 2 (AR & thresholds)
    # ───────────────────────────────────────
    {
        'key': 'pos2-armode', 'section': 'pos2', 'label': 'AR Mode (GPS)',
        'type': 'enum', 'default': 'fix-and-hold',
        'values': [
            {'value': 'off',            'label': 'Off'},
            {'value': 'continuous',     'label': 'Continuous'},
            {'value': 'instantaneous',  'label': 'Instantaneous'},
            {'value': 'fix-and-hold',   'label': 'Fix and Hold'},
        ],
        'help': 'Integer Ambiguity Resolution mode for GPS',
    },
    {
        'key': 'pos2-gloarmode', 'section': 'pos2', 'label': 'AR Mode (GLONASS)',
        'type': 'enum', 'default': 'fix-and-hold',
        'values': [
            {'value': 'off',           'label': 'Off'},
            {'value': 'on',            'label': 'On'},
            {'value': 'autocal',       'label': 'Auto Calibrate'},
            {'value': 'fix-and-hold',  'label': 'Fix and Hold'},
        ],
        'help': 'GLONASS AR mode',
    },
    {
        'key': 'pos2-bdsarmode', 'section': 'pos2', 'label': 'AR Mode (BeiDou)',
        'type': 'bool', 'default': 'on',
        'help': 'BeiDou Ambiguity Resolution',
    },
    {
        'key': 'pos2-arfilter', 'section': 'pos2', 'label': 'AR Filter',
        'type': 'bool', 'default': 'on',
        'help': 'Partial AR filter',
    },
    {
        'key': 'pos2-arthres', 'section': 'pos2', 'label': 'AR Threshold (Ratio)',
        'type': 'number', 'default': '3', 'min': 0, 'max': 999, 'step': 0.1,
        'help': 'Min ratio test threshold for AR validation',
    },
    {
        'key': 'pos2-arthresmin', 'section': 'pos2', 'label': 'AR Threshold Min',
        'type': 'number', 'default': '3', 'min': 0, 'max': 999, 'step': 0.1,
        'help': 'Min satellites threshold for AR',
    },
    {
        'key': 'pos2-arthresmax', 'section': 'pos2', 'label': 'AR Threshold Max',
        'type': 'number', 'default': '3', 'min': 0, 'max': 999, 'step': 0.1,
        'help': 'Max satellites threshold for AR',
    },
    {
        'key': 'pos2-arthres1', 'section': 'pos2', 'label': 'AR Threshold 1',
        'type': 'number', 'default': '0.1', 'min': 0, 'max': 10, 'step': 0.001,
        'help': 'AR validation threshold 1',
    },
    {
        'key': 'pos2-arthres2', 'section': 'pos2', 'label': 'AR Threshold 2',
        'type': 'number', 'default': '0', 'min': 0, 'max': 10, 'step': 0.001,
        'help': 'AR validation threshold 2',
    },
    {
        'key': 'pos2-arthres3', 'section': 'pos2', 'label': 'AR Threshold 3',
        'type': 'number', 'default': '1e-09', 'step': 'any',
        'help': 'AR validation threshold 3',
    },
    {
        'key': 'pos2-arthres4', 'section': 'pos2', 'label': 'AR Threshold 4',
        'type': 'number', 'default': '1e-05', 'step': 'any',
        'help': 'AR validation threshold 4',
    },
    {
        'key': 'pos2-varholdamb', 'section': 'pos2', 'label': 'Hold Ambiguity Variance',
        'type': 'number', 'default': '0.1', 'unit': 'cyc²',
        'min': 0, 'max': 100, 'step': 0.001,
        'help': 'Variance for hold ambiguities',
    },
    {
        'key': 'pos2-gainholdamb', 'section': 'pos2', 'label': 'Hold Ambiguity Gain',
        'type': 'number', 'default': '0.01', 'min': 0, 'max': 1, 'step': 0.001,
        'help': 'Gain for hold ambiguity feedback',
    },
    {
        'key': 'pos2-arlockcnt', 'section': 'pos2', 'label': 'AR Lock Count',
        'type': 'number', 'default': '5', 'min': 0, 'max': 999, 'step': 1,
        'help': 'Min lock count to enable AR',
    },
    {
        'key': 'pos2-minfixsats', 'section': 'pos2', 'label': 'Min Fix Satellites',
        'type': 'number', 'default': '4', 'min': 3, 'max': 30, 'step': 1,
        'help': 'Minimum satellites for fix solution',
    },
    {
        'key': 'pos2-minholdsats', 'section': 'pos2', 'label': 'Min Hold Satellites',
        'type': 'number', 'default': '5', 'min': 3, 'max': 30, 'step': 1,
        'help': 'Minimum satellites for hold solution',
    },
    {
        'key': 'pos2-mindropsats', 'section': 'pos2', 'label': 'Min Drop Satellites',
        'type': 'number', 'default': '10', 'min': 0, 'max': 30, 'step': 1,
        'help': 'Min satellites to drop worst before AR',
    },
    {
        'key': 'pos2-arelmask', 'section': 'pos2', 'label': 'AR Elevation Mask',
        'type': 'number', 'default': '25', 'unit': 'deg',
        'min': 0, 'max': 90, 'step': 1,
        'help': 'Elevation mask for AR (degrees)',
    },
    {
        'key': 'pos2-arminfix', 'section': 'pos2', 'label': 'AR Min Fix',
        'type': 'number', 'default': '20', 'min': 0, 'max': 999, 'step': 1,
        'help': 'Min fix count to hold ambiguity',
    },
    {
        'key': 'pos2-armaxiter', 'section': 'pos2', 'label': 'AR Max Iterations',
        'type': 'number', 'default': '1', 'min': 1, 'max': 20, 'step': 1,
        'help': 'Max iteration count for AR',
    },
    {
        'key': 'pos2-elmaskhold', 'section': 'pos2', 'label': 'Hold Elevation Mask',
        'type': 'number', 'default': '15', 'unit': 'deg',
        'min': 0, 'max': 90, 'step': 1,
        'help': 'Elevation mask for hold (degrees)',
    },
    {
        'key': 'pos2-aroutcnt', 'section': 'pos2', 'label': 'AR Outage Count',
        'type': 'number', 'default': '20', 'min': 0, 'max': 999, 'step': 1,
        'help': 'Max AR outage count to reset',
    },
    {
        'key': 'pos2-maxage', 'section': 'pos2', 'label': 'Max Age of Diff',
        'type': 'number', 'default': '30', 'unit': 's',
        'min': 0, 'max': 300, 'step': 1,
        'help': 'Max age of differential correction (seconds)',
    },
    {
        'key': 'pos2-syncsol', 'section': 'pos2', 'label': 'Sync Solution',
        'type': 'bool', 'default': 'off',
        'help': 'Synchronize solution output',
    },
    {
        'key': 'pos2-slipthres', 'section': 'pos2', 'label': 'Slip Threshold',
        'type': 'number', 'default': '0.05', 'unit': 'm',
        'min': 0, 'max': 10, 'step': 0.001,
        'help': 'Cycle slip threshold (m)',
    },
    {
        'key': 'pos2-dopthres', 'section': 'pos2', 'label': 'Doppler Threshold',
        'type': 'number', 'default': '0', 'unit': 'm',
        'min': 0, 'max': 100, 'step': 0.1,
        'help': 'Doppler validation threshold (0=off)',
    },
    {
        'key': 'pos2-rejionno', 'section': 'pos2', 'label': 'Reject Iono Threshold',
        'type': 'number', 'default': '2', 'unit': 'm',
        'min': 0, 'max': 100, 'step': 0.1,
        'help': 'Reject threshold for ionosphere residual',
    },
    {
        'key': 'pos2-rejcode', 'section': 'pos2', 'label': 'Reject Code Threshold',
        'type': 'number', 'default': '10', 'unit': 'm',
        'min': 0, 'max': 1000, 'step': 1,
        'help': 'Reject threshold for code residual',
    },
    {
        'key': 'pos2-niter', 'section': 'pos2', 'label': 'Filter Iterations',
        'type': 'number', 'default': '1', 'min': 1, 'max': 20, 'step': 1,
        'help': 'Number of filter iterations',
    },
    {
        'key': 'pos2-baselen', 'section': 'pos2', 'label': 'Baseline Length',
        'type': 'number', 'default': '0', 'unit': 'm',
        'min': 0, 'max': 100000, 'step': 0.1,
        'help': 'Baseline length constraint (0=off)',
    },
    {
        'key': 'pos2-basesig', 'section': 'pos2', 'label': 'Baseline Sigma',
        'type': 'number', 'default': '0', 'unit': 'm',
        'min': 0, 'max': 1000, 'step': 0.1,
        'help': 'Baseline length sigma (0=off)',
    },

    # ───────────────────────────────────────
    # out-* : Output settings
    # ───────────────────────────────────────
    {
        'key': 'out-solformat', 'section': 'out', 'label': 'Solution Format',
        'type': 'enum', 'default': 'llh',
        'values': [
            {'value': 'llh',  'label': 'Lat/Lon/Height'},
            {'value': 'xyz',  'label': 'ECEF XYZ'},
            {'value': 'enu',  'label': 'E/N/U Baseline'},
            {'value': 'nmea', 'label': 'NMEA'},
        ],
        'help': 'Output solution coordinate format',
    },
    {
        'key': 'out-outhead', 'section': 'out', 'label': 'Output Header',
        'type': 'bool', 'default': 'on',
        'help': 'Include header in output file',
    },
    {
        'key': 'out-outopt', 'section': 'out', 'label': 'Output Options',
        'type': 'bool', 'default': 'on',
        'help': 'Output processing options in header',
    },
    {
        'key': 'out-outvel', 'section': 'out', 'label': 'Output Velocity',
        'type': 'bool', 'default': 'off',
        'help': 'Output receiver velocity',
    },
    {
        'key': 'out-timesys', 'section': 'out', 'label': 'Time System',
        'type': 'enum', 'default': 'gpst',
        'values': [
            {'value': 'gpst', 'label': 'GPST'},
            {'value': 'utc',  'label': 'UTC'},
            {'value': 'jst',  'label': 'JST'},
        ],
        'help': 'Output time system',
    },
    {
        'key': 'out-timeform', 'section': 'out', 'label': 'Time Format',
        'type': 'enum', 'default': 'hms',
        'values': [
            {'value': 'tow', 'label': 'GPS Week + TOW'},
            {'value': 'hms', 'label': 'yyyy/mm/dd hh:mm:ss'},
        ],
        'help': 'Output time format',
    },
    {
        'key': 'out-timendec', 'section': 'out', 'label': 'Time Decimals',
        'type': 'number', 'default': '3', 'min': 0, 'max': 12, 'step': 1,
        'help': 'Number of decimals in time output',
    },
    {
        'key': 'out-degform', 'section': 'out', 'label': 'Degree Format',
        'type': 'enum', 'default': 'deg',
        'values': [
            {'value': 'deg', 'label': 'Decimal Degrees'},
            {'value': 'dms', 'label': 'DMS (d°m\'s")'},
        ],
        'help': 'Latitude/longitude output format',
    },
    {
        'key': 'out-fieldsep', 'section': 'out', 'label': 'Field Separator',
        'type': 'text', 'default': '',
        'help': 'Output field separator (empty=space)',
    },
    {
        'key': 'out-outsingle', 'section': 'out', 'label': 'Output Single if No Fix',
        'type': 'bool', 'default': 'off',
        'help': 'Output single solution when no fix available',
    },
    {
        'key': 'out-maxsolstd', 'section': 'out', 'label': 'Max Solution StdDev',
        'type': 'number', 'default': '0', 'unit': 'm',
        'min': 0, 'max': 100, 'step': 0.01,
        'help': 'Max solution standard deviation (0=off)',
    },
    {
        'key': 'out-height', 'section': 'out', 'label': 'Height Type',
        'type': 'enum', 'default': 'ellipsoidal',
        'values': [
            {'value': 'ellipsoidal', 'label': 'Ellipsoidal'},
            {'value': 'geodetic',    'label': 'Geodetic (geoid)'},
        ],
        'help': 'Output height reference',
    },
    {
        'key': 'out-geoid', 'section': 'out', 'label': 'Geoid Model',
        'type': 'enum', 'default': 'internal',
        'values': [
            {'value': 'internal',  'label': 'Internal'},
            {'value': 'egm96',     'label': 'EGM96'},
            {'value': 'egm08_2.5', 'label': 'EGM2008 2.5\''},
            {'value': 'egm08_1',   'label': 'EGM2008 1\''},
            {'value': 'gsi2000',   'label': 'GSI2000'},
        ],
        'help': 'Geoid model for geodetic height',
    },
    {
        'key': 'out-solstatic', 'section': 'out', 'label': 'Static Solution',
        'type': 'enum', 'default': 'all',
        'values': [
            {'value': 'all',    'label': 'Output All Epochs'},
            {'value': 'single', 'label': 'Output Single Result'},
        ],
        'help': 'Output strategy for static mode',
    },
    {
        'key': 'out-nmeaintv1', 'section': 'out', 'label': 'NMEA Interval (GGA)',
        'type': 'number', 'default': '0', 'unit': 's',
        'min': 0, 'max': 3600, 'step': 1,
        'help': 'NMEA GGA output interval (0=all)',
    },
    {
        'key': 'out-nmeaintv2', 'section': 'out', 'label': 'NMEA Interval (GSA/GSV)',
        'type': 'number', 'default': '0', 'unit': 's',
        'min': 0, 'max': 3600, 'step': 1,
        'help': 'NMEA GSA/GSV output interval (0=all)',
    },
    {
        'key': 'out-outstat', 'section': 'out', 'label': 'Solution Status',
        'type': 'enum', 'default': 'residual',
        'values': [
            {'value': 'off',      'label': 'Off'},
            {'value': 'state',    'label': 'State'},
            {'value': 'residual', 'label': 'Residual'},
        ],
        'help': 'Output solution status file',
    },

    # ───────────────────────────────────────
    # stats-* : Error model / statistics
    # ───────────────────────────────────────
    {
        'key': 'stats-eratio1', 'section': 'stats', 'label': 'Code/Phase Ratio L1',
        'type': 'number', 'default': '300', 'min': 0, 'max': 9999, 'step': 1,
        'help': 'Code/carrier-phase error ratio for L1',
    },
    {
        'key': 'stats-eratio2', 'section': 'stats', 'label': 'Code/Phase Ratio L2',
        'type': 'number', 'default': '300', 'min': 0, 'max': 9999, 'step': 1,
        'help': 'Code/carrier-phase error ratio for L2',
    },
    {
        'key': 'stats-eratio5', 'section': 'stats', 'label': 'Code/Phase Ratio L5',
        'type': 'number', 'default': '300', 'min': 0, 'max': 9999, 'step': 1,
        'help': 'Code/carrier-phase error ratio for L5',
    },
    {
        'key': 'stats-errphase', 'section': 'stats', 'label': 'Phase Error (a)',
        'type': 'number', 'default': '0.005', 'unit': 'm',
        'min': 0, 'max': 1, 'step': 0.001,
        'help': 'Carrier phase error factor a (m)',
    },
    {
        'key': 'stats-errphaseel', 'section': 'stats', 'label': 'Phase Error (b/el)',
        'type': 'number', 'default': '0.005', 'unit': 'm',
        'min': 0, 'max': 1, 'step': 0.001,
        'help': 'Carrier phase error factor b/sin(el) (m)',
    },
    {
        'key': 'stats-errphasebl', 'section': 'stats', 'label': 'Phase Error (baseline)',
        'type': 'number', 'default': '0', 'unit': 'm/10km',
        'min': 0, 'max': 1, 'step': 0.001,
        'help': 'Carrier phase error baseline factor (m/10km)',
    },
    {
        'key': 'stats-errdoppler', 'section': 'stats', 'label': 'Doppler Error',
        'type': 'number', 'default': '1', 'unit': 'Hz',
        'min': 0, 'max': 100, 'step': 0.1,
        'help': 'Doppler frequency error (Hz)',
    },
    {
        'key': 'stats-snrmax', 'section': 'stats', 'label': 'SNR Max',
        'type': 'number', 'default': '52', 'unit': 'dB·Hz',
        'min': 0, 'max': 60, 'step': 1,
        'help': 'Max SNR for error model (dB·Hz)',
    },
    {
        'key': 'stats-errsnr', 'section': 'stats', 'label': 'SNR Error',
        'type': 'number', 'default': '0', 'unit': 'm', 'step': 'any',
        'help': 'SNR-based error factor (m)',
    },
    {
        'key': 'stats-errrcv', 'section': 'stats', 'label': 'Receiver Error',
        'type': 'number', 'default': '0', 'step': 'any',
        'help': 'Receiver-specific error factor',
    },
    {
        'key': 'stats-stdbias', 'section': 'stats', 'label': 'Init Bias StdDev',
        'type': 'number', 'default': '30', 'unit': 'm',
        'min': 0, 'max': 999, 'step': 0.1,
        'help': 'Initial carrier-phase bias std dev (m)',
    },
    {
        'key': 'stats-stdiono', 'section': 'stats', 'label': 'Init Iono StdDev',
        'type': 'number', 'default': '0.03', 'unit': 'm',
        'min': 0, 'max': 100, 'step': 0.001,
        'help': 'Initial ionosphere std dev (m)',
    },
    {
        'key': 'stats-stdtrop', 'section': 'stats', 'label': 'Init Tropo StdDev',
        'type': 'number', 'default': '0.3', 'unit': 'm',
        'min': 0, 'max': 100, 'step': 0.01,
        'help': 'Initial troposphere std dev (m)',
    },
    {
        'key': 'stats-prnaccelh', 'section': 'stats', 'label': 'Process Noise Accel H',
        'type': 'number', 'default': '3', 'unit': 'm/s²',
        'min': 0, 'max': 100, 'step': 0.1,
        'help': 'Process noise horizontal acceleration (m/s²)',
    },
    {
        'key': 'stats-prnaccelv', 'section': 'stats', 'label': 'Process Noise Accel V',
        'type': 'number', 'default': '1', 'unit': 'm/s²',
        'min': 0, 'max': 100, 'step': 0.1,
        'help': 'Process noise vertical acceleration (m/s²)',
    },
    {
        'key': 'stats-prnbias', 'section': 'stats', 'label': 'Process Noise Bias',
        'type': 'number', 'default': '0.0001', 'unit': 'm', 'step': 'any',
        'help': 'Process noise carrier-phase bias (m)',
    },
    {
        'key': 'stats-prniono', 'section': 'stats', 'label': 'Process Noise Iono',
        'type': 'number', 'default': '0.001', 'unit': 'm', 'step': 'any',
        'help': 'Process noise ionosphere (m)',
    },
    {
        'key': 'stats-prntrop', 'section': 'stats', 'label': 'Process Noise Tropo',
        'type': 'number', 'default': '0.0001', 'unit': 'm', 'step': 'any',
        'help': 'Process noise troposphere (m)',
    },
    {
        'key': 'stats-prnpos', 'section': 'stats', 'label': 'Process Noise Position',
        'type': 'number', 'default': '0', 'unit': 'm', 'step': 'any',
        'help': 'Process noise receiver position (m, 0=fixed)',
    },
    {
        'key': 'stats-clkstab', 'section': 'stats', 'label': 'Clock Stability',
        'type': 'number', 'default': '5e-12', 'unit': 's/s', 'step': 'any',
        'help': 'Receiver clock stability (s/s, Allan deviation)',
    },

    # ───────────────────────────────────────
    # ant1-* : Rover antenna
    # ───────────────────────────────────────
    {
        'key': 'ant1-postype', 'section': 'ant1', 'label': 'Rover Position Type',
        'type': 'enum', 'default': 'llh',
        'values': [
            {'value': 'llh',       'label': 'Lat/Lon/Height'},
            {'value': 'xyz',       'label': 'ECEF XYZ'},
            {'value': 'single',    'label': 'Single Solution'},
            {'value': 'posfile',   'label': 'Position File'},
            {'value': 'rinexhead', 'label': 'RINEX Header'},
            {'value': 'rtcm',      'label': 'RTCM'},
            {'value': 'raw',       'label': 'Raw'},
        ],
        'help': 'Rover antenna position type (for fixed/ppp-fixed modes)',
    },
    {
        'key': 'ant1-pos1', 'section': 'ant1', 'label': 'Rover Pos 1 (Lat/X)',
        'type': 'number', 'default': '90', 'step': 'any', 'unit': 'deg|m',
        'help': 'Rover position: latitude (deg) or X (m)',
    },
    {
        'key': 'ant1-pos2', 'section': 'ant1', 'label': 'Rover Pos 2 (Lon/Y)',
        'type': 'number', 'default': '0', 'step': 'any', 'unit': 'deg|m',
        'help': 'Rover position: longitude (deg) or Y (m)',
    },
    {
        'key': 'ant1-pos3', 'section': 'ant1', 'label': 'Rover Pos 3 (Hgt/Z)',
        'type': 'number', 'default': '-6335367.6285', 'step': 'any', 'unit': 'm',
        'help': 'Rover position: height/altitude (m) or Z (m)',
    },
    {
        'key': 'ant1-anttype', 'section': 'ant1', 'label': 'Rover Antenna Type',
        'type': 'text', 'default': '',
        'help': 'Rover antenna type (as in ANTEX file, empty=auto from RINEX)',
    },
    {
        'key': 'ant1-antdele', 'section': 'ant1', 'label': 'Rover Ant Delta E',
        'type': 'number', 'default': '0', 'step': 0.001, 'unit': 'm',
        'help': 'Rover antenna delta East (m)',
    },
    {
        'key': 'ant1-antdeln', 'section': 'ant1', 'label': 'Rover Ant Delta N',
        'type': 'number', 'default': '0', 'step': 0.001, 'unit': 'm',
        'help': 'Rover antenna delta North (m)',
    },
    {
        'key': 'ant1-antdelu', 'section': 'ant1', 'label': 'Rover Ant Delta U',
        'type': 'number', 'default': '0', 'step': 0.001, 'unit': 'm',
        'help': 'Rover antenna delta Up (m) — usually the ARP-to-APC height',
    },

    # ───────────────────────────────────────
    # ant2-* : Base antenna
    # ───────────────────────────────────────
    {
        'key': 'ant2-postype', 'section': 'ant2', 'label': 'Base Position Type',
        'type': 'enum', 'default': 'rinexhead',
        'values': [
            {'value': 'llh',       'label': 'Lat/Lon/Height'},
            {'value': 'xyz',       'label': 'ECEF XYZ'},
            {'value': 'single',    'label': 'Single Solution'},
            {'value': 'posfile',   'label': 'Position File'},
            {'value': 'rinexhead', 'label': 'RINEX Header'},
            {'value': 'rtcm',      'label': 'RTCM'},
            {'value': 'raw',       'label': 'Raw'},
        ],
        'help': 'Base station position type',
    },
    {
        'key': 'ant2-pos1', 'section': 'ant2', 'label': 'Base Pos 1 (Lat/X)',
        'type': 'number', 'default': '0', 'step': 'any', 'unit': 'deg|m',
        'help': 'Base position: latitude (deg) or X (m)',
    },
    {
        'key': 'ant2-pos2', 'section': 'ant2', 'label': 'Base Pos 2 (Lon/Y)',
        'type': 'number', 'default': '0', 'step': 'any', 'unit': 'deg|m',
        'help': 'Base position: longitude (deg) or Y (m)',
    },
    {
        'key': 'ant2-pos3', 'section': 'ant2', 'label': 'Base Pos 3 (Hgt/Z)',
        'type': 'number', 'default': '0', 'step': 'any', 'unit': 'm',
        'help': 'Base position: height/altitude (m) or Z (m)',
    },
    {
        'key': 'ant2-anttype', 'section': 'ant2', 'label': 'Base Antenna Type',
        'type': 'text', 'default': '',
        'help': 'Base antenna type (as in ANTEX file, empty=auto from RINEX)',
    },
    {
        'key': 'ant2-antdele', 'section': 'ant2', 'label': 'Base Ant Delta E',
        'type': 'number', 'default': '0', 'step': 0.001, 'unit': 'm',
        'help': 'Base antenna delta East (m)',
    },
    {
        'key': 'ant2-antdeln', 'section': 'ant2', 'label': 'Base Ant Delta N',
        'type': 'number', 'default': '0', 'step': 0.001, 'unit': 'm',
        'help': 'Base antenna delta North (m)',
    },
    {
        'key': 'ant2-antdelu', 'section': 'ant2', 'label': 'Base Ant Delta U',
        'type': 'number', 'default': '0', 'step': 0.001, 'unit': 'm',
        'help': 'Base antenna delta Up (m) — ARP height',
    },
    {
        'key': 'ant2-maxaveep', 'section': 'ant2', 'label': 'Max Avg Epochs',
        'type': 'number', 'default': '1', 'min': 1, 'max': 999999, 'step': 1,
        'help': 'Max averaging epochs for base position (single mode)',
    },
    {
        'key': 'ant2-initrst', 'section': 'ant2', 'label': 'Init Restart',
        'type': 'bool', 'default': 'on',
        'help': 'Initialize on restart for base position',
    },

    # ───────────────────────────────────────
    # misc-* : Miscellaneous
    # ───────────────────────────────────────
    {
        'key': 'misc-timeinterp', 'section': 'misc', 'label': 'Time Interpolation',
        'type': 'bool', 'default': 'on',
        'help': 'Interpolate observations for time sync',
    },
    {
        'key': 'misc-sbasatsel', 'section': 'misc', 'label': 'SBAS Sat Selection',
        'type': 'number', 'default': '0', 'min': 0, 'max': 255, 'step': 1,
        'help': 'SBAS satellite selection (0=all)',
    },
    {
        'key': 'misc-rnxopt1', 'section': 'misc', 'label': 'RINEX Option (Rover)',
        'type': 'text', 'default': '',
        'help': 'RINEX observation options for rover',
    },
    {
        'key': 'misc-rnxopt2', 'section': 'misc', 'label': 'RINEX Option (Base)',
        'type': 'text', 'default': '',
        'help': 'RINEX observation options for base',
    },
    {
        'key': 'misc-pppopt', 'section': 'misc', 'label': 'PPP Option',
        'type': 'text', 'default': '',
        'help': 'PPP processing option string',
    },

    # ───────────────────────────────────────
    # file-* : File paths
    # ───────────────────────────────────────
    {
        'key': 'file-satantfile', 'section': 'file', 'label': 'Satellite ANTEX',
        'type': 'text', 'default': '',
        'help': 'Satellite antenna PCV file (.atx)',
    },
    {
        'key': 'file-rcvantfile', 'section': 'file', 'label': 'Receiver ANTEX',
        'type': 'text', 'default': '',
        'help': 'Receiver antenna PCV file (.atx)',
    },
    {
        'key': 'file-staposfile', 'section': 'file', 'label': 'Station Position File',
        'type': 'text', 'default': '',
        'help': 'Station position file',
    },
    {
        'key': 'file-geoidfile', 'section': 'file', 'label': 'Geoid Data File',
        'type': 'text', 'default': '',
        'help': 'Geoid data file for geodetic height',
    },
    {
        'key': 'file-ionofile', 'section': 'file', 'label': 'Ionosphere File',
        'type': 'text', 'default': '',
        'help': 'IONEX ionosphere data file',
    },
    {
        'key': 'file-dcbfile', 'section': 'file', 'label': 'DCB File',
        'type': 'text', 'default': '',
        'help': 'Differential Code Bias file',
    },
    {
        'key': 'file-eopfile', 'section': 'file', 'label': 'EOP File',
        'type': 'text', 'default': '',
        'help': 'Earth Orientation Parameter file',
    },
    {
        'key': 'file-blqfile', 'section': 'file', 'label': 'BLQ File',
        'type': 'text', 'default': '',
        'help': 'Ocean tide loading BLQ file',
    },
    {
        'key': 'file-tempdir', 'section': 'file', 'label': 'Temp Directory',
        'type': 'text', 'default': '',
        'help': 'Temporary file directory',
    },
    {
        'key': 'file-geexefile', 'section': 'file', 'label': 'Google Earth Exe',
        'type': 'text', 'default': '',
        'help': 'Google Earth executable path',
    },
    {
        'key': 'file-solstatfile', 'section': 'file', 'label': 'Solution Status File',
        'type': 'text', 'default': '',
        'help': 'Solution status output file',
    },
    {
        'key': 'file-tracefile', 'section': 'file', 'label': 'Trace File',
        'type': 'text', 'default': '',
        'help': 'Debug trace output file',
    },
]

# Section display metadata
SECTIONS = {
    'pos1':  {'label': 'Position 1',  'icon': '📡', 'desc': 'Mode, frequency, corrections, systems'},
    'pos2':  {'label': 'Position 2',  'icon': '🎯', 'desc': 'Ambiguity resolution, thresholds'},
    'out':   {'label': 'Output',      'icon': '📄', 'desc': 'Solution format, time, coordinate options'},
    'stats': {'label': 'Statistics',  'icon': '📊', 'desc': 'Error model, process noise'},
    'ant1':  {'label': 'Rover Ant.',  'icon': '📶', 'desc': 'Rover antenna position and PCV'},
    'ant2':  {'label': 'Base Ant.',   'icon': '🗼', 'desc': 'Base antenna position and PCV'},
    'misc':  {'label': 'Misc',        'icon': '⚙',  'desc': 'Miscellaneous options'},
    'file':  {'label': 'Files',       'icon': '📁', 'desc': 'ANTEX, geoid, iono, correction files'},
}

# Build lookup dict
_SCHEMA_BY_KEY = {opt['key']: opt for opt in CONF_SCHEMA}

# Convenience alias for navsys bitmask (used by server.py and templates)
NAVSYS_BITS = [v for opt in CONF_SCHEMA if opt['key'] == 'pos1-navsys' for v in opt['values']]


# ═══════════════════════════════════════════════════════════════
# PARSER / WRITER
# ═══════════════════════════════════════════════════════════════

def parse_conf(filepath):
    """
    Parse an RTKLIB .conf file into a dict of key=value pairs.
    Lines format: key =value  # comment
    """
    config = {}
    if not os.path.isfile(filepath):
        return config

    with open(filepath, 'r') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            # Remove inline comment
            if '#' in line:
                line = line[:line.index('#')]
            # Split on first '='
            if '=' not in line:
                continue
            key, _, value = line.partition('=')
            config[key.strip()] = value.strip()

    return config


def write_conf(config, filepath, header_comment=''):
    """
    Write a dict of key=value pairs to RTKLIB .conf format.
    Preserves order from CONF_SCHEMA, adds comments with help text.
    
    Args:
        config: dict of key=value pairs
        filepath: output file path
        header_comment: optional first-line comment
    """
    with open(filepath, 'w') as f:
        # Use _header from config if present, else header_comment, else default
        header = config.get('_header', '') or header_comment or '# rtkpost options'
        if not header.startswith('#'):
            header = f'# {header}'
        f.write(f'{header}\n\n')

        current_section = ''
        for opt in CONF_SCHEMA:
            key = opt['key']
            section = opt['section']

            # Section header comment
            if section != current_section:
                current_section = section
                sec_info = SECTIONS.get(section, {})
                f.write(f'\n# --- {sec_info.get("label", section)} ---\n')

            value = config.get(key, opt.get('default', ''))

            # Format: key  =value  # (help)
            padded_key = f'{key:<22s}'

            # Build comment from enum values or unit
            comment = ''
            if opt['type'] == 'enum' and 'values' in opt:
                vals = ','.join(
                    f"{i}:{v['value']}"
                    for i, v in enumerate(opt['values'])
                )
                comment = f'({vals})'
            elif opt.get('unit'):
                comment = f'({opt["unit"]})'

            if comment:
                f.write(f'{padded_key}={value:<16s} # {comment}\n')
            else:
                f.write(f'{padded_key}={value}\n')


def get_defaults():
    """Return dict of all default values"""
    return {opt['key']: opt.get('default', '') for opt in CONF_SCHEMA}


def merge_with_defaults(config):
    """Merge a partial config with defaults (config values take precedence)"""
    defaults = get_defaults()
    defaults.update(config)
    return defaults


def merge_conf(base, overrides):
    """Merge override values into base config. Alias for template compatibility."""
    result = dict(base)
    for k, v in overrides.items():
        if k.startswith('_'):
            continue  # skip meta keys
        result[k] = v
    return result


def bitmask_to_navsys(mask):
    """Convert bitmask integer to list of system codes (e.g. 45 → ['G','R','E'])"""
    mask = int(mask)
    codes = []
    for bit in NAVSYS_BITS:
        if mask & bit['value']:
            codes.append(bit['code'])
    return codes


def navsys_to_bitmask(codes):
    """Convert list of system codes to bitmask integer (e.g. ['G','R','E'] → 13)"""
    mask = 0
    code_map = {b['code']: b['value'] for b in NAVSYS_BITS}
    for c in codes:
        mask |= code_map.get(c, 0)
    return mask


def get_schema_json():
    """Return schema as JSON-serializable list for the conf editor frontend"""
    return CONF_SCHEMA


def get_sections_json():
    """Return sections metadata for frontend"""
    return SECTIONS


def validate_conf(config):
    """
    Validate config values against schema.
    Returns list of (key, message) tuples for warnings.
    """
    warnings = []
    for opt in CONF_SCHEMA:
        key = opt['key']
        value = config.get(key, '')

        if not value and opt['type'] != 'text':
            continue

        if opt['type'] == 'enum' and 'values' in opt:
            valid = [v['value'] for v in opt['values']]
            if value and value not in valid:
                warnings.append((key, f'"{value}" not in valid values'))

        elif opt['type'] == 'number' and value:
            try:
                num = float(value)
                if 'min' in opt and num < opt['min']:
                    warnings.append((key, f'{num} below minimum {opt["min"]}'))
                if 'max' in opt and num > opt['max']:
                    warnings.append((key, f'{num} above maximum {opt["max"]}'))
            except ValueError:
                warnings.append((key, f'"{value}" is not a valid number'))

    return warnings


def list_conf_presets(conf_dir):
    """List .conf files in a directory"""
    presets = []
    if not os.path.isdir(conf_dir):
        return presets

    for f in sorted(os.listdir(conf_dir)):
        if f.endswith('.conf'):
            fpath = os.path.join(conf_dir, f)
            size = os.path.getsize(fpath)
            desc = ''
            try:
                with open(fpath, 'r') as fh:
                    first = fh.readline().strip()
                    if first.startswith('#'):
                        desc = first.lstrip('# ')
            except Exception:
                pass

            presets.append({
                'name': f,
                'path': fpath,
                'size': size,
                'desc': desc,
            })

    return presets


def list_antex_files(antex_dir):
    """List ANTEX (.atx) files in a directory"""
    files = []
    if not os.path.isdir(antex_dir):
        return files

    for f in sorted(os.listdir(antex_dir)):
        if f.lower().endswith('.atx'):
            fpath = os.path.join(antex_dir, f)
            files.append({
                'name': f,
                'path': fpath,
                'size': os.path.getsize(fpath),
            })

    return files
