"""
modules/ble_gnss.py
Connessione BLE a RTKINO (Nordic UART Service).
- Riceve NMEA/UBX via notify (TX characteristic)
- Invia RTCM via write (RX characteristic)
- Rileva cambio stato fix RTK da GGA
- Emette notifiche Android: popup + suono + vibrazione + TTS
- Auto-reconnect con backoff
"""

import asyncio
import logging
import subprocess
import threading
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# Nordic UART Service UUIDs
_NUS_SERVICE = "6e400001-b5b3-f393-e0a9-e50e24dcca9e"
_NUS_TX = "6e400003-b5b3-f393-e0a9-e50e24dcca9e"   # RTKINO → phone (notify)
_NUS_RX = "6e400002-b5b3-f393-e0a9-e50e24dcca9e"   # phone → RTKINO (write)

_BLE_AVAILABLE = False
try:
    import bleak  # noqa: F401 — presence check only
    _BLE_AVAILABLE = True
except ImportError:
    logger.warning("[ble_gnss] bleak not installed — BLE unavailable. "
                   "Install with: pip install bleak")

# RTK fix quality codes from GGA field 6
_FIX_NAMES = {0: "NoFix", 1: "GPS", 2: "DGPS", 4: "RTK Fixed", 5: "RTK Float"}

# Notification ID for persistent loss-of-fix alert
_NOTIF_ID_LOSS = 9001
_NOTIF_ID_CONN = 9002

# Maximum BLE write chunk size for NUS
_BLE_CHUNK = 200

# Reconnect interval (seconds)
_RECONNECT_INTERVAL = 5.0

# Binary buffer limits
_MAX_RAW_BUFFER = 8192   # bytes before hard reset of the raw buffer
_MAX_NMEA_LINE = 1024    # bytes before discarding an unterminated NMEA line

# UBX packet framing constants
_UBX_HEADER_SIZE = 6     # sync1 + sync2 + class + id + length(2 bytes)
_UBX_CHECKSUM_SIZE = 2   # CK_A + CK_B


def _popen_silent(cmd: list):
    """Run a command non-blocking, silently ignoring FileNotFoundError."""
    try:
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        pass
    except Exception as exc:
        logger.debug("[ble_gnss] notify cmd error: %s", exc)


def _notify(notif_id: int, title: str, content: str,
            sound: bool = False, vibrate: Optional[str] = None,
            ongoing: bool = False):
    """Send an Android notification via Termux:API (non-blocking)."""
    cmd = [
        "termux-notification",
        "--id", str(notif_id),
        "--title", title,
        "--content", content,
        "--priority", "high",
    ]
    if sound:
        cmd.append("--sound")
    if vibrate:
        cmd.extend(["--vibrate", vibrate])
    if ongoing:
        cmd.append("--ongoing")
    _popen_silent(cmd)


def _notify_remove(notif_id: int):
    """Remove an Android notification."""
    _popen_silent(["termux-notification-remove", str(notif_id)])


def _vibrate(pattern: str):
    """Trigger vibration with given pattern via Termux:API (non-blocking)."""
    _popen_silent(["termux-vibrate", "-d", pattern, "-f"])


def _tts(text: str, lang: str = "it"):
    """Speak text via Termux:API TTS (non-blocking)."""
    _popen_silent(["termux-tts-speak", "-l", lang, text])


class BleGnss:
    """
    Manages BLE connection to an RTKINO device (Nordic UART Service).

    Usage::

        ble = BleGnss(device_name="RTKino", passkey=123456)
        ble.set_nmea_callback(lambda s: print(s))
        ble.start()
        ...
        ble.send_rtcm(rtcm_bytes)
        ble.stop()
    """

    def __init__(self, device_name: str = "RTKino", passkey: int = 123456,
                 tts_enabled: bool = True, tts_lang: str = "it"):
        self._device_name = device_name
        self._passkey = passkey
        self.tts_enabled = tts_enabled
        self.tts_lang = tts_lang

        self._nmea_callback: Optional[Callable[[str], None]] = None
        self._connected = False
        self._stop_flag = threading.Event()

        # BLE asyncio event loop running in a background thread
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None

        # RTCM queue — thread-safe send from outside the event loop
        self._rtcm_queue: Optional[asyncio.Queue] = None

        # Binary reassembly buffer (handles both NMEA and UBX)
        self._raw_buf = bytearray()

        # UBX parser pipe and thread
        self._ubx_pipe = None
        self._ubx_thread: Optional[threading.Thread] = None

        # RTK fix state tracking
        self._fix_quality: Optional[int] = None

    # ── Public API ──────────────────────────────────────────────────

    def set_nmea_callback(self, cb: Callable[[str], None]):
        """Register a callback invoked for every complete NMEA sentence."""
        self._nmea_callback = cb

    def start(self):
        """Start BLE in a background daemon thread. Returns immediately."""
        if not _BLE_AVAILABLE:
            logger.warning("[ble_gnss] bleak not installed — cannot start BLE")
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop_flag.clear()

        # Start UBX parser thread if not already running
        if self._ubx_pipe is None:
            try:
                from .state import BytePipe
                self._ubx_pipe = BytePipe()
                self._ubx_thread = threading.Thread(
                    target=self._run_ubx_parser, name="ble-ubx-parser", daemon=True
                )
                self._ubx_thread.start()
                logger.info("[ble_gnss] UBX parser thread started")
            except Exception as exc:
                logger.warning("[ble_gnss] Could not start UBX parser: %s", exc)
                self._ubx_pipe = None

        self._thread = threading.Thread(
            target=self._run_loop, name="ble-gnss", daemon=True
        )
        self._thread.start()
        logger.info("[ble_gnss] BLE thread started (device=%s)", self._device_name)

    def stop(self):
        """Signal the BLE thread to stop and wait for it."""
        self._stop_flag.set()
        if self._loop and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread:
            self._thread.join(timeout=5)
        self._thread = None
        if self._ubx_pipe is not None:
            self._ubx_pipe.close()
            self._ubx_pipe = None
        if self._ubx_thread is not None:
            self._ubx_thread.join(timeout=5)
            self._ubx_thread = None
        logger.info("[ble_gnss] BLE stopped")

    def send_rtcm(self, data: bytes):
        """Enqueue RTCM bytes to send to RTKINO (thread-safe, non-blocking)."""
        if not self._connected or self._loop is None or self._rtcm_queue is None:
            return
        try:
            self._loop.call_soon_threadsafe(self._rtcm_queue.put_nowait, data)
        except Exception as exc:
            logger.debug("[ble_gnss] send_rtcm enqueue error: %s", exc)

    @property
    def connected(self) -> bool:
        return self._connected

    # ── Private: event loop ─────────────────────────────────────────

    def _run_loop(self):
        """Entry point for the BLE background thread."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._connect_loop())
        except Exception as exc:
            logger.error("[ble_gnss] BLE loop error: %s", exc)
        finally:
            self._loop.close()
            self._connected = False

    async def _connect_loop(self):
        """Scan and (re)connect to RTKINO with auto-retry."""
        from bleak import BleakScanner, BleakClient, BleakError

        self._rtcm_queue = asyncio.Queue()

        while not self._stop_flag.is_set():
            device = None
            try:
                logger.info("[ble_gnss] Scanning for '%s'…", self._device_name)
                device = await BleakScanner.find_device_by_name(
                    self._device_name, timeout=10.0
                )
            except Exception as exc:
                logger.debug("[ble_gnss] Scan error: %s", exc)

            if device is None:
                logger.info("[ble_gnss] '%s' not found — retry in %ds",
                            self._device_name, int(_RECONNECT_INTERVAL))
                await asyncio.sleep(_RECONNECT_INTERVAL)
                continue

            try:
                async with BleakClient(device) as client:
                    self._on_connected()
                    await client.start_notify(_NUS_TX, self._on_ble_data)

                    # Drive RTCM send loop until disconnect or stop
                    await self._rtcm_send_loop(client)

            except Exception as exc:  # BleakError or OSError — triggers reconnect
                logger.warning("[ble_gnss] Connection lost: %s", exc)
            finally:
                if self._connected:
                    self._on_disconnected()

            if not self._stop_flag.is_set():
                await asyncio.sleep(_RECONNECT_INTERVAL)

    async def _rtcm_send_loop(self, client):
        """Wait for RTCM data from the queue and send it in chunks."""
        from bleak import BleakError

        while not self._stop_flag.is_set() and client.is_connected:
            try:
                data = await asyncio.wait_for(self._rtcm_queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            # Send in NUS-safe chunks
            for i in range(0, len(data), _BLE_CHUNK):
                chunk = data[i:i + _BLE_CHUNK]
                try:
                    await client.write_gatt_char(_NUS_RX, chunk, response=False)
                except BleakError as exc:
                    logger.warning("[ble_gnss] RTCM write error: %s", exc)
                    return  # triggers reconnect
    # ── Private: BLE callbacks ───────────────────────────────────────

    def _on_ble_data(self, _sender, data: bytearray):
        """Notification callback — parse BLE chunks containing NMEA and/or UBX."""
        self._raw_buf.extend(data)
        # Overflow protection
        if len(self._raw_buf) > _MAX_RAW_BUFFER:
            logger.warning("[ble_gnss] _raw_buf overflow — clearing buffer")
            self._raw_buf.clear()
            return
        while len(self._raw_buf) > 0:
            first = self._raw_buf[0]
            if first == 0xB5:
                # Potential UBX packet: sync1=0xB5, sync2=0x62
                if len(self._raw_buf) < 2:
                    break  # need more data
                if self._raw_buf[1] != 0x62:
                    # False sync byte — skip it
                    self._raw_buf.pop(0)
                    continue
                if len(self._raw_buf) < _UBX_HEADER_SIZE:
                    break  # need full header
                # Bytes 4-5 carry little-endian payload length
                payload_len = self._raw_buf[4] | (self._raw_buf[5] << 8)
                total_len = _UBX_HEADER_SIZE + payload_len + _UBX_CHECKSUM_SIZE
                if len(self._raw_buf) < total_len:
                    break  # need more data
                packet = bytes(self._raw_buf[:total_len])
                del self._raw_buf[:total_len]
                self._feed_ubx(packet)
            elif first == 0x24:
                # NMEA sentence starting with '$'
                nl = self._raw_buf.find(0x0A)  # '\n'
                if nl < 0:
                    if len(self._raw_buf) > _MAX_NMEA_LINE:
                        logger.warning("[ble_gnss] NMEA buffer overflow — clearing")
                        self._raw_buf.clear()
                    break  # need more data
                line_bytes = bytes(self._raw_buf[:nl + 1])
                del self._raw_buf[:nl + 1]
                line = line_bytes.decode("ascii", errors="replace").strip()
                if line.startswith("$"):
                    self._process_nmea(line)
            else:
                # Unknown byte — skip
                self._raw_buf.pop(0)

    def _feed_ubx(self, packet: bytes):
        """Feed a complete UBX packet to the UBX parser pipe."""
        if self._ubx_pipe is not None:
            try:
                self._ubx_pipe.feed(packet)
            except Exception as exc:
                logger.debug("[ble_gnss] UBX pipe feed error: %s", exc)

    def _run_ubx_parser(self):
        """Thread target: run the UBX parse loop on the BLE pipe."""
        try:
            from .ubx_parser import ubx_parse_loop
            ubx_parse_loop(self._ubx_pipe)
        except ImportError:
            logger.warning("[ble_gnss] pyubx2 not installed — UBX parsing disabled")
        except Exception as exc:
            logger.error("[ble_gnss] UBX parser error: %s", exc)

    def _process_nmea(self, sentence: str):
        """Handle a complete NMEA sentence."""
        self._check_rtk_status(sentence)
        if self._nmea_callback:
            try:
                self._nmea_callback(sentence)
            except Exception as exc:
                logger.debug("[ble_gnss] NMEA callback error: %s", exc)

    def _check_rtk_status(self, sentence: str):
        """Parse GGA and emit RTK state-change notifications."""
        if not sentence.startswith("$G") or "GGA" not in sentence:
            return
        parts = sentence.split(",")
        if len(parts) < 7:
            return
        try:
            quality = int(parts[6])
        except ValueError:
            return

        prev = self._fix_quality
        self._fix_quality = quality

        if prev == quality:
            return  # no change

        self._emit_fix_notification(prev, quality)

    def _emit_fix_notification(self, prev: Optional[int], curr: int):
        """Send the appropriate notification/vibration/TTS for a fix transition."""
        if curr == 4:
            # Any → RTK Fixed
            _notify_remove(_NOTIF_ID_LOSS)
            _notify(_NOTIF_ID_CONN,
                    "✅ RTK Fixed acquisito",
                    "Posizione RTK Fixed",
                    sound=True, vibrate="0,200")
            if self.tts_enabled:
                _tts("RTK Fixed", self.tts_lang)

        elif curr == 5 and (prev is None or prev == 0):
            # NoFix → RTK Float
            _notify(_NOTIF_ID_CONN,
                    "🟡 RTK Float",
                    "RTK Float attivo",
                    vibrate="0,300")

        elif prev == 4 and curr in (1, 2, 5):
            # RTK Fixed → Float / Single
            _notify(_NOTIF_ID_LOSS,
                    "⚠️ Fix RTK PERSO",
                    "Passato da Fixed a " + _FIX_NAMES.get(curr, str(curr)),
                    sound=True, vibrate="0,800,200,800", ongoing=True)
            if self.tts_enabled:
                _tts("Fix RTK perso", self.tts_lang)

        elif curr == 0:
            # Any → No Fix
            _notify(_NOTIF_ID_LOSS,
                    "🔴 Segnale perso",
                    "Nessun segnale GNSS",
                    sound=True, vibrate="0,800,200,800,200,800", ongoing=True)
            if self.tts_enabled:
                _tts("Segnale GNSS perso", self.tts_lang)

    def _on_connected(self):
        self._connected = True
        logger.info("[ble_gnss] Connected to '%s'", self._device_name)
        _notify(_NOTIF_ID_CONN,
                "🛰️ RTKINO connesso",
                f"BLE connesso a {self._device_name}")

    def _on_disconnected(self):
        self._connected = False
        logger.info("[ble_gnss] Disconnected from '%s'", self._device_name)
        _notify(_NOTIF_ID_CONN,
                "🔴 RTKINO disconnesso",
                f"BLE disconnesso da {self._device_name}")
