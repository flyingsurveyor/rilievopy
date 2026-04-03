"""
modules/rtkino_manager.py
Singleton leggero per l'integrazione con RTKino.

Responsabilità:
- get_api()        — restituisce un RTKinoAPI configurato
- combined_status() — stato connessione basato su rtkino_host configurato
"""

import logging

from modules import settings as cfg

logger = logging.getLogger(__name__)


class _RTKinoManager:
    """Singleton manager per l'integrazione RTKino."""

    # ── API HTTP helper ───────────────────────────────────────────────────────

    def get_api(self):
        """Restituisce un RTKinoAPI configurato con le impostazioni correnti."""
        from modules.rtkino_api import RTKinoAPI
        from modules.settings import RTKINO_WEBUI_PORT
        s = cfg.load_settings()
        host = s.get("rtkino_host", "")
        if not host:
            return None
        return RTKinoAPI(host=host, port=RTKINO_WEBUI_PORT, timeout=5.0)

    def combined_status(self) -> dict:
        """Stato combinato per l'API /api/rtkino/status."""
        from modules.settings import RTKINO_TCP_PORT, RTKINO_WEBUI_PORT
        s = cfg.load_settings()
        host = s.get("rtkino_host", "")
        conn_mode = "connected" if host else "disconnected"
        result = {
            "conn_mode": conn_mode,
            "host": host,
            "tcp_port": RTKINO_TCP_PORT,
            "api_port": RTKINO_WEBUI_PORT,
        }
        # Prova a leggere lo stato live da RTKino via HTTP
        if host:
            try:
                from modules.rtkino_api import RTKinoAPI
                api = RTKinoAPI(host=host, port=RTKINO_WEBUI_PORT, timeout=3.0)
                live = api.get_status()
                if live and isinstance(live, dict):
                    result["rtkino_live"] = live
            except Exception:
                pass
        return result


# ── Singleton globale ─────────────────────────────────────────────────────────

RTKINO = _RTKinoManager()
