"""
modules/rtkino_manager.py
Singleton per la gestione dell'integrazione con RTKino.

Responsabilità:
- Polling periodico /api/status e /api/position quando connesso via HTTP
- Routing automatico NTRIP→BLE: se BLE attivo e NTRIP configurato, avvia
  NtripClient e inoltra RTCM via BLE
- Stato connessione (tcp/ble/disconnected)
- Aggiornamento STATE con i dati RTKino
"""

import logging
import threading
import time
from typing import Optional

from modules import settings as cfg

logger = logging.getLogger(__name__)

_POLL_MIN = 1.0   # poll interval minimo
_POLL_MAX = 30.0  # poll interval massimo


class _RTKinoManager:
    """Singleton manager per l'integrazione RTKino."""

    def __init__(self):
        self._lock = threading.Lock()
        self._poll_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        # Stato corrente
        self._status: dict = {}
        self._position: dict = {}
        self._conn_mode: str = "disconnected"   # "tcp" | "ble" | "disconnected"
        self._last_ok: float = 0.0

        # API client (creato on-demand)
        self._api = None

        # NTRIP client (per modalità BLE-only)
        self._ntrip = None

    # ── Avvio / stop polling HTTP ─────────────────────────────────────────────

    def start_polling(self):
        """Avvia il polling verso RTKino (se host configurato)."""
        with self._lock:
            if self._poll_thread and self._poll_thread.is_alive():
                return
            self._stop_event.clear()
            self._poll_thread = threading.Thread(
                target=self._poll_loop, daemon=True, name="rtkino-poll"
            )
            self._poll_thread.start()
        logger.info("[rtkino_mgr] polling started")

    def stop_polling(self):
        """Ferma il polling."""
        self._stop_event.set()
        t = self._poll_thread
        if t and t.is_alive():
            t.join(timeout=5.0)
        with self._lock:
            self._poll_thread = None
            self._conn_mode = "disconnected"
        logger.info("[rtkino_mgr] polling stopped")

    # ── Accesso allo stato ────────────────────────────────────────────────────

    @property
    def conn_mode(self) -> str:
        return self._conn_mode

    def get_status(self) -> dict:
        return dict(self._status)

    def get_position(self) -> dict:
        return dict(self._position)

    def combined_status(self) -> dict:
        """Stato combinato per l'API /api/rtkino/status."""
        s = cfg.load_settings()
        return {
            "conn_mode": self._conn_mode,
            "host": s.get("rtkino_host", ""),
            "port": s.get("rtkino_port", 80),
            "polling": s.get("rtkino_polling", False),
            "rtkino_status": dict(self._status),
            "rtkino_position": dict(self._position),
            "ntrip": self._ntrip.status() if self._ntrip else None,
        }

    # ── NTRIP client (per BLE-only) ───────────────────────────────────────────

    def start_ntrip(self, host: str, port: int, mountpoint: str,
                    user: str = "", password: str = "",
                    gga_interval: float = 5.0) -> bool:
        """Avvia il client NTRIP e lo collega al forward BLE."""
        from modules.ble_integration import ble_connected, send_rtcm_via_ble
        from modules.ntrip_client import NtripClient

        with self._lock:
            if self._ntrip:
                self._ntrip.stop()

            self._ntrip = NtripClient(
                host=host, port=port, mountpoint=mountpoint,
                user=user, password=password, gga_interval=gga_interval,
            )

        def _rtcm_cb(data: bytes):
            send_rtcm_via_ble(data)

        def _gga_provider() -> str:
            return _get_current_gga()

        self._ntrip.start(rtcm_callback=_rtcm_cb, gga_provider=_gga_provider)
        logger.info("[rtkino_mgr] NTRIP client started → %s:%d/%s", host, port, mountpoint)
        return True

    def stop_ntrip(self):
        """Ferma il client NTRIP."""
        with self._lock:
            n = self._ntrip
            self._ntrip = None
        if n:
            n.stop()
            logger.info("[rtkino_mgr] NTRIP client stopped")

    def ntrip_status(self) -> dict:
        n = self._ntrip
        if n:
            return n.status()
        return {"connected": False}

    # ── API HTTP helper ───────────────────────────────────────────────────────

    def get_api(self):
        """Restituisce un RTKinoAPI configurato con le impostazioni correnti."""
        from modules.rtkino_api import RTKinoAPI
        s = cfg.load_settings()
        host = s.get("rtkino_host", "")
        port = s.get("rtkino_port", 80)
        if not host:
            return None
        return RTKinoAPI(host=host, port=port, timeout=5.0)

    # ── Loop interno ──────────────────────────────────────────────────────────

    def _poll_loop(self):
        while not self._stop_event.is_set():
            s = cfg.load_settings()
            host = s.get("rtkino_host", "")
            enabled = s.get("rtkino_polling", False)
            interval = max(_POLL_MIN, min(_POLL_MAX, float(s.get("rtkino_poll_interval", 2.0))))

            if host and enabled:
                try:
                    from modules.rtkino_api import RTKinoAPI
                    api = RTKinoAPI(host=host, port=s.get("rtkino_port", 80), timeout=4.0)
                    status = api.get_status()
                    position = api.get_position()
                    if status is not None:
                        self._status = status
                        self._last_ok = time.time()
                        self._conn_mode = "tcp"
                    if position is not None:
                        self._position = position
                        self._update_state(position)
                    if status is None and position is None:
                        if time.time() - self._last_ok > 10.0:
                            self._conn_mode = "disconnected"
                except Exception as exc:
                    logger.debug("[rtkino_mgr] poll error: %s", exc)
                    self._conn_mode = "disconnected"
            else:
                self._conn_mode = "disconnected"

            self._stop_event.wait(timeout=interval)

    def _update_state(self, position: dict):
        """Aggiorna lo STATE globale con i dati di posizione RTKino."""
        try:
            from modules.state import STATE
            fix_map = {1: "RTK fixed", 2: "RTK float", 0: "no fix"}
            carr = position.get("carrSoln", -1)
            if carr == 2:
                rtk_str = "RTK fixed"
            elif carr == 1:
                rtk_str = "RTK float"
            else:
                rtk_str = position.get("fixStr", "no fix")

            STATE.patch("TPV",
                lat=position.get("lat"),
                lon=position.get("lon"),
                alt=position.get("alt"),
                rtk=rtk_str,
                sats=position.get("numSV"),
            )
            hdop = position.get("hdop")
            pdop = position.get("pdop")
            if hdop or pdop:
                STATE.patch("DOP", hDOP=hdop, pDOP=pdop)
        except Exception as exc:
            logger.debug("[rtkino_mgr] state update error: %s", exc)


# ── Singleton globale ─────────────────────────────────────────────────────────

RTKINO = _RTKinoManager()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_current_gga() -> str:
    """Restituisce la stringa GGA corrente dal parser UBX/BLE, se disponibile."""
    try:
        from modules.state import STATE
        snap = STATE.snapshot()
        tpv = snap.get("TPV", {})
        lat = tpv.get("lat")
        lon = tpv.get("lon")
        alt = tpv.get("alt")
        if lat is None or lon is None:
            return ""
        return _build_gga(lat, lon, alt or 0.0)
    except Exception:
        return ""


def _build_gga(lat: float, lon: float, alt: float) -> str:
    """Costruisce una frase GGA minimale da lat/lon/alt."""
    import datetime

    now = datetime.datetime.utcnow()
    hhmmss = now.strftime("%H%M%S.00")

    def dms(deg: float, pos_char: str, neg_char: str):
        sign = pos_char if deg >= 0 else neg_char
        deg = abs(deg)
        d = int(deg)
        m = (deg - d) * 60.0
        return f"{d:02d}{m:07.4f}", sign

    lat_str, lat_dir = dms(lat, "N", "S")
    lon_str, lon_dir = dms(lon, "E", "W")

    body = f"GPGGA,{hhmmss},{lat_str},{lat_dir},{lon_str},{lon_dir},4,12,1.0,{alt:.1f},M,0.0,M,1.0,0000"
    checksum = 0
    for ch in body:
        checksum ^= ord(ch)
    return f"${body}*{checksum:02X}"
