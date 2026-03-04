"""
Utility functions for GNSS RTK/PPK Suite.
Conversions, averaging, sanitization.
"""

import re
import math
from datetime import datetime
from typing import List, Optional


# ---------- Time & Conversion ----------
def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def m_from_mm(mm):
    return None if mm is None else mm / 1000.0


def m_from_01mm(v):
    return None if v is None else v / 10000.0


def rel_from_cm_01mm(cm, hp):
    if cm is None:
        return None
    return cm * 0.01 + (hp if hp is not None else 0) * 0.0001


def hp_posecef(cm, hp):
    if cm is None:
        return None
    return cm * 0.01 + (hp if hp is not None else 0) * 0.0001


def _bits(val, lo, n=2):
    return None if not isinstance(val, int) else (val >> lo) & ((1 << n) - 1)


# ---------- UBX / RTK helpers ----------
def rtk_from_pvt(msg) -> str:
    carr_field = getattr(msg, "carrSoln", None)
    if isinstance(carr_field, int):
        return "RTK fixed" if carr_field == 2 else ("RTK float" if carr_field == 1 else "none")
    flags = getattr(msg, "flags", None)
    flags2 = getattr(msg, "flags2", None)
    for src, lo in (("flags", 6), ("flags2", 6), ("flags", 0), ("flags2", 0)):
        v = flags if src == "flags" else flags2
        c = _bits(v, lo, 2)
        if c == 2:
            return "RTK fixed"
        if c == 1:
            return "RTK float"
    return "none"


def mode_from_fixtype(fx: int) -> int:
    if fx >= 3:
        return 3
    if fx == 2:
        return 2
    return 1


# ---------- Averaging ----------
def avg(values: List[float]) -> Optional[float]:
    vals = [v for v in values if v is not None]
    return sum(vals) / len(vals) if vals else None


# ---------- Robust Average ----------
ROBUST_MODE = "sigma"   # "sigma" | "trim" | "median"
ROBUST_SIGMA = 2.0
ROBUST_TRIM_Q = 0.10
ROBUST_MIN_KEEP = 5


def robust_avg(values: List[float]) -> Optional[float]:
    vals = [float(v) for v in values
            if isinstance(v, (int, float)) and not math.isnan(v) and not math.isinf(v)]
    n = len(vals)
    if n == 0:
        return None
    if n < 3:
        return sum(vals) / n

    def median(xs: List[float]) -> float:
        xs = sorted(xs)
        m = len(xs) // 2
        return xs[m] if len(xs) % 2 else (xs[m - 1] + xs[m]) / 2.0

    if ROBUST_MODE == "median":
        return median(vals)

    if ROBUST_MODE in ("sigma", "trim"):
        xs = sorted(vals)
        if ROBUST_MODE == "sigma":
            mu = sum(xs) / n
            var = sum((x - mu) ** 2 for x in xs) / n
            sd = math.sqrt(var)
            if sd == 0:
                return mu
            kept = [x for x in xs if (mu - ROBUST_SIGMA * sd) <= x <= (mu + ROBUST_SIGMA * sd)]
            if len(kept) >= max(ROBUST_MIN_KEEP, n // 2):
                return sum(kept) / len(kept)
            # fallback → trimmed
        q = ROBUST_TRIM_Q
        lo = int(math.floor(q * n))
        hi = int(math.ceil((1.0 - q) * n))
        kept = xs[lo:hi] if lo < hi else xs
        if len(kept) >= max(ROBUST_MIN_KEEP, n // 2):
            return sum(kept) / len(kept)
        return median(xs)

    return sum(vals) / n


# ---------- Sanitization ----------
def sanitize_point_name(name: str) -> str:
    name = (name or "").strip()[:20]
    name = name.replace(" ", "_")
    name = re.sub(r"[^A-Za-z0-9_\-]", "", name)
    return name or "punto"
