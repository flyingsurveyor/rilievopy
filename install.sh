#!/bin/bash
# ... [Other script content] ...

# Termux installation
pip install ${PIP_EXTRA_FLAGS} flask pyubx2 waitress openpyxl 2>&1 | tail -5

# Info message
info "Installing: flask, pyubx2, openpyxl..."

# Linux/venv installation
pip install flask pyubx2 waitress openpyxl 2>&1 | tail -5

# Final summary section
# ... [Some lines above] ...
python3 -c "import openpyxl; print('  ✓ openpyxl', openpyxl.__version__)" 2>/dev/null || echo "  ✗ openpyxl — NOT INSTALLED"
# ... [Other script content] ...