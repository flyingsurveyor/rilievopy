#!/data/data/com.termux/files/usr/bin/bash
# Termux:Widget — avvia RilievoPY
# Posizionare in ~/.shortcuts/rilievo_avvia.sh

PORT=8000

# Trova la directory del progetto
PROJECT_DIR=""
for candidate in "$HOME/rilievo_gnss" "$HOME/rilievo" "$HOME/rilievo_gnss/rilievo_gnss"; do
    if [ -f "${candidate}/app.py" ]; then
        PROJECT_DIR="${candidate}"
        break
    fi
done

# Fallback: cerca in tutta la home
if [ -z "$PROJECT_DIR" ]; then
    _found=$(find "$HOME" -maxdepth 3 -name "app.py" -path "*/rilievo*" 2>/dev/null | head -1)
    if [ -n "$_found" ]; then
        PROJECT_DIR=$(dirname "$_found")
    fi
fi

if [ -z "$PROJECT_DIR" ]; then
    termux-notification --id 9010 --title "RilievoPY" \
        --content "Progetto non trovato in $HOME" --priority high
    termux-toast "Errore: progetto RilievoPY non trovato"
    exit 1
fi

URL="http://127.0.0.1:${PORT}"

# Controlla se Flask è già in esecuzione
if pgrep -f "python.*app\.py" > /dev/null 2>&1; then
    termux-notification --id 9010 --title "🛰️ RilievoPY" \
        --content "App già attiva — apertura browser" --priority default
    termux-open-url "$URL"
    exit 0
fi

# Avvia Flask in background
cd "$PROJECT_DIR" || exit 1
nohup python app.py >> rilievo.log 2>&1 &
APP_PID=$!

termux-notification --id 9010 --title "🛰️ RilievoPY" \
    --content "Avvio in corso…" --priority high --ongoing

# Attendi che Flask sia pronto (max 10s)
READY=0
for i in $(seq 1 10); do
    sleep 1
    if curl -sf "${URL}/" > /dev/null 2>&1; then
        READY=1
        break
    fi
done

termux-notification-remove 9010

if [ "$READY" -eq 1 ]; then
    termux-notification --id 9010 --title "✅ RilievoPY" \
        --content "App avviata — tap per aprire" --priority default
    termux-open-url "$URL"
else
    termux-notification --id 9010 --title "⚠️ RilievoPY" \
        --content "Avvio lento — riprova tra qualche secondo" --priority high
fi
