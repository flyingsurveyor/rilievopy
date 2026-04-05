"""
mDNS service manager per rilievopy.
Permette l'accesso via http://<hostname>.local/ su rete LAN.
Ispirato all'implementazione di RTKino (ESP32/ESPmDNS).
"""

import re
import socket
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Hostname di default
DEFAULT_HOSTNAME = "rilievopy"

# Istanze globali Zeroconf
_zeroconf = None
_service_info = None
_current_hostname: Optional[str] = None


def is_valid_hostname(hostname: str) -> bool:
    """
    Valida hostname mDNS (identico alla logica RTKino).
    - Solo lettere minuscole, numeri, trattino
    - Non può iniziare o finire con trattino
    - Lunghezza 1-32 caratteri
    """
    if not hostname or len(hostname) > 32:
        return False
    if hostname[0] == '-' or hostname[-1] == '-':
        return False
    return bool(re.match(r'^[a-z0-9-]+$', hostname))


def normalize_hostname(hostname: str) -> str:
    """
    Normalizza hostname: rimuove .local se presente, converte in lowercase.
    """
    h = hostname.strip().lower()
    if h.endswith('.local'):
        h = h[:-6]
    return h


def start_mdns(hostname: str, port: int = 8000) -> bool:
    """
    Avvia il servizio mDNS con l'hostname specificato.
    Identico concettualmente a applyMdnsHostname() di RTKino.

    Args:
        hostname: Nome host (senza .local)
        port: Porta HTTP del server Flask

    Returns:
        True se avviato con successo
    """
    global _zeroconf, _service_info, _current_hostname

    hostname = normalize_hostname(hostname)

    if not is_valid_hostname(hostname):
        logger.error(f"[mDNS] Hostname non valido: '{hostname}'")
        return False

    # Ferma il servizio precedente se esiste
    stop_mdns()

    try:
        from zeroconf import ServiceInfo, Zeroconf

        _zeroconf = Zeroconf()

        # Ottieni IP locale (usa connessione UDP per trovare l'IP LAN reale)
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(0)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()
        except Exception:
            local_ip = "127.0.0.1"

        # Crea ServiceInfo per HTTP (come RTKino: MDNS.addService("http", "tcp", 80))
        _service_info = ServiceInfo(
            "_http._tcp.local.",
            f"{hostname}._http._tcp.local.",
            addresses=[socket.inet_aton(local_ip)],
            port=port,
            properties={},
            server=f"{hostname}.local.",
        )

        # Registra il servizio
        _zeroconf.register_service(_service_info)
        _current_hostname = hostname

        logger.info(f"[mDNS] Avviato: http://{hostname}.local/ (porta {port})")
        print(f"# [mDNS] http://{hostname}.local/")
        return True

    except ImportError:
        logger.warning("[mDNS] Libreria zeroconf non disponibile — installa con: pip install zeroconf")
        return False
    except Exception as e:
        logger.error(f"[mDNS] Avvio fallito: {e}")
        _zeroconf = None
        _service_info = None
        _current_hostname = None
        return False


def stop_mdns():
    """Ferma il servizio mDNS."""
    global _zeroconf, _service_info, _current_hostname

    if _zeroconf and _service_info:
        try:
            _zeroconf.unregister_service(_service_info)
            _zeroconf.close()
            logger.info("[mDNS] Fermato")
        except Exception as e:
            logger.warning(f"[mDNS] Errore durante lo stop: {e}")

    _zeroconf = None
    _service_info = None
    _current_hostname = None


def get_current_hostname() -> Optional[str]:
    """Ritorna l'hostname mDNS attualmente attivo."""
    return _current_hostname
