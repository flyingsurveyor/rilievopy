"""
modules/ble_integration.py
Ponte BLE ↔ RilievoPY.
- Avvia BleGnss in background
- Inietta NMEA ricevuto nel BytePipe di CONN
- Espone send_rtcm_via_ble() per il modulo NTRIP futuro
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from .ble_gnss import BleGnss
    _BLE_AVAILABLE = True
except ImportError:
    _BLE_AVAILABLE = False
    logger.warning("[ble_integration] ble_gnss not available — BLE disabled")

_instance: Optional["BleGnss"] = None  # type: ignore[name-defined]


def start_ble(device_name: str = "RTKino", passkey: int = 123456,
              tts_enabled: bool = True, tts_lang: str = "it"):
    """Create and start a BleGnss instance in the background.

    Safe to call even if bleak is not installed — it will log a warning
    and return without raising.
    """
    global _instance

    if not _BLE_AVAILABLE:
        logger.warning("[ble_integration] bleak not installed — BLE unavailable")
        return

    if _instance is not None:
        logger.info("[ble_integration] BLE already running; stopping first")
        _instance.stop()

    _instance = BleGnss(
        device_name=device_name,
        passkey=passkey,
        tts_enabled=tts_enabled,
        tts_lang=tts_lang,
    )
    _instance.set_nmea_callback(_nmea_cb)
    _instance.start()
    logger.info("[ble_integration] BLE started (device=%s)", device_name)


def stop_ble():
    """Stop the running BleGnss instance, if any."""
    global _instance
    if _instance is not None:
        _instance.stop()
        _instance = None
        logger.info("[ble_integration] BLE stopped")


def send_rtcm_via_ble(data: bytes):
    """Forward RTCM correction bytes to RTKINO via BLE (non-blocking).

    Does nothing if BLE is not connected.
    """
    if _instance is not None and _instance.connected:
        _instance.send_rtcm(data)


def ble_connected() -> bool:
    """Return True if the BLE device is currently connected."""
    return _instance is not None and _instance.connected


def ble_instance() -> "Optional[BleGnss]":  # type: ignore[name-defined]
    """Return the active BleGnss instance, or None."""
    return _instance


# ── Internal NMEA callback ────────────────────────────────────────────────────

def _nmea_cb(sentence: str):
    """Inject a received NMEA sentence into the CONN BytePipe, if active."""
    # Lazy import to avoid circular imports at module load time
    try:
        from .connection import CONN
    except ImportError:
        return

    try:
        if CONN.pipe is not None:
            CONN.pipe.feed(sentence.encode() + b"\r\n")
    except Exception as exc:
        logger.debug("[ble_integration] pipe feed error: %s", exc)
