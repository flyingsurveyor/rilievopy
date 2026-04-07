"""
modules/usb_otg.py
──────────────────────────────────────────────────────────────────────────────
USB OTG GNSS source for Android/Termux + u-blox ZED-F9P.

Architecture
────────────
This module mirrors the TCP upstream_loop() pattern in ubx_parser.py, but
instead of a TCP socket, data comes from a C helper program that reads from
the ZED-F9P USB bulk endpoint via ioctl(USBDEVFS_BULK) — no libusb required.

The C helper (tools/usb_otg_reader) is invoked via:
  termux-usb -e ./tools/usb_otg_reader <device_path>

termux-usb passes the Android USB file descriptor as argv[1] to the helper.
The helper writes raw bytes to stdout; we read subprocess stdout and feed
the BytePipe — exactly like upstream_loop() feeds it from a TCP socket.

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
import time
from typing import Optional

from .utils import now_iso
from .state import BytePipe, TCPRelay

# Paths
_MODULE_DIR  = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_MODULE_DIR)
_READER_SRC  = os.path.join(_PROJECT_DIR, "tools", "usb_otg_reader.c")
_READER_BIN  = os.path.join(_PROJECT_DIR, "tools", "usb_otg_reader")


# ── Availability checks ────────────────────────────────────────────────────────

def is_usb_otg_available() -> bool:
    """Return True if termux-usb is available (i.e. we're on Android/Termux)."""
    return shutil.which("termux-usb") is not None


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

    Mirrors the pattern of upstream_loop() in ubx_parser.py:
      - Runs forever (or until stop_event is set)
      - On error/exit of subprocess, waits `retry` seconds and restarts
      - Feeds pipe.feed(chunk) and relay.broadcast(chunk) for each chunk

    Invokes: termux-usb -e <reader_bin> <device>
    The reader writes raw bytes to stdout; we read subprocess stdout.
    """
    while True:
        if stop_event is not None and stop_event.is_set():
            return

        proc = None
        try:
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

            print(f"# {now_iso()} [usb_otg] starting reader: {device}")
            proc = subprocess.Popen(
                ["termux-usb", "-e", _READER_BIN, device],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            print(f"# {now_iso()} [usb_otg] reader started (pid={proc.pid})")

            while True:
                if stop_event is not None and stop_event.is_set():
                    break

                chunk = proc.stdout.read(4096)
                if not chunk:
                    # EOF — subprocess exited
                    rc = proc.wait()
                    stderr_out = proc.stderr.read().decode("utf-8", errors="replace").strip()
                    if stderr_out:
                        print(f"# {now_iso()} [usb_otg] reader stderr: {stderr_out}")
                    raise ConnectionError(f"reader exited (rc={rc})")

                pipe.feed(chunk)
                if relay is not None:
                    relay.broadcast(chunk)

        except Exception as e:
            print(f"# {now_iso()} [usb_otg] disconnected: {e}. Retry in {retry}s")
            time.sleep(retry)
        finally:
            if proc is not None:
                try:
                    proc.terminate()
                    proc.wait(timeout=3)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass

        if stop_event is not None and stop_event.is_set():
            return
