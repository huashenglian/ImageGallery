from __future__ import annotations

import gzip
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from paths import CACHE_DIR, path_to_cache_filename, cache_filename_to_path, is_full_scan_cache

CACHE_VERSION = 2


def _norm_path(p: str) -> str:
    try:
        return str(Path(p).resolve())
    except OSError:
        return str(Path(p))


class CacheEntry:
    __slots__ = ("filepath", "scan_path", "is_full_scan", "store")

    def __init__(self, filepath: Path, scan_path: Optional[str], is_full_scan: bool,
                 store: Optional["CacheStore"] = None) -> None:
        self.filepath = filepath
        self.scan_path = scan_path
        self.is_full_scan = is_full_scan
        self.store = store

    @property
    def filename(self) -> str:
        return self.filepath.name

    def metadata(self) -> Dict[str, Any]:
        if self.store is None:
            return {}
        total = sum(len(v.get("images", [])) for v in self.store.folders.values())
        return {
            "scan_path": self.scan_path,
            "is_full_scan": self.is_full_scan,
            "updated_at": self.store.updated_at,
            "image_count": total,
            "folder_count": len(self.store.folders),
        }


class CacheStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.version = CACHE_VERSION
        self.updated_at: float = 0.0
        self.roots: List[str] = []
        self.folders: Dict[str, Dict[str, Any]] = {}
        self.folder_children: Dict[str, List[str]] = {}

    def exists(self) -> bool:
        return self.path.is_file()

    def clear(self) -> None:
        self.roots = []
        self.folders = {}
        self.folder_children = {}
        if self.path.is_file():
            try:
                self.path.unlink()
            except OSError:
                pass

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "updated_at": time.time(),
            "roots": self.roots,
            "folders": self.folders,
            "folder_children": self.folder_children,
        }

    @classmethod
    def from_dict(cls, data: dict, path: Path) -> CacheStore:
        store = cls(path)
        store.version = int(data.get("version", 1))
        store.updated_at = float(data.get("updated_at", 0))
        store.roots = list(data.get("roots", []))
        store.folders = dict(data.get("folders", {}))
        store.folder_children = {
            k: list(v) for k, v in data.get("folder_children", {}).items()
        }
        return store

    def save(self) -> None:
        self.updated_at = time.time()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        with gzip.open(self.path, "wb", compresslevel=6) as f:
            f.write(payload)

    def load(self) -> bool:
        if not self.path.is_file():
            return False
        try:
            with gzip.open(self.path, "rb") as f:
                data = json.loads(f.read().decode("utf-8"))
            loaded = CacheStore.from_dict(data, self.path)
            self.version = loaded.version
            self.updated_at = loaded.updated_at
            self.roots = loaded.roots
            self.folders = loaded.folders
            self.folder_children = loaded.folder_children
            return True
        except (OSError, json.JSONDecodeError, EOFError):
            return False

    def folder_images_map(self) -> Dict[str, List[str]]:
        return {k: list(v.get("images", [])) for k, v in self.folders.items()}

    def set_folder(
        self,
        folder_path: str,
        last_modified: float,
        images: List[str],
        parent: Optional[str] = None,
    ) -> None:
        folder_path = _norm_path(folder_path)
        self.folders[folder_path] = {
            "last_modified": last_modified,
            "images": sorted(images, key=str.lower),
        }
        if parent is not None:
            parent = _norm_path(parent)
            children = self.folder_children.setdefault(parent, [])
            if folder_path not in children:
                children.append(folder_path)
                children.sort(key=str.lower)

    def remove_folder(self, folder_path: str) -> None:
        folder_path = _norm_path(folder_path)
        self.folders.pop(folder_path, None)
        self.folder_children.pop(folder_path, None)
        for parent, kids in list(self.folder_children.items()):
            if folder_path in kids:
                kids.remove(folder_path)
            if not kids:
                self.folder_children.pop(parent, None)

    def prune_orphans(self) -> None:
        reachable: set[str] = set()

        def walk(fp: str) -> None:
            if fp in reachable:
                return
            reachable.add(fp)
            for ch in self.folder_children.get(fp, []):
                walk(ch)

        for r in self.roots:
            walk(_norm_path(r))

        for fp in list(self.folders.keys()):
            if fp not in reachable:
                self.remove_folder(fp)

    def total_images(self) -> int:
        return sum(len(v.get("images", [])) for v in self.folders.values())


class CacheManager:
    def __init__(self) -> None:
        self._cache_dir = Path(CACHE_DIR)
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    def list_entries(self) -> List[CacheEntry]:
        entries: List[CacheEntry] = []
        for f in sorted(self._cache_dir.glob("*_galleries_cache.json.gz")):
            sp = cache_filename_to_path(f.name)
            full = is_full_scan_cache(f.name)
            entries.append(CacheEntry(filepath=f, scan_path=sp, is_full_scan=full))
        return entries

    def get_entry(self, scan_path: str, is_full_scan: bool = False) -> Optional[CacheEntry]:
        fname = path_to_cache_filename(scan_path, is_full_scan)
        fpath = self._cache_dir / fname
        if not fpath.is_file():
            return None
        return CacheEntry(filepath=fpath, scan_path=scan_path if not is_full_scan else None,
                          is_full_scan=is_full_scan)

    def get_or_create_store(self, scan_path: str, is_full_scan: bool = False) -> Tuple[CacheStore, bool]:
        fname = path_to_cache_filename(scan_path, is_full_scan)
        fpath = self._cache_dir / fname
        store = CacheStore(fpath)
        loaded = store.load()
        return store, loaded

    def delete_entry(self, entry: CacheEntry) -> bool:
        try:
            if entry.filepath.is_file():
                entry.filepath.unlink()
            return True
        except OSError:
            return False

    def delete_all(self) -> int:
        removed = 0
        for f in self._cache_dir.glob("*_galleries_cache.json.gz"):
            try:
                f.unlink()
                removed += 1
            except OSError:
                pass
        return removed

    def find_child_caches(self, parent_path: str) -> List[CacheEntry]:
        parent_norm = _norm_path(parent_path).rstrip(os.sep).lower()
        children: List[CacheEntry] = []
        for entry in self.list_entries():
            if entry.is_full_scan or entry.scan_path is None:
                continue
            child_norm = _norm_path(entry.scan_path).rstrip(os.sep).lower()
            if child_norm != parent_norm and child_norm.startswith(parent_norm + os.sep):
                children.append(entry)
        return children

    def find_parent_cache(self, child_path: str) -> List[CacheEntry]:
        child_norm = _norm_path(child_path).rstrip(os.sep).lower()
        parents: List[CacheEntry] = []
        for entry in self.list_entries():
            if entry.scan_path is None:
                continue
            entry_norm = _norm_path(entry.scan_path).rstrip(os.sep).lower()
            if child_norm.startswith(entry_norm + os.sep):
                parents.append(entry)
        return parents

    def find_conflicting_caches(self, scan_path: str, is_full_scan: bool) -> List[CacheEntry]:
        conflicts: List[CacheEntry] = []
        if is_full_scan:
            for entry in self.list_entries():
                if not entry.is_full_scan:
                    conflicts.append(entry)
        else:
            conflicts.extend(self.find_child_caches(scan_path))
        return conflicts

    def load_entry_store(self, entry: CacheEntry) -> Optional[CacheStore]:
        store = CacheStore(entry.filepath)
        if store.load():
            entry.store = store
            return store
        return None
