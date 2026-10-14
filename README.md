sudo apt install ffmpeg pipx python3-venv -y

python3 -m venv .venv
source .venv/bin/activate

pip install playwright invisible-playwright
python -m playwright install firefox

python -m invisible_playwright fetch

pipx install yt-dlp
pipx upgrade yt-dlp

python record.py @username --login