"""
Shared state management, BytePipe for UBX streaming, TCP Relay.
"""

import socket
import threading
from typing import Dict, Any, List, Optional, Set

from .utils import now_iso


# ---------- Shared State ----------
class State:
    def __init__(self):
        self._lock = threading.Lock()
        self.data: Dict[str, Dict[str, Any]] = {
            "TPV": {}, "DOP": {}, "COV": {}, "RELPOS": {},
            "HPPOSECEF": {}, "HPPOSLLH": {},
            "RELAY": {"on": False, "bind": "127.0.0.1", "port": 21100, "clients": 0},
            "IMU": {
                "available": False,
                "calibrated": False,
                "tilt_deg": None,
                "stable": True,
                "sampling_active": False,
            },
        }

    def set(self, key: str, val: Dict[str, Any]):
        with self._lock:
            self.data[key] = val

    def patch(self, key: str, **kwargs):
        with self._lock:
            cur = self.data.get(key, {})
            cur.update(kwargs)
            self.data[key] = cur

    def snapshot(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            return {k: dict(v) for k, v in self.data.items()}


# Global singleton
STATE = State()


# ---------- Stakeout storage ----------
STAKEOUT_TARGETS: List[Dict] = []
STAKEOUT_CURRENT: Optional[Dict] = None
STAKEOUT_LOCK = threading.Lock()


# ---------- BytePipe ----------
class BytePipe:
    """Thread-safe byte buffer for UBX stream."""

    def __init__(self):
        self.buf = bytearray()
        self.cv = threading.Condition()
        self.closed = False

    def feed(self, chunk: bytes):
        if not chunk:
            return
        with self.cv:
            self.buf.extend(chunk)
            self.cv.notify_all()

    def close(self):
        with self.cv:
            self.closed = True
            self.cv.notify_all()

    def _pop(self, n: int) -> bytes:
        k = min(n, len(self.buf))
        out = bytes(self.buf[:k])
        del self.buf[:k]
        return out

    def read(self, n: int) -> bytes:
        with self.cv:
            while len(self.buf) < n and not self.closed:
                self.cv.wait()
            if not self.buf and self.closed:
                return b""
            return self._pop(n)

    def readline(self, maxsize: int = -1) -> bytes:
        with self.cv:
            while True:
                idx = self.buf.find(b'\n')
                if idx != -1:
                    end = idx + 1
                    if maxsize > 0:
                        end = min(end, maxsize)
                    line = bytes(self.buf[:end])
                    del self.buf[:end]
                    return line
                if self.closed:
                    if not self.buf:
                        return b""
                    if maxsize > 0:
                        line = bytes(self.buf[:maxsize])
                        del self.buf[:maxsize]
                    else:
                        line = bytes(self.buf)
                        self.buf.clear()
                    return line
                self.cv.wait()


# ---------- TCP Relay ----------
class TCPRelay:
    """Relay raw GNSS data to local TCP clients (e.g. GNSS Master)."""

    def __init__(self, bind: str, port: int):
        self.bind = bind
        self.port = port
        self.clients: Set[socket.socket] = set()
        self.lock = threading.Lock()
        self._stop = threading.Event()
        self._srv = None
        self._accept_thread = None

    def start(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self.bind, self.port))
        srv.listen(5)
        srv.settimeout(1.0)
        self._srv = srv
        self._accept_thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._accept_thread.start()
        STATE.set("RELAY", {"on": True, "bind": self.bind, "port": self.port, "clients": 0})
        print(f"# {now_iso()} [relay] listening on {self.bind}:{self.port}")

    def _accept_loop(self):
        while not self._stop.is_set():
            try:
                c, addr = self._srv.accept()
                c.settimeout(2.0)
                with self.lock:
                    self.clients.add(c)
                    cnt = len(self.clients)
                STATE.patch("RELAY", clients=cnt)
                print(f"# {now_iso()} [relay] client +1 {addr} (total {cnt})")
            except socket.timeout:
                continue
            except Exception as e:
                if not self._stop.is_set():
                    print(f"# {now_iso()} [relay] accept err: {e}")

    def broadcast(self, chunk: bytes):
        if not chunk:
            return
        dead = []
        with self.lock:
            for c in list(self.clients):
                try:
                    c.sendall(chunk)
                except Exception:
                    dead.append(c)
            for c in dead:
                try:
                    c.close()
                except Exception:
                    pass
                self.clients.discard(c)
            if dead:
                STATE.patch("RELAY", clients=len(self.clients))

    def stop(self):
        self._stop.set()
        try:
            if self._srv:
                self._srv.close()
        except Exception:
            pass
        with self.lock:
            for c in list(self.clients):
                try:
                    c.close()
                except Exception:
                    pass
            self.clients.clear()
        STATE.set("RELAY", {"on": False, "bind": self.bind, "port": self.port, "clients": 0})
