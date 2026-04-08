"""
Termux:API wrapper — thin Python bridge to termux-notification and termux-vibrate.

All functions fail silently (return False) when Termux:API is not installed.
"""

import json
import logging
import shutil
import subprocess
from typing import Optional

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


def is_sensor_available() -> bool:
    """Check if termux-sensor binary exists on PATH."""
    return shutil.which("termux-sensor") is not None


# Preferred rotation sensors in priority order
_ROTATION_SENSOR_PRIORITY = [
    "Game Rotation Vector",
    "Rotation Vector",
    "Game Rotation Vector -Wakeup Secondary",
    "Rotation Vector -Wakeup Secondary",
]


def list_sensors() -> list:
    """
    Return the list of sensor names available via termux-sensor -l.
    Returns an empty list if termux-sensor is unavailable or fails.
    """
    try:
        if not is_sensor_available():
            return []
        result = subprocess.run(
            ["termux-sensor", "-l"],
            timeout=5,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            logger.debug("[termux] list_sensors returncode=%d stderr=%s",
                         result.returncode, result.stderr[:200])
            return []
        data = json.loads(result.stdout)
        sensors = data.get("sensors", [])
        if isinstance(sensors, list):
            return [str(s) for s in sensors]
        return []
    except Exception as e:
        logger.debug("[termux] list_sensors error: %s", e)
        return []


def pick_best_rotation_sensor(sensors: list) -> Optional[str]:
    """
    Choose the best rotation sensor from the available sensor list.
    Priority: Game Rotation Vector > Rotation Vector > wakeup variants.
    Returns None if none of the preferred sensors is available.
    """
    sensor_set = set(sensors)
    for name in _ROTATION_SENSOR_PRIORITY:
        if name in sensor_set:
            return name
    return None


def read_game_rotation_vector() -> Optional[list]:
    """
    Read one sample from the best available rotation sensor via termux-sensor.
    Uses imu_sensor_name from settings if set; otherwise autodetects.
    Returns [x, y, z, w] quaternion or None if not available.
    """
    try:
        if not is_sensor_available():
            return None

        # Determine sensor name: prefer settings, fallback to autodetect
        from modules import settings as cfg
        sensor_name: Optional[str] = cfg.load_settings().get("imu_sensor_name", "") or None
        if not sensor_name:
            available = list_sensors()
            sensor_name = pick_best_rotation_sensor(available)
        if not sensor_name:
            logger.debug("[termux] no rotation sensor found")
            return None

        result = subprocess.run(
            ["termux-sensor", "-s", sensor_name, "-n", "1"],
            timeout=3,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            logger.debug("[termux] sensor '%s' returncode=%d stdout=%s stderr=%s",
                         sensor_name, result.returncode,
                         result.stdout[:100], result.stderr[:100])
            return None
        data = json.loads(result.stdout)
        # Key in the JSON response is the exact sensor name
        sensor_data = data.get(sensor_name, {})
        values = sensor_data.get("values")
        if isinstance(values, list) and len(values) >= 4:
            return [float(v) for v in values[:4]]
        return None
    except Exception as e:
        logger.debug("[termux] sensor error: %s", e)
        return None


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
