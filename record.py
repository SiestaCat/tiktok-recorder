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
dataDir = Path(__file__).resolve().parent / "data"
profileDir = dataDir / "profile"
profileDir.mkdir(parents=True, exist_ok=True)
seedFile = profileDir / ".invisible-playwright-seed"
if seedFile.exists():
    profileSeed = int(seedFile.read_text().strip())
else:
    profileSeed = secrets.randbelow(2**31 - 1) + 1
    seedFile.write_text(str(profileSeed))

outputDir = dataDir / "videos" / username
outputDir.mkdir(parents=True, exist_ok=True)

streams = {}

def addStream(value, path="", codec=""):
    if isinstance(value, dict):
        sdkParams = value.get("sdk_params")
        if isinstance(sdkParams, str):
            try:
                sdkParams = json.loads(sdkParams)
            except ValueError:
                sdkParams = {}

        if isinstance(sdkParams, dict):
            codec = str(sdkParams.get("VCodec") or sdkParams.get("v_codec") or codec).lower()
        codec = str(value.get("VCodec") or value.get("v_codec") or codec).lower()

        for key, item in value.items():
            addStream(item, f"{path}/{key}", codec)
    elif isinstance(value, list):
        for item in value:
            addStream(item, path, codec)
    elif isinstance(value, str):
        if value.startswith(("{", "[")):
            try:
                addStream(json.loads(value), path, codec)
            except (ValueError, RecursionError):
                pass
        elif value.startswith("https://"):
            parsed = urlsplit(value)
            isFlv = ".flv" in parsed.path.lower() or "pull-flv" in parsed.netloc.lower()
            isHls = ".m3u8" in parsed.path.lower()

            if isFlv or isHls:
                score = 100 if "origin" in path.lower() else 0
                score += 10 if isFlv else 0
                if codec in ("h264", "avc", "264"):
                    score += 1000
                elif codec in ("h265", "hevc", "265") or "hevc" in path.lower():
                    score -= 1000
                streams[value] = max(streams.get(value, float("-inf")), score)

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

            outputFile = outputDir / f"{username}_{datetime.now():%Y-%m-%d_%H-%M-%S}.mkv"
            userAgent = page.evaluate("navigator.userAgent")

            for streamUrl in sorted(streams, key=streams.get, reverse=True):
                print(f"Grabando: {outputFile}", flush=True)

                result = subprocess.run([
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

                if result.returncode == 0:
                    break
                if outputFile.exists() and outputFile.stat().st_size > 0:
                    print(f"FFmpeg terminó con error; se conserva la grabación parcial: {outputFile}")
                    break
                outputFile.unlink(missing_ok=True)
                print("La URL no se pudo grabar. Probando otra transmisión disponible...")
            else:
                print("No se pudo grabar ninguna transmisión. Si solo hay H.265, actualiza FFmpeg a 8.1 o superior.")

            print("Grabación terminada. Reintentando...")
            time.sleep(60)

    except KeyboardInterrupt:
        print("\nDetenido.")
    finally:
        context.close()
