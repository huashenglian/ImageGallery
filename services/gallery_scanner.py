from __future__ import annotations

import os
import queue
import string
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, List, Optional, Set, Tuple

from models.gallery import IMAGE_EXTENSIONS, IMAGE_SUFFIX_TUPLE
from paths import is_project_path, is_excluded_scan_path
from services.cache_manager import CacheStore, _norm_path

ProgressCb = Callable[[int, int, str], None]
PathCb = Callable[[str], None]

SYSTEM_DIR_NAMES: Set[str] = {
    "windows",
    "winnt",
    "program files",
    "program files (x86)",
    "programdata",
    "system volume information",
    "$recycle.bin",
    "recovery",
    "perflogs",
    "temp",
    "appdata",
}

PRIORITY_DIR_NAMES: Set[str] = {
    "users",
    "desktop",
}


def _is_system_dir(path_str: str) -> bool:
    name = os.path.basename(os.path.normpath(path_str)).lower()
    return name in SYSTEM_DIR_NAMES


def _is_priority_dir(path_str: str) -> bool:
    name = os.path.basename(os.path.normpath(path_str)).lower()
    if name in PRIORITY_DIR_NAMES:
        return True
    if len(path_str) >= 3 and path_str[1] == ":" and path_str[0].upper() != "C":
        return True
    return False


def list_windows_drives() -> List[Path]:
    drives: List[Path] = []
    if sys.platform == "win32":
        for letter in string.ascii_uppercase:
            p = Path(f"{letter}:\\")
            try:
                if p.exists():
                    drives.append(p)
            except OSError:
                continue
    else:
        drives.append(Path("/"))
    return drives


def folder_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


_ScanResult = List[Tuple[str, float, List[str], Optional[str], List[str]]]


def _scan_tree_worker(
    root_path: str,
    parent: Optional[str],
    cancel_check: Optional[Callable[[], bool]],
) -> _ScanResult:
    results: _ScanResult = []
    norm_root = os.path.normpath(root_path)

    def _scan(dir_path: str, cur_parent: Optional[str]) -> None:
        if cancel_check and cancel_check():
            return
        if is_excluded_scan_path(dir_path):
            return

        images: list[str] = []
        subdirs: list[str] = []

        try:
            with os.scandir(dir_path) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if entry.is_symlink():
                                continue
                            full = entry.path
                            if _is_system_dir(full):
                                continue
                            if is_excluded_scan_path(full):
                                continue
                            subdirs.append(entry.name)
                        elif entry.is_file(follow_symlinks=False):
                            ext = os.path.splitext(entry.name)[1].lower()
                            if ext in IMAGE_EXTENSIONS:
                                images.append(_norm_path(entry.path))
                    except OSError:
                        continue
        except OSError:
            return

        subdirs.sort(key=str.lower)
        images.sort(key=str.lower)

        try:
            mtime = os.path.getmtime(dir_path)
        except OSError:
            mtime = 0.0

        children = [_norm_path(os.path.join(dir_path, d)) for d in subdirs]
        results.append((_norm_path(dir_path), mtime, images, cur_parent, children))

        for d in subdirs:
            if cancel_check and cancel_check():
                return
            child_path = os.path.join(dir_path, d)
            child_parent = cur_parent if os.path.normpath(dir_path) == norm_root else _norm_path(dir_path)
            _scan(child_path, child_parent)

    _scan(root_path, parent)
    return results


def full_scan_drives(
    store: CacheStore,
    drives: Optional[List[Path]] = None,
    on_progress: Optional[ProgressCb] = None,
    on_path: Optional[PathCb] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
    max_workers: int = 8,
) -> None:
    drive_list = drives or list_windows_drives()
    store.roots = [_norm_path(str(d.resolve())) for d in drive_list]
    store.folders.clear()
    store.folder_children.clear()

    for drive in drive_list:
        drive_str = _norm_path(str(drive.resolve()))
        try:
            mtime = folder_mtime(drive)
            images = sorted(
                [
                    str(f.resolve())
                    for f in drive.iterdir()
                    if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS
                ],
                key=str.lower,
            )
        except OSError:
            mtime = 0.0
            images = []
        store.set_folder(drive_str, mtime, images, None)

    scan_targets: List[Tuple[str, Optional[str]]] = []
    for drive in drive_list:
        drive_str = _norm_path(str(drive.resolve()))
        try:
            for child in drive.iterdir():
                if not child.is_dir() or child.is_symlink():
                    continue
                child_str = _norm_path(str(child.resolve()))
                if _is_system_dir(child_str):
                    continue
                if is_excluded_scan_path(child_str):
                    continue
                scan_targets.append((child_str, drive_str))
        except OSError:
            continue

    scan_targets.sort(
        key=lambda x: (0 if _is_priority_dir(x[0]) else 1, x[0].lower())
    )

    total = len(scan_targets)
    if total == 0:
        if on_progress:
            on_progress(0, 0, "无可用目录")
        return

    progress_q: queue.Queue[Tuple[str, _ScanResult]] = queue.Queue()

    def _worker(target: str, parent: Optional[str]) -> None:
        result = _scan_tree_worker(target, parent, cancel_check)
        progress_q.put((target, result))

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = []
        for target, parent in scan_targets:
            if cancel_check and cancel_check():
                break
            futures.append(pool.submit(_worker, target, parent))

        done = 0
        while done < total:
            if cancel_check and cancel_check():
                for f in futures:
                    f.cancel()
                return
            try:
                item = progress_q.get(timeout=0.5)
            except queue.Empty:
                if all(f.done() for f in futures) and progress_q.empty():
                    break
                continue

            target, results = item
            done += 1

            for folder_str, mtime, images, parent_str, children in results:
                store.folders[folder_str] = {
                    "last_modified": mtime,
                    "images": images,
                }
                if parent_str is not None:
                    siblings = store.folder_children.setdefault(parent_str, [])
                    if folder_str not in siblings:
                        siblings.append(folder_str)
                        siblings.sort(key=str.lower)
                if children:
                    store.folder_children[folder_str] = children

            if on_path:
                on_path(target)
            if on_progress:
                on_progress(done, total, f"扫描: {Path(target).name}")

    if on_progress:
        on_progress(total, total, "全盘扫描完成")


def scan_single_path(
    store: CacheStore,
    root_path: str,
    on_progress: Optional[ProgressCb] = None,
    on_path: Optional[PathCb] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
    max_workers: int = 8,
) -> None:
    root = Path(root_path)
    root_str = _norm_path(str(root.resolve()))
    store.roots = [root_str]
    store.folders.clear()
    store.folder_children.clear()

    try:
        mtime = folder_mtime(root)
        images = sorted(
            [
                str(f.resolve())
                for f in root.iterdir()
                if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS
            ],
            key=str.lower,
        )
    except OSError:
        mtime = 0.0
        images = []
    store.set_folder(root_str, mtime, images, None)

    scan_targets: List[Tuple[str, Optional[str]]] = []
    try:
        for child in root.iterdir():
            if not child.is_dir() or child.is_symlink():
                continue
            child_str = _norm_path(str(child.resolve()))
            if _is_system_dir(child_str):
                continue
            if is_excluded_scan_path(child_str):
                continue
            scan_targets.append((child_str, root_str))
    except OSError:
        pass

    scan_targets.sort(key=lambda x: x[0].lower())

    total = len(scan_targets)
    if total == 0:
        if on_progress:
            on_progress(1, 1, "扫描完成（无子目录）")
        return

    progress_q: queue.Queue[Tuple[str, _ScanResult]] = queue.Queue()

    def _worker(target: str, parent: Optional[str]) -> None:
        result = _scan_tree_worker(target, parent, cancel_check)
        progress_q.put((target, result))

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = []
        for target, parent in scan_targets:
            if cancel_check and cancel_check():
                break
            futures.append(pool.submit(_worker, target, parent))

        done = 0
        while done < total:
            if cancel_check and cancel_check():
                for f in futures:
                    f.cancel()
                return
            try:
                item = progress_q.get(timeout=0.5)
            except queue.Empty:
                if all(f.done() for f in futures) and progress_q.empty():
                    break
                continue

            target, results = item
            done += 1

            for folder_str, mtime, images, parent_str, children in results:
                store.folders[folder_str] = {
                    "last_modified": mtime,
                    "images": images,
                }
                if parent_str is not None:
                    siblings = store.folder_children.setdefault(parent_str, [])
                    if folder_str not in siblings:
                        siblings.append(folder_str)
                        siblings.sort(key=str.lower)
                if children:
                    store.folder_children[folder_str] = children

            if on_path:
                on_path(target)
            if on_progress:
                on_progress(done, total, f"扫描: {Path(target).name}")

    if on_progress:
        on_progress(total, total, "扫描完成")


def incremental_scan(
    store: CacheStore,
    on_progress: Optional[ProgressCb] = None,
    on_path: Optional[PathCb] = None,
    max_workers: int = 8,
    cancel_check: Optional[Callable[[], bool]] = None,
    discover_new_roots: bool = True,
) -> bool:
    changed = False
    folders = list(store.folders.items())
    total = max(len(folders), 1)
    done = 0
    to_remove: List[str] = []
    updates: dict[str, dict] = {}

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(_incremental_update_one, fp, rec): fp for fp, rec in folders
        }
        for fut in futures:
            if cancel_check and cancel_check():
                return changed
            fp, new_rec, remove = fut.result()
            done += 1
            if on_path:
                on_path(fp)
            if on_progress and (done % 100 == 0 or done == total):
                on_progress(done, total, f"增量检查 {done}/{total}")
            if remove:
                to_remove.append(fp)
                changed = True
            elif new_rec is not None:
                updates[fp] = new_rec
                changed = True

    for fp in to_remove:
        store.remove_folder(fp)
    for fp, rec in updates.items():
        store.folders[fp] = rec

    if discover_new_roots:
        if _discover_new_folders(store):
            changed = True

    store.prune_orphans()
    return changed


def _incremental_update_one(
    folder_path: str,
    record: dict,
) -> Tuple[str, Optional[dict], bool]:
    p = Path(folder_path)
    if not p.is_dir():
        return folder_path, None, True
    try:
        current_mtime = folder_mtime(p)
    except OSError:
        return folder_path, None, True

    cached_mtime = float(record.get("last_modified", 0))
    images = list(record.get("images", []))
    valid = [img for img in images if Path(img).is_file()]

    if current_mtime <= cached_mtime:
        if len(valid) != len(images):
            return folder_path, {
                "last_modified": current_mtime,
                "images": sorted(valid, key=str.lower),
            }, False
        return folder_path, None, False

    new_images = sorted(
        [
            str(f.resolve())
            for f in p.iterdir()
            if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS
        ],
        key=str.lower,
    )
    return folder_path, {"last_modified": current_mtime, "images": new_images}, False


def _discover_new_folders(store: CacheStore) -> bool:
    changed = False
    for drive in list_windows_drives():
        root = _norm_path(str(drive.resolve()))
        if root not in store.roots:
            store.roots.append(root)
            changed = True
        try:
            entries = list(drive.iterdir())
        except OSError:
            continue
        for child in entries:
            if not child.is_dir() or child.is_symlink():
                continue
            try:
                cp = _norm_path(str(child.resolve()))
            except OSError:
                continue
            if _is_system_dir(cp):
                continue
            if is_excluded_scan_path(cp):
                continue
            if cp in store.folders:
                continue
            _scan_subtree_into_store(store, cp, root)
            changed = True
    return changed


def _scan_subtree_into_store(
    store: CacheStore,
    folder_path: str,
    parent: Optional[str],
) -> None:
    p = Path(folder_path)
    if not p.is_dir():
        return
    mtime = folder_mtime(p)
    images = sorted(
        [
            str(f.resolve())
            for f in p.iterdir()
            if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS
        ],
        key=str.lower,
    )
    store.set_folder(folder_path, mtime, images, parent)
    try:
        subdirs: List[str] = []
        for child in p.iterdir():
            if not child.is_dir() or child.is_symlink():
                continue
            try:
                cp = _norm_path(str(child.resolve()))
            except OSError:
                continue
            if _is_system_dir(cp):
                continue
            if is_excluded_scan_path(cp):
                continue
            subdirs.append(cp)
        if subdirs:
            store.folder_children[folder_path] = sorted(subdirs, key=str.lower)
            for sd in subdirs:
                _scan_subtree_into_store(store, sd, folder_path)
    except OSError:
        pass
