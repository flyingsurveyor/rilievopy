"""
PPK Configuration — paths, binary discovery, directories.
Adapted from rtklib-web config.py for the unified suite.
"""

import os
import shutil

# Base directory = project root (where app.py lives)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _find_binary(name, project_path):
    """
    Find an RTKLIB binary. Search order:
      1. Environment variable RTKLIB_{NAME}_BIN
      2. Project tools/ directory (compiled by install.sh)
      3. System PATH
    Returns the absolute path if found, otherwise the project_path as fallback.
    """
    env_key = f'RTKLIB_{name.upper()}_BIN'
    env_val = os.environ.get(env_key, '')
    if env_val and os.path.isfile(env_val) and os.access(env_val, os.X_OK):
        return env_val

    if os.path.isfile(project_path) and os.access(project_path, os.X_OK):
        return project_path

    found = shutil.which(name)
    if found:
        return found

    return project_path


class PPKConfig:
    """PPK-specific configuration."""

    # Directories
    DATA_DIR = os.path.join(BASE_DIR, 'data')
    UPLOAD_DIR = os.path.join(DATA_DIR, 'uploads')
    RINEX_DIR = os.path.join(DATA_DIR, 'rinex')
    RESULTS_DIR = os.path.join(DATA_DIR, 'results')
    POS_DIR = os.path.join(DATA_DIR, 'pos')
    CONF_DIR = os.path.join(DATA_DIR, 'conf')
    ANTEX_DIR = os.path.join(DATA_DIR, 'antex')
    DEFAULT_CONF_DIR = os.path.join(BASE_DIR, 'conf')

    # RTKLIB binaries
    TOOLS_DIR = os.path.join(BASE_DIR, 'tools')
    CONVBIN_BIN = _find_binary('convbin', os.path.join(TOOLS_DIR, 'convbin'))
    RNX2RTKP_BIN = _find_binary('rnx2rtkp', os.path.join(TOOLS_DIR, 'rnx2rtkp'))
    STR2STR_BIN = _find_binary('str2str', os.path.join(TOOLS_DIR, 'str2str'))
    POS2KML_BIN = _find_binary('pos2kml', os.path.join(TOOLS_DIR, 'pos2kml'))

    # Limits
    MAX_CONTENT_LENGTH = 2 * 1024 * 1024 * 1024  # 2GB
    RINEX_MAX_EPOCHS_DEFAULT = 5000
    RINEX_DECIMATE_DEFAULT = 1

    @classmethod
    def ensure_dirs(cls):
        """Create all PPK data directories if they don't exist."""
        for d in [cls.UPLOAD_DIR, cls.RINEX_DIR, cls.RESULTS_DIR,
                  cls.CONF_DIR, cls.ANTEX_DIR, cls.POS_DIR]:
            os.makedirs(d, exist_ok=True)
