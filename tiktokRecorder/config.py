import re
import secrets
from pathlib import Path


def normalizeUsername(value):
    username = value.strip().lstrip("@")
    if not re.fullmatch(r"[a-zA-Z0-9._]+", username):
        raise ValueError("Usuario no válido")
    return username


def getProfileSeed(profileDir):
    profileDir.mkdir(parents=True, exist_ok=True)
    seedFile = profileDir / ".invisible-playwright-seed"
    if seedFile.exists():
        return int(seedFile.read_text().strip())

    profileSeed = secrets.randbelow(2**31 - 1) + 1
    seedFile.write_text(str(profileSeed))
    return profileSeed


def getDataDir():
    return Path(__file__).resolve().parent.parent / "data"
