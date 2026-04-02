"""
Workspace manager — single source of truth for all user-data paths.

The workspace is a directory that contains:
  surveys/        RTK survey GeoJSON files
  ppk/conf/       RTKLIB .conf files
  ppk/uploads/    raw GNSS uploads (not migrated, stays in data/ for now)
  ...

Default path logic:
  - Termux (Android): ~/storage/shared/RilievoGNSS  if ~/storage/shared exists,
                      else ~/RilievoGNSS
  - Other (RPi/Linux): ~/RilievoGNSS
"""

import os
import shutil
from typing import Optional

# ─── Default workspace detection ─────────────────────────────────────────────

def _is_termux() -> bool:
    """Return True if running inside Termux (Android)."""
    return (
        os.path.isdir("/data/data/com.termux") or
        "com.termux" in os.environ.get("PREFIX", "") or
        "com.termux" in os.environ.get("HOME", "")
    )


def default_workspace() -> str:
    """
    Compute the default workspace path:
    - Termux + shared storage available → ~/storage/shared/RilievoGNSS
    - Termux (no shared storage) or other → ~/RilievoGNSS
    """
    home = os.path.expanduser("~")
    if _is_termux():
        shared = os.path.join(home, "storage", "shared")
        if os.path.isdir(shared):
            return os.path.join(shared, "RilievoGNSS")
    return os.path.join(home, "RilievoGNSS")


# ─── Path helpers ─────────────────────────────────────────────────────────────

def get_workspace() -> str:
    """
    Return the current workspace path from settings (or compute default).
    Import is deferred to avoid circular imports at module load time.
    """
    from modules import settings as cfg
    ws = cfg.get("workspace_dir")
    if not ws:
        ws = default_workspace()
    return os.path.expanduser(str(ws))


def surveys_dir(workspace: Optional[str] = None) -> str:
    ws = workspace or get_workspace()
    return os.path.join(ws, "surveys")


def ppk_conf_dir(workspace: Optional[str] = None) -> str:
    ws = workspace or get_workspace()
    return os.path.join(ws, "ppk", "conf")


def ppk_uploads_dir(workspace: Optional[str] = None) -> str:
    ws = workspace or get_workspace()
    return os.path.join(ws, "ppk", "uploads")


def ppk_rinex_dir(workspace: Optional[str] = None) -> str:
    ws = workspace or get_workspace()
    return os.path.join(ws, "ppk", "rinex")


def ppk_results_dir(workspace: Optional[str] = None) -> str:
    ws = workspace or get_workspace()
    return os.path.join(ws, "ppk", "results")


def ppk_pos_dir(workspace: Optional[str] = None) -> str:
    ws = workspace or get_workspace()
    return os.path.join(ws, "ppk", "pos")


def ppk_antex_dir(workspace: Optional[str] = None) -> str:
    ws = workspace or get_workspace()
    return os.path.join(ws, "ppk", "antex")


# ─── Workspace initialisation ────────────────────────────────────────────────

_WORKSPACE_SUBDIRS = [
    "surveys",
    os.path.join("ppk", "conf"),
    os.path.join("ppk", "uploads"),
    os.path.join("ppk", "rinex"),
    os.path.join("ppk", "results"),
    os.path.join("ppk", "pos"),
    os.path.join("ppk", "antex"),
]


def init_workspace(workspace: Optional[str] = None) -> str:
    """Create required subdirectories under *workspace* (default: current workspace)."""
    ws = workspace or get_workspace()
    ws = os.path.expanduser(ws)
    for sub in _WORKSPACE_SUBDIRS:
        os.makedirs(os.path.join(ws, sub), exist_ok=True)
    return ws


# ─── Migration helpers ────────────────────────────────────────────────────────

def _copy_dir_contents(src: str, dst: str) -> list:
    """
    Recursively copy all files/dirs from *src* into *dst*.
    Returns list of relative paths copied.
    """
    copied = []
    if not os.path.isdir(src):
        return copied
    os.makedirs(dst, exist_ok=True)
    for item in os.listdir(src):
        s = os.path.join(src, item)
        d = os.path.join(dst, item)
        if os.path.isdir(s):
            sub = _copy_dir_contents(s, d)
            copied.extend(os.path.join(item, p) for p in sub)
        else:
            shutil.copy2(s, d)
            copied.append(item)
    return copied


def copy_data_to_workspace(src_workspace: str, dst_workspace: str) -> dict:
    """
    Copy surveys/ and ppk/conf/ from *src_workspace* to *dst_workspace*.
    Returns a dict with 'surveys' and 'ppk_conf' keys listing files copied.
    """
    src_workspace = os.path.expanduser(src_workspace)
    dst_workspace = os.path.expanduser(dst_workspace)

    init_workspace(dst_workspace)

    result = {
        "surveys": _copy_dir_contents(
            os.path.join(src_workspace, "surveys"),
            os.path.join(dst_workspace, "surveys"),
        ),
        "ppk_conf": _copy_dir_contents(
            os.path.join(src_workspace, "ppk", "conf"),
            os.path.join(dst_workspace, "ppk", "conf"),
        ),
    }
    return result


def delete_workspace_data(workspace: str) -> dict:
    """
    Delete surveys/ and ppk/conf/ from *workspace*.
    Returns a dict with counts of files removed.
    """
    workspace = os.path.expanduser(workspace)
    removed = {"surveys": 0, "ppk_conf": 0}

    surveys = os.path.join(workspace, "surveys")
    if os.path.isdir(surveys):
        for f in os.listdir(surveys):
            fp = os.path.join(surveys, f)
            if os.path.isfile(fp):
                os.remove(fp)
                removed["surveys"] += 1
            elif os.path.isdir(fp):
                shutil.rmtree(fp, ignore_errors=True)
                removed["surveys"] += 1

    conf = os.path.join(workspace, "ppk", "conf")
    if os.path.isdir(conf):
        for f in os.listdir(conf):
            fp = os.path.join(conf, f)
            if os.path.isfile(fp):
                os.remove(fp)
                removed["ppk_conf"] += 1

    return removed


def workspace_has_data(workspace: str) -> dict:
    """
    Check whether *workspace* already contains surveys or ppk/conf files.
    Returns {'surveys': int, 'ppk_conf': int}.
    """
    workspace = os.path.expanduser(workspace)
    result = {"surveys": 0, "ppk_conf": 0}

    surveys = os.path.join(workspace, "surveys")
    if os.path.isdir(surveys):
        result["surveys"] = sum(
            1 for f in os.listdir(surveys)
            if os.path.isfile(os.path.join(surveys, f))
            and not f.startswith(".")
        )

    conf = os.path.join(workspace, "ppk", "conf")
    if os.path.isdir(conf):
        result["ppk_conf"] = sum(
            1 for f in os.listdir(conf)
            if os.path.isfile(os.path.join(conf, f))
        )

    return result
