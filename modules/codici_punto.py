"""
Point code library — loads conf/codici_punto.json.
"""

import json
import logging
import os
from typing import List, Tuple, Dict, Any

_PATH = os.path.join(os.path.dirname(__file__), '..', 'conf', 'codici_punto.json')
_log = logging.getLogger(__name__)


def load_codici() -> Dict[str, Any]:
    """Load and return the full codici_punto JSON structure."""
    try:
        with open(_PATH, encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        _log.warning("codici_punto.json not found at %s", _PATH)
        return {"categorie": []}
    except json.JSONDecodeError as e:
        _log.error("codici_punto.json is malformed: %s", e)
        return {"categorie": []}


def flat_codici() -> List[Tuple[str, str]]:
    """Return list of (cod, label) for all codes."""
    data = load_codici()
    result = []
    for cat in data.get('categorie', []):
        for c in cat.get('codici', []):
            result.append((c['cod'], c['label']))
    return result
