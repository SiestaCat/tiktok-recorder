"""Create isolated Playwright profiles from the saved TikTok login session."""

import hashlib
import secrets
import shutil
from pathlib import Path

from .config import getProfileSeed


PROFILE_FILES = (
    "cookies.sqlite",
    "cookies.sqlite-wal",
    "storage",
    "storage.sqlite",
)


def cloneProfile(profileDir, username):
    """Copy login state to a new profile_USERNAME_HASH directory.

    The base profile is read-only here: --login is the only mode that opens it
    in Playwright. Missing optional files (such as a checkpointed SQLite WAL)
    are simply skipped.
    """
    profileDir = Path(profileDir)
    if not profileDir.is_dir():
        raise FileNotFoundError(f"No existe {profileDir}. Ejecuta primero: python record.py --login")

    # The WAL is not guaranteed to exist: SQLite may have checkpointed it.
    if not (profileDir / "cookies.sqlite").is_file():
        raise FileNotFoundError(f"Falta {profileDir / 'cookies.sqlite'}. Ejecuta primero: python record.py --login")

    while True:
        profileHash = hashlib.sha256(f"{username.lower()}:{secrets.token_hex(16)}".encode()).hexdigest()[:12]
        profileCopy = profileDir.parent / f"profile_{username.lower()}_{profileHash}"
        try:
            profileCopy.mkdir(mode=0o700)
            break
        except FileExistsError:
            continue

    try:
        for name in PROFILE_FILES:
            source = profileDir / name
            target = profileCopy / name
            if not source.exists():
                continue
            if source.is_dir():
                shutil.copytree(source, target)
            else:
                shutil.copy2(source, target)
                target.chmod(0o600)
    except Exception:
        shutil.rmtree(profileCopy)
        raise

    return profileCopy


def createBrowserFactory(profileDir, username, playwrightClass=None):
    """Return a factory which clones the login profile on *every* open.

    BrowserSession.restart() calls this factory again, so browser recovery also
    gets a fresh, isolated profile rather than reusing a closed browser's data.
    """
    profileDir = Path(profileDir)
    profileSeed = getProfileSeed(profileDir)

    if playwrightClass is None:
        from invisible_playwright import InvisiblePlaywright
        playwrightClass = InvisiblePlaywright

    def browserFactory():
        profileCopy = cloneProfile(profileDir, username)
        print(f"@{username} | Perfil de Playwright: {profileCopy}", flush=True)
        return playwrightClass(profile_dir=profileCopy, seed=profileSeed, headless=False)

    return browserFactory
