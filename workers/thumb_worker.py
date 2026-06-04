from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal


class ThumbSignals(QObject):
    ready = Signal(str, str)
    error = Signal(str, str)


class ThumbTask(QRunnable):
    def __init__(self, source_path: str, cache, signals: ThumbSignals) -> None:
        super().__init__()
        self._source_path = source_path
        self._cache = cache
        self._signals = signals

    def run(self) -> None:
        try:
            if not Path(self._source_path).is_file():
                self._signals.error.emit(self._source_path, "文件不存在")
                return
            out = self._cache.ensure_thumbnail(self._source_path)
            self._signals.ready.emit(self._source_path, str(out))
        except RuntimeError:
            pass
        except Exception as exc:
            try:
                self._signals.error.emit(self._source_path, str(exc))
            except RuntimeError:
                pass


class ThumbBatchTask(QRunnable):
    """批量缩略图任务，一次提交多张图片给 WIC 扩展处理。"""

    def __init__(self, source_paths: list[str], cache, signals: ThumbSignals) -> None:
        super().__init__()
        self._source_paths = source_paths
        self._cache = cache
        self._signals = signals

    def run(self) -> None:
        try:
            results = self._cache.ensure_thumbnails_batch(self._source_paths)
            for sp in self._source_paths:
                if sp in results:
                    self._signals.ready.emit(sp, str(results[sp]))
                else:
                    self._signals.error.emit(sp, "生成失败")
        except RuntimeError:
            pass
        except Exception as exc:
            try:
                for sp in self._source_paths:
                    self._signals.error.emit(sp, str(exc))
            except RuntimeError:
                pass


def global_thumb_pool() -> QThreadPool:
    pool = QThreadPool.globalInstance()
    pool.setMaxThreadCount(min(8, pool.maxThreadCount()))
    return pool
