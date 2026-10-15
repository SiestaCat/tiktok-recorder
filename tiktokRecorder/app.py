import argparse
import queue
import shutil
import threading
import time

from .browser import BrowserSession, checkLiveWithRecovery
from .config import getDataDir, getProfileSeed, normalizeUsername
from .recording import RecordingJob, RETRY_SECONDS
from .profile import createBrowserFactory
from .streams import getLiveStreams


PROMPT = "\nUsuario (@nombre), 'estado', 'parar @nombre' o 'salir': "


def readCommands(commands):
    while True:
        try:
            command = input(PROMPT)
        except EOFError:
            commands.put(None)
            return
        except KeyboardInterrupt:
            commands.put("salir")
            return
        commands.put(command)
        if command.strip().lower() in ("salir", "exit", "quit", "q"):
            return


def addUser(jobs, value, dataDir):
    try:
        username = normalizeUsername(value)
    except ValueError as error:
        print(error, flush=True)
        return

    if username.lower() in jobs:
        print(f"@{username} ya está en seguimiento.", flush=True)
        return

    jobs[username.lower()] = RecordingJob(username, dataDir / "videos" / username)
    print(f"@{username} añadido. Cada comprobación usará un Playwright independiente con las cookies del login.", flush=True)


def showStatus(jobs):
    if not jobs:
        print("No hay usuarios en seguimiento.", flush=True)
        return
    for job in jobs.values():
        if job.isRecording():
            print(f"@{job.username}: grabando -> {job.outputFile}", flush=True)
        else:
            seconds = max(0, int(job.nextCheckAt - time.monotonic()))
            print(f"@{job.username}: esperando (próxima comprobación en {seconds} s)", flush=True)


def handleCommand(command, jobs, dataDir):
    value = command.strip()
    if not value:
        return True
    if value.lower() in ("salir", "exit", "quit", "q"):
        return False
    if value.lower() in ("estado", "status", "list"):
        showStatus(jobs)
        return True
    if value.lower().startswith("parar "):
        try:
            username = normalizeUsername(value[6:])
        except ValueError as error:
            print(error, flush=True)
            return True
        job = jobs.pop(username.lower(), None)
        if job is None:
            print(f"@{username} no está en seguimiento.", flush=True)
        else:
            job.stop()
            print(f"@{username} eliminado del seguimiento.", flush=True)
        return True

    addUser(jobs, value, dataDir)
    return True


def runRecorder(initialUsername, dataDir, browserFactoryForUser=None):
    if browserFactoryForUser is None:
        browserFactoryForUser = lambda username: createBrowserFactory(dataDir / "profile", username)
    jobs = {}
    commands = queue.Queue()

    if initialUsername:
        addUser(jobs, initialUsername, dataDir)

    print("Puedes añadir otro @usuario en cualquier momento sin detener las grabaciones.", flush=True)
    print("Comandos disponibles: estado, parar @usuario, salir.", flush=True)
    threading.Thread(target=readCommands, args=(commands,), daemon=True).start()

    try:
        running = True
        while running:
            while True:
                try:
                    command = commands.get_nowait()
                except queue.Empty:
                    break
                if command is None:
                    if not jobs:
                        print("Entrada cerrada y ningún usuario en seguimiento.", flush=True)
                        running = False
                    else:
                        print("Entrada cerrada. El seguimiento continuará hasta Ctrl+C.", flush=True)
                    continue
                running = handleCommand(command, jobs, dataDir)
                if not running:
                    break

            if not running:
                break

            for job in list(jobs.values()):
                job.update()
                if job.isRecording() or time.monotonic() < job.nextCheckAt:
                    continue

                print(f"Comprobando @{job.username}...", flush=True)
                try:
                    # Every attempt uses a brand-new Playwright instance and a
                    # fresh cloned profile. Close it after obtaining the URLs;
                    # FFmpeg continues recording independently.
                    with BrowserSession(browserFactoryForUser(job.username)) as session:
                        streamUrls, userAgent = checkLiveWithRecovery(session, job.username, getLiveStreams)
                    if not streamUrls:
                        print(f"@{job.username} | No se encontró una URL de transmisión.", flush=True)
                        continue
                    job.start(streamUrls, userAgent)
                except Exception as error:
                    print(f"@{job.username} | No se pudo comprobar el LIVE: {error}", flush=True)
                finally:
                    if not job.isRecording():
                        job.nextCheckAt = time.monotonic() + RETRY_SECONDS

            time.sleep(0.2)
    except KeyboardInterrupt:
        print("\nDeteniendo...", flush=True)
    finally:
        for job in jobs.values():
            job.stop()


def main():
    parser = argparse.ArgumentParser(description="Graba varios LIVE con Playwright independiente por intento y un login compartido.")
    parser.add_argument("username", nargs="?", help="Usuario inicial opcional (@usuario)")
    parser.add_argument("--login", action="store_true", help="Iniciar sesión en TikTok en el perfil compartido")
    args = parser.parse_args()

    if args.username:
        try:
            args.username = normalizeUsername(args.username)
        except ValueError as error:
            parser.error(str(error))

    if not args.login and shutil.which("ffmpeg") is None:
        parser.error("No se encontró ffmpeg en PATH")

    dataDir = getDataDir()
    profileDir = dataDir / "profile"
    if args.login:
        # --login is the only mode that opens (and writes to) data/profile.
        from invisible_playwright import InvisiblePlaywright
        profileSeed = getProfileSeed(profileDir)
        with BrowserSession(lambda: InvisiblePlaywright(profile_dir=profileDir, seed=profileSeed, headless=False)) as session:
            session.getPage().goto("https://www.tiktok.com")
            input("Inicia sesión en TikTok y pulsa Enter aquí...")
        return

    # Do not open data/profile with Playwright when recording. Each attempt
    # copies the selected session files to profile_USERNAME_HASH instead.
    runRecorder(args.username, dataDir)
