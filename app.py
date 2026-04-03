#!/usr/bin/env python3
"""
RilievoPY — Unified RTK/PPK Suite
==================================
RTK real-time surveying + PPK post-processing in one app.

Starts immediately — configure via web UI at /rtkino (RTKino settings)
and /settings (general settings).
Settings are persisted in rilievo_settings.json (project root).
"""

import argparse
import os

from flask import Flask

from modules.utils import now_iso
from modules import settings as cfg
from modules import utils
from modules.connection import CONN
from modules.ppk_config import PPKConfig


# ---------- Flask App Factory ----------
def create_app():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    app = Flask(__name__,
                template_folder=os.path.join(base_dir, 'templates'),
                static_folder=os.path.join(base_dir, 'static'))

    app.config['MAX_CONTENT_LENGTH'] = PPKConfig.MAX_CONTENT_LENGTH

    # Ensure PPK data directories exist
    PPKConfig.ensure_dirs()

    # Register RTK blueprints
    from routes.dashboard import bp as dashboard_bp
    from routes.surveys import bp as surveys_bp
    from routes.cogo import bp as cogo_bp
    from routes.stakeout import bp as stakeout_bp
    from routes.compare import bp as compare_bp
    from routes.import_export import bp as import_bp
    from routes.settings import bp as settings_bp

    app.register_blueprint(dashboard_bp)
    app.register_blueprint(surveys_bp)
    app.register_blueprint(cogo_bp)
    app.register_blueprint(stakeout_bp)
    app.register_blueprint(compare_bp)
    app.register_blueprint(import_bp)
    app.register_blueprint(settings_bp)

    # Register PPK blueprint
    from routes.ppk import bp as ppk_bp
    app.register_blueprint(ppk_bp)

    # Register Topo Tools blueprint (DTM, traverses, CAD)
    from routes.topo_tools import bp as topo_bp
    app.register_blueprint(topo_bp)

    # Register RTKino integration blueprint
    from routes.rtkino import bp as rtkino_bp
    app.register_blueprint(rtkino_bp)

    return app


def apply_settings(s: dict):
    """Apply loaded settings to runtime modules."""
    utils.ROBUST_MODE = s.get("robust_mode", "sigma")
    utils.ROBUST_SIGMA = s.get("robust_sigma", 2.0)
    utils.ROBUST_TRIM_Q = s.get("robust_trim_q", 0.10)


def start_gnss_if_configured(s: dict):
    """Start GNSS connection using RTKino host and fixed TCP port 7856.

    RTKino-first: connection is always derived from rtkino_host + RTKINO_TCP_PORT.
    The legacy gnss_host/gnss_port fields are ignored when rtkino_host is set.
    """
    from modules.settings import RTKINO_TCP_PORT
    rtkino_host = s.get("rtkino_host", "")
    if rtkino_host and s.get("gnss_autoconnect", True):
        CONN.start(
            gnss_host=rtkino_host,
            gnss_port=RTKINO_TCP_PORT,
            relay_enabled=s.get("relay_enabled", False),
            relay_bind=s.get("relay_bind", "127.0.0.1"),
            relay_port=s.get("relay_port", 21100),
            retry=s.get("retry_interval", 3.0),
        )
        print(f"# {now_iso()} [app] RTKino TCP → {rtkino_host}:{RTKINO_TCP_PORT}")
    else:
        if not rtkino_host:
            print(f"# {now_iso()} [app] RTKino non configurato — vai su /rtkino per configurare l'IP")
        else:
            print(f"# {now_iso()} [app] autoconnect disabilitato — connetti manualmente da /rtkino")


# ---------- Main ----------
def main():
    ap = argparse.ArgumentParser(
        description="RilievoPY — RTK/PPK Suite"
    )
    ap.add_argument("--port", type=int, help="Override porta HTTP")
    ap.add_argument("--bind", help="Override bind address")
    args = ap.parse_args()

    # Load persisted settings
    s = cfg.load_settings()
    print(f"# {now_iso()} [app] settings caricati da {cfg.settings_path()}")

    # Apply runtime settings
    apply_settings(s)

    # HTTP bind/port
    http_bind = args.bind or s.get("http_bind", "0.0.0.0")
    http_port = args.port or s.get("http_port", 8000)

    # PPK tools status
    convbin_ok = os.path.isfile(PPKConfig.CONVBIN_BIN) and os.access(PPKConfig.CONVBIN_BIN, os.X_OK)
    rnx2rtkp_ok = os.path.isfile(PPKConfig.RNX2RTKP_BIN) and os.access(PPKConfig.RNX2RTKP_BIN, os.X_OK)
    if convbin_ok and rnx2rtkp_ok:
        print(f"# {now_iso()} [ppk] RTKLIB tools OK: convbin, rnx2rtkp")
    else:
        missing = []
        if not convbin_ok: missing.append("convbin")
        if not rnx2rtkp_ok: missing.append("rnx2rtkp")
        print(f"# {now_iso()} [ppk] RTKLIB tools mancanti: {', '.join(missing)} — PPK non disponibile")

    # Start GNSS connection in background
    start_gnss_if_configured(s)

    # Start RTKino polling if configured
    if s.get("rtkino_host") and s.get("rtkino_polling", False):
        from modules.rtkino_manager import RTKINO
        RTKINO.start_polling()
        print(f"# {now_iso()} [rtkino] polling avviato → {s.get('rtkino_host')}:{s.get('rtkino_port', 80)}")

    # Create Flask app
    app = create_app()
    print(f"# {now_iso()} [web] http://{http_bind}:{http_port}")
    print(f"# {now_iso()} [web] RTK: /  /surveys  /cogo  /stakeout")
    print(f"# {now_iso()} [web] PPK: /convbin  /rinex  /ppk  /posview")
    print(f"# {now_iso()} [web] Settings: /settings")

    # Use Waitress as WSGI server (production-ready, pure Python, works everywhere).
    # Falls back to Werkzeug dev server with a warning if Waitress is not installed.
    #
    # Key Waitress options:
    #   threads=8          — thread pool for concurrent requests
    #   channel_timeout=0  — REQUIRED: no idle timeout, keeps SSE connections
    #                        (/events, /stakeout/events) alive indefinitely
    #   cleanup_interval=30 — scan for dead connections every 30s
    try:
        from waitress import serve
        print(f"# {now_iso()} [web] server: waitress")
        serve(
            app,
            host=http_bind,
            port=http_port,
            threads=8,
            channel_timeout=0,
            cleanup_interval=30,
        )
    except ImportError:
        print(f"# {now_iso()} [web] server: werkzeug dev (installa waitress: pip install waitress)")
        app.run(host=http_bind, port=http_port, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
