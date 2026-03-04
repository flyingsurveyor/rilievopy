"""
Persistent settings manager.
Loads/saves configuration from rilievo_settings.json.
Lives alongside the surveys directory.
"""

import json
import os
import threading
from typing import Dict, Any, Optional

from .utils import now_iso

# ---------- Defaults ----------
DEFAULTS: Dict[str, Any] = {
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
}

# ---------- File path ----------
_SETTINGS_DIR = os.path.abspath(os.path.join(os.getcwd(), "surveys"))
_SETTINGS_FILE = os.path.join(_SETTINGS_DIR, "rilievo_settings.json")
_lock = threading.Lock()


def _ensure_dir():
    os.makedirs(_SETTINGS_DIR, exist_ok=True)


def settings_path() -> str:
    return _SETTINGS_FILE


# ---------- Load ----------
def load_settings() -> Dict[str, Any]:
    """Load settings from JSON file. Returns defaults if file doesn't exist."""
    _ensure_dir()
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
