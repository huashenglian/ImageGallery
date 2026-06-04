from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app_meta import APP_VERSION, CHANGELOG
from paths import CACHE_DIR, THUMBNAIL_DIR


class HelpDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("帮助")
        self.resize(560, 420)

        about = QWidget()
        about_lay = QVBoxLayout(about)
        about_lay.addWidget(QLabel(f"<b>当前版本</b>：{APP_VERSION}"))
        cache_lbl = QLabel(f"<b>缓存目录</b>：<br><code>{CACHE_DIR}</code>")
        cache_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        cache_lbl.setWordWrap(True)
        about_lay.addWidget(cache_lbl)
        thumb_lbl = QLabel(f"<b>缩略图目录</b>：<br><code>{THUMBNAIL_DIR}</code>")
        thumb_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        thumb_lbl.setWordWrap(True)
        about_lay.addWidget(thumb_lbl)
        about_lay.addWidget(
            QLabel(
                "缓存为 gzip 压缩 JSON，每个扫描路径独立存储；"
                "缩略图保存在项目目录下，便于移动和清理。"
            )
        )
        about_lay.addStretch(1)

        log_edit = QTextEdit()
        log_edit.setReadOnly(True)
        log_edit.setPlainText(CHANGELOG)
        log_edit.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)

        tabs = QTabWidget()
        tabs.addTab(about, "关于")
        tabs.addTab(log_edit, "更新日志")

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self.accept)

        root = QVBoxLayout(self)
        root.addWidget(tabs)
        root.addWidget(buttons)
