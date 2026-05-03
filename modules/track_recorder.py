"""
modules/track_recorder.py
Track recorder: campiona la posizione da STATE a intervallo regolare
e salva su file GPX incrementale (sempre valido) e CSV.
"""

import csv
import os
import threading
from datetime import datetime, timezone
from typing import Optional

from .state import STATE

TRACKS_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "tracks")

_GPX_FOOTER = b"  </trkseg></trk>\n</gpx>"


def _ensure_dir():
    os.makedirs(TRACKS_DIR, exist_ok=True)


class TrackRecorder:
    """Registra una traccia GPS campionando STATE.TPV a intervallo fisso."""

    def __init__(self):
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._stop_evt = threading.Event()

        self.recording: bool = False
        self.track_name: Optional[str] = None
        self.interval: float = 1.0
        self.min_fix: int = 3
        self.max_hacc: Optional[float] = None
        self.point_count: int = 0
        self.start_time: Optional[datetime] = None

        self._gpx_path: Optional[str] = None
        self._csv_path: Optional[str] = None
        self._gpx_file = None   # binary file handle
        self._csv_file = None
        self._csv_writer = None

    # ── public API ──────────────────────────────────────────────────────────

    def start(self, name: str = None, interval: float = 1.0,
              min_fix: int = 3, max_hacc: Optional[float] = None) -> dict:
        with self._lock:
            if self.recording:
                return {"ok": False, "error": "already recording"}
            _ensure_dir()
            ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            self.track_name = (name or f"track_{ts}").strip() or f"track_{ts}"
            self.interval = max(0.2, float(interval))
            self.min_fix = int(min_fix)
            self.max_hacc = float(max_hacc) if max_hacc is not None else None
            self.point_count = 0
            self.start_time = datetime.now(timezone.utc)
            self._gpx_path = os.path.join(TRACKS_DIR, f"{self.track_name}.gpx")
            self._csv_path = os.path.join(TRACKS_DIR, f"{self.track_name}.csv")

            # Open GPX in binary mode for reliable seek
            self._gpx_file = open(self._gpx_path, "wb")
            header = (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<gpx version="1.1" creator="rilievopy"\n'
                '  xmlns="http://www.topografix.com/GPX/1/1">\n'
                f'  <trk><name>{self.track_name}</name><trkseg>\n'
            ).encode("utf-8")
            self._gpx_file.write(header)
            self._gpx_file.write(_GPX_FOOTER)
            self._gpx_file.flush()
            os.fsync(self._gpx_file.fileno())

            # Open CSV
            self._csv_file = open(self._csv_path, "w", newline="", encoding="utf-8")
            self._csv_writer = csv.writer(self._csv_file)
            self._csv_writer.writerow(
                ["time", "lat", "lon", "altMSL", "hAcc", "vAcc", "fixType", "rtk", "numSV"]
            )
            self._csv_file.flush()

            self.recording = True
            self._stop_evt.clear()
            self._thread = threading.Thread(
                target=self._run, name="TrackRecorder", daemon=True
            )
            self._thread.start()
            return {"ok": True, "track": self.track_name, "interval": self.interval}

    def stop(self) -> dict:
        with self._lock:
            if not self.recording:
                return {"ok": False, "error": "not recording"}
            self._stop_evt.set()

        if self._thread:
            self._thread.join(timeout=self.interval + 2)

        with self._lock:
            self.recording = False
            if self._gpx_file:
                try:
                    self._gpx_file.close()
                except Exception:
                    pass
                self._gpx_file = None
            if self._csv_file:
                try:
                    self._csv_file.close()
                except Exception:
                    pass
                self._csv_file = None
                self._csv_writer = None
            name = self.track_name
            count = self.point_count

        return {"ok": True, "track": name, "points": count}

    def status(self) -> dict:
        with self._lock:
            if self.recording and self.start_time is not None:
                elapsed = (datetime.now(timezone.utc) - self.start_time).total_seconds()
            else:
                elapsed = 0
            return {
                "recording": self.recording,
                "track": self.track_name,
                "interval": self.interval,
                "points": self.point_count,
                "start_time": self.start_time.isoformat() if self.start_time else None,
                "elapsed_seconds": elapsed,
            }

    # ── thread ──────────────────────────────────────────────────────────────

    def _run(self):
        while not self._stop_evt.is_set():
            self._sample()
            self._stop_evt.wait(self.interval)

    def _sample(self):
        snap = STATE.snapshot()
        tpv = snap.get("TPV") or {}
        lat = tpv.get("lat")
        lon = tpv.get("lon")
        if lat is None or lon is None:
            return
        fix = tpv.get("fixType", 0) or 0
        if fix < self.min_fix:
            return
        hacc = tpv.get("hAcc")
        with self._lock:
            if self.max_hacc is not None and hacc is not None and hacc > self.max_hacc:
                return

        ts_raw = tpv.get("time")
        if ts_raw:
            ts = str(ts_raw)
        else:
            ts = datetime.now(timezone.utc).isoformat()

        alt = tpv.get("altMSL")
        vacc = tpv.get("vAcc")
        rtk = tpv.get("rtk", "")
        nsat = tpv.get("numSV", 0)

        with self._lock:
            if not self.recording:
                return
            # GPX incremental write
            if self._gpx_file:
                self._append_trkpt(lat, lon, alt, ts, hacc, rtk, nsat)
            # CSV row
            if self._csv_writer:
                self._csv_writer.writerow([
                    ts, lat, lon,
                    round(alt, 4) if alt is not None else "",
                    round(hacc, 4) if hacc is not None else "",
                    round(vacc, 4) if vacc is not None else "",
                    fix, rtk, nsat,
                ])
                self._csv_file.flush()
            self.point_count += 1

    def _append_trkpt(self, lat, lon, alt, ts, hacc, rtk, nsat):
        """Append a <trkpt> before the footer using seek in binary mode."""
        ele_tag = ""
        if alt is not None:
            ele_tag = f"  <ele>{round(alt, 4)}</ele>\n"
        hacc_str = f"{hacc:.4f}" if hacc is not None else ""
        trkpt = (
            f'    <trkpt lat="{lat}" lon="{lon}">\n'
            f'{ele_tag}'
            f'      <time>{ts}</time>\n'
            f'      <extensions>\n'
            f'        <hacc>{hacc_str}</hacc>\n'
            f'        <rtk>{rtk}</rtk>\n'
            f'        <nsat>{nsat}</nsat>\n'
            f'      </extensions>\n'
            f'    </trkpt>\n'
        ).encode("utf-8")
        self._gpx_file.seek(-len(_GPX_FOOTER), 2)
        self._gpx_file.write(trkpt)
        self._gpx_file.write(_GPX_FOOTER)
        self._gpx_file.flush()
        os.fsync(self._gpx_file.fileno())


# ── singleton globale ─────────────────────────────────────────────────────────
TRACK_RECORDER = TrackRecorder()
