"""
modules/usb_otg.py
──────────────────────────────────────────────────────────────────────────────
USB OTG GNSS source for Android/Termux + u-blox ZED-F9P.

Architecture
────────────
Data flow:
  ZED-F9P (USB bulk endpoint 0x82)
    → ioctl(USBDEVFS_BULK) in C reader
    → FIFO /tmp/rilievopy_gnss.fifo  (binario puro, nessun bash in mezzo)
    → open(fifo, "rb") in Python
    → pipe.feed(chunk) → ubx_parse_loop()

Il C helper (tools/usb_otg_reader) è invocato via:
  GNSS_OUT=/tmp/rilievopy_gnss.fifo termux-usb -e ./tools/usb_otg_reader <device_path>

La FIFO bypassa il NUL-stripping di bash (che affligge stdout quando
termux-usb usa command substitution $(...) internamente).

Public API
──────────
  is_usb_otg_available()          → bool (termux-usb in PATH)
  list_usb_devices()              → list[str] | None
  request_usb_permission(device)  → bool
  compile_usb_reader()            → (ok: bool, message: str)
  usb_reader_compiled()           → bool
  usb_otg_upstream_loop(device, pipe, relay, retry, stop_event)
"""

import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from typing import Optional

from .utils import now_iso
from .state import BytePipe, TCPRelay

# Paths
_MODULE_DIR  = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_MODULE_DIR)
_READER_SRC  = os.path.join(_PROJECT_DIR, "tools", "usb_otg_reader.c")
_READER_BIN  = os.path.join(_PROJECT_DIR, "tools", "usb_otg_reader")

# Named FIFO path for binary-clean IPC between C reader and Python
_FIFO_PATH = os.path.join(tempfile.gettempdir(), "rilievopy_gnss.fifo")

# Read buffer size used in both the Python FIFO reader and C reader (READ_BUF_SIZE)
_READ_BUF_SIZE = 4096

# If True, the binary is deleted and recompiled at every startup of the loop.
# Set to False once the reader is stable to skip recompilation on each run.
ALWAYS_RECOMPILE = True


# ── Availability checks ────────────────────────────────────────────────────────

def is_usb_otg_available() -> bool:
    """Return True if termux-usb is available (i.e. we're on Android/Termux)."""
    return shutil.which("termux-usb") is not None


# ── FIFO helpers ───────────────────────────────────────────────────────────────

def _ensure_fifo(path: str) -> bool:
    """Create or recreate the named FIFO. Returns True on success."""
    try:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        os.mkfifo(path)
        return True
    except OSError as e:
        print(f"# [usb_otg] cannot create FIFO {path}: {e}")
        return False


def _cleanup_fifo(path: str):
    """Remove the named FIFO if it exists, ignoring errors."""
    try:
        os.remove(path)
    except OSError:
        pass


def usb_reader_compiled() -> bool:
    """Return True if the C helper binary already exists and is executable."""
    return os.path.isfile(_READER_BIN) and os.access(_READER_BIN, os.X_OK)


# ── Device discovery ───────────────────────────────────────────────────────────

def list_usb_devices() -> Optional[list]:
    """
    Return list of USB device paths from 'termux-usb -l'.
    Returns None on error, empty list if no devices found.
    """
    if not is_usb_otg_available():
        return None
    try:
        result = subprocess.run(
            ["termux-usb", "-l"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0:
            return None
        raw = result.stdout.strip()
        if not raw:
            return []
        devices = json.loads(raw)
        if isinstance(devices, list):
            return [str(d) for d in devices]
        return []
    except (subprocess.TimeoutExpired, json.JSONDecodeError, Exception):
        return None


# ── Permission request ─────────────────────────────────────────────────────────

def request_usb_permission(device: str) -> bool:
    """
    Request USB permission for a device via 'termux-usb -r <device>'.
    Returns True if the command exited without error.
    This shows an Android permission dialog; call before connect.
    """
    if not is_usb_otg_available():
        return False
    try:
        result = subprocess.run(
            ["termux-usb", "-r", device],
            capture_output=True, text=True, timeout=30
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, Exception):
        return False


# ── C helper compilation ───────────────────────────────────────────────────────

def compile_usb_reader() -> tuple:
    """
    Compile tools/usb_otg_reader.c using clang.
    Returns (ok: bool, message: str).
    """
    if not os.path.isfile(_READER_SRC):
        return False, f"Source not found: {_READER_SRC}"

    if not shutil.which("clang"):
        return False, "clang not found. Install with: pkg install clang"

    try:
        result = subprocess.run(
            ["clang", _READER_SRC, "-o", _READER_BIN],
            capture_output=True, text=True, timeout=60
        )
        if result.returncode == 0:
            os.chmod(_READER_BIN, 0o755)
            return True, "Compiled successfully"
        else:
            msg = result.stderr.strip() or result.stdout.strip() or "Compilation failed"
            return False, msg
    except subprocess.TimeoutExpired:
        return False, "Compilation timed out"
    except Exception as e:
        print(f"# [usb_otg] compile error: {e}")
        return False, "Compilation error (check logs)"


# ── USB OTG upstream loop ──────────────────────────────────────────────────────

def usb_otg_upstream_loop(
    device: str,
    pipe: BytePipe,
    relay: Optional[TCPRelay],
    retry: float = 3.0,
    stop_event=None,
):
    """
    Read raw GNSS bytes from ZED-F9P via USB OTG, feed BytePipe and relay.

    Uses a named FIFO to bypass bash NUL-stripping: termux-usb is a bash
    script that uses command substitution $(...) internally, which strips
    all NUL bytes from binary UBX/NMEA streams passing through stdout.
    The C reader writes to a FIFO (via GNSS_OUT env var) instead of stdout,
    providing a pure binary channel with no bash in between.

    Mirrors the pattern of upstream_loop() in ubx_parser.py:
      - Runs forever (or until stop_event is set)
      - On error/exit of subprocess, waits `retry` seconds and restarts
      - Feeds pipe.feed(chunk) and relay.broadcast(chunk) for each chunk

    Invokes: GNSS_OUT=<fifo> termux-usb -e <reader_bin> <device>
    Python opens the FIFO directly with open(fifo_path, "rb").
    """
    while True:
        if stop_event is not None and stop_event.is_set():
            return

        proc = None
        fifo_fd = None
        stderr_thread = None
        try:
            # If ALWAYS_RECOMPILE is set, force a fresh compilation every startup
            if ALWAYS_RECOMPILE and os.path.isfile(_READER_BIN):
                print(f"# {now_iso()} [usb_otg] ALWAYS_RECOMPILE: removing old binary")
                try:
                    os.remove(_READER_BIN)
                except OSError as e:
                    print(f"# {now_iso()} [usb_otg] could not remove old binary: {e}")

            if not usb_reader_compiled():
                print(f"# {now_iso()} [usb_otg] reader binary not found, attempting compile")
                ok, msg = compile_usb_reader()
                if not ok:
                    print(f"# {now_iso()} [usb_otg] compile failed: {msg}. Retry in {retry}s")
                    time.sleep(retry)
                    continue

            if not is_usb_otg_available():
                print(f"# {now_iso()} [usb_otg] termux-usb not available. Retry in {retry}s")
                time.sleep(retry)
                continue

            # Create FIFO for binary-clean IPC (bypasses bash NUL-stripping)
            if not _ensure_fifo(_FIFO_PATH):
                time.sleep(retry)
                continue

            # Build env with GNSS_OUT pointing to FIFO
            env = os.environ.copy()
            env["GNSS_OUT"] = _FIFO_PATH

            print(f"# {now_iso()} [usb_otg] starting reader: {device} → FIFO {_FIFO_PATH}")
            proc = subprocess.Popen(
                ["termux-usb", "-e", _READER_BIN, device],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            print(f"# {now_iso()} [usb_otg] reader started (pid={proc.pid})")

            # Background thread: forward reader stderr to Python log in real time
            def _stderr_reader(p):
                for line in p.stderr:
                    text = line.decode("utf-8", errors="replace").rstrip()
                    if text:
                        print(f"# [usb_otg reader] {text}")

            stderr_thread = threading.Thread(target=_stderr_reader, args=(proc,), daemon=True)
            stderr_thread.start()

            # Open FIFO for reading (blocks until C reader opens it for writing)
            print(f"# {now_iso()} [usb_otg] opening FIFO for reading…")
            try:
                fifo_fd = open(_FIFO_PATH, "rb")
            except OSError as e:
                raise ConnectionError(f"cannot open FIFO {_FIFO_PATH}: {e} "
                                      "(reader may have failed to start)") from e
            print(f"# {now_iso()} [usb_otg] FIFO open — stream avviato")

            while True:
                if stop_event is not None and stop_event.is_set():
                    break

                chunk = fifo_fd.read(_READ_BUF_SIZE)
                if not chunk:
                    # EOF — subprocess exited or FIFO closed
                    rc = proc.wait()
                    raise ConnectionError(f"reader exited (rc={rc})")

                pipe.feed(chunk)
                if relay is not None:
                    relay.broadcast(chunk)

        except Exception as e:
            print(f"# {now_iso()} [usb_otg] disconnected: {e}. Retry in {retry}s")
            time.sleep(retry)
        finally:
            if fifo_fd is not None:
                try:
                    fifo_fd.close()
                except Exception:
                    pass
            _cleanup_fifo(_FIFO_PATH)
            if proc is not None:
                try:
                    proc.terminate()
                    proc.wait(timeout=3)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
            if stderr_thread is not None:
                stderr_thread.join(timeout=2)
                if stderr_thread.is_alive():
                    print(f"# {now_iso()} [usb_otg] stderr reader thread did not terminate within timeout")

        if stop_event is not None and stop_event.is_set():
            return
