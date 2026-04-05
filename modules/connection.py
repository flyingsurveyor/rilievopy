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


def _resolve_mdns_host(hostname: str) -> str:
    """
    If hostname ends with '.local', try to resolve it via Zeroconf
    (bypasses the system DNS resolver which doesn't handle mDNS on Android/Termux).
    Returns the resolved IP string, or the original hostname if resolution fails.
    """
    if not hostname.lower().endswith('.local'):
        return hostname
    try:
        from zeroconf import Zeroconf
        import socket as _socket
        import time as _time

        # Strip .local suffix (reserved for service lookup if needed)
        zc = Zeroconf()
        # Try simple socket resolution first (works if avahi is running)
        try:
            ip = _socket.getaddrinfo(hostname, None, _socket.AF_INET)[0][4][0]
            if ip and not ip.startswith('127.'):
                zc.close()
                print(f"# [conn] mDNS resolved {hostname} → {ip} (system resolver)")
                return ip
        except Exception:
            pass

        # Fallback: use Zeroconf ServiceBrowser to find _http._tcp services
        from zeroconf import ServiceBrowser, ServiceListener

        resolved_ip = [None]

        class _Listener(ServiceListener):
            def add_service(self, zc, type_, name_):
                info = zc.get_service_info(type_, name_)
                if info and info.addresses:
                    import ipaddress
                    ip_str = str(ipaddress.ip_address(info.addresses[0]))
                    resolved_ip[0] = ip_str

            def remove_service(self, zc, type_, name_): pass
            def update_service(self, zc, type_, name_): pass

        browser = ServiceBrowser(zc, "_http._tcp.local.", _Listener())
        # Wait up to 3 seconds for a response
        deadline = _time.time() + 3.0
        while _time.time() < deadline and resolved_ip[0] is None:
            _time.sleep(0.1)

        zc.close()

        if resolved_ip[0]:
            print(f"# [conn] mDNS resolved {hostname} → {resolved_ip[0]} (zeroconf)")
            return resolved_ip[0]
        else:
            print(f"# [conn] mDNS resolution failed for {hostname}, using as-is")
            return hostname

    except ImportError:
        print(f"# [conn] zeroconf not available, using {hostname} as-is")
        return hostname
    except Exception as e:
        print(f"# [conn] mDNS resolve error for {hostname}: {e}")
        return hostname


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

            # Resolve .local mDNS hostnames (Android/Termux DNS doesn't handle mDNS)
            resolved_host = _resolve_mdns_host(gnss_host)
            if resolved_host != gnss_host:
                print(f"# {now_iso()} [conn] mDNS: {gnss_host} → {resolved_host}")

            self._stop_event.clear()
            self.pipe = BytePipe()
            self.connected_host = gnss_host  # keep original for display
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
                args=(resolved_host, gnss_port, self.pipe, self.relay, retry),
                daemon=True
            )
            self._upstream_thread.start()

            print(f"# {now_iso()} [conn] started: {gnss_host}:{gnss_port}"
                  + (f" (resolved: {resolved_host})" if resolved_host != gnss_host else "")
                  + f" relay={'on' if relay_enabled else 'off'}")

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
