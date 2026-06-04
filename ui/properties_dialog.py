from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


def _format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} 字节"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def _format_time(timestamp: float) -> str:
    from datetime import datetime
    return datetime.fromtimestamp(timestamp).strftime("%Y/%m/%d %H:%M:%S")


def _get_file_type_suffix(path: str) -> str:
    return Path(path).suffix.upper().lstrip(".")


def _get_occupied_space(path: str) -> int:
    cluster_size = 4096
    file_size = os.path.getsize(path)
    return ((file_size + cluster_size - 1) // cluster_size) * cluster_size


def _get_image_resolution(path: str) -> str:
    pm = QPixmap(path)
    if pm.isNull():
        return "未知"
    return f"{pm.width()} × {pm.height()}"


class ImagePropertiesDialog(QDialog):
    def __init__(self, image_path: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("属性")
        self.setMinimumWidth(360)
        layout = QVBoxLayout(self)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        p = Path(image_path)
        stat = os.stat(image_path)

        form.addRow("类型：", QLabel(_get_file_type_suffix(image_path) + " 文件"))
        form.addRow("大小：", QLabel(_format_size(stat.st_size)))
        form.addRow("占用空间：", QLabel(_format_size(_get_occupied_space(image_path))))
        form.addRow("分辨率：", QLabel(_get_image_resolution(image_path)))
        form.addRow("位置：", QLabel(str(p.parent)))
        form.addRow("创建时间：", QLabel(_format_time(stat.st_ctime)))
        form.addRow("修改时间：", QLabel(_format_time(stat.st_mtime)))
        form.addRow("访问时间：", QLabel(_format_time(stat.st_atime)))

        layout.addLayout(form)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        ok_btn = QPushButton("确定")
        ok_btn.clicked.connect(self.accept)
        btn_row.addWidget(ok_btn)
        layout.addLayout(btn_row)


class FolderPropertiesDialog(QDialog):
    def __init__(self, folder_path: str, sub_gallery_count: int, image_count: int, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("属性")
        self.setMinimumWidth(360)
        layout = QVBoxLayout(self)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        p = Path(folder_path)
        stat = os.stat(folder_path)

        total_size = 0
        try:
            for f in p.rglob("*"):
                if f.is_file():
                    try:
                        total_size += f.stat().st_size
                    except (OSError, PermissionError):
                        pass
        except (OSError, PermissionError):
            pass

        form.addRow("类型：", QLabel("文件夹"))
        form.addRow("大小：", QLabel(_format_size(total_size)))
        form.addRow("占用空间：", QLabel(_format_size(_get_occupied_space_recursive(str(p)))))
        form.addRow("位置：", QLabel(str(p.parent)))
        form.addRow("创建时间：", QLabel(_format_time(stat.st_ctime)))
        form.addRow("包含：", QLabel(f"{sub_gallery_count} 个子图库，{image_count} 张图片"))

        layout.addLayout(form)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        ok_btn = QPushButton("确定")
        ok_btn.clicked.connect(self.accept)
        btn_row.addWidget(ok_btn)
        layout.addLayout(btn_row)


def _get_occupied_space_recursive(folder_path: str) -> int:
    cluster_size = 4096
    total = 0
    try:
        for f in Path(folder_path).rglob("*"):
            if f.is_file():
                try:
                    file_size = f.stat().st_size
                    total += ((file_size + cluster_size - 1) // cluster_size) * cluster_size
                except (OSError, PermissionError):
                    pass
    except (OSError, PermissionError):
        pass
    return total
