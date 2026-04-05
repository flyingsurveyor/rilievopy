"""
Persistent settings manager.
Loads/saves configuration from rilievo_settings.json (project root).
"""

import json
import os
import threading
from typing import Dict, Any, Optional

from .utils import now_iso

# RTKino fixed ports (hard-coded, not configurable by user)
RTKINO_TCP_PORT: int = 7856   # TCP Streamer (NMEA/UBX data)
RTKINO_WEBUI_PORT: int = 80   # WebUI HTTP API

# ---------- Defaults ----------
DEFAULTS: Dict[str, Any] = {
    # Workspace (user data directory: surveys + ppk/conf).
    # Empty string means "use the auto-detected default" (see modules.workspace).
    "workspace_dir": "",

    # GNSS connection
    "gnss_host": "",
    "gnss_port": 1234,
    "gnss_autoconnect": True,

    # Web server
    "http_bind": "0.0.0.0",
    "http_port": 8000,

    # TCP Relay
    "relay_enabled": True,
    "relay_bind": "127.0.0.1",
    "relay_port": 21100,

    # Connection
    "retry_interval": 3.0,

    # Survey defaults
    "default_sample_duration": 10.0,
    "default_sample_interval": 0.5,
    "robust_mode": "sigma",       # "sigma" | "trim" | "median"
    "robust_sigma": 2.0,
    "robust_trim_q": 0.10,

    # UI
    "language": "it",
    "theme": "light",

    # RTK quality gate
    "max_hacc": 0.05,       # m
    "max_pdop": 3.0,
    "min_sv": 8,
    "rtk_quality_gate": True,  # se False, disabilita tutti i check

    # RTKino HTTP integration
    "rtkino_host": "",

    # mDNS hostname (accesso via http://<hostname>.local/)
    "mdns_hostname": "rilievopy",
}

# ---------- File path ----------
# Settings are stored next to the modules package (project root).
# This must NOT be inside the workspace directory so that workspace_dir
# can be read before the workspace exists.
_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SETTINGS_FILE = os.path.join(_PROJECT_DIR, "rilievo_settings.json")

# Backward-compat: if old location still exists but new doesn't, migrate silently.
_OLD_SETTINGS_FILE = os.path.join(_PROJECT_DIR, "surveys", "rilievo_settings.json")
_lock = threading.Lock()


def _migrate_settings_once():
    """Move settings from the old surveys/ location to project root on first run."""
    if not os.path.isfile(_SETTINGS_FILE) and os.path.isfile(_OLD_SETTINGS_FILE):
        try:
            import shutil
            shutil.copy2(_OLD_SETTINGS_FILE, _SETTINGS_FILE)
        except Exception:
            pass


def _ensure_dir():
    os.makedirs(_PROJECT_DIR, exist_ok=True)


def settings_path() -> str:
    return _SETTINGS_FILE


# ---------- Load ----------
def load_settings() -> Dict[str, Any]:
    """Load settings from JSON file. Returns defaults if file doesn't exist."""
    _ensure_dir()
    _migrate_settings_once()
    settings = dict(DEFAULTS)
    try:
        with _lock:
            if os.path.isfile(_SETTINGS_FILE):
                with open(_SETTINGS_FILE, "r", encoding="utf-8") as fh:
                    saved = json.load(fh)
                # Merge: saved values override defaults, but new defaults are kept
                settings.update(saved)
    except Exception as e:
        print(f"# {now_iso()} [settings] load error: {e}, using defaults")
    return settings


# ---------- Save ----------
def save_settings(settings: Dict[str, Any]):
    """Save settings to JSON file (atomic write)."""
    _ensure_dir()
    tmp = _SETTINGS_FILE + ".tmp"
    with _lock:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(settings, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, _SETTINGS_FILE)


# ---------- Get / Set helpers ----------
def get(key: str, default=None):
    """Get a single setting value."""
    settings = load_settings()
    return settings.get(key, default)


def update(changes: Dict[str, Any]) -> Dict[str, Any]:
    """Update multiple settings and save. Returns the full updated settings."""
    settings = load_settings()
    settings.update(changes)
    save_settings(settings)
    return settings


def reset_to_defaults() -> Dict[str, Any]:
    """Reset all settings to defaults."""
    save_settings(dict(DEFAULTS))
    return dict(DEFAULTS)


# ---------- Validation ----------
def validate_port(value) -> Optional[int]:
    """Validate and return port number, or None if invalid."""
    try:
        p = int(value)
        if 1 <= p <= 65535:
            return p
    except (ValueError, TypeError):
        pass
    return None


def validate_ip(value: str) -> Optional[str]:
    """Basic IP/hostname validation."""
    s = (value or "").strip()
    if not s:
        return None
    # Accept hostname or IP-like strings
    return s
