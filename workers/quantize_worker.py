from __future__ import annotations

from PySide6.QtCore import QThread, Signal


class QuantizeWorker(QThread):
    progress = Signal(str, int, str)
    finished_ok = Signal(bool, str)

    def __init__(self, model_id: str, delete_fp32: bool = True, parent=None):
        super().__init__(parent)
        self._model_id = model_id
        self._delete_fp32 = delete_fp32

    def run(self) -> None:
        from services.quantize_service import quantize_model
        if self.isInterruptionRequested():
            self.finished_ok.emit(False, "量化已取消")
            return
        ok, msg = quantize_model(
            self._model_id,
            progress_callback=self._emit_progress,
            delete_fp32=self._delete_fp32,
            cancel_check=self.isInterruptionRequested,
        )
        self.finished_ok.emit(ok, msg)

    def _emit_progress(self, phase: str, pct: int, msg: str) -> None:
        self.progress.emit(phase, pct, msg)
