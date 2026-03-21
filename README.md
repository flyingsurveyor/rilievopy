# Rilievo GNSS — Unified RTK/PPK Suite

RTK real-time surveying + PPK post-processing in a single app.

## Quick Start

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
