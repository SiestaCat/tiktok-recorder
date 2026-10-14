
#!/usr/bin/env bash

if [[ $# -ne 1 ]]; then
    echo "Uso: $0 @usuario"
    exit 1
fi

username="${1#@}"

if [[ ! "$username" =~ ^[a-zA-Z0-9._]+$ ]]; then
    echo "Nombre de usuario no válido"
    exit 1
fi

outputDir="./Videos/TikTok/$username"
mkdir -p "$outputDir"

command -v yt-dlp >/dev/null || { echo "Falta yt-dlp"; exit 1; }
command -v ffmpeg >/dev/null || { echo "Falta ffmpeg"; exit 1; }

trap 'echo "Grabación detenida"; exit 0' INT TERM

echo "Monitorizando TikTok LIVE: @$username"

while true; do
    outputFile="$outputDir/${username}_$(date +%Y-%m-%d_%H-%M-%S).%(ext)s"

    yt-dlp \
        --cookies-from-browser firefox \
        --impersonate firefox \
        --no-part \
        --retries 3 \
        --fragment-retries 10 \
        --hls-use-mpegts \
        -f "flv-origin/best" \
        -o "$outputFile" \
        "https://www.tiktok.com/@${username}/live"

    echo "Directo finalizado o no disponible."
    echo "Reintentando en 60 segundos..."
    sleep 60
done
