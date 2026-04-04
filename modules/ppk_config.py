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


def _get_ppk_conf_dir() -> str:
    """Return the workspace-aware PPK conf directory."""
    try:
        from modules.workspace import ppk_conf_dir
        return ppk_conf_dir()
    except Exception:
        return os.path.join(BASE_DIR, 'data', 'conf')


def _get_ppk_uploads_dir() -> str:
    try:
        from modules.workspace import ppk_uploads_dir
        return ppk_uploads_dir()
    except Exception:
        return os.path.join(BASE_DIR, 'data', 'uploads')


def _get_ppk_rinex_dir() -> str:
    try:
        from modules.workspace import ppk_rinex_dir
        return ppk_rinex_dir()
    except Exception:
        return os.path.join(BASE_DIR, 'data', 'rinex')


def _get_ppk_results_dir() -> str:
    try:
        from modules.workspace import ppk_results_dir
        return ppk_results_dir()
    except Exception:
        return os.path.join(BASE_DIR, 'data', 'results')


def _get_ppk_pos_dir() -> str:
    try:
        from modules.workspace import ppk_pos_dir
        return ppk_pos_dir()
    except Exception:
        return os.path.join(BASE_DIR, 'data', 'pos')


def _get_ppk_antex_dir() -> str:
    try:
        from modules.workspace import ppk_antex_dir
        return ppk_antex_dir()
    except Exception:
        return os.path.join(BASE_DIR, 'data', 'antex')


class _ClassProperty:
    """Descriptor that acts like @property but for class-level access."""
    def __init__(self, func):
        self._func = func

    def __get__(self, obj, objtype=None):
        return self._func(objtype or type(obj))


class PPKConfig:
    """PPK-specific configuration — all user-data paths are workspace-aware."""

    # Legacy fallback dirs (used only if workspace fails)
    DATA_DIR = os.path.join(BASE_DIR, 'data')
    DEFAULT_CONF_DIR = os.path.join(BASE_DIR, 'conf')

    # RTKLIB binaries (always under project tools/)
    TOOLS_DIR = os.path.join(BASE_DIR, 'tools')
    CONVBIN_BIN  = _find_binary('convbin',  os.path.join(BASE_DIR, 'tools', 'convbin'))
    RNX2RTKP_BIN = _find_binary('rnx2rtkp', os.path.join(BASE_DIR, 'tools', 'rnx2rtkp'))
    STR2STR_BIN  = _find_binary('str2str',  os.path.join(BASE_DIR, 'tools', 'str2str'))
    POS2KML_BIN  = _find_binary('pos2kml',  os.path.join(BASE_DIR, 'tools', 'pos2kml'))

    # Limits
    MAX_CONTENT_LENGTH = 2 * 1024 * 1024 * 1024  # 2GB
    RINEX_MAX_EPOCHS_DEFAULT = 5000
    RINEX_DECIMATE_DEFAULT = 1

    # All data dirs are workspace-aware via _ClassProperty
    UPLOAD_DIR  = _ClassProperty(lambda cls: _get_ppk_uploads_dir())
    RINEX_DIR   = _ClassProperty(lambda cls: _get_ppk_rinex_dir())
    RESULTS_DIR = _ClassProperty(lambda cls: _get_ppk_results_dir())
    POS_DIR     = _ClassProperty(lambda cls: _get_ppk_pos_dir())
    ANTEX_DIR   = _ClassProperty(lambda cls: _get_ppk_antex_dir())
    CONF_DIR    = _ClassProperty(lambda cls: _get_ppk_conf_dir())

    @classmethod
    def ensure_dirs(cls):
        """Create all PPK data directories if they don't exist."""
        for d in [cls.UPLOAD_DIR, cls.RINEX_DIR, cls.RESULTS_DIR,
                  cls.CONF_DIR, cls.ANTEX_DIR, cls.POS_DIR]:
            os.makedirs(d, exist_ok=True)
