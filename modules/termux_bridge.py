"""
Termux:API wrapper — thin Python bridge to termux-notification and termux-vibrate.

All functions fail silently (return False) when Termux:API is not installed.
"""

import json
import logging
import select
import shutil
import subprocess
import threading
from typing import Optional

from modules import settings as cfg

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


class SensorStream:
    """
    Processo termux-sensor persistente in modalità streaming (-d flag).
    Molto più efficiente di lanciare un subprocess per ogni campione (-n 1).
    Latenza reale: ~50ms invece di ~300ms per campione.
    """

    def __init__(self, sensor_name: str, delay_ms: int = 50):
        self._sensor_name = sensor_name
        self._delay_ms = delay_ms
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()

    def start(self) -> bool:
        """Avvia il processo streaming. Ritorna True se avviato con successo."""
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                return True  # già in esecuzione
            try:
                self._proc = subprocess.Popen(
                    ["termux-sensor", "-s", self._sensor_name, "-d", str(self._delay_ms)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                )
                return True
            except Exception as e:
                logger.debug("[SensorStream] start error: %s", e)
                self._proc = None
                return False

    def read(self, timeout: float = 0.5) -> Optional[list]:
        """
        Legge il prossimo campione dallo stream.
        Ritorna [x, y, z, w] quaternion o None se timeout/errore.
        """
        with self._lock:
            proc = self._proc

        if proc is None or proc.poll() is not None:
            return None

        try:
            ready, _, _ = select.select([proc.stdout], [], [], timeout)
            if not ready:
                return None
            line = proc.stdout.readline()
            if not line:
                return None
            data = json.loads(line.strip())
            sensor_data = data.get(self._sensor_name, {})
            values = sensor_data.get("values")
            if isinstance(values, list) and len(values) >= 4:
                return [float(v) for v in values[:4]]
            return None
        except Exception as e:
            logger.debug("[SensorStream] read error: %s", e)
            return None

    def close(self):
        """Termina il processo streaming."""
        with self._lock:
            proc = self._proc
            self._proc = None
        if proc is not None:
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    def is_alive(self) -> bool:
        """Ritorna True se il processo è in esecuzione."""
        with self._lock:
            return self._proc is not None and self._proc.poll() is None


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
