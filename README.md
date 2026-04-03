# RilievoPY — Unified RTK/PPK Suite

RTK real-time surveying + PPK post-processing in a single app.

---

## Install (one command)

### Android (Termux)

```bash
pkg install -y git && git clone https://github.com/flyingsurveyor/rilievo_gnss.git && cd rilievo_gnss && bash install.sh
```

### Raspberry Pi

```bash
git clone https://github.com/flyingsurveyor/rilievo_gnss.git && cd rilievo_gnss && bash install.sh
```

---

## Getting Started

### Android (Termux) — step by step

1. **Install Termux from F-Droid** (not Google Play — the Play Store version is outdated and unsupported):
   `https://f-droid.org/packages/com.termux/`

2. **Open Termux** and paste the one-liner above. It will download the app and run the installer automatically.

3. **The installer will ask a few questions:**
   - **RTKLIB tools** (`convbin`, `rnx2rtkp`): these are needed only for PPK post-processing (replaying raw GNSS logs after the survey). For RTK-only field use, choose **Skip**.
   - **geopandas**: optional library for advanced GeoPackage export. Skip it on low-RAM devices.
   - **systemd service**: not applicable on Termux — skip this.

4. **Install Termux:Widget from F-Droid:**
   `https://f-droid.org/packages/com.termux.widget/`

5. **Add the widget to your home screen:** long-press an empty area on the home screen → Widget → Termux → select `rilievo_avvia`.

6. **Tap the widget** — the app starts in the background and the browser opens automatically at `http://127.0.0.1:8000`.

7. **First time only:** go to **Settings** (`/settings`) and enter the IP address and port of your GNSS receiver (the TCP server running on the rover or base station).

---

### Raspberry Pi Zero 2W — step by step

1. **Start from a fresh Raspberry Pi OS Lite** (64-bit recommended).

2. **Make sure git is installed:**
   ```bash
   sudo apt install -y git
   ```

3. **Clone and install:**
   ```bash
   git clone https://github.com/flyingsurveyor/rilievo_gnss.git
   cd rilievo_gnss
   bash install.sh
   ```

4. **The installer will ask:**
   - **RTKLIB tools**: choose **Build** if you need PPK post-processing (compilation takes ~10–15 minutes on RPi Zero 2W). Choose **Skip** if unsure.
   - **geopandas**: skip on RPi Zero (low RAM, very long install time).
   - **systemd service**: choose **Yes** if you want the app to start automatically every time the Pi boots.

5. **Start manually** (if you chose not to install the systemd service):
   ```bash
   cd rilievo_gnss
   source venv/bin/activate
   python3 app.py
   ```

6. **Find the Pi's IP address:**
   ```bash
   hostname -I
   ```

7. **Open the browser** on any device connected to the same network:
   `http://<rpi-ip>:8000`

8. **First time only:** go to **Settings** (`/settings`) and configure the GNSS TCP connection (IP address and port of the rover or base station).

---

## Daily Use

### Android

- Tap the **`rilievo_avvia`** widget on the home screen to start the app.
- The app checks if Flask is already running — if yes, it opens the browser directly without restarting.
- Tap the **`rilievo_ferma`** widget to stop the app.
- Log file is at `rilievo_gnss/rilievo.log` — useful for debugging if something goes wrong.

### Raspberry Pi

- If the systemd service is installed, the app **starts automatically on every boot** — nothing to do.
- Check service status:
  ```bash
  sudo systemctl status rilievo
  ```
- Restart the service:
  ```bash
  sudo systemctl restart rilievo
  ```
- View live logs:
  ```bash
  journalctl -u rilievo -f
  ```
- Access the app from any device on the same network: `http://<rpi-ip>:8000`

---

## GNSS Connection

The app connects to your GNSS receiver over TCP (the receiver acts as a TCP server).

1. Open **Settings** at `/settings` on first launch.
2. Set the **GNSS receiver IP** and **port** (for example `192.168.1.100:9001`).
3. The **TCPRelay** option allows the app to forward the incoming NTRIP/RTCM stream to other clients on the same local network — useful when multiple devices need the same correction stream.
4. Save the settings and return to the dashboard. Fix status (float/fixed/no fix) will appear as soon as the receiver is connected and sending data.

---

## Quick Start (advanced / developers)

```bash
# First time: install dependencies + optionally compile RTKLIB
./install.sh

# Run
source venv/bin/activate   # (not needed on Termux)
python3 app.py
```

The app starts immediately with no prompts. Open the browser:

- **RTK Dashboard**: `http://localhost:8000/`
- **PPK Home**: `http://localhost:8000/ppk/home`
- **Settings**: `http://localhost:8000/settings`

## Pages

### RTK (real-time surveying)
| Route | Description |
|-------|-------------|
| `/` | Dashboard — live GNSS status (fix, DOP, satellites) |
| `/surveys` | Survey list — create/view/export surveys |
| `/cogo` | COGO tools — trilateration, intersections, Helmert, etc. |
| `/stakeout` | Stakeout — navigate to target points |
| `/compare` | Compare two survey points |
| `/import` | Import CSV/TXT/GeoJSON |
| `/settings` | Configure GNSS connection, relay, survey params |

### Topografia (ufficio)
| Route | Description |
|-------|-------------|
| `/cad` | CAD web — disegno topografico 2D con snap, layer, misure |
| `/dtm` | DTM analysis — TIN Delaunay, curve di livello, volumi, profili |
| `/traverses` | Poligonali — aperte/chiuse, livellazione, frazionamento aree |

### PPK (post-processing)
| Route | Description |
|-------|-------------|
| `/ppk/home` | PPK dashboard — file counts, tool status |
| `/convbin` | Convert raw GNSS → RINEX |
| `/rinex` | RINEX observation QC — header, satellites, SNR |
| `/ppk` | PPK processing — full rnx2rtkp config editor |
| `/posview` | Position viewer — rtkplot-style charts |
| `/files` | File explorer — uploads, rinex, results, pos, conf |

## Structure

```
rilievo/
├── app.py                          # Entry point
├── install.sh                      # Interactive installer
├── requirements.txt                # flask, pyubx2
│
├── modules/                        # Business logic (no Flask deps)
│   ├── utils.py                    # Conversions, robust averaging
│   ├── geodesy.py                  # WGS84, ECEF ↔ ENU ↔ Geodetic
│   ├── cogo.py                     # COGO: trilateration, intersections, Helmert
│   ├── dtm.py                      # DTM: TIN Delaunay, contours, volumes, profiles
│   ├── traverses.py                # Traverses: open/closed, leveling, area division
│   ├── state.py                    # Shared state, BytePipe, TCPRelay
│   ├── ubx_parser.py              # UBX parser + upstream TCP
│   ├── connection.py               # GNSS connection manager
│   ├── settings.py                 # Persistent settings (JSON)
│   ├── survey.py                   # Survey CRUD (GeoJSON)
│   ├── exports.py                  # DXF, GeoPackage, TXT
│   ├── compare.py                  # Point comparison
│   ├── templates_html.py          # RTK HTML templates
│   ├── ppk_config.py              # PPK paths + binary discovery
│   ├── convbin.py                  # Convbin wrapper
│   ├── rnx2rtkp.py                # rnx2rtkp wrapper
│   ├── rinex_parser.py            # RINEX observation parser
│   ├── pos_parser.py              # .pos solution parser
│   └── conf_manager.py            # RTKLIB .conf editor
│
├── routes/                         # Flask blueprints
│   ├── dashboard.py               # / + /events (SSE)
│   ├── settings.py                # /settings + /api/settings
│   ├── surveys.py                 # /surveys, exports
│   ├── cogo.py                    # /cogo/*
│   ├── stakeout.py                # /stakeout
│   ├── compare.py                 # /compare
│   ├── import_export.py           # /import
│   └── ppk.py                     # All PPK routes
│
├── templates/                      # Jinja2 templates (PPK pages)
│   ├── base.html                  # Unified nav (RTK + PPK)
│   ├── index.html                 # PPK home
│   ├── convbin.html               # Convbin page
│   ├── rinex.html                 # RINEX QC
│   ├── rnx2rtkp.html             # PPK processing
│   ├── posview.html               # Position viewer
│   └── files.html                 # File explorer
│
├── static/css/style.css            # PPK page styles
├── static/js/upload.js             # Upload helper
├── conf/                           # Default RTKLIB configs
├── tools/                          # RTKLIB binaries (convbin, rnx2rtkp)
├── data/                           # PPK working data
│   ├── uploads/                   # Raw GNSS files
│   ├── rinex/                     # RINEX files
│   ├── results/                   # Processing results
│   ├── pos/                       # .pos solutions
│   ├── conf/                      # User configs
│   └── antex/                     # Antenna models
└── surveys/                        # RTK surveys + settings
```

## Dependencies

**Required:** `flask>=3.0`, `pyubx2`
**Optional:** `geopandas`, `shapely` (enhanced GeoPackage export)
**PPK tools:** `convbin`, `rnx2rtkp` (compiled by `install.sh`)
