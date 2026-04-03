#!/data/data/com.termux/files/usr/bin/bash
# Termux:Widget — ferma RilievoPY
# Posizionare in ~/.shortcuts/rilievo_ferma.sh

# Ferma il processo Flask
if pgrep -f "python.*app\.py" > /dev/null 2>&1; then
    pkill -f "python.*app\.py"
    termux-notification-remove 9010
    termux-toast "RilievoPY fermato"
else
    termux-toast "RilievoPY non in esecuzione"
fi
