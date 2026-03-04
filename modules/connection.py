"""
GNSS connection manager.
Handles start/stop/restart of the upstream connection and relay
based on current settings. Thread-safe.
"""

import threading
from typing import Optional

from .utils import now_iso
from .state import STATE, BytePipe, TCPRelay
from .ubx_parser import ubx_parse_loop, upstream_loop


class ConnectionManager:
    """Manages GNSS upstream connection and TCP relay lifecycle."""

    def __init__(self):
        self._lock = threading.Lock()
        self.pipe: Optional[BytePipe] = None
        self.relay: Optional[TCPRelay] = None
        self._upstream_thread: Optional[threading.Thread] = None
        self._parser_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self.connected_host: Optional[str] = None
        self.connected_port: Optional[int] = None

    @property
    def is_running(self) -> bool:
        return (self._upstream_thread is not None
                and self._upstream_thread.is_alive())

    def status(self) -> dict:
        """Return current connection status."""
        with self._lock:
            return {
                "gnss_connected": self.is_running,
                "gnss_host": self.connected_host or "",
                "gnss_port": self.connected_port or 0,
                "relay_active": self.relay is not None,
            }

    def start(self, gnss_host: str, gnss_port: int,
              relay_enabled: bool = False, relay_bind: str = "127.0.0.1",
              relay_port: int = 21100, retry: float = 3.0):
        """Start GNSS connection and optionally TCP relay."""
        with self._lock:
            # Stop existing if running
            self._stop_internal()

            if not gnss_host:
                print(f"# {now_iso()} [conn] no GNSS host configured, skipping")
                STATE.set("RELAY", {"on": False, "bind": relay_bind,
                                    "port": relay_port, "clients": 0})
                return

            self._stop_event.clear()
            self.pipe = BytePipe()
            self.connected_host = gnss_host
            self.connected_port = gnss_port

            # Start relay if enabled
            if relay_enabled:
                try:
                    self.relay = TCPRelay(relay_bind, relay_port)
                    self.relay.start()
                except Exception as e:
                    print(f"# {now_iso()} [conn] relay start error: {e}")
                    self.relay = None
            else:
                self.relay = None
                STATE.set("RELAY", {"on": False, "bind": relay_bind,
                                    "port": relay_port, "clients": 0})

            # Start parser thread
            self._parser_thread = threading.Thread(
                target=ubx_parse_loop, args=(self.pipe,), daemon=True
            )
            self._parser_thread.start()

            # Start upstream thread
            self._upstream_thread = threading.Thread(
                target=upstream_loop,
                args=(gnss_host, gnss_port, self.pipe, self.relay, retry),
                daemon=True
            )
            self._upstream_thread.start()

            print(f"# {now_iso()} [conn] started: {gnss_host}:{gnss_port}"
                  f" relay={'on' if relay_enabled else 'off'}")

    def stop(self):
        """Stop all connections."""
        with self._lock:
            self._stop_internal()

    def _stop_internal(self):
        """Internal stop (caller must hold lock)."""
        if self.relay:
            try:
                self.relay.stop()
            except Exception:
                pass
            self.relay = None

        if self.pipe:
            self.pipe.close()
            self.pipe = None

        self._stop_event.set()
        self.connected_host = None
        self.connected_port = None

        # Threads are daemon, they'll die with the process
        # but we clear references
        self._upstream_thread = None
        self._parser_thread = None

    def restart(self, gnss_host: str, gnss_port: int,
                relay_enabled: bool = False, relay_bind: str = "127.0.0.1",
                relay_port: int = 21100, retry: float = 3.0):
        """Stop and restart with new parameters."""
        self.stop()
        self.start(gnss_host, gnss_port, relay_enabled,
                   relay_bind, relay_port, retry)


# Global singleton
CONN = ConnectionManager()
