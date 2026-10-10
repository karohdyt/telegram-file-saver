"""Central configuration.

Everything is read from environment variables (or from a ``.env`` file next to
this script, if python-dotenv is installed). See ``.env.example``.
"""
import os

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # python-dotenv is optional
    pass


def _env(name, default=""):
    value = os.getenv(name)
    return default if value is None else value.strip()


def _int(name, default):
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


def _id_set(raw):
    ids = set()
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if part.lstrip("-").isdigit():
            ids.add(int(part))
    return ids


# --- Bot ---------------------------------------------------------------------
BOT_TOKEN = _env("BOT_TOKEN")

# Base URL of the Bot API server. Set it to an empty value to use the official
# cloud API (https://api.telegram.org) - files are then limited to 20 MB.
API_URL_BASE = _env("TELEGRAM_API_URL", "http://localhost:8081").rstrip("/")
USES_LOCAL_API = bool(API_URL_BASE)

# Telegram numeric user IDs that may use the bot (comma separated).
# The bot writes files to your server, so it is private by design.
ALLOWED_USER_IDS = _id_set(_env("ALLOWED_USER_IDS"))

# --- Storage -----------------------------------------------------------------
DEFAULT_SAVE_PATH = _env("DEFAULT_SAVE_PATH") or "/root/savedfile"

# The local Bot API server runs in a container and returns absolute paths
# *inside that container*. These two settings translate them to the real
# location of the same folder on this machine (the Docker volume's mount point on the host).
# Leave both empty if the API server runs directly on this machine.
API_CONTAINER_DIR = _env("API_CONTAINER_DIR", "/var/lib/telegram-bot-api").rstrip("/")
API_HOST_DIR = _env(
    "API_HOST_DIR", "/var/lib/docker/volumes/telegram-bot-api-data/_data"
).rstrip("/")

# Optional sandbox: if set, users may only pick folders inside SAVE_ROOT (and
# can then simply type a folder name such as "movies"). Empty = any folder.
SAVE_ROOT = _env("SAVE_ROOT").rstrip("/")

# Safety margin that must stay free after saving a file.
MIN_FREE_MB = _int("MIN_FREE_MB", 200)

# Largest file the bot accepts (2000 MB with a local server, 20 MB on the cloud API).
MAX_FILE_MB = _int("MAX_FILE_MB", 2000 if USES_LOCAL_API else 20)

# HTTP read timeout (seconds). getFile on a local server blocks until the whole
# file has been downloaded, so big files need a generous value.
REQUEST_TIMEOUT = _int("REQUEST_TIMEOUT", 1800)

# Where per-user settings (chosen save path) are persisted.
SETTINGS_FILE = _env("SETTINGS_FILE") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "settings.json"
)

# Users may not choose these locations as a save path.
BLOCKED_PATHS = (
    "/bin", "/boot", "/dev", "/etc", "/lib", "/lib64",
    "/proc", "/run", "/sbin", "/sys", "/usr",
)
