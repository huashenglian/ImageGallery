from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QMenu,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from services.cache_manager import CacheEntry, CacheManager
from ui.theme import _surface, _border, _accent, _text, _hover, _text_dim, DEFAULT_BASE


class _CacheCard(QFrame):
    clicked = Signal()
    delete_requested = Signal()
    rescan_requested = Signal()

    def __init__(self, entry: CacheEntry, store_data: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.entry = entry
        self.setObjectName("cacheCard")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(90)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

        is_full = entry.is_full_scan
        path_text = "全盘扫描" if is_full else (entry.scan_path or entry.filename)
        img_count = store_data.get("image_count", 0)
        folder_count = store_data.get("folder_count", 0)
        updated = store_data.get("updated_at", 0)
        time_str = time.strftime("%Y-%m-%d %H:%M", time.localtime(updated)) if updated else "未知"

        path_label = QLabel(path_text)
        path_label.setStyleSheet("font-size:14px;font-weight:bold;")
        path_label.setWordWrap(True)

        info = f"{img_count} 张图片 · {folder_count} 个文件夹 · {time_str}"
        info_label = QLabel(info)
        info_label.setStyleSheet("font-size:11px;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.addWidget(path_label)
        layout.addWidget(info_label)
        layout.addStretch()

        self.customContextMenuRequested.connect(self._show_menu)

    def _show_menu(self, pos) -> None:
        menu = QMenu(self)
        menu.addAction("删除此缓存", self.delete_requested.emit)
        menu.addAction("重新扫描", self.rescan_requested.emit)
        menu.exec(self.mapToGlobal(pos))

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class StartPage(QWidget):
    scan_requested = Signal(str)
    full_cache_scan_requested = Signal()
    cancel_requested = Signal()
    open_cache_requested = Signal(object)
    delete_cache_requested = Signal(object)
    rescan_cache_requested = Signal(object, str, bool)
    classify_requested = Signal()
    clear_all_requested = Signal()

    def __init__(self, parent: QWidget | None = None, base: str = DEFAULT_BASE) -> None:
        super().__init__(parent)
        self._base = base
        self._cards: list[_CacheCard] = []

        self._path_edit = QLineEdit()
        self._path_edit.setPlaceholderText("选择文件夹进行单独扫描…")
        browse = QPushButton("浏览…")
        browse.clicked.connect(self._browse)

        self._full_btn = QPushButton("全盘扫描所有盘符")
        self._full_btn.clicked.connect(self.full_cache_scan_requested.emit)
        self._scan_btn = QPushButton("扫描所选文件夹")
        self._scan_btn.clicked.connect(self._on_folder_scan)

        self._progress = QProgressBar()
        self._progress.setVisible(False)
        self._status = QLabel()
        self._status.setWordWrap(True)

        self._scan_path_label = QLabel("")
        self._scan_path_label.setWordWrap(True)
        self._scan_path_label.setStyleSheet(f"color:{_text_dim(self._base)};font-size:11px;padding:4px 0;")
        self._scan_path_label.setVisible(False)

        self._cancel_btn = QPushButton("中断扫描")
        self._cancel_btn.setStyleSheet(
            "QPushButton{background:#c0392b;color:white;padding:8px 24px;"
            "border-radius:4px;font-weight:bold;}"
            "QPushButton:hover{background:#e74c3c;}"
        )
        self._cancel_btn.setVisible(False)
        self._cancel_btn.clicked.connect(self.cancel_requested.emit)

        path_row = QHBoxLayout()
        path_row.addWidget(self._path_edit, 1)
        path_row.addWidget(browse)

        self._cache_section_label = QLabel("已缓存的图库")
        self._cache_section_label.setStyleSheet("font-size:15px;font-weight:bold;padding:8px 0 4px 0;")

        self._clear_all_btn = QPushButton("清除所有缓存")
        self._clear_all_btn.setFixedHeight(28)
        self._clear_all_btn.clicked.connect(self._on_clear_all)

        cache_header = QHBoxLayout()
        cache_header.addWidget(self._cache_section_label, 1)
        cache_header.addWidget(self._clear_all_btn)

        self._cache_grid_widget = QWidget()
        self._cache_grid = QGridLayout(self._cache_grid_widget)
        self._cache_grid.setContentsMargins(0, 0, 0, 0)
        self._cache_grid.setSpacing(6)

        self._no_cache_label = QLabel("暂无缓存，请扫描文件夹或全盘扫描")
        self._no_cache_label.setStyleSheet(f"color:{_text_dim(self._base)};padding:16px;")

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setWidget(self._cache_grid_widget)
        self._scroll.setVisible(False)

        bottom_row = QHBoxLayout()
        bottom_row.addStretch(1)
        bottom_row.addWidget(self._cancel_btn)

        self._classify_btn = QPushButton("🤖 自动分类")
        self._classify_btn.setFixedHeight(40)
        self._classify_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._classify_btn.clicked.connect(self.classify_requested.emit)
        self._classify_btn.setVisible(False)

        layout = QVBoxLayout(self)
        layout.addWidget(self._full_btn)
        layout.addWidget(QLabel("— 或扫描单个路径 —"))
        layout.addLayout(path_row)
        layout.addWidget(self._scan_btn)
        layout.addWidget(self._progress)
        layout.addWidget(self._status)
        layout.addWidget(self._scan_path_label)
        layout.addLayout(cache_header)
        layout.addWidget(self._no_cache_label)
        layout.addWidget(self._scroll, 1)
        layout.addStretch(1)
        layout.addWidget(self._classify_btn)
        layout.addLayout(bottom_row)

    def refresh_cache_list(self, mgr: CacheManager) -> None:
        for c in self._cards:
            self._cache_grid.removeWidget(c)
            c.deleteLater()
        self._cards.clear()

        entries = mgr.list_entries()
        if not entries:
            self._no_cache_label.setVisible(True)
            self._scroll.setVisible(False)
            return

        self._no_cache_label.setVisible(False)
        self._scroll.setVisible(True)

        for i, entry in enumerate(entries):
            store = mgr.load_entry_store(entry)
            meta = entry.metadata() if entry.store else {}
            if store:
                meta = {
                    "image_count": store.total_images(),
                    "folder_count": len(store.folders),
                    "updated_at": store.updated_at,
                }
            card = _CacheCard(entry, meta)
            card.clicked.connect(lambda e=entry: self.open_cache_requested.emit(e))
            card.delete_requested.connect(lambda e=entry: self.delete_cache_requested.emit(e))
            card.rescan_requested.connect(
                lambda e=entry: self.rescan_cache_requested.emit(
                    e, e.scan_path or "", e.is_full_scan
                )
            )
            self._cards.append(card)
            row, col = divmod(i, 2)
            self._cache_grid.addWidget(card, row, col)

    def _browse(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "选择文件夹")
        if d:
            self._path_edit.setText(d)

    def _on_folder_scan(self) -> None:
        path = self._path_edit.text().strip()
        if not path:
            QMessageBox.warning(self, "提示", "请选择路径。")
            return
        if not Path(path).exists():
            QMessageBox.warning(self, "提示", "路径不存在。")
            return
        self.scan_requested.emit(str(Path(path).resolve()))

    def _on_clear_all(self) -> None:
        if (
            QMessageBox.question(self, "确认", "清除所有缓存文件？\n\n将同时清除图库缓存、分类结果、标签缓存和缩略图。")
            != QMessageBox.StandardButton.Yes
        ):
            return
        self.clear_all_requested.emit()

    def set_busy(self, active: bool) -> None:
        self._full_btn.setEnabled(not active)
        self._scan_btn.setEnabled(not active)
        self._progress.setVisible(active)
        self._scan_path_label.setVisible(active)
        self._cancel_btn.setVisible(active)
        if active:
            self._progress.setRange(0, 0)
            self._scan_path_label.setText("准备扫描…")
        else:
            self._scan_path_label.setText("扫描完成")

    def on_progress(self, current: int, total: int, message: str) -> None:
        if total <= 0:
            self._progress.setRange(0, 0)
        else:
            self._progress.setRange(0, total)
            self._progress.setValue(min(current, total))
        self._status.setText(message)

    def on_finished(self) -> None:
        self.set_busy(False)
        self._status.clear()

    def on_cancelled(self) -> None:
        self.set_busy(False)
        self._status.setText("扫描已中断")
        self._scan_path_label.setText("已中断")

    def on_failed(self, message: str) -> None:
        self.on_finished()
        QMessageBox.critical(self, "失败", message)

    def set_current_path(self, path: str) -> None:
        self._scan_path_label.setText(path)

    def update_theme(self, base: str) -> None:
        self._base = base
        self._scan_path_label.setStyleSheet(f"color:{_text_dim(base)};font-size:11px;padding:4px 0;")
        self._no_cache_label.setStyleSheet(f"color:{_text_dim(base)};padding:16px;")

    def set_classify_visible(self, visible: bool) -> None:
        self._classify_btn.setVisible(visible)
