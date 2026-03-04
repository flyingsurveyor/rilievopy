"""
Session log: append-only log of work sessions.
Each entry: timestamp, survey_id, action (point_saved | point_failed | session_start), metadata.
Stored in data/session_log.jsonl (one JSON per line).
"""
import json
import os

from .utils import now_iso

LOG_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'session_log.jsonl')


def log_event(action: str, survey_id: str = None, meta: dict = None):
    os.makedirs(os.path.dirname(os.path.abspath(LOG_PATH)), exist_ok=True)
    entry = {"ts": now_iso(), "action": action, "sid": survey_id, **(meta or {})}
    with open(LOG_PATH, 'a', encoding='utf-8') as f:
        f.write(json.dumps(entry, ensure_ascii=False) + '\n')


def read_log(limit: int = 200) -> list:
    if not os.path.exists(LOG_PATH):
        return []
    lines = []
    try:
        with open(LOG_PATH, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    lines.append(json.loads(line))
    except Exception:
        return []
    return lines[-limit:]
