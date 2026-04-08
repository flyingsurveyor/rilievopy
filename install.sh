#!/bin/bash
# ... previous script content ...

install_deps() {
    case ${PLATFORM} in
        termux)
            pkg install -y python git 2>/dev/null || true
            if [ "${DO_BUILD}" -eq 1 ]; then
                pkg install -y make clang 2>/dev/null || true
            fi
            # clang: required for USB OTG ZED-F9P reader compilation (tools/usb_otg_reader.c)
            pkg install -y clang 2>/dev/null || true
            ;;
        # ... other cases ...
    esac
}

# ... other script content ...

# Building loop
        echo -e "${CYAN}───────────────────────────────────────────${NC}"
        echo -e "${BOLD}Building ${tool}${NC}  (${MAKE_DIR})"
        echo -e "${CYAN}───────────────────────────────────────────${NC}"

        cd "${MAKE_DIR}"
        make clean 2>/dev/null || true

        # ── Termux: patch makefile — remove -lgfortran (not available in pkg)
        #    and use clang instead of gcc
        if [ "${PLATFORM}" = "termux" ]; then
            MF=$(ls makefile Makefile 2>/dev/null | head -1)
            if [ -n "${MF}" ]; then
                sed -i 's/-lgfortran//g' "${MF}"
                info "${tool}: patched makefile (removed -lgfortran for Termux)"
            fi
            MAKE_CC="CC=clang"
        else
            MAKE_CC=""
        fi

        timer_start

        if make -j${MAKE_JOBS} ${MAKE_CC} 2>&1; then
