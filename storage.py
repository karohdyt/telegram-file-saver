"""Filesystem helpers: path validation, safe file names, disk-space checks and
per-user settings. No Telegram code in here, so it is easy to test."""
import json
import logging
import os
import re
import shutil
import threading
from dataclasses import dataclass
from typing import Optional

import config

log = logging.getLogger("filesaver.storage")


# --- Formatting --------------------------------------------------------------
def human_size(num):
    if num is None:
        return "نامشخص"
    num = float(num)
    units = ("B", "KB", "MB", "GB", "TB")
    for unit in units:
        if num < 1024 or unit == units[-1]:
            return f"{int(num)} B" if unit == "B" else f"{num:.2f} {unit}"
        num /= 1024
    return str(num)


# --- Paths and names ---------------------------------------------------------
def is_blocked(path):
    if path == "/":
        return True
    return any(path == b or path.startswith(b + "/") for b in config.BLOCKED_PATHS)


def _inside_root(path):
    """True if ``path`` is allowed by the SAVE_ROOT sandbox (if one is set)."""
    root = config.SAVE_ROOT
    if not root:
        return True
    return any(
        path == r or path.startswith(r + "/") for r in {root, os.path.realpath(root)}
    )


def validate_save_path(raw):
    """Validate (and create) a save directory.

    Returns ``(real_path, None)`` on success or ``(None, error_message)``.
    With SAVE_ROOT set, relative names ("movies") are accepted and placed
    inside the sandbox.
    """
    raw = (raw or "").strip()
    if not raw:
        return None, "مسیر خالی است."
    if "\x00" in raw or "\n" in raw:
        return None, "مسیر نامعتبر است."

    if config.SAVE_ROOT and not raw.startswith("/"):
        raw = os.path.join(config.SAVE_ROOT, raw)
    if not raw.startswith("/"):
        return None, "مسیر باید مطلق باشد و با / شروع شود."

    path = os.path.normpath("/" + raw.lstrip("/"))
    if is_blocked(path):
        return None, "این مسیر مجاز نیست (مسیرهای سیستمی قابل انتخاب نیستند)."
    out_of_root = f"فقط داخل این پوشه می‌توانید مسیر انتخاب کنید: {config.SAVE_ROOT}"
    if not _inside_root(path):
        return None, out_of_root

    try:
        os.makedirs(path, exist_ok=True)
    except OSError as exc:
        return None, f"ساخت پوشه ممکن نشد: {exc.strerror or exc}"

    real = os.path.realpath(path)
    if is_blocked(real):
        return None, "این مسیر مجاز نیست (مسیرهای سیستمی قابل انتخاب نیستند)."
    if not _inside_root(real):
        return None, out_of_root
    if not os.path.isdir(real) or not os.access(real, os.W_OK | os.X_OK):
        return None, "روی این پوشه دسترسی نوشتن وجود ندارد."
    return real, None


_BAD_CHARS = re.compile(r"[\x00-\x1f\x7f/\\]")


def safe_filename(name, fallback):
    """Strip directories / control characters so a name can never escape the
    target folder (e.g. ``../../etc/passwd``)."""
    name = os.path.basename((name or "").replace("\\", "/"))
    name = _BAD_CHARS.sub("_", name).strip().strip(".")
    if not name:
        name = fallback
    root, ext = os.path.splitext(name)
    while len((root + ext).encode("utf-8")) > 200 and root:
        root = root[:-1]
    return (root + ext) or fallback


def unique_path(directory, filename):
    """Never overwrite: ``file.pdf`` -> ``file (1).pdf`` -> ``file (2).pdf`` ..."""
    candidate = os.path.join(directory, filename)
    base, ext = os.path.splitext(filename)
    counter = 1
    while os.path.exists(candidate) or os.path.exists(candidate + ".part"):
        candidate = os.path.join(directory, f"{base} ({counter}){ext}")
        counter += 1
    return candidate


def recent_files(directory, limit=10):
    """Return ``(newest_files, file_count, total_bytes)`` for a directory."""
    entries, total, count = [], 0, 0
    with os.scandir(directory) as it:
        for entry in it:
            try:
                if not entry.is_file(follow_symlinks=False) or entry.name.endswith(".part"):
                    continue
                st = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            count += 1
            total += st.st_size
            entries.append((st.st_mtime, entry.name, st.st_size))
    entries.sort(reverse=True)
    return entries[:limit], count, total


# --- Disk space --------------------------------------------------------------
@dataclass
class SpaceReport:
    ok: bool
    dest_needed: int
    dest_free: int
    staging_needed: Optional[int]  # None when there is no separate staging dir
    staging_free: Optional[int]
    shared_disk: bool  # staging and destination draw from one pool of free space


def _mount_point(path):
    path = os.path.realpath(path)
    while not os.path.ismount(path):
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent
    return path


def check_space(dest_dir, size):
    """Can ``size`` bytes be received and stored?

    With a local Bot API server the file is first downloaded into the server's
    data directory ("staging") and then moved to ``dest_dir``:

    * same mount            -> the move is an instant rename: room for ONE copy
    * same disk, other mount (e.g. a bind mount / btrfs subvolume) -> the move
      is copy + delete, so two copies exist at the same time
    * different disks       -> room for one copy on each disk
    """
    margin = config.MIN_FREE_MB * 1024 * 1024
    size = size or 0
    dest_free = shutil.disk_usage(dest_dir).free

    staging = config.API_HOST_DIR
    if not (config.USES_LOCAL_API and staging and os.path.isdir(staging)):
        needed = size + margin
        return SpaceReport(dest_free >= needed, needed, dest_free, None, None, False)

    staging_free = shutil.disk_usage(staging).free
    if _mount_point(staging) == _mount_point(dest_dir):
        needed = size + margin
        return SpaceReport(staging_free >= needed, needed, dest_free, needed, staging_free, True)
    if os.stat(staging).st_dev == os.stat(dest_dir).st_dev:
        needed = 2 * size + margin
        return SpaceReport(staging_free >= needed, needed, dest_free, needed, staging_free, True)
    needed = size + margin
    ok = staging_free >= needed and dest_free >= needed
    return SpaceReport(ok, needed, dest_free, needed, staging_free, False)


# --- Per-user settings -------------------------------------------------------
class SettingsStore:
    """Tiny JSON-backed store: remembers each user's chosen save path."""

    def __init__(self, path):
        self._path = path
        self._lock = threading.Lock()
        self._data = {}
        try:
            with open(path, encoding="utf-8") as fh:
                self._data = json.load(fh)
        except (OSError, ValueError):
            self._data = {}

    def get_path(self, user_id):
        return self._data.get(str(user_id), {}).get("save_path") or config.DEFAULT_SAVE_PATH

    def is_default(self, user_id):
        return self.get_path(user_id) == config.DEFAULT_SAVE_PATH

    def set_path(self, user_id, path):
        with self._lock:
            self._data.setdefault(str(user_id), {})["save_path"] = path
            self._flush()

    def reset_path(self, user_id):
        with self._lock:
            self._data.get(str(user_id), {}).pop("save_path", None)
            self._flush()

    def _flush(self):
        tmp = self._path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, self._path)
        except OSError as exc:
            log.warning("Could not write settings file %s: %s", self._path, exc)
