#!/bin/bash
# ═══════════════════════════════════════════════════════════════════
# RilievoPY — Installer
#
# Unified installer for RilievoPY (RTK surveying + PPK post-processing).
# Installs Python dependencies, optionally builds RTKLIB CLI tools
# (convbin, rnx2rtkp) for PPK processing.
#
# Supported platforms:
#   Linux (Debian/Ubuntu, Raspberry Pi OS, Arch, Fedora)
#   Termux (Android)
#   macOS (Homebrew)
#   Windows (MSYS2, Git Bash, WSL)
#
# Usage:
#   ./install.sh                Interactive install (recommended)
#   ./install.sh --skip-build   Skip RTKLIB compilation
#   ./install.sh --force-build  Rebuild RTKLIB without asking
#   ./install.sh --help         Show help
#
# ═══════════════════════════════════════════════════════════════════

# Don't use set -e globally — we handle errors ourselves
# so optional installs don't abort everything
set +e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOLS_DIR="${SCRIPT_DIR}/tools"
RTKLIB_REPO="https://github.com/rtklibexplorer/RTKLIB.git"
RTKLIB_BRANCH="main"
RTKLIB_DIR="${SCRIPT_DIR}/.rtklib-src"
SERVICE_NAME="rilievo"
DEFAULT_PORT=8000

# PPK tools to compile
REQUIRED_TOOLS="convbin rnx2rtkp"

# ─── Parse arguments ───
SKIP_BUILD=0
FORCE_BUILD=0
for arg in "$@"; do
    case "$arg" in
        --skip-build)    SKIP_BUILD=1 ;;
        --force-build)   FORCE_BUILD=1 ;;
        --help|-h)
            echo "RilievoPY — Installer"
            echo ""
            echo "Usage: $0 [options]"
            echo ""
            echo "Options:"
            echo "  --skip-build    Skip RTKLIB compilation (RTK works without it)"
            echo "  --force-build   Rebuild RTKLIB without asking"
            echo "  --help          Show this help"
            echo ""
            echo "Environment variables:"
            echo "  RTKLIB_CONVBIN_BIN   Path to convbin binary"
            echo "  RTKLIB_RNX2RTKP_BIN  Path to rnx2rtkp binary"
            exit 0 ;;
        *)
            echo "Unknown option: $arg (try --help)"
            exit 1 ;;
    esac
done

# ═══════════════════════════════════════════════════════════════════
# Logging & utilities
# ═══════════════════════════════════════════════════════════════════

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m'

log()   { echo -e "${GREEN}[✓]${NC} $1"; }
warn()  { echo -e "${YELLOW}[!]${NC} $1"; }
err()   { echo -e "${RED}[✗]${NC} $1"; }
info()  { echo -e "${CYAN}[i]${NC} $1"; }

ask_yn() {
    local prompt="$1"
    local reply
    read -p "$(echo -e "${CYAN}[?]${NC} ${prompt} [y/N] ")" -n 1 -r reply
    echo ""
    [[ "$reply" =~ ^[Yy]$ ]]
}

ask_Yn() {
    local prompt="$1"
    local reply
    read -p "$(echo -e "${CYAN}[?]${NC} ${prompt} [Y/n] ")" -n 1 -r reply
    echo ""
    [[ ! "$reply" =~ ^[Nn]$ ]]
}

timer_start() { _TIMER=$(date +%s); }
timer_show() {
    local e=$(( $(date +%s) - _TIMER ))
    if [ $e -ge 60 ]; then echo "$((e/60))m $((e%60))s"
    else echo "${e}s"; fi
}

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║       RilievoPY — Installer              ║"
echo "║   RTK Surveying + PPK Post-processing    ║"
echo "╚══════════════════════════════════════════╝"
echo ""

# ═══════════════════════════════════════════════════════════════════
# 1. Platform detection
# ═══════════════════════════════════════════════════════════════════

PLATFORM="unknown"
PKG_MANAGER="none"
HAS_SUDO=0
IS_LOW_RAM=0
PIP_EXTRA_FLAGS=""

if [ -n "${TERMUX_VERSION}" ] || [ -d "/data/data/com.termux" ]; then
    PLATFORM="termux"
    PKG_MANAGER="pkg"
    PIP_EXTRA_FLAGS="--break-system-packages"
elif [ "$(uname -s)" = "Darwin" ]; then
    PLATFORM="macos"
    PKG_MANAGER="brew"
elif [ -n "${MSYSTEM}" ] || [ "$(uname -o 2>/dev/null)" = "Msys" ]; then
    PLATFORM="msys2"
    PKG_MANAGER="pacman"
elif grep -qi microsoft /proc/version 2>/dev/null; then
    PLATFORM="wsl"
    PKG_MANAGER="apt"
    HAS_SUDO=1
elif [ -f /etc/debian_version ]; then
    PLATFORM="debian"
    PKG_MANAGER="apt"
    HAS_SUDO=1
elif [ -f /etc/arch-release ]; then
    PLATFORM="arch"
    PKG_MANAGER="pacman"
    HAS_SUDO=1
elif [ "$(uname -s)" = "Linux" ]; then
    PLATFORM="linux"
    if command -v apt-get &>/dev/null; then
        PKG_MANAGER="apt"; HAS_SUDO=1
    elif command -v pacman &>/dev/null; then
        PKG_MANAGER="pacman"; HAS_SUDO=1
    elif command -v dnf &>/dev/null; then
        PKG_MANAGER="dnf"; HAS_SUDO=1
    fi
fi

# Check if pip needs --break-system-packages (Python 3.11+ on managed systems)
if [ "${PLATFORM}" != "termux" ]; then
    PY_MAJOR=$(python3 -c 'import sys; print(sys.version_info.minor)' 2>/dev/null || echo "0")
    if [ "${PY_MAJOR}" -ge 11 ] && [ -f /usr/lib/python3/dist-packages/pip/__init__.py ] 2>/dev/null; then
        # Externally managed environment — venv will handle it, but flag for termux-like edge cases
        :
    fi
fi

# System info
ARCH=$(uname -m)
CPU_CORES=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 1)
TOTAL_RAM=0
if command -v free &>/dev/null; then
    TOTAL_RAM=$(free -m 2>/dev/null | awk '/^Mem:/{print $2}' || echo 0)
elif [ "${PLATFORM}" = "macos" ]; then
    TOTAL_RAM=$(( $(sysctl -n hw.memsize 2>/dev/null || echo 0) / 1048576 ))
fi

if [ "${TOTAL_RAM}" -gt 0 ] && [ "${TOTAL_RAM}" -lt 600 ]; then
    IS_LOW_RAM=1
fi

info "Platform: ${BOLD}${PLATFORM}${NC} (${ARCH}, ${CPU_CORES} cores, ${TOTAL_RAM}MB RAM)"

# ═══════════════════════════════════════════════════════════════════
# 2. Check for existing RTKLIB tools
# ═══════════════════════════════════════════════════════════════════

echo ""
info "Checking for RTKLIB tools (needed for PPK post-processing)..."

FOUND_TOOLS=0
MISSING_TOOLS=0
FOUND_LIST=""
MISSING_LIST=""

for tool in ${REQUIRED_TOOLS}; do
    PROJECT_BIN="${TOOLS_DIR}/${tool}"
    SYSTEM_BIN=$(command -v "${tool}" 2>/dev/null || true)

    if [ -x "${PROJECT_BIN}" ]; then
        VERSION=$("${PROJECT_BIN}" --version 2>&1 | head -1 || echo "unknown version")
        log "  ${BOLD}${tool}${NC} found: ${PROJECT_BIN}"
        echo -e "    ${DIM}${VERSION}${NC}"
        FOUND_TOOLS=$((FOUND_TOOLS + 1))
        FOUND_LIST="${FOUND_LIST} ${tool}"
    elif [ -n "${SYSTEM_BIN}" ]; then
        VERSION=$("${SYSTEM_BIN}" --version 2>&1 | head -1 || echo "unknown version")
        log "  ${BOLD}${tool}${NC} found: ${SYSTEM_BIN}"
        echo -e "    ${DIM}${VERSION}${NC}"
        FOUND_TOOLS=$((FOUND_TOOLS + 1))
        FOUND_LIST="${FOUND_LIST} ${tool}"
    else
        warn "  ${BOLD}${tool}${NC} — not found"
        MISSING_TOOLS=$((MISSING_TOOLS + 1))
        MISSING_LIST="${MISSING_LIST} ${tool}"
    fi
done

# ═══════════════════════════════════════════════════════════════════
# 3. Decide whether to build RTKLIB
# ═══════════════════════════════════════════════════════════════════

DO_BUILD=0

echo ""

if [ "${SKIP_BUILD}" -eq 1 ]; then
    info "Skipping RTKLIB build (--skip-build)"
    if [ "${MISSING_TOOLS}" -gt 0 ]; then
        warn "Missing tools:${MISSING_LIST}"
        warn "PPK post-processing won't work without them."
        warn "RTK surveying works fine without RTKLIB tools."
    fi

elif [ "${FORCE_BUILD}" -eq 1 ]; then
    DO_BUILD=1

elif [ "${FOUND_TOOLS}" -eq 2 ] && [ "${MISSING_TOOLS}" -eq 0 ]; then
    echo -e "${BOLD}All RTKLIB tools are already installed.${NC}"
    echo ""
    echo "  1) Keep current installation (recommended)"
    echo "  2) Rebuild from RTKLIBExplorer main/RTKLIB-EX 2.5.0 (latest fixes)"
    echo ""
    read -p "$(echo -e "${CYAN}[?]${NC} Choose [1]: ")" -n 1 -r CHOICE
    echo ""

    case "$CHOICE" in
        2) DO_BUILD=1 ;;
        *) info "Keeping existing RTKLIB tools" ;;
    esac

elif [ "${MISSING_TOOLS}" -gt 0 ]; then
    echo -e "${BOLD}Missing RTKLIB tools:${YELLOW}${MISSING_LIST}${NC}"
    echo ""
    echo "  These are needed only for PPK post-processing."
    echo "  RTK surveying (dashboard, rilievi, COGO, stakeout) works without them."
    echo ""
    echo "  1) Build from RTKLIBExplorer main/RTKLIB-EX 2.5.0 (recommended)"
    echo "  2) Skip — I'll install them manually later"
    echo ""
    read -p "$(echo -e "${CYAN}[?]${NC} Choose [1]: ")" -n 1 -r CHOICE
    echo ""

    case "$CHOICE" in
        2) info "Skipping build. PPK features will show 'tool not found'." ;;
        *) DO_BUILD=1 ;;
    esac
fi

# ═══════════════════════════════════════════════════════════════════
# 4. System dependencies
# ═══════════════════════════════════════════════════════════════════

echo ""
info "Checking system dependencies..."

install_deps() {
    case "${PLATFORM}" in
        termux)
            pkg install -y python git 2>/dev/null || true
            if [ "${DO_BUILD}" -eq 1 ]; then
                pkg install -y make clang 2>/dev/null || true
            fi
            ;;
        debian|wsl)
            sudo apt-get update -qq
            local pkgs="python3 python3-pip python3-venv git"
            if [ "${DO_BUILD}" -eq 1 ]; then
                pkgs="${pkgs} build-essential gfortran"
            fi
            sudo apt-get install -y -qq ${pkgs} 2>&1 | tail -3
            ;;
        arch)
            local pkgs="python python-pip git"
            if [ "${DO_BUILD}" -eq 1 ]; then
                pkgs="${pkgs} base-devel gcc-fortran"
            fi
            sudo pacman -S --noconfirm --needed ${pkgs} 2>&1 | tail -3
            ;;
        macos)
            if ! command -v brew &>/dev/null; then
                warn "Homebrew not found. Install from https://brew.sh"
            else
                brew install python3 git 2>/dev/null || true
                if [ "${DO_BUILD}" -eq 1 ]; then
                    brew install gcc 2>/dev/null || true
                fi
            fi
            ;;
        msys2)
            local pkgs="python python-pip git"
            if [ "${DO_BUILD}" -eq 1 ]; then
                pkgs="${pkgs} make ${MINGW_PACKAGE_PREFIX}-gcc ${MINGW_PACKAGE_PREFIX}-gcc-fortran"
            fi
            pacman -S --noconfirm --needed ${pkgs} 2>&1 | tail -3
            ;;
        *)
            warn "Unknown platform — please install manually: python3, pip, git"
            if [ "${DO_BUILD}" -eq 1 ]; then
                warn "Also needed for build: gcc, make, gfortran"
            fi
            ;;
    esac
}

install_deps
log "System dependencies OK"

# ═══════════════════════════════════════════════════════════════════
# 5. Build RTKLIB (if needed)
# ═══════════════════════════════════════════════════════════════════

if [ "${DO_BUILD}" -eq 1 ]; then

    # ── Low RAM check ──
    if [ "${IS_LOW_RAM}" -eq 1 ]; then
        CURRENT_SWAP=$(free -m 2>/dev/null | awk '/^Swap:/{print $2}' || echo 0)
        if [ "${CURRENT_SWAP}" -lt 512 ]; then
            echo ""
            warn "Low RAM (${TOTAL_RAM}MB) with only ${CURRENT_SWAP}MB swap."
            warn "Compilation may be very slow or fail."
            echo ""
            echo "  To increase swap (Linux/RPi):"
            echo "    sudo dphys-swapfile swapoff"
            echo "    sudo sed -i 's/CONF_SWAPSIZE=.*/CONF_SWAPSIZE=2048/' /etc/dphys-swapfile"
            echo "    sudo dphys-swapfile setup && sudo dphys-swapfile swapon"
            echo ""
            if ! ask_yn "Continue anyway?"; then
                echo "Fix swap first, then re-run."
                exit 1
            fi
        fi
    fi

    # ── Determine -j value ──
    if [ "${IS_LOW_RAM}" -eq 1 ]; then
        MAKE_JOBS=1
        info "Low RAM — using make -j1"
    elif [ "${TOTAL_RAM}" -lt 2048 ]; then
        MAKE_JOBS=2
    else
        MAKE_JOBS="${CPU_CORES}"
    fi

    # ── Clone source ──
    echo ""
    mkdir -p "${TOOLS_DIR}"

    if [ ! -d "${RTKLIB_DIR}" ]; then
        info "Cloning RTKLIBExplorer (${RTKLIB_BRANCH} branch)..."
        timer_start
        if git clone --depth 1 --branch "${RTKLIB_BRANCH}" "${RTKLIB_REPO}" "${RTKLIB_DIR}"; then
            log "Cloned ($(timer_show))"
        else
            err "Git clone failed. Check your internet connection."
            err "You can re-run the installer later, or install RTKLIB manually."
            DO_BUILD=0
        fi
    else
        info "Source already present: ${RTKLIB_DIR}"
        # Check current branch — if it's the retired demo5, re-clone from main
        CURRENT_BRANCH=$(git -C "${RTKLIB_DIR}" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
        if [ "${CURRENT_BRANCH}" = "demo5" ]; then
            warn "Source is on the retired 'demo5' branch."
            warn "Switching to '${RTKLIB_BRANCH}' branch (RTKLIB-EX)..."
            rm -rf "${RTKLIB_DIR}"
            info "Cloning RTKLIBExplorer (${RTKLIB_BRANCH} branch)..."
            timer_start
            if git clone --depth 1 --branch "${RTKLIB_BRANCH}" "${RTKLIB_REPO}" "${RTKLIB_DIR}"; then
                log "Cloned ($(timer_show))"
            else
                err "Git clone failed."
                DO_BUILD=0
            fi
        else
            if ask_Yn "Pull latest updates?"; then
                cd "${RTKLIB_DIR}"
                git pull --ff-only || warn "Could not pull (no network?)"
                cd "${SCRIPT_DIR}"
            fi
        fi
    fi
fi

if [ "${DO_BUILD}" -eq 1 ]; then

    # ── Compile ──
    echo ""
    echo -e "${BOLD}═══ Building RTKLIB tools (make -j${MAKE_JOBS}) ═══${NC}"
    echo ""

    COMPILE_START=$(date +%s)
    BUILD_OK=0

    for tool in ${REQUIRED_TOOLS}; do
        APP_DIR="${RTKLIB_DIR}/app/consapp/${tool}"

        # Find makefile
        MAKE_DIR=""
        if [ -d "${APP_DIR}/gcc" ]; then
            MAKE_DIR="${APP_DIR}/gcc"
        elif [ -d "${APP_DIR}/gcc_mkl" ]; then
            MAKE_DIR="${APP_DIR}/gcc_mkl"
        elif [ -f "${APP_DIR}/makefile" ] || [ -f "${APP_DIR}/Makefile" ]; then
            MAKE_DIR="${APP_DIR}"
        fi

        if [ -z "${MAKE_DIR}" ]; then
            err "${tool}: no makefile found in ${APP_DIR}"
            continue
        fi

        echo -e "${CYAN}───────────────────────────────────────────${NC}"
        echo -e "${BOLD}Building ${tool}${NC}  (${MAKE_DIR})"
        echo -e "${CYAN}───────────────────────────────────────────${NC}"

        cd "${MAKE_DIR}"
        make clean 2>/dev/null || true

        timer_start

        if make -j${MAKE_JOBS} 2>&1; then
            BIN=$(find "${MAKE_DIR}" -maxdepth 1 -name "${tool}" -type f -executable 2>/dev/null | head -1)
            if [ -z "${BIN}" ]; then
                BIN=$(find "${APP_DIR}" -name "${tool}" -type f -executable 2>/dev/null | head -1)
            fi

            if [ -n "${BIN}" ]; then
                cp "${BIN}" "${TOOLS_DIR}/${tool}"
                chmod +x "${TOOLS_DIR}/${tool}"
                SIZE=$(du -h "${TOOLS_DIR}/${tool}" | cut -f1)
                echo ""
                log "${tool} compiled OK ($(timer_show), ${SIZE})"
                BUILD_OK=$((BUILD_OK + 1))
            else
                echo ""
                err "${tool}: make succeeded but binary not found!"
            fi
        else
            echo ""
            err "${tool}: compilation failed!"
            if [ "${IS_LOW_RAM}" -eq 1 ]; then
                warn "  On low-RAM devices, this may be an OOM kill."
                warn "  Try increasing swap to 2GB and re-run."
            fi
        fi

        cd "${SCRIPT_DIR}"
        echo ""
    done

    COMPILE_TIME=$(( $(date +%s) - COMPILE_START ))
    echo -e "${BOLD}═══ Build complete ($(( COMPILE_TIME / 60 ))m $(( COMPILE_TIME % 60 ))s) ═══${NC}"
    echo ""

    # Summary
    for tool in ${REQUIRED_TOOLS}; do
        if [ -x "${TOOLS_DIR}/${tool}" ]; then
            VERSION=$("${TOOLS_DIR}/${tool}" --version 2>&1 | head -1 || echo "")
            SIZE=$(du -h "${TOOLS_DIR}/${tool}" | cut -f1)
            log "${tool} (${SIZE}) ${DIM}${VERSION}${NC}"
        else
            err "${tool} — NOT COMPILED"
        fi
    done

    # ── Source cleanup prompt ──
    echo ""
    SRC_SIZE=$(du -sh "${RTKLIB_DIR}" 2>/dev/null | cut -f1 || echo "?")
    info "RTKLIB source: ${RTKLIB_DIR} (${SRC_SIZE})"
    if ask_yn "Remove source to save disk space? (binaries are self-contained)"; then
        rm -rf "${RTKLIB_DIR}"
        log "Source removed"
    else
        info "Source kept (useful for recompiling later)"
    fi
fi

# ═══════════════════════════════════════════════════════════════════
# 6. Python environment
# ═══════════════════════════════════════════════════════════════════

echo ""
info "Setting up Python environment..."
timer_start

VENV_DIR=""

if [ "${PLATFORM}" = "termux" ]; then
    # Termux: install directly (no venv)
    info "Installing: flask, pyubx2, waitress, openpyxl, zeroconf..."
    pip install ${PIP_EXTRA_FLAGS} flask pyubx2 waitress openpyxl zeroconf 2>&1 | tail -5
    PIP_CMD="pip"
else
    VENV_DIR="${SCRIPT_DIR}/venv"
    if [ ! -d "${VENV_DIR}" ]; then
        info "Creating virtual environment..."
        python3 -m venv "${VENV_DIR}"
    fi
    source "${VENV_DIR}/bin/activate"
    pip install --upgrade pip -q 2>/dev/null
    PIP_CMD="pip"

    # Install required dependencies
    info "Installing: flask, pyubx2, waitress, openpyxl, zeroconf..."
    pip install flask pyubx2 waitress openpyxl zeroconf 2>&1 | tail -5
fi

# Verify core deps installed correctly
DEPS_OK=1
python3 -c "import flask" 2>/dev/null || { err "flask not installed!"; DEPS_OK=0; }
python3 -c "import pyubx2" 2>/dev/null || { err "pyubx2 not installed!"; DEPS_OK=0; }
python3 -c "import openpyxl" 2>/dev/null || { err "openpyxl not installed!"; DEPS_OK=0; }

if [ "${DEPS_OK}" -eq 1 ]; then
    log "Core dependencies OK ($(timer_show))"
else
    err "Some dependencies failed to install. Check errors above."
fi

info "GeoPackage export: pure-SQLite implementation (no geopandas required)"

# ═══════════════════════════════════════════════════════════════════
# 9. Termux extras (BLE, notifications, widget)
# ═══════════════════════════════════════════════════════════════════

if [ "${PLATFORM}" = "termux" ]; then

    echo ""
    echo -e "${BOLD}═══ Termux extras ═══${NC}"

    # ── 9a. bleak (BLE for RTKINO) ──────────────────────────────────
    echo ""
    info "Installazione bleak (BLE per RTKINO)..."
    if pip install bleak; then
        log "bleak installato"
        python3 -c "import bleak" 2>/dev/null && log "bleak OK" \
            || warn "bleak non importabile"
    else
        warn "bleak non installato — connessione BLE non disponibile"
    fi

    # ── 9b. Termux:API ──────────────────────────────────────────────
    echo ""
    if command -v termux-notification &>/dev/null; then
        log "Termux:API già installata"
    else
        warn "Termux:API non trovata — notifiche Android non disponibili"
        echo ""
        echo "  Termux:API è necessaria per:"
        echo "  • Notifiche push (perdita fix RTK)"
        echo "  • Vibrazione"
        echo "  • Text-to-speech"
        echo ""
        echo "  Scarica e installa:"
        echo "  https://f-droid.org/packages/com.termux.api/"
        echo ""
        if ask_yn "Apro il link di download nel browser?"; then
            termux-open-url "https://f-droid.org/packages/com.termux.api/" 2>/dev/null \
                || am start -a android.intent.action.VIEW \
                   -d "https://f-droid.org/packages/com.termux.api/" 2>/dev/null \
                || info "Apri manualmente: https://f-droid.org/packages/com.termux.api/"
        fi
    fi

    # ── 9c. Termux:Widget ───────────────────────────────────────────
    SHORTCUTS_DIR="$HOME/.shortcuts"
    echo ""
    if [ -d "$SHORTCUTS_DIR" ] || command -v termux-widget &>/dev/null; then
        log "Termux:Widget disponibile"
    else
        warn "Termux:Widget non trovato"
        echo ""
        echo "  Termux:Widget ti permette di avviare Rilievo con un tap dalla home screen."
        echo ""
        echo "  Scarica e installa:"
        echo "  https://f-droid.org/packages/com.termux.widget/"
        echo ""
        if ask_yn "Apro il link di download nel browser?"; then
            termux-open-url "https://f-droid.org/packages/com.termux.widget/" 2>/dev/null \
                || am start -a android.intent.action.VIEW \
                   -d "https://f-droid.org/packages/com.termux.widget/" 2>/dev/null \
                || info "Apri manualmente: https://f-droid.org/packages/com.termux.widget/"
        fi
    fi

    # ── 9d. Install widget scripts in tasks/ (background execution, no visible session) ──
    TASKS_DIR="$HOME/.shortcuts/tasks"
    echo ""
    mkdir -p "$SHORTCUTS_DIR"
    mkdir -p "$TASKS_DIR"
    chmod 700 "$SHORTCUTS_DIR"
    chmod 700 "$TASKS_DIR"

    if [ -f "${SCRIPT_DIR}/scripts/termux_widget_start.sh" ]; then
        cp "${SCRIPT_DIR}/scripts/termux_widget_start.sh" \
            "$TASKS_DIR/rilievo_avvia.sh"
        cp "${SCRIPT_DIR}/scripts/termux_widget_stop.sh" \
            "$TASKS_DIR/rilievo_ferma.sh"
        chmod +x "$TASKS_DIR/rilievo_avvia.sh"
        chmod +x "$TASKS_DIR/rilievo_ferma.sh"
        log "Script widget installati in $TASKS_DIR"
        echo ""
        info "Per usare i widget (modalità Task — nessuna sessione visibile):"
        echo "  1. Tieni premuto sulla home screen → Widget"
        echo "  2. Cerca 'Termux' → seleziona Termux:Task (non Shortcut)"
        echo "  3. Seleziona 'rilievo_avvia' per avviare, 'rilievo_ferma' per fermare"
        echo "  Nota: i task girano in background senza aprire una sessione Termux"
    else
        warn "Script widget non trovati in ${SCRIPT_DIR}/scripts/ — salto installazione"
    fi

fi

# ═══════════════════════════════════════════════════════════════════
# 7. Data directories & default configs
# ═══════════════════════════════════════════════════════════════════

echo ""
info "Creating data directories..."

# RTK directories
mkdir -p "${SCRIPT_DIR}/surveys"

# PPK directories
mkdir -p "${SCRIPT_DIR}/data/uploads"
mkdir -p "${SCRIPT_DIR}/data/rinex"
mkdir -p "${SCRIPT_DIR}/data/results"
mkdir -p "${SCRIPT_DIR}/data/pos"
mkdir -p "${SCRIPT_DIR}/data/conf"
mkdir -p "${SCRIPT_DIR}/data/antex"
mkdir -p "${SCRIPT_DIR}/conf"
mkdir -p "${TOOLS_DIR}"

# Copy default RTKLIB configs (if present)
if [ -d "${SCRIPT_DIR}/conf" ]; then
    for cf in "${SCRIPT_DIR}"/conf/*.conf; do
        [ -f "$cf" ] || continue
        bn=$(basename "$cf")
        if [ ! -f "${SCRIPT_DIR}/data/conf/${bn}" ]; then
            cp "$cf" "${SCRIPT_DIR}/data/conf/${bn}"
            log "  Default config: ${bn}"
        fi
    done
fi

log "Directories ready"

# ═══════════════════════════════════════════════════════════════════
# 8. Systemd service (Linux only, not Termux)
# ═══════════════════════════════════════════════════════════════════

if [ "${HAS_SUDO}" -eq 1 ] && command -v systemctl &>/dev/null; then
    echo ""
    if ask_yn "Install as systemd service (auto-start on boot)?"; then
        info "Creating systemd service..."

        if [ -n "${VENV_DIR}" ] && [ -d "${VENV_DIR}" ]; then
            PYTHON_BIN="${VENV_DIR}/bin/python"
        else
            PYTHON_BIN=$(command -v python3)
        fi

        sudo tee /etc/systemd/system/${SERVICE_NAME}.service > /dev/null << EOF
[Unit]
Description=RilievoPY — RTK/PPK Survey Suite
After=network.target

[Service]
Type=simple
User=$(whoami)
WorkingDirectory=${SCRIPT_DIR}
ExecStart=${PYTHON_BIN} ${SCRIPT_DIR}/app.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

        sudo systemctl daemon-reload
        sudo systemctl enable ${SERVICE_NAME}

        # Ask whether to start now
        if ask_Yn "Start the service now?"; then
            sudo systemctl start ${SERVICE_NAME}
            log "Service started"
        fi

        log "Service installed: ${SERVICE_NAME}"
        echo ""
        info "Manage with:"
        echo "    sudo systemctl status ${SERVICE_NAME}"
        echo "    sudo systemctl restart ${SERVICE_NAME}"
        echo "    sudo systemctl stop ${SERVICE_NAME}"
        echo "    journalctl -u ${SERVICE_NAME} -f"
    fi
fi

# ═══════════════════════════════════════════════════════════════════
# Done
# ═══════════════════════════════════════════════════════════════════

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║        Installation Complete!            ║"
echo "╚══════════════════════════════════════════╝"
echo ""

# Final summary
echo -e "${BOLD}Components:${NC}"

# Python deps
python3 -c "import flask; print('  ✓ Flask', flask.__version__)" 2>/dev/null || echo "  ✗ Flask — NOT INSTALLED"
python3 -c "import pyubx2; print('  ✓ pyubx2', pyubx2.version)" 2>/dev/null \
    || python3 -c "import pyubx2; print('  ✓ pyubx2')" 2>/dev/null \
    || echo "  ✗ pyubx2 — NOT INSTALLED"
python3 -c "import openpyxl; print('  ✓ openpyxl', openpyxl.__version__)" 2>/dev/null \
    || echo "  ✗ openpyxl — NOT INSTALLED"
echo ""

# RTKLIB tools
echo -e "${BOLD}RTKLIB tools (PPK):${NC}"
ALL_TOOLS_OK=1
for tool in ${REQUIRED_TOOLS}; do
    BIN="${TOOLS_DIR}/${tool}"
    SYS=$(command -v "${tool}" 2>/dev/null || true)

    if [ -x "${BIN}" ]; then
        log "${tool} → ${BIN}"
    elif [ -n "${SYS}" ]; then
        log "${tool} → ${SYS} (system)"
    else
        warn "${tool} → not found (PPK features unavailable)"
        ALL_TOOLS_OK=0
    fi
done

echo ""

if [ "${ALL_TOOLS_OK}" -eq 0 ]; then
    info "RTK features (dashboard, rilievi, COGO, stakeout) work without RTKLIB tools."
    info "To enable PPK, re-run: ${BOLD}./install.sh --force-build${NC}"
    echo ""
fi

# How to run
echo -e "${BOLD}How to start:${NC}"
if [ "${PLATFORM}" = "termux" ]; then
    echo "    cd ${SCRIPT_DIR}"
    echo "    python3 app.py"
    echo ""
    echo -e "${BOLD}Avvio rapido con Termux:Task:${NC}"
    echo "    Tap su widget 'rilievo_avvia' dalla home screen (Termux:Task)"
    echo ""
    echo -e "${BOLD}Connessione BLE a RTKINO:${NC}"
    echo "    Abilitare BLE nelle impostazioni: http://127.0.0.1:${DEFAULT_PORT}/settings"
    echo "    Dispositivo: RTKino | PIN: 123456 (default)"
elif [ -n "${VENV_DIR}" ] && [ -d "${VENV_DIR}" ]; then
    echo "    cd ${SCRIPT_DIR}"
    echo "    source venv/bin/activate"
    echo "    python3 app.py"
fi

echo ""
echo -e "${BOLD}First time?${NC} Open the browser and go to ${BOLD}/settings${NC} to configure"
echo "  the GNSS receiver connection (IP, port, relay, etc.)"

echo ""
IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "localhost")
echo -e "  ${BOLD}Dashboard:${NC}     http://${IP}:${DEFAULT_PORT}"
echo -e "  ${BOLD}Settings:${NC}      http://${IP}:${DEFAULT_PORT}/settings"

echo ""
log "Done!"
