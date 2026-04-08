"""
IMU Monitor — game_rotation_vector via termux-sensor.
Sessione-scoped: la calibrazione è in memoria, non persistita.

Il sensore game_rotation_vector usa accelerometro + giroscopio (NO magnetometro),
più affidabile vicino a strutture metalliche e paline.

Funziona solo su Termux (Android). Su Raspberry Pi degrada silenziosamente.
"""

import collections
import logging
import math
import threading
import time
from typing import Optional

from modules import settings as cfg
from modules import termux_bridge as termux
from modules.state import STATE

logger = logging.getLogger(__name__)

# Finestra mobile per calcolo stabilità (campioni)
_WINDOW_SIZE = 10


def _quat_angle_deg(q1: list, q2: list) -> float:
    """
    Calcola l'angolo di rotazione tra due quaternioni unitari in gradi.

    Args:
        q1: Quaternione unitario [x, y, z, w].
        q2: Quaternione unitario [x, y, z, w].

    Returns:
        Angolo in gradi tra i due orientamenti (sempre >= 0).
    """
    dot = sum(a * b for a, b in zip(q1, q2))
    dot = max(-1.0, min(1.0, abs(dot)))
    return math.degrees(2.0 * math.acos(dot))


class ImuMonitor:
    """
    Monitor IMU basato su game_rotation_vector.

    - Thread daemon a ~10Hz che legge il sensore continuo.
    - calibrate() salva il quaternione corrente come baseline verticale.
    - get_tilt_deg() restituisce l'angolo corrente vs baseline (None se non calibrato).
    - is_stable() controlla la varianza della finestra mobile.
    - set_sampling_active(True/False) attiva il monitoraggio durante la media GNSS.
    - Singleton IMU = ImuMonitor() — usato da routes/surveys.py e routes/imu.py.
    """

    def __init__(self):
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._window: collections.deque = collections.deque(maxlen=_WINDOW_SIZE)
        self._baseline: Optional[list] = None  # [x, y, z, w] calibrated quaternion
        self._last_quat: Optional[list] = None
        self._sampling_active = False
        self._was_unstable = False             # reset at start of each media
        self._tilt_max_during_sampling: Optional[float] = None

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def start(self):
        """Start the monitor thread. No-op (silent) if termux-sensor unavailable."""
        if self._thread and self._thread.is_alive():
            return
        if not cfg.load_settings().get("imu_enabled", True):
            logger.info("[imu] IMU disabilitato dalle impostazioni")
            return
        if not termux.is_sensor_available():
            logger.info("[imu] termux-sensor non disponibile — IMU disabilitato")
            STATE.patch("IMU", available=False)
            return
        STATE.patch("IMU", available=True)
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="imu-monitor"
        )
        self._thread.start()
        logger.info("[imu] monitor IMU avviato")

    def stop(self):
        """Stop the monitor thread."""
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)

    def calibrate(self) -> bool:
        """
        Salva il quaternione corrente come baseline verticale.
        Chiamato quando l'operatore dichiara 'palina in bolla'.
        Ritorna True se la calibrazione ha avuto successo.
        """
        with self._lock:
            if self._last_quat is None:
                logger.warning("[imu] calibrate() senza campioni disponibili")
                return False
            # Media degli ultimi campioni disponibili in finestra
            if len(self._window) >= 3:
                n = len(self._window)
                avg = [sum(q[i] for q in self._window) / n for i in range(4)]
                # Normalizza il quaternione medio
                norm = math.sqrt(sum(v * v for v in avg))
                if norm > 0:
                    avg = [v / norm for v in avg]
                self._baseline = avg
            else:
                self._baseline = list(self._last_quat)
        STATE.patch("IMU", calibrated=True, tilt_deg=0.0)
        logger.info("[imu] calibrato: %s", self._baseline)
        return True

    def is_calibrated(self) -> bool:
        """Return True if baseline has been set."""
        with self._lock:
            return self._baseline is not None

    def get_tilt_deg(self) -> Optional[float]:
        """Return tilt angle in degrees vs calibrated baseline, or None."""
        with self._lock:
            if self._baseline is None or self._last_quat is None:
                return None
            return _quat_angle_deg(self._baseline, self._last_quat)

    def is_stable(self, window: int = _WINDOW_SIZE) -> bool:
        """
        Return True if the pole was stable in the last N quaternion samples.
        Uses max angle between consecutive samples in the window.
        """
        threshold = cfg.load_settings().get("imu_stability_threshold_deg", 0.8)
        with self._lock:
            samples = list(self._window)
        if len(samples) < 2:
            return True
        last_n = samples[-window:] if len(samples) >= window else samples
        if len(last_n) < 2:
            return True
        angles = [_quat_angle_deg(last_n[i], last_n[i + 1]) for i in range(len(last_n) - 1)]
        return max(angles) <= threshold

    def set_sampling_active(self, active: bool):
        """
        Attivato dalla route survey prima/dopo il loop di sampling GNSS.
        set_sampling_active(True) resetta i flag was_unstable / tilt_max.
        """
        with self._lock:
            if active and not self._sampling_active:
                # Reset per ogni nuova media
                self._was_unstable = False
                self._tilt_max_during_sampling = None
            self._sampling_active = active
        STATE.patch("IMU", sampling_active=active)

    def get_status(self) -> dict:
        """Return IMU status dict for /api/imu/status."""
        with self._lock:
            tilt = (
                _quat_angle_deg(self._baseline, self._last_quat)
                if self._baseline is not None and self._last_quat is not None
                else None
            )
            return {
                "available": termux.is_sensor_available(),
                "calibrated": self._baseline is not None,
                "tilt_deg": round(tilt, 2) if tilt is not None else None,
                "stable": self._is_stable_unlocked(),
                "sampling_active": self._sampling_active,
                "was_unstable": self._was_unstable,
                "tilt_max_during_sampling": (
                    round(self._tilt_max_during_sampling, 2)
                    if self._tilt_max_during_sampling is not None
                    else None
                ),
            }

    # ------------------------------------------------------------------ #
    # Internal                                                             #
    # ------------------------------------------------------------------ #

    def _is_stable_unlocked(self) -> bool:
        """is_stable() without acquiring lock (caller must hold lock)."""
        threshold = cfg.load_settings().get("imu_stability_threshold_deg", 0.8)
        samples = list(self._window)
        if len(samples) < 2:
            return True
        angles = [_quat_angle_deg(samples[i], samples[i + 1]) for i in range(len(samples) - 1)]
        return max(angles) <= threshold
    def _loop(self):
        """Main loop: sample game_rotation_vector at ~10Hz."""
        hz = cfg.load_settings().get("imu_sampling_hz", 10)
        interval = 1.0 / max(1, hz)
        while not self._stop.is_set():
            sample = termux.read_game_rotation_vector()
            if sample is not None:
                with self._lock:
                    self._window.append(sample)
                    self._last_quat = sample
                    self._update_state_unlocked()
            self._stop.wait(interval)

    def _update_state_unlocked(self):
        """Update STATE["IMU"] — called with self._lock held."""
        tilt = (
            _quat_angle_deg(self._baseline, self._last_quat)
            if self._baseline is not None and self._last_quat is not None
            else None
        )
        stable = self._is_stable_unlocked()

        # During sampling: track max tilt and unstable flag
        if self._sampling_active:
            if not stable:
                self._was_unstable = True
            if tilt is not None:
                if self._tilt_max_during_sampling is None or tilt > self._tilt_max_during_sampling:
                    self._tilt_max_during_sampling = tilt

        STATE.patch(
            "IMU",
            available=True,
            calibrated=self._baseline is not None,
            tilt_deg=round(tilt, 2) if tilt is not None else None,
            stable=stable,
            sampling_active=self._sampling_active,
        )


# Singleton
IMU = ImuMonitor()
