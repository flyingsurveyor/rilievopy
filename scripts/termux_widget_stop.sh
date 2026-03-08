#!/data/data/com.termux/files/usr/bin/bash
# Termux:Widget — ferma Rilievo GNSS
# Posizionare in ~/.shortcuts/rilievo_ferma.sh

# Ferma il processo Flask
if pgrep -f "python.*app\.py" > /dev/null 2>&1; then
    pkill -f "python.*app\.py"
    termux-notification-remove 9010
    termux-toast "Rilievo GNSS fermato"
else
    termux-toast "Rilievo GNSS non era in esecuzione"
fi
