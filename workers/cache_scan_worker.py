from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from services.cache_manager import CacheStore
from services.gallery_scanner import full_scan_drives, scan_single_path, incremental_scan


class FullCacheScanWorker(QThread):
    progress = Signal(int, int, str)
    current_path = Signal(str)
    finished_ok = Signal(object)
    failed = Signal(str)

    def __init__(
        self, store: CacheStore, max_workers: int = 8, parent=None
    ) -> None:
        super().__init__(parent)
        self._store = store
        self._max_workers = max_workers

    def run(self) -> None:
        try:

            def prog(cur: int, total: int, msg: str) -> None:
                self.progress.emit(cur, total, msg)

            def on_path(p: str) -> None:
                self.current_path.emit(p)

            full_scan_drives(
                self._store,
                on_progress=prog,
                on_path=on_path,
                cancel_check=self.isInterruptionRequested,
                max_workers=self._max_workers,
            )
            if self.isInterruptionRequested():
                return
            self._store.save()
            self.finished_ok.emit(self._store)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


class SinglePathScanWorker(QThread):
    progress = Signal(int, int, str)
    current_path = Signal(str)
    finished_ok = Signal(object, str)
    failed = Signal(str)

    def __init__(
        self, store: CacheStore, scan_path: str, max_workers: int = 8, parent=None
    ) -> None:
        super().__init__(parent)
        self._store = store
        self._scan_path = scan_path
        self._max_workers = max_workers

    def run(self) -> None:
        try:

            def prog(cur: int, total: int, msg: str) -> None:
                self.progress.emit(cur, total, msg)

            def on_path(p: str) -> None:
                self.current_path.emit(p)

            scan_single_path(
                self._store,
                self._scan_path,
                on_progress=prog,
                on_path=on_path,
                cancel_check=self.isInterruptionRequested,
                max_workers=self._max_workers,
            )
            if self.isInterruptionRequested():
                return
            self._store.save()
            self.finished_ok.emit(self._store, self._scan_path)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


class IncrementalCacheScanWorker(QThread):
    progress = Signal(int, int, str)
    current_path = Signal(str)
    finished_ok = Signal(object, bool)
    failed = Signal(str)

    def __init__(
        self, store: CacheStore, max_workers: int = 8, parent=None
    ) -> None:
        super().__init__(parent)
        self._store = store
        self._max_workers = max_workers

    def run(self) -> None:
        try:

            def prog(cur: int, total: int, msg: str) -> None:
                self.progress.emit(cur, total, msg)

            def on_path(p: str) -> None:
                self.current_path.emit(p)

            changed = incremental_scan(
                self._store,
                on_progress=prog,
                on_path=on_path,
                max_workers=self._max_workers,
                cancel_check=self.isInterruptionRequested,
                discover_new_roots=True,
            )
            if self.isInterruptionRequested():
                return
            if changed:
                self._store.save()
            self.finished_ok.emit(self._store, changed)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
