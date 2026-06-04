from __future__ import annotations

import os
from typing import Optional

from PySide6.QtCore import Qt, QSettings
from PySide6.QtGui import QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QMessageBox,
)

from services.models_registry import load_model_config


class ImageTagsDialog(QDialog):
    def __init__(
        self,
        image_path: str,
        model_id: str,
        model_manager,
        current_category: Optional[str] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle(f"图片标签 - {os.path.basename(image_path)}")
        self.resize(640, 520)

        self._image_path = image_path
        self._model_id = model_id
        self._model_manager = model_manager
        self._current_category = current_category
        self._threshold = 0.0
        self._display_threshold = QSettings("ImageGallery", "ImageGallery").value(
            "classify/display_threshold", 0.8, type=float
        )
        self._label_set: set[str] = set()
        self._all_pairs: list[tuple[str, float]] = []
        self._showing_all = False

        layout = QVBoxLayout(self)

        header_row = QHBoxLayout()
        if current_category:
            cat_label = QLabel(f"当前分类：{current_category}")
            cat_label.setStyleSheet("font-weight:bold;color:#4a9eff;font-size:12px;")
            header_row.addWidget(cat_label)
        header_row.addStretch()

        threshold_label = QLabel("显示阈值:")
        threshold_label.setStyleSheet("font-size:11px;")
        header_row.addWidget(threshold_label)

        self._threshold_spin = QDoubleSpinBox()
        self._threshold_spin.setRange(0.0, 1.0)
        self._threshold_spin.setSingleStep(0.05)
        self._threshold_spin.setDecimals(2)
        self._threshold_spin.setValue(self._display_threshold)
        self._threshold_spin.setFixedWidth(72)
        self._threshold_spin.valueChanged.connect(self._on_display_threshold_changed)
        header_row.addWidget(self._threshold_spin)

        layout.addLayout(header_row)

        self._info_label = QLabel("正在加载标签数据…")
        self._info_label.setStyleSheet("color:#888;font-size:11px;padding:2px;")
        layout.addWidget(self._info_label)

        self._empty_label = QLabel("暂无标签达到显示阈值")
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setStyleSheet("color:#666;font-size:14px;")

        empty_layout = QVBoxLayout()
        empty_layout.addStretch()
        empty_layout.addWidget(self._empty_label)
        empty_layout.addStretch()

        self._empty_container = QWidget()
        self._empty_container.setLayout(empty_layout)
        self._empty_container.setVisible(False)

        self._view_all_btn = QPushButton("查看全部标签")
        self._view_all_btn.clicked.connect(self._on_view_all)
        self._view_all_btn.setVisible(False)

        view_all_row = QHBoxLayout()
        view_all_row.addStretch()
        view_all_row.addWidget(self._view_all_btn)
        view_all_row.addStretch()

        self._table = QTableWidget()
        self._table.setColumnCount(2)
        self._table.setHorizontalHeaderLabels(["标签名称", "权重"])
        self._table.horizontalHeader().setStretchLastSection(False)
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(1, 100)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSortingEnabled(False)
        self._table.setShowGrid(True)
        layout.addWidget(self._table)
        layout.addWidget(self._empty_container)
        layout.addLayout(view_all_row)

        btn_row = QHBoxLayout()
        self._copy_btn = QPushButton("复制标签")
        self._copy_btn.clicked.connect(self._on_copy_tags)
        self._copy_btn.setEnabled(False)
        btn_row.addWidget(self._copy_btn)

        self._close_btn = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self._close_btn.accepted.connect(self.accept)
        self._close_btn.rejected.connect(self.reject)
        btn_row.addStretch()
        btn_row.addWidget(self._close_btn)
        layout.addLayout(btn_row)

        QShortcut(QKeySequence.StandardKey.Copy, self, activated=self._on_copy_selected)

        self._load_data()

    def done(self, result: int) -> None:
        super().done(result)
        if self._model_manager.should_release_after_classify():
            self._model_manager.release_model(self._model_id)

    def _load_data(self) -> None:
        try:
            labels, probs = self._model_manager.get_full_probs(self._model_id, self._image_path)
        except FileNotFoundError:
            QMessageBox.warning(self, "错误", f"图片文件不存在：\n{self._image_path}")
            self.accept()
            return
        except RuntimeError as e:
            QMessageBox.warning(self, "错误", f"模型加载失败：\n{e}")
            self.accept()
            return
        except Exception as e:
            QMessageBox.warning(self, "错误", f"推理失败：\n{e}")
            self.accept()
            return

        config = load_model_config(self._model_id)
        self._threshold = config.confidence_threshold if config.confidence_threshold > 0 else 0.2

        self._all_pairs = sorted(zip(labels, probs), key=lambda x: x[1], reverse=True)

        top_n = min(30, len(self._all_pairs))
        top_results = self._all_pairs[:top_n]
        self._label_set = self._compute_passed_labels(top_results)

        self._refresh_display()

    def _on_display_threshold_changed(self, value: float) -> None:
        self._display_threshold = value
        QSettings("ImageGallery", "ImageGallery").setValue("classify/display_threshold", value)
        self._showing_all = False
        self._refresh_display()

    def _refresh_display(self) -> None:
        if self._showing_all:
            pairs = self._all_pairs
        else:
            pairs = [(l, p) for l, p in self._all_pairs if p >= self._display_threshold]

        total = len(self._all_pairs)
        shown = len(pairs)
        passed = len(self._label_set)

        if not pairs and not self._showing_all:
            self._info_label.setText(
                f"共 {total} 个标签  ·  "
                f"所有标签权重均低于显示阈值（{self._display_threshold:.2f}）"
            )
            self._table.setVisible(False)
            self._empty_container.setVisible(True)
            self._view_all_btn.setVisible(True)
            self._copy_btn.setEnabled(False)
            return

        self._table.setVisible(True)
        self._empty_container.setVisible(False)
        self._view_all_btn.setVisible(not self._showing_all)

        if self._showing_all:
            self._info_label.setText(
                f"共 {total} 个标签（显示全部）"
            )
        else:
            self._info_label.setText(
                f"共 {total} 个标签  ·  显示 {shown} 个（≥{self._display_threshold:.2f}）"
                f"  ·  达分类阈值（≥{self._threshold:.2f}）: {passed} 个"
            )

        self._build_table(pairs)
        self._copy_btn.setEnabled(True)

    def _on_view_all(self) -> None:
        self._showing_all = True
        self._refresh_display()

    def _compute_passed_labels(self, top_results) -> set[str]:
        threshold = self._threshold
        passed: set[str] = set()
        for label, conf in top_results:
            if conf >= threshold:
                norm = label.lower().replace("_", " ")
                passed.add(norm)
        return passed

    def _build_table(self, pairs) -> None:
        self._table.setRowCount(len(pairs))
        threshold = self._threshold

        color_triggered = QColor(0x1a, 0x7a, 0x1a)
        color_normal = QColor(0xe8, 0xe8, 0xe8)
        color_dim = QColor(0xAA, 0xAA, 0xAA)
        font_normal = self.font()
        font_bold = self.font()
        font_bold.setBold(True)

        for row_idx, (label, prob) in enumerate(pairs):
            norm = label.lower().replace("_", " ")
            passed = prob >= threshold
            triggered = norm in self._label_set

            name_item = QTableWidgetItem(label)
            conf_item = QTableWidgetItem(f"{prob:.4f}")
            conf_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

            if triggered:
                name_item.setForeground(color_triggered)
                name_item.setFont(font_bold)
                conf_item.setForeground(color_triggered)
                conf_item.setFont(font_bold)
            elif passed:
                name_item.setForeground(color_normal)
                name_item.setFont(font_normal)
                conf_item.setForeground(color_normal)
                conf_item.setFont(font_normal)
            else:
                name_item.setForeground(color_dim)
                name_item.setFont(font_normal)
                conf_item.setForeground(color_dim)
                conf_item.setFont(font_normal)

            self._table.setItem(row_idx, 0, name_item)
            self._table.setItem(row_idx, 1, conf_item)

        self._table.resizeRowsToContents()

    def _on_copy_tags(self) -> None:
        rows = []
        threshold = self._threshold
        for row in range(self._table.rowCount()):
            label_item = self._table.item(row, 0)
            conf_item = self._table.item(row, 1)
            if label_item and conf_item:
                label = label_item.text()
                conf = conf_item.text()
                triggered = label.lower().replace("_", " ") in self._label_set
                passed = float(conf) >= threshold
                if triggered:
                    rows.append(f"✅ {label}  {conf}")
                elif passed:
                    rows.append(f"   {label}  {conf}")
                else:
                    rows.append(f"   {label}  {conf}  (未达阈值)")

        text = "\n".join(rows)
        clipboard = QApplication.clipboard()
        clipboard.setText(text)

        self._copy_btn.setText("已复制 ✓")
        self._copy_btn.setEnabled(False)

    def _on_copy_selected(self) -> None:
        indexes = self._table.selectionModel().selectedRows()
        if not indexes:
            return
        rows = sorted(idx.row() for idx in indexes)
        lines: list[str] = []
        for row in rows:
            label_item = self._table.item(row, 0)
            conf_item = self._table.item(row, 1)
            if label_item and conf_item:
                lines.append(f"{label_item.text()}:{conf_item.text()}")
        clipboard = QApplication.clipboard()
        clipboard.setText("\n".join(lines))
