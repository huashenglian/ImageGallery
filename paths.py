from __future__ import annotations

import logging
import os
import shutil
import sys
import tempfile
from pathlib import Path

def _resolve_project_root() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


PROJECT_ROOT = _resolve_project_root()
CACHE_DIR = os.path.join(PROJECT_ROOT, "cache")
THUMBNAIL_DIR = os.path.join(PROJECT_ROOT, "thumbnails")
MODEL_DIR = os.path.join(PROJECT_ROOT, "model_cache")
LOG_DIR = os.path.join(PROJECT_ROOT, "logs")

_logger = logging.getLogger("paths")


def ensure_dirs() -> None:
    for d in (CACHE_DIR, THUMBNAIL_DIR, LOG_DIR, MODEL_DIR):
        os.makedirs(d, exist_ok=True)


def path_to_cache_filename(scan_path: str, is_full_scan: bool = False) -> str:
    if is_full_scan:
        return "all_disks_galleries_cache.json.gz"
    p = scan_path.rstrip(os.sep).rstrip(":")
    name = p.replace(":", "").replace(os.sep, "_").replace("/", "_")
    while "__" in name:
        name = name.replace("__", "_")
    name = name.strip("_")
    return f"{name}_galleries_cache.json.gz"


def cache_filename_to_path(filename: str) -> str | None:
    if filename == "all_disks_galleries_cache.json.gz":
        return None
    stem = filename.replace("_galleries_cache.json.gz", "")
    if not stem:
        return None
    if len(stem) >= 2 and stem[1] == "_":
        drive = stem[0] + ":\\"
        rest = stem[2:].replace("_", "\\")
        return drive + rest if rest else drive
    return stem.replace("_", "\\")


def is_full_scan_cache(filename: str) -> bool:
    return filename == "all_disks_galleries_cache.json.gz"


def migrate_old_files() -> None:
    old_cache = os.path.join(PROJECT_ROOT, "galleries_cache.json.gz")
    new_cache = os.path.join(CACHE_DIR, "all_disks_galleries_cache.json.gz")
    if os.path.isfile(old_cache):
        if not os.path.isfile(new_cache):
            shutil.move(old_cache, new_cache)
            _logger.info("Migrated old cache to %s", new_cache)
        else:
            os.remove(old_cache)
            _logger.info("Removed old cache (new one already exists)")

    old_thumb_dir = os.path.join(tempfile.gettempdir(), "ImageGalleryApp_thumbs")
    if os.path.isdir(old_thumb_dir):
        moved = 0
        for f in os.listdir(old_thumb_dir):
            src = os.path.join(old_thumb_dir, f)
            dst = os.path.join(THUMBNAIL_DIR, f)
            if os.path.isfile(src) and not os.path.isfile(dst):
                try:
                    shutil.move(src, dst)
                    moved += 1
                except OSError:
                    pass
        if moved:
            _logger.info("Migrated %d thumbnails from temp to %s", moved, THUMBNAIL_DIR)
        try:
            shutil.rmtree(old_thumb_dir, ignore_errors=True)
        except OSError:
            pass

    _migrate_model_dir()


def _migrate_model_dir() -> None:
    old_model_dir = os.path.join(PROJECT_ROOT, "models")
    if not os.path.isdir(old_model_dir):
        return
    moved = 0
    for f in os.listdir(old_model_dir):
        if f in ("model.onnx", "imagenet_classes.txt"):
            src = os.path.join(old_model_dir, f)
            dst = os.path.join(MODEL_DIR, f)
            if not os.path.isfile(dst):
                os.makedirs(MODEL_DIR, exist_ok=True)
                try:
                    shutil.move(src, dst)
                    moved += 1
                    _logger.info("Migrated model file: %s -> %s", src, dst)
                except OSError:
                    pass
    if moved:
        _logger.info("Migrated %d model file(s) from models/ to model_cache/", moved)


def is_project_path(path: str) -> bool:
    try:
        abs_path = os.path.abspath(path)
        return abs_path.startswith(PROJECT_ROOT)
    except OSError:
        return False


def is_excluded_scan_path(path: str) -> bool:
    try:
        abs_path = os.path.abspath(path)
        abs_lower = abs_path.lower().rstrip(os.sep)
    except OSError:
        return False

    if abs_path.startswith(PROJECT_ROOT):
        return True

    if getattr(sys, "frozen", False):
        nuitka_tmp = os.path.dirname(os.path.abspath(sys.executable))
        if abs_path.startswith(nuitka_tmp) and nuitka_tmp != PROJECT_ROOT:
            return True

    thumb_abs = os.path.abspath(THUMBNAIL_DIR).lower().rstrip(os.sep)
    if abs_lower.startswith(thumb_abs):
        return True

    for env_var in ("TEMP", "TMP"):
        tmp = os.environ.get(env_var, "")
        if tmp:
            try:
                tmp_abs = os.path.abspath(tmp).lower().rstrip(os.sep)
            except OSError:
                continue
            if abs_lower == tmp_abs or abs_lower.startswith(tmp_abs + os.sep):
                return True

    if sys.platform == "win32":
        user_profile = os.environ.get("USERPROFILE", "")
        if user_profile:
            try:
                user_lower = os.path.abspath(user_profile).lower().rstrip(os.sep)
            except OSError:
                user_lower = ""
            if user_lower and (abs_lower == user_lower or abs_lower.startswith(user_lower + os.sep)):
                return True

    return False


def cleanup_on_exit() -> None:
    import shutil
    for subdir in (THUMBNAIL_DIR,):
        if os.path.isdir(subdir):
            for f in os.listdir(subdir):
                fp = os.path.join(subdir, f)
                if os.path.isfile(fp):
                    try:
                        os.remove(fp)
                    except OSError:
                        pass
    if os.path.isdir(LOG_DIR):
        for f in os.listdir(LOG_DIR):
            fp = os.path.join(LOG_DIR, f)
            if os.path.isfile(fp):
                try:
                    os.remove(fp)
                except OSError:
                    pass
