"""
modules/rtkino_api.py
Client HTTP per le API di RTKino.
Wrappa tutti gli endpoint REST esposti da RTKino (branch DEV).
Thread-safe, gestisce timeout ed errori.
"""

import json
import logging
import threading
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class RTKinoAPI:
    """Client HTTP verso le API di RTKino.

    Tutti i metodi restituiscono il contenuto JSON come dict/str
    oppure None in caso di errore (l'errore viene loggato).
    """

    def __init__(self, host: str, port: int = 80, timeout: float = 5.0):
        self.base_url = f"http://{host}:{port}"
        self.timeout = timeout
        self._lock = threading.Lock()

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Optional[Any]:
        """HTTP GET → parsed JSON (or raw text if not JSON)."""
        url = self.base_url + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        try:
            req = urllib.request.Request(
                url,
                headers={"Accept": "application/json", "User-Agent": "RilievoPY/1.0"},
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return raw
        except urllib.error.URLError as exc:
            logger.debug("[rtkino_api] GET %s error: %s", path, exc)
            return None
        except Exception as exc:
            logger.debug("[rtkino_api] GET %s unexpected error: %s", path, exc)
            return None

    def _post(self, path: str, body: Any) -> Optional[Any]:
        """HTTP POST with JSON body → parsed JSON."""
        url = self.base_url + path
        data = json.dumps(body).encode("utf-8")
        try:
            req = urllib.request.Request(
                url,
                data=data,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "User-Agent": "RilievoPY/1.0",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return raw
        except urllib.error.URLError as exc:
            logger.debug("[rtkino_api] POST %s error: %s", path, exc)
            return None
        except Exception as exc:
            logger.debug("[rtkino_api] POST %s unexpected error: %s", path, exc)
            return None

    # ── Connection test ───────────────────────────────────────────────────────

    def ping(self) -> bool:
        """Return True if RTKino responds to /api/status."""
        result = self._get("/api/status")
        return result is not None

    # ── Status ────────────────────────────────────────────────────────────────

    def get_status(self) -> Optional[Dict]:
        """GET /api/status → wifi, ntrip, log, tcpIn, bleRtcm, mode, ble."""
        return self._get("/api/status")

    def get_position(self) -> Optional[Dict]:
        """GET /api/position → lat, lon, alt, fix, fixStr, sats, DOP, hAcc, vAcc."""
        return self._get("/api/position")

    def get_rtcm_stats(self) -> Optional[Dict]:
        """GET /api/rtcm → statistiche RTCM."""
        return self._get("/api/rtcm")

    # ── Control ───────────────────────────────────────────────────────────────

    def ntrip_toggle(self, enable: bool) -> Optional[Any]:
        """GET /ntrip/toggle?enable=0|1"""
        return self._get("/ntrip/toggle", {"enable": 1 if enable else 0})

    def logging_start(self) -> Optional[Any]:
        """GET /log/start — avvia logging raw UBX."""
        return self._get("/log/start")

    def logging_stop(self) -> Optional[Any]:
        """GET /log/stop — ferma logging raw UBX."""
        return self._get("/log/stop")

    def switch_to_rover(self) -> Optional[Any]:
        """GET /api/switchToRover"""
        return self._get("/api/switchToRover")

    def zed_reset(self, reset_type: str = "hot") -> Optional[Any]:
        """GET /api/zed/reset?type=hot|cold"""
        return self._get("/api/zed/reset", {"type": reset_type})

    def get_zed_tmode(self) -> Optional[Dict]:
        """GET /api/zed/tmode"""
        return self._get("/api/zed/tmode")

    def refresh_zed_tmode(self) -> Optional[Dict]:
        """GET /api/zed/tmode/refresh"""
        return self._get("/api/zed/tmode/refresh")

    # ── Survey (base position averaging) ─────────────────────────────────────

    def survey_start(self, duration: int, height: float, arp: float = 0.0) -> Optional[Any]:
        """GET /api/survey/start?duration=X&height=Y&arp=Z"""
        return self._get("/api/survey/start", {
            "duration": duration, "height": height, "arp": arp,
        })

    def survey_stop(self) -> Optional[Any]:
        """GET /api/survey/stop"""
        return self._get("/api/survey/stop")

    def survey_status(self) -> Optional[Dict]:
        """GET /api/survey/status → active, complete, progress, samples, lat, lon, alt, stdDev"""
        return self._get("/api/survey/status")

    # ── Survey Points (misurazioni punti) ────────────────────────────────────

    def pts_list(self) -> Optional[Dict]:
        """GET /api/pts/list → lista rilievi sul dispositivo."""
        return self._get("/api/pts/list")

    def pts_create(self, title: str, desc: str = "") -> Optional[Any]:
        """POST /api/pts/create → crea nuovo rilievo."""
        return self._post("/api/pts/create", {"title": title, "desc": desc})

    def pts_set_active(self, sid: str) -> Optional[Any]:
        """GET /api/pts/setactive?sid=X"""
        return self._get("/api/pts/setactive", {"sid": sid})

    def pts_quality(self) -> Optional[Dict]:
        """GET /api/pts/quality → quality gate corrente."""
        return self._get("/api/pts/quality")

    def pts_measure(self, name: str, codice: str = "",
                    duration: int = 10, interval: int = 1) -> Optional[Any]:
        """GET /api/pts/measure → avvia misurazione punto."""
        return self._get("/api/pts/measure", {
            "name": name, "codice": codice,
            "duration": duration, "interval": interval,
        })

    def pts_measure_status(self) -> Optional[Dict]:
        """GET /api/pts/measure/status → progresso misurazione."""
        return self._get("/api/pts/measure/status")

    def pts_points(self, sid: str) -> Optional[Dict]:
        """GET /api/pts/points?sid=X → GeoJSON punti di un rilievo."""
        return self._get("/api/pts/points", {"sid": sid})

    def pts_download(self, sid: str) -> Optional[Dict]:
        """GET /api/pts/download?sid=X → GeoJSON completo."""
        return self._get("/api/pts/download", {"sid": sid})

    def pts_download_csv(self, sid: str) -> Optional[str]:
        """GET /api/pts/download/csv?sid=X → CSV come stringa."""
        return self._get("/api/pts/download/csv", {"sid": sid})

    def pts_delete_survey(self, sid: str) -> Optional[Any]:
        """POST /api/pts/delete → elimina rilievo."""
        return self._post("/api/pts/delete", {"sid": sid})

    def pts_delete_point(self, sid: str, pid: str) -> Optional[Any]:
        """POST /api/pts/point/delete → elimina singolo punto."""
        return self._post("/api/pts/point/delete", {"sid": sid, "pid": pid})

    def pts_sync(self) -> Optional[Any]:
        """GET /api/pts/sync → sync su SD."""
        return self._get("/api/pts/sync")

    # ── Config ────────────────────────────────────────────────────────────────

    def config_export(self) -> Optional[Dict]:
        """GET /api/config/export → export JSON impostazioni RTKino."""
        return self._get("/api/config/export")

    def config_import(self, config: Dict) -> Optional[Any]:
        """POST /api/config/import → import impostazioni RTKino."""
        return self._post("/api/config/import", config)
