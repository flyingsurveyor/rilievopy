"""
Termux:API wrapper — thin Python bridge to termux-notification and termux-vibrate.

All functions fail silently (return False) when Termux:API is not installed.

Note: sensor access (termux-sensor) has been removed. The digital level (IMU)
now uses the browser DeviceOrientation API directly, which works on any mobile
browser and also when the server runs on Raspberry Pi.
"""

import logging
import shutil
import subprocess

logger = logging.getLogger(__name__)


def is_available() -> bool:
    """Check if termux-notification binary exists on PATH."""
    return shutil.which("termux-notification") is not None


def notify(
    title: str,
    content: str,
    priority: str = "default",
    vibrate: bool = True,
    id: str = "rilievopy",
) -> bool:
    """Send a push notification via termux-notification.

    priority: "low", "default", "high", "max"
    id: notification ID for replacing/updating
    Returns True on success, False on error or Termux:API not available.
    """
    try:
        if not is_available():
            return False
        cmd = [
            "termux-notification",
            "--title", title,
            "--content", content,
            "--priority", priority,
            "--id", str(id),
        ]
        if vibrate:
            cmd.append("--vibrate")
        result = subprocess.run(cmd, timeout=5, capture_output=True)
        return result.returncode == 0
    except Exception as e:
        logger.debug("[termux] notify error: %s", e)
        return False


def vibrate(duration_ms: int = 200) -> bool:
    """Vibrate the device via termux-vibrate.

    Returns True on success, False on error.
    """
    try:
        if shutil.which("termux-vibrate") is None:
            return False
        result = subprocess.run(
            ["termux-vibrate", "-d", str(duration_ms)],
            timeout=5,
            capture_output=True,
        )
        return result.returncode == 0
    except Exception as e:
        logger.debug("[termux] vibrate error: %s", e)
        return False
