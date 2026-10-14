#!/usr/bin/env python3

import json
import re
import secrets
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit
from invisible_playwright import InvisiblePlaywright

if len(sys.argv) < 2:
    print(f"Uso: {sys.argv[0]} @usuario [--login]")
    sys.exit(1)

username = sys.argv[1].lstrip("@")
if not re.fullmatch(r"[a-zA-Z0-9._]+", username):
    sys.exit("Usuario no válido")

liveUrl = f"https://www.tiktok.com/@{username}/live"
profileDir = Path.home() / ".local/share/tiktok-playwright"
profileDir.mkdir(parents=True, exist_ok=True)
seedFile = profileDir / ".invisible-playwright-seed"
if seedFile.exists():
    profileSeed = int(seedFile.read_text().strip())
else:
    profileSeed = secrets.randbelow(2**31 - 1) + 1
    seedFile.write_text(str(profileSeed))

outputDir = Path.home() / "Videos/TikTok" / username
outputDir.mkdir(parents=True, exist_ok=True)

streams = {}

def addStream(value, path=""):
    if isinstance(value, dict):
        for key, item in value.items():
            addStream(item, f"{path}/{key}")
    elif isinstance(value, list):
        for item in value:
            addStream(item, path)
    elif isinstance(value, str):
        if value.startswith(("{", "[")):
            try:
                addStream(json.loads(value), path)
            except (ValueError, RecursionError):
                pass
        elif value.startswith("https://"):
            parsed = urlsplit(value)
            isFlv = ".flv" in parsed.path.lower() or "pull-flv" in parsed.netloc.lower()
            isHls = ".m3u8" in parsed.path.lower()

            if isFlv or isHls:
                score = 100 if "origin" in path.lower() else 0
                score += 10 if isFlv else 0
                streams[value] = max(streams.get(value, 0), score)

def onResponse(response):
    if "json" not in response.headers.get("content-type", ""):
        return
    if not any(part in response.url for part in ("live", "room", "webcast")):
        return
    try:
        addStream(response.json())
    except Exception:
        pass

with InvisiblePlaywright(profile_dir=profileDir, seed=profileSeed, headless=False) as context:

    page = context.pages[0] if context.pages else context.new_page()
    page.on("request", lambda request: addStream(request.url))
    page.on("response", onResponse)

    if "--login" in sys.argv:
        page.goto("https://www.tiktok.com")
        input("Inicia sesión en TikTok y pulsa Enter aquí...")
        context.close()
        sys.exit(0)

    try:
        while True:
            streams.clear()
            print(f"Comprobando @{username}...", flush=True)

            try:
                page.goto(liveUrl, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(7000)

                for scriptId in ("SIGI_STATE", "__UNIVERSAL_DATA_FOR_REHYDRATION__"):
                    element = page.query_selector(f"script#{scriptId}")
                    if element:
                        addStream(element.text_content() or "")

                try:
                    info = page.evaluate("""async username => {
                        const params = new URLSearchParams({
                            aid: "1988",
                            sourceType: "54",
                            staleTime: "600000",
                            uniqueId: username.toLowerCase()
                        });
                        const response = await fetch(
                            `/api-live/user/room?${params}`,
                            {credentials: "include"}
                        );
                        if (!response.ok) return null;
                        return await response.json();
                    }""", username)
                    addStream(info)
                except Exception as error:
                    print(f"API no disponible: {error}")

            except Exception as error:
                print(f"Error de navegación: {error}")

            if not streams:
                print("No se encontró una URL de transmisión.")
                time.sleep(60)
                continue

            streamUrl = max(streams, key=streams.get)
            outputFile = outputDir / f"{username}_{datetime.now():%Y-%m-%d_%H-%M-%S}.mkv"

            print(f"Grabando: {outputFile}", flush=True)

            userAgent = page.evaluate("navigator.userAgent")

            subprocess.run([
                "ffmpeg",
                "-hide_banner",
                "-user_agent", userAgent,
                "-referer", liveUrl,
                "-analyzeduration", "30000000",
                "-probesize", "50000000",
                "-i", streamUrl,
                "-map", "0:v:0",
                "-map", "0:a:0?",
                "-c", "copy",
                "-y",
                str(outputFile)
            ])

            print("Grabación terminada. Reintentando...")
            time.sleep(60)

    except KeyboardInterrupt:
        print("\nDetenido.")
    finally:
        context.close()