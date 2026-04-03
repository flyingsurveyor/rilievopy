"""
modules/ntrip_client.py
Client NTRIP puro Python (solo stdlib) per la modalità BLE-only.

Quando RTKino è connesso via BLE ma non ha accesso WiFi/Internet,
RilievoPY si connette a un caster NTRIP, riceve RTCM3 e lo
inoltra a RTKino via BLE usando send_rtcm_via_ble().

Nessuna dipendenza esterna: solo socket, base64, threading, time.
"""

import base64
import logging
import socket
import threading
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)

_DEFAULT_GGA_INTERVAL = 5.0   # secondi tra invii GGA periodici
_RECONNECT_DELAY_MIN  = 2.0   # secondi minimo tra riconnessioni
_RECONNECT_DELAY_MAX  = 60.0  # cap backoff
_RECV_BUFSIZE         = 4096


class NtripClient:
    """Client NTRIP v2 che riceve RTCM3 e lo passa al callback fornito.

    Uso tipico:
        client = NtripClient("rtk2go.com", 2101, "MOUNTPOINT", "user", "pass")
        client.start(rtcm_callback=send_rtcm_via_ble, gga_provider=lambda: gga_str)
        ...
        client.stop()
    """

    def __init__(self, host: str, port: int, mountpoint: str,
                 user: str = "", password: str = "",
                 gga_interval: float = _DEFAULT_GGA_INTERVAL):
        self.host = host
        self.port = port
        self.mountpoint = mountpoint.lstrip("/")
        self.user = user
        self.password = password
        self.gga_interval = gga_interval

        self._rtcm_callback: Optional[Callable[[bytes], None]] = None
        self._gga_provider: Optional[Callable[[], str]] = None

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        self._connected = False
        self._bytes_received: int = 0
        self._chunks: int = 0
        self._start_time: Optional[float] = None
        self._last_error: str = ""

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self, rtcm_callback: Callable[[bytes], None],
              gga_provider: Optional[Callable[[], str]] = None):
        """Avvia la connessione NTRIP in un thread di background.

        Args:
            rtcm_callback: riceve ogni chunk RTCM grezzo da inoltrare.
            gga_provider: callable che restituisce la stringa GGA corrente
                          (usata per il posizionamento VRS).
        """
        with self._lock:
            if self._thread and self._thread.is_alive():
                logger.info("[ntrip] already running — stop first")
                return
            self._rtcm_callback = rtcm_callback
            self._gga_provider = gga_provider
            self._stop_event.clear()
            self._bytes_received = 0
            self._chunks = 0
            self._start_time = time.time()
            self._connected = False
            self._thread = threading.Thread(
                target=self._run_loop, daemon=True, name="ntrip-client"
            )
            self._thread.start()
        logger.info("[ntrip] started → %s:%d/%s", self.host, self.port, self.mountpoint)

    def stop(self):
        """Ferma il client NTRIP."""
        self._stop_event.set()
        t = self._thread
        if t and t.is_alive():
            t.join(timeout=5.0)
        with self._lock:
            self._connected = False
            self._thread = None
        logger.info("[ntrip] stopped")

    @property
    def connected(self) -> bool:
        return self._connected

    def status(self) -> dict:
        """Restituisce statistiche correnti."""
        uptime = (time.time() - self._start_time) if self._start_time else 0
        return {
            "connected": self._connected,
            "host": self.host,
            "port": self.port,
            "mountpoint": self.mountpoint,
            "bytes_received": self._bytes_received,
            "chunks": self._chunks,
            "uptime": round(uptime, 1),
            "last_error": self._last_error,
        }

    # ── Internal loop ─────────────────────────────────────────────────────────

    def _run_loop(self):
        delay = _RECONNECT_DELAY_MIN
        while not self._stop_event.is_set():
            try:
                self._connect_and_receive()
                # se _connect_and_receive() ritorna normalmente → stop richiesto
            except Exception as exc:
                self._connected = False
                self._last_error = str(exc)
                logger.warning("[ntrip] connection error: %s — retry in %.1fs", exc, delay)
            if self._stop_event.is_set():
                break
            self._stop_event.wait(timeout=delay)
            delay = min(delay * 2, _RECONNECT_DELAY_MAX)

    def _connect_and_receive(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(10.0)
        sock.connect((self.host, self.port))
        logger.info("[ntrip] TCP connected %s:%d", self.host, self.port)

        # Invia richiesta HTTP NTRIP
        self._send_request(sock)

        # Leggi risposta HTTP (fino a \r\n\r\n)
        response = self._read_http_response(sock)
        if "200 OK" not in response and not response.startswith("ICY 200 OK"):
            raise ConnectionError(f"NTRIP response: {response[:80]!r}")

        logger.info("[ntrip] NTRIP stream attivo (mountpoint=%s)", self.mountpoint)
        self._connected = True
        self._last_error = ""

        # Riduci timeout per recv bloccante
        sock.settimeout(5.0)

        last_gga = time.time()

        try:
            while not self._stop_event.is_set():
                # Invia GGA periodicamente per VRS
                now = time.time()
                if now - last_gga >= self.gga_interval:
                    self._send_gga(sock)
                    last_gga = now

                try:
                    chunk = sock.recv(_RECV_BUFSIZE)
                except socket.timeout:
                    continue

                if not chunk:
                    raise ConnectionError("NTRIP server closed connection")

                self._bytes_received += len(chunk)
                self._chunks += 1

                if self._rtcm_callback:
                    try:
                        self._rtcm_callback(chunk)
                    except Exception as exc:
                        logger.debug("[ntrip] rtcm_callback error: %s", exc)
        finally:
            self._connected = False
            try:
                sock.close()
            except Exception:
                pass

    def _build_auth_header(self) -> str:
        if self.user or self.password:
            creds = base64.b64encode(
                f"{self.user}:{self.password}".encode("utf-8")
            ).decode("ascii")
            return f"Authorization: Basic {creds}\r\n"
        return ""

    def _send_request(self, sock: socket.socket):
        auth = self._build_auth_header()
        gga = ""
        if self._gga_provider:
            try:
                gga_line = self._gga_provider()
                if gga_line:
                    gga = f"Ntrip-GGA: {gga_line.strip()}\r\n"
            except Exception:
                pass

        request = (
            f"GET /{self.mountpoint} HTTP/1.1\r\n"
            f"Host: {self.host}:{self.port}\r\n"
            f"Ntrip-Version: Ntrip/2.0\r\n"
            f"User-Agent: NTRIP RilievoPY/1.0\r\n"
            f"{auth}"
            f"{gga}"
            f"Connection: keep-alive\r\n"
            f"\r\n"
        )
        sock.sendall(request.encode("ascii"))

    def _read_http_response(self, sock: socket.socket) -> str:
        """Legge la risposta HTTP fino al doppio CRLF."""
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = sock.recv(512)
            if not chunk:
                break
            buf += chunk
            if len(buf) > 8192:
                break
        return buf.decode("utf-8", errors="replace")

    def _send_gga(self, sock: socket.socket):
        """Invia la stringa GGA corrente al caster per il VRS."""
        if not self._gga_provider:
            return
        try:
            gga = self._gga_provider()
            if gga:
                line = gga.strip() + "\r\n"
                sock.sendall(line.encode("ascii", errors="replace"))
        except Exception as exc:
            logger.debug("[ntrip] gga send error: %s", exc)
