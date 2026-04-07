"""
Alert monitor — daemon thread that watches GNSS state and fires alerts.

Alerts are sent via:
- Termux:API push notifications (termux-notification)
- Termux:API vibration (termux-vibrate)
- Browser Web Audio API (events queued for SSE pickup)
"""

import logging
import threading
import time
from typing import List, Optional

from modules import settings as cfg
from modules import termux_bridge as termux
from modules.state import STATE

logger = logging.getLogger(__name__)

# Cooldown in seconds for the same alert type (default; overridden by settings)
_DEFAULT_COOLDOWN = 60


class AlertMonitor:
    """Singleton alert monitor running as a daemon thread."""

    def __init__(self):
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._prev_quality: Optional[int] = None  # track Q1/Q2/Q5 transitions
        self._prev_connected = True
        self._last_tpv_time: float = time.time()
        self._cooldowns: dict = {}  # alert_key -> last_fire_time
        self._pending_audio: List[str] = []
        self._audio_lock = threading.Lock()
        self._settings: dict = {}

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def start(self):
        """Start the monitor thread. Called from app.py main()."""
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._settings = cfg.load_settings()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="alert-monitor")
        self._thread.start()
        logger.info("[alerts] monitor avviato")

    def stop(self):
        """Stop the monitor thread."""
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)

    def reload_settings(self):
        """Reload alert settings from cfg. Called after settings save."""
        self._settings = cfg.load_settings()

    def notify_point_measured(self, point_name: str, hacc_mm: Optional[float] = None):
        """Called from survey save route to trigger point feedback."""
        hacc_str = f" — hAcc {hacc_mm/1000:.3f} m" if hacc_mm is not None else ""
        self._fire(
            alert_key="point_measured",
            title="📍 Punto registrato",
            content=f"Punto {point_name} registrato{hacc_str}",
            priority="default",
            vibrate_ms=150,
            audio_kind="confirm",
        )

    def pop_pending_audio(self) -> List[str]:
        """Return and clear pending audio events. Called from SSE /events endpoint."""
        with self._audio_lock:
            items = list(self._pending_audio)
            self._pending_audio.clear()
        return items

    def is_running(self) -> bool:
        """Return True if the monitor thread is alive."""
        return bool(self._thread and self._thread.is_alive())

    def queue_test_audio(self, kind: str = "success"):
        """Queue a test audio event for browser pickup."""
        with self._audio_lock:
            self._pending_audio.append(kind)

    # ------------------------------------------------------------------ #
    # Internal helpers                                                     #
    # ------------------------------------------------------------------ #

    def _s(self, key, default=None):
        """Get setting value from cached settings dict."""
        return self._settings.get(key, default)

    def _loop(self):
        """Main loop: check state every 1s, fire alerts on transitions."""
        while not self._stop.is_set():
            try:
                self._check()
            except Exception as exc:
                logger.debug("[alerts] _check error: %s", exc)
            self._stop.wait(1.0)

    def _check(self):
        """Evaluate all alert conditions against current STATE."""
        if not self._s("alerts_enabled", True):
            return

        snap = STATE.snapshot()
        tpv = snap.get("TPV", {})
        hp = snap.get("HPPOSLLH", {})

        # ── Detect TPV update (connection monitoring) ──────────────────
        tpv_time_str = tpv.get("time")
        if tpv_time_str:
            self._last_tpv_time = time.time()

        age = time.time() - self._last_tpv_time

        # ── connection_lost ────────────────────────────────────────────
        if self._s("alert_connection_lost", True):
            is_connected = age < 10.0
            if self._prev_connected and not is_connected:
                self._fire(
                    alert_key="connection_lost",
                    title="🔴 Connessione RTKino persa",
                    content="Nessun aggiornamento GNSS da più di 10 secondi",
                    priority="max",
                    vibrate_ms=500,
                    audio_kind="error",
                )
            self._prev_connected = is_connected

        # ── Quality transitions ────────────────────────────────────────
        quality = tpv.get("quality")  # numeric quality flag from ubx_parser
        # Fallback to rtk string if quality not present
        if quality is None:
            rtk = tpv.get("rtk", "")
            if rtk in ("RTK fixed", "likely fixed"):
                quality = 1
            elif rtk == "RTK float":
                quality = 2
            elif rtk:
                quality = 5
            else:
                quality = None

        if quality is not None:
            prev_q = self._prev_quality

            # fix_lost: was Q1, now not Q1
            if self._s("alert_fix_lost", True):
                if prev_q == 1 and quality != 1:
                    self._fire(
                        alert_key="fix_lost",
                        title="⚠️ Fix RTK perso",
                        content="Qualità RTK degradata (float/single/nessuna)",
                        priority="high",
                        vibrate_ms=500,
                        audio_kind="error",
                    )

            # fix_recovered: was not Q1, now Q1
            if self._s("alert_fix_recovered", True):
                if prev_q is not None and prev_q != 1 and quality == 1:
                    self._fire(
                        alert_key="fix_recovered",
                        title="✅ Fix RTK recuperato",
                        content="Fix RTK ripristinato",
                        priority="default",
                        vibrate_ms=200,
                        audio_kind="success",
                    )

            self._prev_quality = quality

        # ── hacc_degraded: Q1 fix but hAcc > threshold ─────────────────
        if self._s("alert_hacc_degraded", True) and quality == 1:
            max_hacc = self._s("max_hacc", 0.05)
            hacc = hp.get("hAcc") or tpv.get("hAcc")
            if hacc is not None and hacc > max_hacc:
                self._fire(
                    alert_key="hacc_degraded",
                    title="⚠️ Precisione degradata",
                    content=f"hAcc {hacc:.3f} m supera la soglia {max_hacc:.3f} m",
                    priority="default",
                    vibrate_ms=200,
                    audio_kind="warning",
                )

        # ── rtcm_stale ─────────────────────────────────────────────────
        if self._s("alert_rtcm_stale", True):
            threshold = self._s("alert_rtcm_stale_threshold", 30)
            rtcm_age = tpv.get("rtcmAge")
            if rtcm_age is None:
                rtcm_age = snap.get("RELPOS", {}).get("rtcmAge")
            if rtcm_age is not None and rtcm_age > threshold:
                self._fire(
                    alert_key="rtcm_stale",
                    title="⚠️ Correzioni RTCM ritardate",
                    content=f"Age correzioni RTCM: {rtcm_age:.0f}s (soglia: {threshold}s)",
                    priority="high",
                    vibrate_ms=300,
                    audio_kind="warning",
                )

    def _fire(
        self,
        alert_key: str,
        title: str,
        content: str,
        priority: str,
        vibrate_ms: int,
        audio_kind: str,
    ):
        """Fire an alert if enabled and not in cooldown."""
        # 1. Master toggle
        if not self._s("alerts_enabled", True):
            return

        # 2. Individual toggle
        toggle_key = f"alert_{alert_key}"
        if not self._s(toggle_key, True):
            return

        # 3. Cooldown
        cooldown = self._s("alerts_cooldown", _DEFAULT_COOLDOWN)
        now = time.time()
        last = self._cooldowns.get(alert_key, 0)
        if now - last < cooldown:
            return

        self._cooldowns[alert_key] = now

        # 4. Send notification
        if self._s("alerts_notify", True):
            try:
                termux.notify(title=title, content=content, priority=priority, vibrate=False)
            except Exception as exc:
                logger.debug("[alerts] notify error: %s", exc)

        # 5. Vibrate
        do_vibrate = self._s("alerts_vibrate", True)
        if alert_key == "point_measured":
            do_vibrate = self._s("alert_point_vibrate", True)
        if do_vibrate:
            try:
                termux.vibrate(duration_ms=vibrate_ms)
            except Exception as exc:
                logger.debug("[alerts] vibrate error: %s", exc)

        # 6. Queue browser audio
        do_audio = self._s("alerts_audio", True)
        if alert_key == "point_measured":
            do_audio = self._s("alert_point_audio", True)
        if do_audio:
            with self._audio_lock:
                self._pending_audio.append(audio_kind)

        logger.debug("[alerts] fired %s: %s", alert_key, title)


# Singleton
ALERTS = AlertMonitor()
