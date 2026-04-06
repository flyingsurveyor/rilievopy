# RilievoPY — RTK/PPK GNSS Surveying Suite

<p align="center">
  <b>RTK real-time surveying + PPK post-processing in a single web app</b><br>
  Runs on Android (Termux) and Raspberry Pi. No internet required in the field.
</p>

[![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL%203.0-blue.svg)](LICENSE)
[![Platform: Android](https://img.shields.io/badge/Platform-Android%20(Termux)-green.svg)](https://termux.dev/)
[![Platform: RPi](https://img.shields.io/badge/Platform-Raspberry%20Pi-red.svg)](https://www.raspberrypi.com/)
[![Python: 3.10+](https://img.shields.io/badge/Python-3.10%2B-yellow.svg)](https://www.python.org/)
[![Companion: RTKino](https://img.shields.io/badge/Companion-RTKino-orange.svg)](https://github.com/flyingsurveyor/RTKino)

**RilievoPY** is an open-source web application for RTK real-time GNSS surveying and PPK post-processing. It runs entirely on a smartphone (via Termux) or a Raspberry Pi, with no cloud dependency, no subscription, and no proprietary software. The interface is accessible from any browser on the same network.

Designed and field-tested as the software companion to [RTKino](https://github.com/flyingsurveyor/RTKino) — a low-cost open-source RTK/PPK GNSS receiver based on ESP32-S3 and u-blox ZED-F9P.

## Note on Development

RilievoPY was built from real-world surveying needs, not from a software-first approach.

It has been shaped through practical use in the field, with AI-assisted tools used to support development and speed up implementation.

The focus of this project is function, reliability, and real usability in difficult field conditions.

At the moment the interface is still in Italian language and evolving in its functionalities. I'm working on a i18n UI.

---

## Features

### RTK Dashboard

- **Live GNSS status** with fix type (No Fix / Float / Fixed), position, accuracy, and DOP values
- **Real-time satellite count** and signal quality monitoring
- **SSE-based updates** — no page refresh needed, data streams continuously
- **RTKino integration** — connects directly to RTKino's TCP streamer (port 7856) over WiFi
- **TCP Relay** — forwards the incoming NMEA/UBX stream to other clients on the same LAN (e.g. GNSS Master, external loggers)
- **mDNS** for easy access via `http://rilievopy.local/` without knowing the IP address

### Survey Management

- **Create, manage, and export surveys** with full point metadata
- **Timed point measurement** with configurable duration and sampling interval
- **Robust averaging** with three selectable modes: sigma-clipping, trimmed mean, median
- **RTK quality gate** with configurable thresholds for horizontal accuracy, PDOP, and satellite count
- **Full GNSS snapshot per point**: coordinates, accuracy, DOP, covariance, fix type
- **Point codes** — customizable categories for field annotation (compatible with Italian surveying conventions)
- **Export formats**: GeoJSON, CSV, DXF, GeoPackage (GPKG), Excel (XLSX)
- **Import**: CSV, TXT, GeoJSON
- **Backup & Restore** of surveys and PPK configuration as ZIP archive

### COGO Tools EXPERIMENTAL!!

Complete set of coordinate geometry calculations:

| Tool | Description |
|------|-------------|
| **Trilateration** | Compute unknown point from distances to known points |
| **Intersection** | Compute point from bearings or distance+bearing |
| **Polar** | Compute point from known station, bearing and distance |
| **Bearing & Distance** | Compute bearing and distance between two points |
| **Offset** | Compute offset point from line |
| **Perpendicular** | Drop perpendicular from point to line |
| **Alignment** | Stake points along a polyline at given intervals |
| **Helmert (2D)** | Transform between coordinate systems (4-parameter similarity) |

### Stakeout (Setting Out)

- **Navigate to design points** with real-time guidance
- **Live distance and azimuth** to target, updated via SSE
- **Import target points** from CSV, GeoJSON, or existing surveys
- **Point list management** with survey-based organization

### Topographic Tools EXPERIMENTAL!!

- **CAD Web** — 2D topographic drawing with snap, layers, dimensions, DXF export
- **DTM Analysis** — TIN Delaunay triangulation, contour lines, volume computation, cross-section profiles
- **Traverses** — open and closed traverse computation, leveling, area division

### PPK Post-processing

Full PPK workflow integrated in the web UI, powered by [RTKLIB (RTKLIBExplorer demo5)](https://github.com/rtklibexplorer/RTKLIB):

| Page | Description |
|------|-------------|
| **PPK Home** | Dashboard with file counts and RTKLIB tool status |
| **convbin** | Convert raw GNSS logs (UBX, RTCM) to RINEX format |
| **RINEX QC** | Observation quality check: header, satellite list, SNR plots |
| **PPK Processing** | Full `rnx2rtkp` configuration editor and processing runner |
| **Position Viewer** | rtkplot-style charts: position, residuals, satellite availability |
| **File Explorer** | Manage uploads, RINEX, results, .pos solutions, and RTKLIB configs |

> PPK features require `convbin` and `rnx2rtkp` compiled from RTKLIB. The installer handles this automatically — see [Installation](#installation).

### Workspace Management

- **Configurable workspace directory** — store surveys and PPK data anywhere, including shared storage visible to Android File Manager (`~/storage/shared/RilievoPY`)
- **Copy / migrate** data between workspaces without data loss
- **Backup and restore** surveys + PPK configs as a single ZIP file with manifest

---

## Companion Hardware: RTKino

RilievoPY is designed to work with **[RTKino](https://github.com/flyingsurveyor/RTKino)** — an open-source RTK/PPK GNSS receiver built on ESP32-S3 and u-blox ZED-F9P.

| | RTKino | RilievoPY |
|---|---|---|
| **Role** | GNSS receiver + corrections | Surveying app + post-processing |
| **Hardware** | ESP32-S3 + ZED-F9P | Android phone / Raspberry Pi |
| **Access** | `http://rtkino.local/` | `http://rilievopy.local/` |
| **Connection** | TCP streamer on port 7856 | Connects to RTKino via WiFi |

Together, RTKino + RilievoPY form a complete, low-cost, open-source RTK surveying system.

---

## Installation

### Android (Termux) — one command

```bash
pkg install -y git && git clone https://github.com/flyingsurveyor/rilievopy.git && cd rilievopy && bash install.sh
```

### Raspberry Pi

```bash
git clone https://github.com/flyingsurveyor/rilievopy.git
cd rilievopy
chmod +x install.sh
./install.sh
```

The installer is interactive and handles everything:

| Step | What happens |
|------|-------------|
| Platform detection | Termux, Raspberry Pi OS, Debian, Arch, macOS, WSL |
| RTKLIB tools | Optionally clones and compiles `convbin` + `rnx2rtkp` from RTKLIBExplorer demo5 |
| Python dependencies | Installs Flask, pyubx2, waitress, openpyxl, zeroconf (+ optional geopandas) |
| Data directories | Creates `surveys/`, `data/uploads/`, `data/rinex/`, `data/results/`, etc. |
| Termux extras | Installs bleak (BLE); Termux:API and Termux:Widget are **optional** — the app works without them |
| systemd service | (Linux only) Optionally installs auto-start on boot |

```
./install.sh                # Interactive (recommended)
./install.sh --skip-build   # Skip RTKLIB compilation
./install.sh --force-build  # Rebuild RTKLIB without asking
```

### Termux:Widget (Android) — optional

Termux:Widget and Termux:API are **not required** to use RilievoPY. The app runs and is fully usable via the browser at `http://127.0.0.1:8000` (or `http://localhost:8000`) without them.

**Starting without a widget:**
```bash
cd ~/rilievopy && python app.py
```
Then open `http://127.0.0.1:8000` in any browser on the device.

**One-tap start with Termux:Widget (optional convenience):**

1. Install **Termux:Widget** from [F-Droid](https://f-droid.org/packages/com.termux.widget/)
2. Long-press an empty area on the home screen → Widget → Termux
3. Add **`rilievo_avvia`** (start) and **`rilievo_ferma`** (stop)

The start widget checks if the app is already running — if yes, it opens the browser directly without restarting.

**Push notifications with Termux:API (optional):**

Install **Termux:API** from [F-Droid](https://f-droid.org/packages/com.termux.api/) to enable Android push notifications (RTK fix loss alerts), vibration, and text-to-speech. The app works fully without it.

### systemd Service (Raspberry Pi)

The installer offers to install a systemd service for automatic start on boot:

```bash
sudo systemctl status rilievo
sudo systemctl restart rilievo
journalctl -u rilievo -f
```

---

## Getting Started

### Android (Termux) — step by step

1. **Install Termux from F-Droid** (not Google Play — the Play Store version is outdated):
   `https://f-droid.org/packages/com.termux/`

2. **Paste the one-liner** above in Termux. The installer will guide you through the setup.

3. **During installation:**
   - **RTKLIB tools**: needed only for PPK. Choose **Skip** for RTK-only field use.
   - **geopandas**: optional, skip on low-RAM devices.

4. **Start the app** — either:
   - From Termux: `cd ~/rilievopy && python app.py`
   - Or with the widget: install **Termux:Widget** (optional) and tap **`rilievo_avvia`**

5. **Open the browser** at `http://127.0.0.1:8000` (or `http://localhost:8000`).

6. **First time only:** go to `/rtkino` and enter the IP address of your RTKino receiver.

### Raspberry Pi Zero 2W — step by step

1. Start from a fresh **Raspberry Pi OS Lite** (64-bit).

2. Clone and install:
   ```bash
   git clone https://github.com/flyingsurveyor/rilievopy.git
   cd rilievopy
   chmod +x install.sh
   ./install.sh
   ```

3. During installation, choose **Build** for RTKLIB if you need PPK (takes ~10–15 min on RPi Zero 2W). Choose **Yes** for systemd service.

4. Find the Pi's IP:
   ```bash
   hostname -I
   ```

5. Open `http://<rpi-ip>:8000` or `http://rilievopy.local/` from any device on the same network.

6. **First time only:** go to `/rtkino` and configure the RTKino connection.

---

## Web Interface

### RTK

| Route | Description |
|-------|-------------|
| `/` | Dashboard — live GNSS status, fix type, DOP, satellites |
| `/surveys` | Survey list — create, view, export surveys |
| `/cogo` | COGO tools hub |
| `/stakeout` | Stakeout — navigate to target points |
| `/compare` | Compare two survey points |
| `/import` | Import CSV / TXT / GeoJSON |
| `/rtkino` | RTKino connection and configuration |
| `/settings` | App settings, workspace, mDNS, backup/restore |

### Topographic Tools

| Route | Description |
|-------|-------------|
| `/cad` | CAD web — 2D topographic drawing |
| `/dtm` | DTM analysis — TIN, contours, volumes, profiles |
| `/traverses` | Traverse computation, leveling, area division |

### PPK

| Route | Description |
|-------|-------------|
| `/ppk/home` | PPK dashboard |
| `/convbin` | Raw GNSS → RINEX conversion |
| `/rinex` | RINEX observation QC |
| `/ppk` | PPK processing (rnx2rtkp) |
| `/posview` | Position viewer |
| `/files` | File explorer |

---

## mDNS Access

RilievoPY supports **mDNS** for easy access on LAN without knowing the IP address:

- Default: `http://rilievopy.local/` (port 8000)
- Configurable from `/settings` → mDNS Hostname
- Works on Termux and Raspberry Pi — accessible from any device on the same WiFi network

**Pair with RTKino:**
- `http://rtkino.local/` → GNSS receiver
- `http://rilievopy.local/` → surveying app

---

## Project Structure

```
rilievopy/
├── app.py                          # Entry point (Flask + Waitress)
├── install.sh                      # Interactive installer
├── requirements.txt                # flask, pyubx2, waitress, openpyxl, zeroconf
│
├── modules/                        # Business logic (no Flask deps)
│   ├── utils.py                    # Conversions, robust averaging
│   ├── geodesy.py                  # WGS84, ECEF ↔ ENU ↔ Geodetic
│   ├── cogo.py                     # COGO: trilateration, intersections, Helmert
│   ├── dtm.py                      # DTM: TIN Delaunay, contours, volumes, profiles
│   ├── traverses.py                # Traverses: open/closed, leveling, area division
│   ├── state.py                    # Shared state, BytePipe, TCPRelay
│   ├── ubx_parser.py               # UBX parser + upstream TCP
│   ├── connection.py               # GNSS connection manager
│   ├── settings.py                 # Persistent settings (JSON)
│   ├── survey.py                   # Survey CRUD (GeoJSON)
│   ├── exports.py                  # DXF, GeoPackage, XLSX, CSV
│   ├── mdns_service.py             # mDNS via zeroconf
│   ├── ppk_config.py               # PPK paths + binary discovery
│   ├── convbin.py                  # convbin wrapper
│   ├── rnx2rtkp.py                 # rnx2rtkp wrapper
│   ├── rinex_parser.py             # RINEX observation parser
│   ├── pos_parser.py               # .pos solution parser
│   ├── conf_manager.py             # RTKLIB .conf editor
│   └── workspace.py                # Workspace management
│
├── routes/                         # Flask blueprints
│   ├── dashboard.py                # / + /events (SSE)
│   ├── settings.py                 # /settings + workspace + mDNS APIs
│   ├── surveys.py                  # /surveys, exports
│   ├── cogo.py                     # /cogo/*
│   ├── stakeout.py                 # /stakeout
│   ├── compare.py                  # /compare
│   ├── import_export.py            # /import
│   ├── rtkino.py                   # /rtkino (RTKino integration)
│   ├── topo_tools.py               # /cad, /dtm, /traverses
│   └── ppk.py                      # All PPK routes
│
├── templates/                      # Jinja2 HTML templates
├── static/                         # CSS, JS assets
├── conf/                           # Default RTKLIB configs
├── tools/                          # RTKLIB binaries (compiled by installer)
├── scripts/                        # Termux:Widget scripts
│   ├── termux_widget_start.sh      # Start app + open browser
│   └── termux_widget_stop.sh       # Stop app
├── data/                           # PPK working data (gitignored)
└── surveys/                        # RTK surveys + settings (gitignored)
```

---

## Dependencies

| Package | Role | Required |
|---------|------|----------|
| `flask>=3.0` | Web framework | ✅ |
| `pyubx2` | UBX protocol parser | ✅ |
| `waitress>=3.0` | Production WSGI server | ✅ |
| `openpyxl` | Excel export | ✅ |
| `zeroconf` | mDNS service | ✅ |
| `bleak` | BLE connectivity (Termux) | Optional |
| `convbin` | Raw GNSS → RINEX (RTKLIB) | Optional (PPK) |
| `rnx2rtkp` | PPK processing (RTKLIB) | Optional (PPK) |

---

## Daily Use

### Android

- **Without widget:** run `cd ~/rilievopy && python app.py` in Termux, then open `http://127.0.0.1:8000` in any browser.
- **With Termux:Widget (optional):** tap **`rilievo_avvia`** to start. The app checks if Flask is already running — if yes, opens the browser directly.
- Tap **`rilievo_ferma`** widget (or Ctrl+C in Termux) to stop.
- Log file: `rilievopy/rilievo.log`

### Raspberry Pi

- If systemd service is installed: app starts automatically on every boot.
- Check status: `sudo systemctl status rilievo`
- View live logs: `journalctl -u rilievo -f`
- Access from any device on the network: `http://<rpi-ip>:8000` or `http://rilievopy.local/`

---

## Troubleshooting

| Problem | Likely Cause | Solution |
|---------|-------------|----------|
| App doesn't start | Missing dependency | Check `rilievo.log`, re-run `install.sh` |
| No GNSS data on dashboard | RTKino not configured or not reachable | Go to `/rtkino`, verify IP and that RTKino is on the same WiFi |
| PPK tools show "not found" | RTKLIB not compiled | Re-run `./install.sh --force-build` |
| `http://rilievopy.local/` not reachable | mDNS blocked by network | Use IP address directly; check `/settings` → mDNS |
| Export fails (GeoPackage) | geopandas not installed | Install: `pip install geopandas shapely`, or use the SQLite fallback |
| Low RAM on RPi Zero | OOM during RTKLIB build | Increase swap to 2 GB before building |

---

## Contributing

Contributions are welcome. Please open an issue before submitting a pull request for significant changes.

---

## License

This project is licensed under the **GNU Affero General Public License v3.0 (AGPL-3.0)**.

You are free to use, modify, and distribute this software. If you modify it and deploy it as a network service, you must release your source code under the same license.

See [LICENSE](LICENSE) for the full text.

---

## Disclaimer

RilievoPY is not a commercial product. It is provided as-is, without warranty of any kind. The user is solely responsible for validating all measurements and ensuring fitness for their intended application. Do not use this software for safety-critical or legally binding surveys without independent verification.

---

## Acknowledgments

- [Tim Everett / RTKLIBExplorer](https://rtklibexplorer.wordpress.com/) for maintaining and evolving RTKLIB
- [u-blox](https://www.u-blox.com/) for the ZED-F9P and its documentation
- [pyubx2](https://github.com/semuconsulting/pyubx2) by semuconsulting
- [Flask](https://flask.palletsprojects.com/) and the Pallets project
- [Termux](https://termux.dev/) for making Linux on Android a reality
- The open-source GNSS community

---

<p align="center">
  <b>Made with ❤️ for land surveying</b><br><br>
  <a href="https://github.com/flyingsurveyor">FlyingSurveyor</a> · Italy
</p>
