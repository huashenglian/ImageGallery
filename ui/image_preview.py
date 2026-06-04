from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
)


class ImagePreviewDialog(QDialog):
    def __init__(self, paths: list[str], start_index: int, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("图片预览")
        self.setModal(True)
        self.resize(960, 720)
        self._paths = list(paths)
        self._index = max(0, min(start_index, len(self._paths) - 1)) if self._paths else 0

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image_label = QLabel()
        self._image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image_label.setMinimumSize(400, 300)
        self._scroll.setWidget(self._image_label)

        self._resolution_label = QLabel("")
        self._resolution_label.setStyleSheet("color: gray; font-size: 11px;")
        self._path_label = QLabel()
        self._path_label.setWordWrap(True)
        self._path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        prev_btn = QPushButton("上一张 (←)")
        next_btn = QPushButton("下一张 (→)")
        prev_btn.clicked.connect(self._prev)
        next_btn.clicked.connect(self._next)
        info_btn = QPushButton("复制路径")
        info_btn.clicked.connect(self._copy_path)

        bar = QHBoxLayout()
        bar.addWidget(prev_btn)
        bar.addWidget(next_btn)
        bar.addStretch(1)
        bar.addWidget(info_btn)

        root = QVBoxLayout(self)
        root.addWidget(self._scroll, 1)
        root.addWidget(self._resolution_label)
        root.addWidget(self._path_label)
        root.addLayout(bar)
        self._reload_image()

    def _copy_path(self) -> None:
        if not self._paths:
            return
        from PySide6.QtGui import QGuiApplication

        QGuiApplication.clipboard().setText(self._paths[self._index])
        QMessageBox.information(self, "已复制", "完整路径已复制到剪贴板。")

    def _reload_image(self) -> None:
        if not self._paths:
            self._image_label.setText("无图片")
            self._path_label.setText("")
            self._resolution_label.setText("")
            return
        path = self._paths[self._index]
        self._path_label.setText(path)
        pix = QPixmap(path)
        if pix.isNull():
            self._image_label.setText("无法加载图片")
            self._resolution_label.setText("")
            return
        self._image_label.setText("")
        self._resolution_label.setText(f"{pix.width()} × {pix.height()}")
        area = self._scroll.viewport().size()
        if area.width() < 100 or area.height() < 100:
            area = self.size()
        self._image_label.setPixmap(
            pix.scaled(
                area,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def resizeEvent(self, event) -> None:  # noqa: ANN001
        super().resizeEvent(event)
        self._reload_image()

    def _prev(self) -> None:
        if len(self._paths) <= 1:
            return
        self._index = (self._index - 1) % len(self._paths)
        self._reload_image()

    def _next(self) -> None:
        if len(self._paths) <= 1:
            return
        self._index = (self._index + 1) % len(self._paths)
        self._reload_image()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Left:
            self._prev()
        elif event.key() == Qt.Key.Key_Right:
            self._next()
        elif event.key() == Qt.Key.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)
