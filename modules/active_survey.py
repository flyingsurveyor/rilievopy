"""
Active survey persistence — reads/writes data/active_survey.json.
"""

import json
import os
from typing import Optional

_DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'data'))
_ACTIVE_FILE = os.path.join(_DATA_DIR, 'active_survey.json')


def _ensure_data_dir():
    os.makedirs(_DATA_DIR, exist_ok=True)


def get_active_survey_id() -> Optional[str]:
    """Return the active survey ID, or None if not set."""
    try:
        with open(_ACTIVE_FILE, encoding='utf-8') as f:
            data = json.load(f)
        return data.get('sid') or None
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def set_active_survey_id(sid: str):
    """Persist the active survey ID."""
    _ensure_data_dir()
    with open(_ACTIVE_FILE, 'w', encoding='utf-8') as f:
        json.dump({'sid': sid}, f)


def clear_active_survey():
    """Clear the active survey (write empty object)."""
    _ensure_data_dir()
    with open(_ACTIVE_FILE, 'w', encoding='utf-8') as f:
        json.dump({}, f)
