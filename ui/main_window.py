from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

from PySide6.QtCore import Qt, QSettings, Signal
from PySide6.QtGui import QAction, QColor
from PySide6.QtWidgets import (
    QColorDialog,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QInputDialog,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QDialog,
    QLabel,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QWidget,
    QProgressBar,
    QGroupBox,
    QTextEdit,
    QSizePolicy,
)

from app_meta import APP_VERSION
from models.gallery import Gallery, build_gallery_tree_from_cache
from services.cache_manager import CacheManager, CacheStore, CacheEntry, _norm_path
from services.model_manager import ModelManager
from services.classify_cache import ClassifyCache
from services.thumbnail_cache import ThumbnailCache
from ui.classify_page import ClassifyPage
from ui.gallery_page import GalleryPage, SIZE_PRESETS
from ui.help_dialog import HelpDialog
from ui.start_page import StartPage
from ui.theme import (
    DEFAULT_BASE,
    SVG_FOLDER,
    SVG_SETTINGS,
    build_qss,
    svg_icon,
    _text,
    _text_dim,
    _hover,
    _accent,
    _border,
)
from services.models_registry import (
    CATEGORY_MAP_IMAGENET,
    CATEGORY_MAP_DANBOORU,
    CATEGORY_MAP_WD14,
    CATEGORY_MAP_GENERIC,
)

from workers.cache_scan_worker import FullCacheScanWorker, SinglePathScanWorker, IncrementalCacheScanWorker
from workers.classify_worker import ClassifyWorker

_logger = logging.getLogger(__name__)


class TextExpandDialog(QDialog):
    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("文本编辑")
        self.resize(520, 360)
        self._confirmed = False
        self._original_text = text

        layout = QVBoxLayout(self)

        self._editor = QTextEdit()
        self._editor.setPlainText(text)
        self._editor.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._editor.setFocus()
        layout.addWidget(self._editor)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        ok_btn = QPushButton("确定")
        ok_btn.clicked.connect(self._on_confirm)
        btn_row.addWidget(ok_btn)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self._on_cancel)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

    def _on_confirm(self) -> None:
        self._confirmed = True
        self.accept()

    def _on_cancel(self) -> None:
        self.close()

    def _is_modified(self) -> bool:
        return self._editor.toPlainText() != self._original_text

    def get_text(self) -> str:
        return self._editor.toPlainText()

    def closeEvent(self, event) -> None:
        if self._confirmed:
            event.accept()
            return
        if not self._is_modified():
            event.accept()
            return
        reply = QMessageBox.question(
            self, "确认关闭",
            "内容将不会修改，是否确认关闭？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            event.accept()
        else:
            event.ignore()


class ExpandableLineEdit(QLineEdit):
    def keyPressEvent(self, event) -> None:
        if event.modifiers() == (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier) \
                and event.key() == Qt.Key.Key_T:
            self._open_expand_dialog()
            return
        super().keyPressEvent(event)

    def _open_expand_dialog(self) -> None:
        dlg = TextExpandDialog(self.text(), self.window())
        dlg.exec()
        if dlg._confirmed:
            self.setText(dlg.get_text())


class PreferencesDialog(QDialog):

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("首选项")
        self.setMinimumWidth(420)

        self._settings = QSettings("ImageGallery", "ImageGallery", self)

        layout = QVBoxLayout(self)

        form = QFormLayout()

        self._workers_spin = QSpinBox()
        self._workers_spin.setRange(1, 32)
        self._workers_spin.setValue(self._settings.value("scan/max_workers", 8, type=int))
        self._workers_spin.setToolTip("全盘扫描与增量扫描的并行线程数（默认 8）")
        form.addRow("固定扫描线程数:", self._workers_spin)

        self._dynamic_check = QCheckBox("非全盘动态线程")
        self._dynamic_check.setChecked(self._settings.value("scan/dynamic_threads", True, type=bool))
        self._dynamic_check.setToolTip("非全盘扫描时根据子目录数量自动调整线程数")
        form.addRow("", self._dynamic_check)

        self._min_spin = QSpinBox()
        self._min_spin.setRange(1, 32)
        self._min_spin.setValue(self._settings.value("scan/min_workers", 2, type=int))
        self._max_spin = QSpinBox()
        self._max_spin.setRange(1, 32)
        self._max_spin.setValue(self._settings.value("scan/max_dynamic_workers", 8, type=int))
        dyn_row = QHBoxLayout()
        dyn_row.addWidget(QLabel("最少:"))
        dyn_row.addWidget(self._min_spin)
        dyn_row.addWidget(QLabel("最多:"))
        dyn_row.addWidget(self._max_spin)
        form.addRow("动态线程范围:", dyn_row)

        self._base = self._settings.value("appearance/base_color", DEFAULT_BASE, type=str)
        self._base_btn = QPushButton()
        self._base_btn.setFixedSize(60, 28)
        self._update_base_btn()
        self._base_btn.clicked.connect(self._pick_color)
        color_row = QHBoxLayout()
        color_row.addWidget(self._base_btn)
        color_row.addWidget(QLabel("点击选择界面颜色"))
        color_row.addStretch()
        form.addRow("界面颜色:", color_row)

        layout.addLayout(form)

        thumb_group = QGroupBox("缩略图管理")
        thumb_lay = QVBoxLayout(thumb_group)
        self._thumb_info = QLabel()
        self._thumb_info.setStyleSheet(f"color:{_text_dim(self._base)};font-size:11px;")
        self._clear_thumb_btn = QPushButton("清除所有缩略图")
        self._clear_thumb_btn.clicked.connect(self._on_clear_thumbnails)
        thumb_lay.addWidget(self._thumb_info)
        thumb_lay.addWidget(self._clear_thumb_btn)
        self._update_thumb_info()
        layout.addWidget(thumb_group)

        hint = QLabel("线程数越大，扫描越快，但占用 CPU 和磁盘 I/O 也越多。\n建议设为 4~16 之间。")
        hint.setStyleSheet(f"color:{_text_dim(self._base)};font-size:11px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _update_base_btn(self) -> None:
        bdr = _border(self._base)
        self._base_btn.setStyleSheet(
            f"QPushButton{{background:{self._base};border:2px solid {bdr};"
            f"border-radius:6px;}}"
        )

    def _pick_color(self) -> None:
        color = QColorDialog.getColor(QColor(self._base), self, "选择界面颜色")
        if color.isValid():
            self._base = color.name()
            self._update_base_btn()

    def _save(self) -> None:
        self._settings.setValue("scan/max_workers", self._workers_spin.value())
        self._settings.setValue("scan/dynamic_threads", self._dynamic_check.isChecked())
        self._settings.setValue("scan/min_workers", self._min_spin.value())
        self._settings.setValue("scan/max_dynamic_workers", self._max_spin.value())
        self._settings.setValue("appearance/base_color", self._base)
        self._settings.sync()
        self.accept()

    def _update_thumb_info(self) -> None:
        from paths import THUMBNAIL_DIR
        count = 0
        size = 0
        if os.path.isdir(THUMBNAIL_DIR):
            for f in os.listdir(THUMBNAIL_DIR):
                fp = os.path.join(THUMBNAIL_DIR, f)
                if os.path.isfile(fp):
                    count += 1
                    size += os.path.getsize(fp)
        size_mb = size / (1024 * 1024)
        self._thumb_info.setText(f"缩略图数量: {count}  ·  占用空间: {size_mb:.1f} MB")

    def _on_clear_thumbnails(self) -> None:
        if (
            QMessageBox.question(self, "确认", "确定清除所有缩略图？\n下次浏览图库时会重新生成。")
            != QMessageBox.StandardButton.Yes
        ):
            return
        from paths import THUMBNAIL_DIR
        import shutil
        if os.path.isdir(THUMBNAIL_DIR):
            shutil.rmtree(THUMBNAIL_DIR, ignore_errors=True)
            os.makedirs(THUMBNAIL_DIR, exist_ok=True)
        self._update_thumb_info()

    @staticmethod
    def max_workers() -> int:
        s = QSettings("ImageGallery", "ImageGallery")
        return s.value("scan/max_workers", 8, type=int)

    @staticmethod
    def dynamic_threads() -> bool:
        s = QSettings("ImageGallery", "ImageGallery")
        return s.value("scan/dynamic_threads", True, type=bool)

    @staticmethod
    def min_workers() -> int:
        s = QSettings("ImageGallery", "ImageGallery")
        return s.value("scan/min_workers", 2, type=int)

    @staticmethod
    def max_dynamic_workers() -> int:
        s = QSettings("ImageGallery", "ImageGallery")
        return s.value("scan/max_dynamic_workers", 8, type=int)

    @staticmethod
    def compute_workers(subdir_count: int) -> int:
        if not PreferencesDialog.dynamic_threads():
            return PreferencesDialog.max_workers()
        lo = PreferencesDialog.min_workers()
        hi = PreferencesDialog.max_dynamic_workers()
        return min(hi, max(lo, subdir_count))

    @staticmethod
    def base_color() -> str:
        s = QSettings("ImageGallery", "ImageGallery")
        return s.value("appearance/base_color", DEFAULT_BASE, type=str)


class ModelConfigDialog(QDialog):
    _quantize_progress = Signal(str, int, str)
    _quantize_done = Signal(bool, str)
    _redownload_progress = Signal(int, str)
    _redownload_status = Signal(str)
    _redownload_done = Signal(bool, str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("分类模型配置")
        self.setMinimumSize(520, 500)

        self._settings = QSettings("ImageGallery", "ImageGallery", self)
        self._quantize_progress.connect(self._handle_quantize_progress)
        self._quantize_done.connect(self._on_quantize_done)
        self._redownload_progress.connect(self._handle_redownload_progress)
        self._redownload_status.connect(self._handle_redownload_status)
        self._redownload_done.connect(self._on_redownload_done)
        self._model_mgr = ModelManager()
        self._redownload_model_id = None
        self._dl_progress_widgets: dict[str, dict] = {}

        base = PreferencesDialog.base_color()
        layout = QVBoxLayout(self)

        self._release_check = QCheckBox("分类完成后释放模型内存")
        self._release_check.setChecked(self._settings.value("classify/release_after_classify", True, type=bool))
        self._release_check.setToolTip("开启时分类完成后自动释放模型，节省内存")
        layout.addWidget(self._release_check)

        display_row = QHBoxLayout()
        display_row.addWidget(QLabel("图片标签显示阈值:"))
        self._display_threshold_spin = QDoubleSpinBox()
        self._display_threshold_spin.setRange(0.0, 1.0)
        self._display_threshold_spin.setSingleStep(0.05)
        self._display_threshold_spin.setDecimals(2)
        self._display_threshold_spin.setValue(
            self._settings.value("classify/display_threshold", 0.8, type=float)
        )
        self._display_threshold_spin.setToolTip(
            "查看图片标签时，仅显示权重≥此阈值的标签。\n"
            "此设置不影响分类结果，仅影响标签窗口的显示数量。"
        )
        display_row.addWidget(self._display_threshold_spin)
        display_row.addStretch()
        layout.addLayout(display_row)

        cache_row = QHBoxLayout()
        cache_row.addWidget(QLabel("标签缓存阈值:"))
        self._tag_cache_limit_spin = QSpinBox()
        self._tag_cache_limit_spin.setRange(1, 100)
        self._tag_cache_limit_spin.setValue(
            self._settings.value("classify/tag_cache_limit", 3, type=int)
        )
        self._tag_cache_limit_spin.setToolTip(
            "磁盘上最多保留多少张图片的标签缓存。\n"
            "超过此数量后，最早的缓存将被自动删除。"
        )
        cache_row.addWidget(self._tag_cache_limit_spin)
        cache_row.addStretch()
        layout.addLayout(cache_row)

        fallback_row = QHBoxLayout()
        fallback_row.addWidget(QLabel("回退级别:"))
        self._fallback_spin = QSpinBox()
        self._fallback_spin.setRange(-1, 99)
        self._fallback_spin.setValue(
            self._settings.value("classify/fallback_level", 2, type=int)
        )
        self._fallback_spin.setToolTip(
            "当主模型将图片分入「其他」时，尝试其他模型重新分类。\n"
            "-1 = 全部模型回退\n"
            "0 = 仅使用主模型（不回退）\n"
            "N = 最多回退 N 次（尝试 N+1 个模型）"
        )
        fallback_row.addWidget(self._fallback_spin)
        fallback_hint = QLabel("-1=全部回退  0=仅主模型  N=回退N次")
        fallback_hint.setStyleSheet(f"color:{_text_dim(base)};font-size:11px;")
        fallback_row.addWidget(fallback_hint)
        fallback_row.addStretch()
        layout.addLayout(fallback_row)

        installed_group = QGroupBox("已拥有的模型")
        installed_lay = QVBoxLayout(installed_group)
        self._installed_list = QVBoxLayout()
        self._installed_list.setSpacing(6)
        installed_lay.addLayout(self._installed_list)
        installed_lay.addWidget(QLabel("提示：点击模型名称可查看详细配置"))
        installed_lay.itemAt(installed_lay.count() - 1).widget().setStyleSheet(
            f"color:{_text_dim(base)};font-size:11px;"
        )
        layout.addWidget(installed_group)

        download_group = QGroupBox("下载模型")
        download_lay = QVBoxLayout(download_group)
        self._download_list = QVBoxLayout()
        self._download_list.setSpacing(6)
        download_lay.addLayout(self._download_list)
        import_row = QHBoxLayout()
        self._import_btn = QPushButton("导入本地模型…")
        self._import_btn.clicked.connect(self._on_import_model)
        import_row.addWidget(self._import_btn)
        import_row.addStretch()
        download_lay.addLayout(import_row)
        download_hint = QLabel("模型从 Hugging Face 下载，仅需一次，完全离线运行，不上传任何图片。\n也可点击「导入本地模型」手动导入 .onnx 文件。")
        download_hint.setStyleSheet(f"color:{_text_dim(base)};font-size:11px;")
        download_hint.setWordWrap(True)
        download_lay.addWidget(download_hint)
        layout.addWidget(download_group)

        self._task_progress_widget = QWidget()
        task_progress_lay = QVBoxLayout(self._task_progress_widget)
        task_progress_lay.setContentsMargins(0, 0, 0, 0)
        self._task_progress = QProgressBar()
        self._task_progress.setVisible(False)
        self._task_progress.setRange(0, 100)
        self._task_progress.setFormat("%p%")
        self._task_progress.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._task_progress.setMinimumHeight(20)
        self._task_progress_label = QLabel()
        self._task_progress_label.setStyleSheet(f"color:{_text_dim(base)};font-size:11px;")
        self._task_progress_label.setVisible(False)
        task_progress_lay.addWidget(self._task_progress)
        task_progress_lay.addWidget(self._task_progress_label)
        self._task_progress_widget.setVisible(False)
        layout.addWidget(self._task_progress_widget)

        layout.addStretch()

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
        )
        buttons.accepted.connect(self._ok)
        layout.addWidget(buttons)

        self._refresh_model_lists()

    def _ok(self) -> None:
        self._settings.setValue("classify/release_after_classify", self._release_check.isChecked())
        self._settings.setValue("classify/display_threshold", self._display_threshold_spin.value())
        self._settings.setValue("classify/tag_cache_limit", self._tag_cache_limit_spin.value())
        self._settings.setValue("classify/fallback_level", self._fallback_spin.value())
        self._settings.sync()
        self.accept()

    def _refresh_model_lists(self) -> None:
        self._dl_progress_widgets.clear()
        while self._installed_list.count():
            item = self._installed_list.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        while self._download_list.count():
            item = self._download_list.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        from services.models_registry import scan_installed_models, BUILTIN_MODELS, get_model_order
        base = PreferencesDialog.base_color()
        installed = scan_installed_models()
        order = get_model_order()
        if order:
            ordered = []
            seen = set()
            for mid in order:
                for m in installed:
                    if m["model_id"] == mid and mid not in seen:
                        ordered.append(m)
                        seen.add(mid)
                        break
            for m in installed:
                if m["model_id"] not in seen:
                    ordered.append(m)
                    seen.add(m["model_id"])
            installed = ordered
        installed_ids = {m["model_id"] for m in installed}

        if not installed:
            hint = QLabel("暂无已安装的模型，请从下方下载或导入。")
            hint.setStyleSheet(f"color:{_text_dim(base)};font-size:12px;")
            self._installed_list.addWidget(hint)
        else:
            for m in installed:
                row = self._create_installed_row(m, base)
                self._installed_list.addWidget(row)

        for entry in BUILTIN_MODELS:
            if entry.id in installed_ids:
                continue
            row = self._create_download_row(entry, base)
            self._download_list.addWidget(row)

        if not any(e.id not in installed_ids for e in BUILTIN_MODELS):
            hint = QLabel("所有内置模型已安装。")
            hint.setStyleSheet(f"color:{_text_dim(base)};font-size:12px;")
            self._download_list.addWidget(hint)

    def _create_installed_row(self, model_info: dict, base: str) -> QWidget:
        from services.models_registry import load_model_config
        row_widget = QWidget()
        row_lay = QHBoxLayout(row_widget)
        row_lay.setContentsMargins(4, 2, 4, 2)

        config = model_info.get("config") or load_model_config(model_info["model_id"])

        enable_cb = QCheckBox()
        enable_cb.setChecked(config.enabled)
        enable_cb.stateChanged.connect(
            lambda state, mid=model_info["model_id"]: self._on_toggle_model(mid, state)
        )
        row_lay.addWidget(enable_cb)

        name_btn = QPushButton(f"{model_info['name']}  ({model_info['size_mb']:.1f} MB)")
        name_btn.setFlat(True)
        name_btn.setStyleSheet(
            "QPushButton{text-align:left;padding:4px 8px;border:none;"
            f"color:{_text(base)};font-size:13px;}}"
            f"QPushButton:hover{{background:{_hover(base)};border-radius:4px;}}"
        )
        name_btn.clicked.connect(
            lambda checked, mi=model_info: self._show_model_detail(mi)
        )
        row_lay.addWidget(name_btn, 1)

        desc_label = QLabel(model_info.get("description", ""))
        desc_label.setStyleSheet(f"color:{_text_dim(base)};font-size:11px;")
        row_lay.addWidget(desc_label)

        up_btn = QPushButton("▲")
        up_btn.setFixedWidth(28)
        up_btn.setStyleSheet(
            "QPushButton{background:transparent;border:1px solid " + _border(base) + ";"
            "border-radius:3px;padding:2px;font-size:10px;color:" + _text(base) + ";}"
            "QPushButton:hover{background:" + _hover(base) + ";}"
        )
        up_btn.clicked.connect(
            lambda checked, mid=model_info["model_id"]: self._on_move_model(mid, -1)
        )
        row_lay.addWidget(up_btn)

        down_btn = QPushButton("▼")
        down_btn.setFixedWidth(28)
        down_btn.setStyleSheet(
            "QPushButton{background:transparent;border:1px solid " + _border(base) + ";"
            "border-radius:3px;padding:2px;font-size:10px;color:" + _text(base) + ";}"
            "QPushButton:hover{background:" + _hover(base) + ";}"
        )
        down_btn.clicked.connect(
            lambda checked, mid=model_info["model_id"]: self._on_move_model(mid, 1)
        )
        row_lay.addWidget(down_btn)

        has_int8 = model_info.get("has_int8", False)
        int8_only = model_info.get("int8_only", False)
        fp32_size = model_info.get("fp32_size_mb") or 0
        if fp32_size == 0 and not int8_only:
            fp32_size = model_info["size_mb"]
        if fp32_size == 0:
            from services.models_registry import get_builtin_by_id
            builtin = get_builtin_by_id(model_info["model_id"])
            if builtin:
                fp32_size = builtin.size_mb
        too_large = fp32_size >= 500
        is_large = fp32_size >= 100
        int8_cb = QCheckBox("INT8")
        int8_cb.setChecked(int8_only or (config.use_int8 and has_int8))
        if too_large and not int8_only:
            int8_cb.setEnabled(False)
            int8_cb.setToolTip("模型过大（≥500MB），不支持 INT8 量化")
        elif int8_only:
            int8_cb.setEnabled(True)
            int8_cb.setToolTip("INT8 量化已启用（原始模型已删除）")
        elif has_int8 and config.use_int8:
            int8_cb.setEnabled(True)
            int8_cb.setToolTip("使用 INT8 量化模型（体积更小、速度更快）")
        elif has_int8:
            int8_cb.setEnabled(True)
            int8_cb.setToolTip("点击启用量化模型")
        elif is_large:
            int8_cb.setEnabled(True)
            int8_cb.setToolTip("点击进行 INT8 量化（模型较大，量化时间可能较长，预计节省约 70% 空间）")
        else:
            int8_cb.setEnabled(True)
            int8_cb.setToolTip("点击进行 INT8 量化（预计节省约 70% 空间）")
        int8_cb.setStyleSheet(f"color:{_text(base)};font-size:11px;")
        int8_cb.clicked.connect(
            lambda checked, mid=model_info["model_id"]: self._on_toggle_int8(mid, checked)
        )
        row_lay.addWidget(int8_cb)

        delete_btn = QPushButton("删除")
        delete_btn.setFixedWidth(50)
        delete_btn.setStyleSheet(
            "QPushButton{background:#e74c3c;color:white;border:none;"
            "border-radius:4px;padding:3px 8px;font-size:11px;}"
            "QPushButton:hover{background:#c0392b;}"
        )
        delete_btn.clicked.connect(
            lambda checked, mid=model_info["model_id"]: self._on_delete_model(mid)
        )
        row_lay.addWidget(delete_btn)

        return row_widget

    def _on_move_model(self, model_id: str, direction: int) -> None:
        from services.models_registry import get_model_order, set_model_order
        order = get_model_order()
        installed = [m["model_id"] for m in self._current_installed_models()]
        if not order:
            order = installed[:]
        seen = set(order)
        for mid in installed:
            if mid not in seen:
                order.append(mid)
                seen.add(mid)
        order = [mid for mid in order if mid in set(installed)]
        idx = order.index(model_id)
        new_idx = idx + direction
        if new_idx < 0 or new_idx >= len(order):
            return
        order[idx], order[new_idx] = order[new_idx], order[idx]
        set_model_order(order)
        self._refresh_model_lists()

    def _current_installed_models(self) -> list[dict]:
        from services.models_registry import scan_installed_models
        return scan_installed_models()

    def _create_download_row(self, entry, base: str) -> QWidget:
        row_widget = QWidget()
        row_lay = QVBoxLayout(row_widget)
        row_lay.setContentsMargins(4, 2, 4, 2)
        row_lay.setSpacing(2)

        info_row = QHBoxLayout()
        name_label = QLabel(f"{entry.name}  (~{entry.size_mb:.0f} MB)")
        name_label.setStyleSheet(f"color:{_text(base)};font-size:13px;")
        info_row.addWidget(name_label)

        desc_label = QLabel(entry.description)
        desc_label.setStyleSheet(f"color:{_text_dim(base)};font-size:11px;")
        info_row.addWidget(desc_label, 1)

        dl_btn = QPushButton("下载")
        dl_btn.setFixedWidth(50)
        dl_btn.setObjectName(f"dl_btn_{entry.id}")

        main_win = self.parent()
        while main_win and not isinstance(main_win, MainWindow):
            main_win = main_win.parent()
        is_downloading = main_win and entry.id in main_win._active_downloads

        if is_downloading:
            dl_btn.setText("取消")
            dl_btn.setStyleSheet(
                "QPushButton{background:#e67e22;color:white;border:none;"
                "border-radius:4px;padding:3px 8px;font-size:11px;}"
                "QPushButton:hover{background:#d35400;}"
            )
            dl_btn.clicked.connect(lambda checked, mid=entry.id: self._on_cancel_download(mid))
        else:
            dl_btn.setStyleSheet(
                "QPushButton{background:#27ae60;color:white;border:none;"
                "border-radius:4px;padding:3px 8px;font-size:11px;}"
                "QPushButton:hover{background:#219a52;}"
            )
            dl_btn.clicked.connect(lambda checked, e=entry: self._on_download_model(e))
        info_row.addWidget(dl_btn)
        row_lay.addLayout(info_row)

        progress_bar = QProgressBar()
        progress_bar.setRange(0, 100)
        progress_bar.setFormat("%p%")
        progress_bar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        progress_bar.setMinimumHeight(18)
        progress_label = QLabel()
        progress_label.setStyleSheet(f"color:{_text_dim(base)};font-size:11px;")

        self._dl_progress_widgets[entry.id] = {
            "bar": progress_bar,
            "label": progress_label,
        }

        if is_downloading:
            progress_bar.setVisible(True)
            progress_label.setVisible(True)
            progress_label.setText(f"正在下载 {entry.name}…")
        else:
            progress_bar.setVisible(False)
            progress_label.setVisible(False)

        row_lay.addWidget(progress_bar)
        row_lay.addWidget(progress_label)

        return row_widget

    def _on_toggle_model(self, model_id: str, state: int) -> None:
        from services.models_registry import set_model_enabled
        set_model_enabled(model_id, state == 2)

    def _on_toggle_int8(self, model_id: str, enable: bool) -> None:
        if enable:
            from services.models_registry import scan_installed_models, get_builtin_by_id
            fp32_size = 0
            for m in scan_installed_models():
                if m["model_id"] == model_id:
                    fp32_size = m.get("fp32_size_mb") or 0
                    if fp32_size == 0 and not m.get("int8_only"):
                        fp32_size = m["size_mb"]
                    break
            if fp32_size == 0:
                builtin = get_builtin_by_id(model_id)
                if builtin:
                    fp32_size = builtin.size_mb
            is_large = fp32_size >= 100

            msg_text = f"将对模型「{model_id}」进行 INT8 量化。\n\n"
            msg_text += "量化完成后原始模型将被删除以节省存储空间。\n"
            if is_large:
                msg_text += "⚠ 模型较大，量化时间可能较长（可能需要 10 分钟以上），请耐心等待。\n"
            else:
                msg_text += "量化过程可能需要几分钟，期间请勿关闭窗口。\n"
            msg_text += "\n预计可节省约 70% 存储空间。是否继续？"

            reply = QMessageBox.question(
                self, "启用量化", msg_text,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                self._refresh_model_lists()
                return

            self._start_quantize(model_id)
        else:
            from services.models_registry import get_builtin_by_id
            builtin = get_builtin_by_id(model_id)
            if builtin is None:
                QMessageBox.warning(
                    self, "无法关闭量化",
                    f"模型「{model_id}」不是内置模型，无法自动重新下载原始版本。\n"
                    "如需恢复原始模型，请手动导入 .onnx 文件。",
                )
                self._refresh_model_lists()
                return

            reply = QMessageBox.question(
                self, "关闭量化",
                f"关闭 INT8 量化将重新下载原始模型并删除量化版本。\n"
                "下载过程可能需要一些时间。是否继续？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                self._refresh_model_lists()
                return

            self._start_redownload(model_id)

    def _start_quantize(self, model_id: str) -> None:
        main_win = self.parent()
        while main_win and not isinstance(main_win, MainWindow):
            main_win = main_win.parent()
        if main_win:
            main_win.start_quantize(model_id)
        self._task_progress_widget.setVisible(True)
        self._task_progress.setVisible(True)
        self._task_progress.setValue(0)
        self._task_progress_label.setVisible(True)
        self._task_progress_label.setText(f"准备量化 {model_id}…")

    def _handle_quantize_progress(self, phase: str, pct: int, msg: str) -> None:
        if not self._task_progress_widget.isVisible():
            self._task_progress_widget.setVisible(True)
            self._task_progress.setVisible(True)
            self._task_progress_label.setVisible(True)
        self._task_progress.setValue(pct)
        self._task_progress_label.setText(msg)

    def _on_quantize_done(self, ok: bool, msg: str) -> None:
        self._task_progress_widget.setVisible(False)
        self._refresh_model_lists()
        if ok:
            QMessageBox.information(self, "量化完成", msg)
        else:
            QMessageBox.warning(self, "量化失败", msg)

    def show_quantize_progress(self, model_id: str) -> None:
        self._task_progress_widget.setVisible(True)
        self._task_progress.setVisible(True)
        self._task_progress.setValue(0)
        self._task_progress_label.setVisible(True)
        self._task_progress_label.setText(f"正在量化 {model_id}…")

    def _start_redownload(self, model_id: str) -> None:
        self._redownload_model_id = model_id
        self._task_progress_widget.setVisible(True)
        self._task_progress.setVisible(True)
        self._task_progress.setValue(0)
        self._task_progress_label.setVisible(True)
        self._task_progress_label.setText(f"准备重新下载 {model_id}…")
        self._cancel_download = threading.Event()

        def on_progress(filename, downloaded, total, speed, using_mirror):
            pct = int(downloaded / total * 100) if total > 0 else 0
            mirror_tag = " (镜像加速)" if using_mirror else ""
            speed_str = f"{speed / 1024 / 1024:.1f} MB/s" if speed > 0 else "计算中…"
            size_str = f"{downloaded / 1024 / 1024:.1f} / {total / 1024 / 1024:.1f} MB" if total > 0 else f"{downloaded / 1024 / 1024:.1f} MB"
            text = f"{filename}{mirror_tag}  {size_str}  {speed_str}"
            self._redownload_progress.emit(pct, text)

        def on_status(msg):
            self._redownload_status.emit(msg)

        def do_redownload():
            ok, msg = self._model_mgr.redownload_fp32(
                model_id, on_progress, self._cancel_download, on_status
            )
            self._redownload_done.emit(ok, msg)

        self._redownload_thread = threading.Thread(target=do_redownload, daemon=True)
        self._redownload_thread.start()

    def _handle_redownload_progress(self, pct: int, text: str) -> None:
        self._task_progress.setValue(pct)
        self._task_progress_label.setText(text)

    def _handle_redownload_status(self, msg: str) -> None:
        self._task_progress_label.setText(msg)

    def _on_redownload_done(self, ok: bool, msg: str) -> None:
        self._task_progress_widget.setVisible(False)
        model_id = self._redownload_model_id
        self._redownload_model_id = None

        if ok:
            self._model_mgr.release_model(model_id)
            self._refresh_model_lists()
            QMessageBox.information(self, "恢复完成", msg)
        else:
            self._refresh_model_lists()
            QMessageBox.warning(self, "恢复失败", msg)

    def _on_delete_model(self, model_id: str) -> None:
        if (
            QMessageBox.question(
                self, "确认删除",
                f"确定要删除模型「{model_id}」吗？\n删除后分类图库将被清空。",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        from services.models_registry import delete_model
        self._model_mgr.release_model(model_id)
        delete_model(model_id)
        cache = ClassifyCache()
        cache.clear()
        self._refresh_model_lists()

    def _on_download_model(self, entry) -> None:
        main_win = self.parent()
        while main_win and not isinstance(main_win, MainWindow):
            main_win = main_win.parent()
        if main_win:
            main_win.start_download(entry)
        self._refresh_model_lists()

    def _on_cancel_download(self, model_id: str) -> None:
        main_win = self.parent()
        while main_win and not isinstance(main_win, MainWindow):
            main_win = main_win.parent()
        if main_win:
            main_win.cancel_download(model_id)
        self._refresh_model_lists()

    def update_download_progress(self, model_id: str, pct: int, text: str) -> None:
        widgets = self._dl_progress_widgets.get(model_id)
        if widgets:
            bar = widgets["bar"]
            label = widgets["label"]
            if not bar.isVisible():
                bar.setVisible(True)
                label.setVisible(True)
            bar.setValue(pct)
            label.setText(text)

    def _on_download_done(self, model_id: str, ok: bool, msg: str, entry_size_mb: float = 0.0) -> None:
        self._refresh_model_lists()
        if ok:
            if entry_size_mb >= 500:
                QMessageBox.information(
                    self, "成功",
                    f"模型下载完成！{msg}\n\n当前模型超过 500MB，INT8 开关已禁用，无法进行量化。",
                )
            elif entry_size_mb >= 100:
                QMessageBox.information(
                    self, "成功",
                    f"模型下载完成！{msg}\n\n模型较大，未自动量化。如需量化请手动点击 INT8 开关。",
                )
            else:
                QMessageBox.information(self, "成功", f"模型下载完成！{msg}")
        else:
            if "取消" not in msg:
                QMessageBox.warning(self, "下载失败", msg)

    def _on_import_model(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择 ONNX 模型文件", "", "ONNX 模型 (*.onnx);;所有文件 (*)"
        )
        if not files:
            return

        imported, warnings = self._model_mgr.import_model_files(files)
        if imported > 0:
            self._refresh_model_lists()
            from PySide6.QtWidgets import QApplication
            QApplication.processEvents()
        if warnings:
            QMessageBox.warning(self, "导入警告", "\n".join(warnings))
        if imported > 0:
            QMessageBox.information(self, "成功", f"成功导入 {imported} 个模型！")
        elif not warnings:
            QMessageBox.warning(self, "导入失败", "未能导入任何模型文件。")

    def _show_model_detail(self, model_info: dict) -> None:
        from services.models_registry import load_model_config, save_model_config
        config = model_info.get("config") or load_model_config(model_info["model_id"])
        dlg = ModelDetailDialog(model_info, config, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            save_model_config(dlg.get_config())
            self._refresh_model_lists()


class ModelDetailDialog(QDialog):
    def __init__(self, model_info: dict, config, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"模型配置 - {model_info['name']}")
        self.setMinimumSize(480, 560)

        from services.category_presets import (
            list_presets, load_preset, save_preset, delete_preset, CategoryPreset,
            DEFAULT_PRESET_NAMES, preset_to_mapping,
        )

        self._config = config
        self._list_presets = list_presets
        self._load_preset = load_preset
        self._save_preset = save_preset
        self._delete_preset = delete_preset
        self._CategoryPreset = CategoryPreset
        self._DEFAULT_PRESET_NAMES = DEFAULT_PRESET_NAMES
        self._preset_to_mapping = preset_to_mapping
        self._model_info = model_info
        self._custom_rows: list[dict] = []

        base = PreferencesDialog.base_color()
        layout = QVBoxLayout(self)

        info_group = QGroupBox("模型信息")
        info_lay = QFormLayout(info_group)
        name_edit = ExpandableLineEdit(model_info["name"])
        name_edit.setReadOnly(True)
        name_edit.setStyleSheet(
            f"QLineEdit{{background:transparent;border:1px solid {_border(base)};"
            f"border-radius:3px;padding:2px 6px;color:{_text(base)};}}"
            f"QLineEdit:hover{{border-color:{_accent(base)};}}"
        )
        name_edit.setToolTip("可选中后复制模型名称（Shift+Ctrl+T 展开编辑）")
        info_lay.addRow("名称:", name_edit)
        info_lay.addRow("描述:", QLabel(model_info.get("description", "")))
        size_text = f"{model_info['size_mb']:.1f} MB"
        if model_info.get("int8_only"):
            size_text += " (INT8 量化)"
        elif model_info.get("has_int8") and model_info.get("config") and model_info["config"].use_int8:
            size_text += " (INT8 量化)"
        info_lay.addRow("大小:", QLabel(size_text))
        info_lay.addRow("类别数:", QLabel(str(model_info.get("classes", "未知"))))
        if model_info.get("int8_only"):
            info_lay.addRow("文件:", QLabel(model_info.get("int8_path", "").split(os.sep)[-1]))
        else:
            info_lay.addRow("文件:", QLabel(model_info.get("onnx_filename", "")))
        layout.addWidget(info_group)

        config_group = QGroupBox("分类配置")
        config_lay = QFormLayout(config_group)

        self._enabled_cb = QCheckBox("启用此模型")
        self._enabled_cb.setChecked(config.enabled)
        config_lay.addRow("", self._enabled_cb)

        self._threshold_spin = QDoubleSpinBox()
        self._threshold_spin.setRange(0.0, 1.0)
        self._threshold_spin.setSingleStep(0.05)
        self._threshold_spin.setValue(config.confidence_threshold)
        self._threshold_spin.setToolTip("低于此置信度的分类结果归入「其他」")
        config_lay.addRow("置信度阈值:", self._threshold_spin)

        self._map_combo = QComboBox()
        presets = list_presets()
        for pn in presets:
            self._map_combo.addItem(pn, pn)
        idx = self._map_combo.findText(config.preset_name or config.category_map_type)
        if idx >= 0:
            self._map_combo.setCurrentIndex(idx)
        self._map_combo.currentIndexChanged.connect(self._on_preset_changed)
        config_lay.addRow("分类预设:", self._map_combo)

        self._input_size_label = QLabel(f"{config.input_size[0]}×{config.input_size[1]}")
        config_lay.addRow("输入尺寸:", self._input_size_label)

        self._preprocess_label = QLabel(config.preprocess_type)
        config_lay.addRow("预处理方式:", self._preprocess_label)

        layout.addWidget(config_group)

        custom_group = QGroupBox("自定义映射栏")
        custom_outer = QVBoxLayout(custom_group)

        self._custom_scroll = QScrollArea()
        self._custom_scroll.setWidgetResizable(True)
        self._custom_scroll.setMinimumHeight(180)
        self._custom_container = QWidget()
        self._custom_layout = QVBoxLayout(self._custom_container)
        self._custom_layout.setSpacing(4)
        self._custom_layout.addStretch()
        self._custom_scroll.setWidget(self._custom_container)
        custom_outer.addWidget(self._custom_scroll)

        add_btn = QPushButton("+ 添加自定义标签")
        add_btn.clicked.connect(lambda: self._add_custom_row())
        custom_outer.addWidget(add_btn)

        ctrl_row = QHBoxLayout()
        self._load_btn = QPushButton("读取预设")
        self._load_btn.clicked.connect(self._load_preset_into_custom)
        ctrl_row.addWidget(self._load_btn)

        self._preset_name_input = ExpandableLineEdit()
        self._preset_name_input.setPlaceholderText("预设名称（保存为新预设）")
        self._preset_name_input.textChanged.connect(self._check_preset_name)
        ctrl_row.addWidget(self._preset_name_input, 1)

        self._save_preset_btn = QPushButton("保存预设")
        self._save_preset_btn.clicked.connect(self._do_save_preset)
        ctrl_row.addWidget(self._save_preset_btn)

        self._delete_preset_btn = QPushButton("删除预设")
        self._delete_preset_btn.clicked.connect(self._do_delete_preset)
        self._delete_preset_btn.setStyleSheet(
            "QPushButton{background:#c0392b;color:white;border:none;"
            "border-radius:4px;padding:4px 10px;font-size:12px;}"
            "QPushButton:hover{background:#e74c3c;}"
        )
        ctrl_row.addWidget(self._delete_preset_btn)
        custom_outer.addLayout(ctrl_row)

        self._preset_warning = QLabel("")
        self._preset_warning.setStyleSheet("color:#ff4444;font-size:12px;font-weight:bold;")
        self._preset_warning.setWordWrap(True)
        self._preset_warning.setVisible(False)
        custom_outer.addWidget(self._preset_warning)

        layout.addWidget(custom_group)

        layout.addStretch()

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._load_preset_into_custom()

    def _on_preset_changed(self, index: int) -> None:
        pass

    def _add_custom_row(self, name: str = "", keywords: str = "") -> None:
        row_w = QWidget()
        row_lay = QHBoxLayout(row_w)
        row_lay.setContentsMargins(0, 2, 0, 2)
        name_input = ExpandableLineEdit()
        name_input.setPlaceholderText("标签名称")
        name_input.setFixedWidth(100)
        name_input.setText(name)
        kw_input = ExpandableLineEdit()
        kw_input.setPlaceholderText("提示词（空格分隔）")
        kw_input.setText(keywords)
        del_btn = QPushButton("删除")
        del_btn.setFixedWidth(44)
        del_btn.setStyleSheet(
            "QPushButton{background:#e74c3c;color:white;border:none;"
            "border-radius:4px;padding:3px 6px;font-size:11px;}"
            "QPushButton:hover{background:#c0392b;}"
        )

        idx = self._custom_layout.count() - 1
        del_btn.clicked.connect(lambda: self._remove_custom_row(row_w))
        self._custom_layout.insertWidget(idx, row_w)
        row_lay.addWidget(name_input)
        row_lay.addWidget(kw_input, 1)
        row_lay.addWidget(del_btn)

    def _remove_custom_row(self, row_w: QWidget) -> None:
        row_w.deleteLater()

    def _get_custom_data(self) -> list[dict]:
        data = []
        for i in range(self._custom_layout.count()):
            w = self._custom_layout.itemAt(i).widget()
            if w is None:
                continue
            children = w.findChildren(ExpandableLineEdit)
            if len(children) >= 2:
                name = children[0].text().strip()
                keywords = children[1].text().strip()
                if name:
                    data.append({"name": name, "keywords": keywords.split() if keywords else []})
        return data

    def _load_preset_into_custom(self) -> None:
        for i in range(self._custom_layout.count() - 1, -1, -1):
            w = self._custom_layout.itemAt(i).widget()
            if w:
                w.deleteLater()

        preset_name = self._map_combo.currentText()
        if not preset_name:
            return
        preset = self._load_preset(preset_name)
        if preset and preset.categories:
            for cat in preset.categories:
                kw_str = " ".join(cat.get("keywords", [])) if isinstance(cat.get("keywords"), list) else str(cat.get("keywords", ""))
                self._add_custom_row(cat.get("name", ""), kw_str)

        self._preset_name_input.setText(preset_name if not preset or not preset.is_default else "")

    def _check_preset_name(self, text: str) -> None:
        t = text.strip()
        if not t:
            self._preset_warning.setVisible(False)
            return
        if t in self._DEFAULT_PRESET_NAMES:
            self._preset_warning.setText("无法掩盖默认预设")
            self._preset_warning.setVisible(True)
        elif t in self._list_presets() and t not in self._DEFAULT_PRESET_NAMES:
            self._preset_warning.setText(f"将覆盖已有预设「{t}」")
            self._preset_warning.setVisible(True)
        else:
            self._preset_warning.setVisible(False)

    def _do_save_preset(self) -> None:
        name = self._preset_name_input.text().strip()
        if not name:
            QMessageBox.warning(self, "提示", "请输入预设名称")
            return
        if name in self._DEFAULT_PRESET_NAMES:
            QMessageBox.warning(self, "提示", "无法覆盖默认预设")
            return

        categories = self._get_custom_data()
        if not categories:
            QMessageBox.warning(self, "提示", "请至少添加一个自定义标签")
            return

        if name in self._list_presets():
            reply = QMessageBox.question(
                self, "确认覆盖",
                f"预设「{name}」已存在，是否覆盖？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        preset = self._CategoryPreset(name=name, is_default=False, categories=categories)
        ok, err = self._save_preset(preset)
        if not ok:
            QMessageBox.warning(self, "保存失败", err)
            return

        current = self._map_combo.currentIndex()
        self._map_combo.blockSignals(True)
        self._map_combo.clear()
        for pn in self._list_presets():
            self._map_combo.addItem(pn, pn)
        idx = self._map_combo.findText(name)
        if idx >= 0:
            self._map_combo.setCurrentIndex(idx)
        self._map_combo.blockSignals(False)

        self._preset_warning.setVisible(False)
        QMessageBox.information(self, "成功", f"预设「{name}」已保存")

    def _do_delete_preset(self) -> None:
        preset_name = self._map_combo.currentText()
        if not preset_name:
            QMessageBox.warning(self, "提示", "没有可删除的预设")
            return
        if preset_name in self._DEFAULT_PRESET_NAMES:
            QMessageBox.warning(self, "提示", "无法删除默认预设")
            return

        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要删除预设「{preset_name}」吗？\n此操作不可撤销。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        ok = self._delete_preset(preset_name)
        if not ok:
            QMessageBox.warning(self, "删除失败", f"无法删除预设「{preset_name}」")
            return

        self._map_combo.blockSignals(True)
        self._map_combo.clear()
        for pn in self._list_presets():
            self._map_combo.addItem(pn, pn)
        self._map_combo.setCurrentIndex(0)
        self._map_combo.blockSignals(False)

        self._preset_name_input.clear()
        self._preset_warning.setVisible(False)
        self._load_preset_into_custom()
        QMessageBox.information(self, "成功", f"预设「{preset_name}」已删除")

    def get_config(self):
        self._config.enabled = self._enabled_cb.isChecked()
        self._config.confidence_threshold = self._threshold_spin.value()
        selected = self._map_combo.currentText()
        self._config.category_map_type = selected
        self._config.preset_name = selected
        from services.classifier import clear_mapping_cache
        clear_mapping_cache()
        return self._config


class MainWindow(QMainWindow):
    _quantize_progress_sig = Signal(str, int, str)
    _quantize_done_sig = Signal(bool, str)
    _download_progress_sig = Signal(str, int, str)
    _download_done_sig = Signal(str, bool, str, float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"图形化图库 v{APP_VERSION}")
        self.resize(1150, 760)

        self._apply_theme()

        self._cache_mgr = CacheManager()
        self._thumb_cache = ThumbnailCache(max_edge=256)
        self._model_mgr = ModelManager()
        self._classify_cache = ClassifyCache()
        self._classify_cache.load()
        self._scan_gen = 0
        self._full_worker: FullCacheScanWorker | None = None
        self._single_worker: SinglePathScanWorker | None = None
        self._incr_worker: IncrementalCacheScanWorker | None = None
        self._classify_worker: ClassifyWorker | None = None
        self._active_store: CacheStore | None = None
        self._quantize_worker = None
        self._quantize_model_id = None
        self._downloading_model_entry = None
        self._active_downloads: dict[str, dict] = {}

        self._quantize_progress_sig.connect(self._handle_quantize_progress)
        self._quantize_done_sig.connect(self._on_quantize_done)
        self._download_progress_sig.connect(self._handle_download_progress)
        self._download_done_sig.connect(self._on_download_done)

        self._stack = QStackedWidget()
        self.setCentralWidget(self._stack)
        self._start = StartPage(base=PreferencesDialog.base_color())
        self._gallery = GalleryPage(self._thumb_cache, PreferencesDialog.base_color())
        self._classify_page = ClassifyPage(self._thumb_cache, PreferencesDialog.base_color())
        self._stack.addWidget(self._start)
        self._stack.addWidget(self._gallery)
        self._stack.addWidget(self._classify_page)

        self._status = QStatusBar()
        self.setStatusBar(self._status)

        self._build_menu()

        self._start.scan_requested.connect(self._on_folder_scan)
        self._start.full_cache_scan_requested.connect(self._on_rescan_all)
        self._start.cancel_requested.connect(self._on_cancel_scan)
        self._start.open_cache_requested.connect(self._on_open_cache)
        self._start.delete_cache_requested.connect(self._on_delete_cache)
        self._start.rescan_cache_requested.connect(self._on_rescan_cache)
        self._start.classify_requested.connect(self._show_classify)
        self._start.clear_all_requested.connect(self._on_clear_all_caches)
        self._gallery.home_requested.connect(self._on_gallery_home)
        self._classify_page.home_requested.connect(self._show_start)
        self._classify_page.start_classify_requested.connect(self._on_start_classify)
        self._classify_page.reclassify_requested.connect(self._on_reclassify)
        self._classify_page.pause_classify_requested.connect(self._on_pause_classify)
        self._classify_page.resume_classify_requested.connect(self._on_resume_classify)
        self._classify_page.category_clicked.connect(self._on_category_clicked)
        self._classify_page.clear_classify_cache_requested.connect(self._on_clear_classify_cache)

        self._start.refresh_cache_list(self._cache_mgr)
        self._update_classify_visibility()
        self._update_classify_page_state()

    def _apply_theme(self) -> None:
        base = PreferencesDialog.base_color()
        self.setStyleSheet(build_qss(base))

    def _build_menu(self) -> None:
        self.menuBar().clear()
        base = PreferencesDialog.base_color()
        icon_color = _text(base)
        accent_color = _accent(base)
        file_menu = self.menuBar().addMenu("文件")
        act_rescan = QAction("重新全盘扫描", self)
        act_rescan.setShortcut("Ctrl+Shift+R")
        act_rescan.triggered.connect(self._on_rescan_all)
        file_menu.addAction(act_rescan)

        view_menu = self.menuBar().addMenu("视图")
        act_depth = QAction("图库深度展平…", self)
        act_depth.setShortcut("Ctrl+D")
        act_depth.triggered.connect(self._on_set_flatten_depth)
        view_menu.addAction(act_depth)
        act_depth0 = QAction("展平 depth = 0（根目录显示全部图片）", self)
        act_depth0.triggered.connect(lambda: self._gallery.set_flatten_depth(0))
        view_menu.addAction(act_depth0)
        act_depth_normal = QAction("层级模式 (depth = -1)", self)
        act_depth_normal.triggered.connect(lambda: self._gallery.set_flatten_depth(-1))
        view_menu.addAction(act_depth_normal)

        view_menu.addSeparator()
        size_menu = view_menu.addMenu("缩略图大小")
        for name, px in SIZE_PRESETS.items():
            act = QAction(name, self)
            act.triggered.connect(lambda checked, s=px: self._gallery.set_tile_size(s))
            size_menu.addAction(act)

        edit_menu = self.menuBar().addMenu("编辑")
        act_prefs = QAction(svg_icon(SVG_SETTINGS.format(color=icon_color)), "首选项…", self)
        act_prefs.setShortcut("Ctrl+,")
        act_prefs.triggered.connect(self._show_preferences)
        edit_menu.addAction(act_prefs)
        act_model = QAction("分类模型配置…", self)
        act_model.triggered.connect(self._show_model_config)
        edit_menu.addAction(act_model)

        ui_menu = self.menuBar().addMenu("界面")
        act_cached = QAction(svg_icon(SVG_FOLDER.format(color=accent_color)), "返回已缓存图库", self)
        act_cached.triggered.connect(self._show_start)
        ui_menu.addAction(act_cached)

        help_menu = self.menuBar().addMenu("帮助")
        act_help = QAction("帮助与更新日志", self)
        act_help.setShortcut("F1")
        act_help.triggered.connect(self._show_help)
        help_menu.addAction(act_help)

    def _show_help(self) -> None:
        HelpDialog(self).exec()

    def _show_preferences(self) -> None:
        old_base = PreferencesDialog.base_color()
        PreferencesDialog(self).exec()
        new_base = PreferencesDialog.base_color()
        if new_base != old_base:
            self._apply_theme()
            self._build_menu()
            self._start.update_theme(new_base)
            self._gallery.set_base(new_base)
            self._classify_page.set_base(new_base)
        self._update_classify_visibility()

    def _show_model_config(self) -> None:
        dlg = ModelConfigDialog(self)
        if self._quantize_worker and self._quantize_worker.isRunning():
            dlg.show_quantize_progress(self._quantize_model_id)
        self._quantize_progress_sig.connect(dlg._handle_quantize_progress)
        self._quantize_done_sig.connect(dlg._on_quantize_done)
        self._download_progress_sig.connect(dlg.update_download_progress)
        self._download_done_sig.connect(dlg._on_download_done)
        dlg.finished.connect(lambda: self._disconnect_dialog_signals(dlg))
        dlg.exec()
        self._update_classify_visibility()
        self._update_classify_page_state()

    def _disconnect_dialog_signals(self, dlg) -> None:
        try:
            self._quantize_progress_sig.disconnect(dlg._handle_quantize_progress)
        except RuntimeError:
            pass
        try:
            self._quantize_done_sig.disconnect(dlg._on_quantize_done)
        except RuntimeError:
            pass
        try:
            self._download_progress_sig.disconnect(dlg.update_download_progress)
        except RuntimeError:
            pass
        try:
            self._download_done_sig.disconnect(dlg._on_download_done)
        except RuntimeError:
            pass

    def start_quantize(self, model_id: str) -> None:
        from workers.quantize_worker import QuantizeWorker
        self._quantize_model_id = model_id
        worker = QuantizeWorker(model_id, delete_fp32=True, parent=self)
        self._quantize_worker = worker
        worker.progress.connect(self._quantize_progress_sig.emit)
        worker.finished_ok.connect(self._quantize_done_sig.emit)
        worker.finished.connect(lambda: setattr(self, "_quantize_worker", None))
        worker.start()
        self._status.showMessage(f"正在量化模型 {model_id}…")

    def _handle_quantize_progress(self, phase: str, pct: int, msg: str) -> None:
        self._status.showMessage(msg)

    def _on_quantize_done(self, ok: bool, msg: str) -> None:
        model_id = self._quantize_model_id
        self._quantize_model_id = None

        if ok:
            self._model_mgr.release_model(model_id)
            entry = self._downloading_model_entry
            if entry and entry.id == model_id:
                self._downloading_model_entry = None
                self._status.showMessage("模型下载并量化完成", 8000)
            else:
                self._status.showMessage("模型量化完成", 8000)
        else:
            entry = self._downloading_model_entry
            if entry and entry.id == model_id:
                self._downloading_model_entry = None
                self._status.showMessage("模型下载成功但量化失败，将以原始模式使用", 8000)
            else:
                self._status.showMessage(f"模型量化失败: {msg}", 8000)

    def start_download(self, entry) -> None:
        if entry.id in self._active_downloads:
            return
        cancel_event = threading.Event()
        self._active_downloads[entry.id] = {
            "entry": entry,
            "cancel_event": cancel_event,
            "thread": None,
        }

        def on_progress(filename, downloaded, total, speed, using_mirror):
            pct = int(downloaded / total * 100) if total > 0 else 0
            mirror_tag = " (镜像加速)" if using_mirror else ""
            speed_str = f"{speed / 1024 / 1024:.1f} MB/s" if speed > 0 else "计算中…"
            size_str = f"{downloaded / 1024 / 1024:.1f} / {total / 1024 / 1024:.1f} MB" if total > 0 else f"{downloaded / 1024 / 1024:.1f} MB"
            text = f"{filename}{mirror_tag}  {size_str}  {speed_str}"
            self._download_progress_sig.emit(entry.id, pct, text)

        def on_status(msg):
            self._download_progress_sig.emit(entry.id, 0, msg)

        def do_download():
            ok, msg = self._model_mgr.download_registry_model(
                entry, on_progress, cancel_event, on_status
            )
            self._download_done_sig.emit(entry.id, ok, msg, entry.size_mb)

        t = threading.Thread(target=do_download, daemon=True)
        self._active_downloads[entry.id]["thread"] = t
        t.start()
        self._status.showMessage(f"正在下载模型 {entry.name}…")

    def cancel_download(self, model_id: str) -> None:
        info = self._active_downloads.get(model_id)
        if info:
            info["cancel_event"].set()

    def _handle_download_progress(self, model_id: str, pct: int, text: str) -> None:
        self._status.showMessage(text)

    def _on_download_done(self, model_id: str, ok: bool, msg: str, entry_size_mb: float = 0.0) -> None:
        info = self._active_downloads.pop(model_id, None)
        entry = info["entry"] if info else None

        if ok:
            if entry and entry.size_mb < 100:
                self._downloading_model_entry = entry
                self.start_quantize(model_id)
            else:
                if entry and entry.size_mb >= 500:
                    self._status.showMessage("模型下载完成（超过 500MB，INT8 不可用）", 8000)
                elif entry and entry.size_mb >= 100:
                    self._status.showMessage("模型下载完成（较大模型，未自动量化）", 8000)
                else:
                    self._status.showMessage("模型下载完成", 8000)
        else:
            if "取消" not in msg:
                self._status.showMessage(f"模型下载失败: {msg}", 8000)
            else:
                self._status.showMessage("下载已取消", 5000)

    def _on_set_flatten_depth(self) -> None:
        if self._stack.currentWidget() != self._gallery:
            QMessageBox.information(self, "提示", "请先进入图库界面后再设置展平深度。")
            return
        value, ok = QInputDialog.getInt(
            self, "图库深度展平",
            "输入 depth（≥ -1）：\n  -1 = 完整层级\n   0 = 根目录展平全部图片\n   N = 保留前 N 层",
            self._gallery.flatten_depth, -1, 99,
        )
        if ok:
            self._gallery.set_flatten_depth(value)
            self._status.showMessage(f"已设置展平 depth = {value}", 4000)

    def _gallery_from_store(self, store: CacheStore) -> Gallery:
        return build_gallery_tree_from_cache(
            store.roots,
            store.folder_images_map(),
            store.folder_children,
        )

    def _show_gallery_with_store(self, store: CacheStore) -> None:
        self._active_store = store
        tree = self._gallery_from_store(store)
        if tree.sub_galleries or tree.images:
            self._gallery.set_root_gallery(tree)
            self._stack.setCurrentWidget(self._gallery)

    def _on_gallery_home(self) -> None:
        if self._gallery._return_target == "classify":
            self._gallery.set_return_target("start")
            self._show_classify()
        else:
            self._show_start()

    def _show_start(self) -> None:
        self._scan_gen += 1
        self._stop_workers()
        self._stack.setCurrentWidget(self._start)
        self._start.on_finished()
        self._start.refresh_cache_list(self._cache_mgr)

    def _stop_workers(self) -> None:
        for w in (self._full_worker, self._single_worker, self._incr_worker):
            if w is not None and w.isRunning():
                w.requestInterruption()
                w.wait(2000)

    def _on_cancel_scan(self) -> None:
        self._scan_gen += 1
        self._stop_workers()
        self._start.on_cancelled()
        self._status.showMessage("扫描已中断", 4000)

    def _on_open_cache(self, entry: CacheEntry) -> None:
        store = self._cache_mgr.load_entry_store(entry)
        if not store or not store.folders:
            QMessageBox.information(self, "提示", "缓存为空或已损坏。")
            return
        self._show_gallery_with_store(store)
        self._status.showMessage("已从缓存加载图库", 4000)
        if entry.is_full_scan:
            self._start_incremental_scan(store)

    def _on_delete_cache(self, entry: CacheEntry) -> None:
        path_text = "全盘扫描" if entry.is_full_scan else (entry.scan_path or entry.filename)
        if (
            QMessageBox.question(self, "确认", f"删除缓存？\n{path_text}")
            != QMessageBox.StandardButton.Yes
        ):
            return
        store = self._cache_mgr.load_entry_store(entry)
        if store:
            all_images: list[str] = []
            for rec in store.folders.values():
                all_images.extend(rec.get("images", []))
            if all_images:
                valid_keys = self._thumb_cache.collect_valid_keys(all_images)
                self._thumb_cache.purge_orphans(valid_keys)
        self._cache_mgr.delete_entry(entry)
        self._start.refresh_cache_list(self._cache_mgr)
        self._status.showMessage("缓存已删除", 4000)

    def _on_rescan_cache(self, entry: CacheEntry, scan_path: str, is_full: bool) -> None:
        if is_full:
            self._on_rescan_all()
        elif scan_path:
            self._on_folder_scan(scan_path)

    def _check_conflicts(self, scan_path: str, is_full: bool) -> bool:
        conflicts = self._cache_mgr.find_conflicting_caches(scan_path, is_full)
        if not conflicts:
            return True
        names = []
        for c in conflicts:
            names.append("全盘扫描" if c.is_full_scan else (c.scan_path or c.filename))
        msg = "检测到以下缓存与当前扫描范围重叠，将被自动清理：\n\n"
        for n in names:
            msg += f"  • {n}\n"
        msg += "\n是否继续？"
        return QMessageBox.question(
            self, "缓存冲突", msg
        ) == QMessageBox.StandardButton.Yes

    def _on_rescan_all(self) -> None:
        if not self._check_conflicts("", True):
            return
        self._scan_gen += 1
        self._stop_workers()

        conflicts = self._cache_mgr.find_conflicting_caches("", True)
        for c in conflicts:
            self._cache_mgr.delete_entry(c)

        gen = self._scan_gen
        self._start.set_busy(True)
        self._status.showMessage("正在全盘扫描…")

        store, _ = self._cache_mgr.get_or_create_store("", is_full_scan=True)
        workers = PreferencesDialog.max_workers()
        worker = FullCacheScanWorker(store, max_workers=workers, parent=self)
        self._full_worker = worker
        worker.progress.connect(self._start.on_progress)
        worker.current_path.connect(self._start.set_current_path)
        worker.finished_ok.connect(lambda s, g=gen: self._on_full_done(g, s))
        worker.failed.connect(self._on_full_failed)
        worker.finished.connect(lambda: setattr(self, "_full_worker", None))
        worker.start()

    def _on_full_done(self, gen: int, store: CacheStore) -> None:
        if gen != self._scan_gen:
            return
        self._start.on_finished()
        self._show_gallery_with_store(store)
        self._status.showMessage("全盘扫描完成，缓存已保存", 8000)
        self._start_incremental_scan(store)

    def _on_full_failed(self, msg: str) -> None:
        self._start.on_failed(msg)
        self._status.showMessage("扫描失败", 5000)

    def _on_folder_scan(self, path: str) -> None:
        norm = _norm_path(path)

        parent_caches = self._cache_mgr.find_parent_cache(norm)
        if parent_caches:
            store = self._cache_mgr.load_entry_store(parent_caches[0])
            if store and store.folders:
                self._show_gallery_with_store(store)
                self._status.showMessage("已从父路径缓存加载", 4000)
                return

        if not self._check_conflicts(norm, False):
            return

        conflicts = self._cache_mgr.find_conflicting_caches(norm, False)
        for c in conflicts:
            self._cache_mgr.delete_entry(c)

        self._scan_gen += 1
        gen = self._scan_gen
        self._start.set_busy(True)
        self._status.showMessage(f"正在扫描 {path}…")

        store, loaded = self._cache_mgr.get_or_create_store(norm, is_full_scan=False)
        if loaded and store.folders:
            self._show_gallery_with_store(store)
            self._status.showMessage("已从缓存加载该路径", 4000)
            self._start.on_finished()
            return

        subdir_count = 0
        try:
            subdir_count = sum(1 for p in Path(path).iterdir() if p.is_dir() and not p.is_symlink())
        except OSError:
            pass
        workers = PreferencesDialog.compute_workers(subdir_count)

        worker = SinglePathScanWorker(store, norm, max_workers=workers, parent=self)
        self._single_worker = worker
        worker.progress.connect(self._start.on_progress)
        worker.current_path.connect(self._start.set_current_path)
        worker.finished_ok.connect(lambda s, sp, g=gen: self._on_single_done(g, s, sp))
        worker.failed.connect(self._start.on_failed)
        worker.finished.connect(lambda: setattr(self, "_single_worker", None))
        worker.start()

    def _on_single_done(self, gen: int, store: CacheStore, scan_path: str) -> None:
        if gen != self._scan_gen:
            return
        self._start.on_finished()
        self._show_gallery_with_store(store)
        self._status.showMessage("扫描完成，缓存已保存", 8000)

    def _start_incremental_scan(self, store: CacheStore) -> None:
        if self._incr_worker is not None and self._incr_worker.isRunning():
            return
        self._status.showMessage("后台增量扫描中…")

        workers = PreferencesDialog.max_workers()
        worker = IncrementalCacheScanWorker(store, max_workers=workers, parent=self)
        self._incr_worker = worker
        worker.progress.connect(self._on_incr_progress)
        worker.current_path.connect(self._start.set_current_path)
        worker.finished_ok.connect(self._on_incr_done)
        worker.failed.connect(self._on_incr_failed)
        worker.finished.connect(lambda: setattr(self, "_incr_worker", None))
        worker.start()

    def _on_incr_progress(self, cur: int, total: int, msg: str) -> None:
        self._status.showMessage(msg)

    def _on_incr_done(self, _store: object, changed: bool) -> None:
        self._purge_stale_thumbnails()
        if changed:
            store = self._active_store
            if store:
                self._show_gallery_with_store(store)
            self._status.showMessage("增量扫描完成，图库已更新", 6000)
        else:
            self._status.showMessage("增量扫描完成，无变化", 4000)

    def _on_incr_failed(self, msg: str) -> None:
        self._status.showMessage(f"增量扫描失败: {msg}", 8000)

    def _purge_stale_thumbnails(self) -> None:
        store = self._active_store
        if not store:
            return
        all_images: list[str] = []
        for rec in store.folders.values():
            all_images.extend(rec.get("images", []))
        if not all_images:
            return
        valid_keys = self._thumb_cache.collect_valid_keys(all_images)
        removed = self._thumb_cache.purge_orphans(valid_keys)
        if removed > 0:
            self._status.showMessage(f"已清理 {removed} 个过期缩略图", 4000)

    def _update_classify_visibility(self) -> None:
        from services.models_registry import get_enabled_models
        self._start.set_classify_visible(len(get_enabled_models()) > 0)

    def _update_classify_page_state(self) -> None:
        worker = self._classify_worker
        if worker and worker.isRunning():
            preset_name = self._classify_cache.preset_name or self._get_current_preset_name()
            if worker.is_paused:
                self._classify_page.show_paused_state(self._classify_cache, preset_name)
            else:
                self._classify_page.show_classifying_state(self._classify_cache, preset_name)
            return
        incomplete = self._classify_cache.has_incomplete()
        total = self._classify_cache.total_classified
        _logger.info("分类页面状态检测: has_incomplete=%s total_classified=%d progress=%d/%d",
                     incomplete, total,
                     self._classify_cache.progress_current,
                     self._classify_cache.progress_total)
        if incomplete and total > 0:
            cur = self._classify_cache.progress_current
            tot = self._classify_cache.progress_total
            if cur <= 0 and tot <= 0:
                _logger.warning("检测到残留 incomplete 标志但无进度数据，重置为空状态")
                self._classify_cache.mark_complete()
                self._classify_page.show_empty_state()
                return
            preset_name = self._classify_cache.preset_name or self._get_current_preset_name()
            self._classify_page.show_paused_state(self._classify_cache, preset_name)
        elif total > 0:
            self._classify_page.show_results(self._classify_cache)
        else:
            self._classify_page.show_empty_state()

    def _show_classify(self) -> None:
        worker = self._classify_worker
        preset_name = self._classify_cache.preset_name or self._get_current_preset_name()
        if worker and worker.isRunning():
            if worker.is_paused:
                self._classify_page.show_paused_state(self._classify_cache, preset_name)
            else:
                self._classify_page.show_classifying_state(self._classify_cache, preset_name)
        else:
            self._update_classify_page_state()
        self._stack.setCurrentWidget(self._classify_page)

    def _on_start_classify(self) -> None:
        self._do_classify(clear_cache=False)

    def _on_reclassify(self) -> None:
        self._do_classify(clear_cache=True)

    def _get_current_preset_name(self) -> str:
        from services.models_registry import get_enabled_models
        enabled = get_enabled_models()
        if enabled:
            config = enabled[0].get("config")
            if config:
                return config.category_map_type
        return "imagenet"

    def _do_classify(self, clear_cache: bool = False) -> None:
        from services.models_registry import get_enabled_models
        enabled = get_enabled_models()
        if not enabled:
            QMessageBox.warning(self, "提示", "请先在「编辑→分类模型配置」中下载或启用分类模型。")
            return
        entries = self._cache_mgr.list_entries()
        if not entries:
            QMessageBox.warning(self, "提示", "暂无已缓存的图库，请先扫描图片。")
            return
        total = 0
        for e in entries:
            s = self._cache_mgr.load_entry_store(e)
            if s:
                total += s.total_images()
        if total == 0:
            QMessageBox.warning(self, "提示", "暂无已缓存的图库，请先扫描图片。")
            return
        if clear_cache:
            self._classify_cache.clear()
            self._classify_cache.load()
        preset_name = self._get_current_preset_name()
        self._classify_page.show_classifying(self._classify_cache, preset_name)
        worker = ClassifyWorker(self._model_mgr, self._classify_cache, self._cache_mgr, parent=self)
        self._classify_worker = worker
        worker.progress.connect(self._on_classify_progress)
        worker.category_found.connect(self._on_category_found)
        worker.finished_ok.connect(self._on_classify_done)
        worker.stopped.connect(self._on_classify_stopped)
        worker.failed.connect(self._on_classify_failed)
        worker.finished.connect(lambda: setattr(self, "_classify_worker", None))
        worker.start()

    def _on_classify_progress(self, current: int, total: int, path: str) -> None:
        self._classify_page.update_progress(current, total, path)

    def _on_category_found(self, path: str, category: str, confidence: float) -> None:
        self._classify_page.increment_category(category)
        if (self._stack.currentWidget() is self._gallery
                and self._gallery.viewing_category == category):
            self._gallery.add_image(path)

    def _on_classify_done(self, total_classified: int, skipped: int) -> None:
        self._classify_page.show_results(self._classify_cache)
        skip_msg = f"，已跳过 {skipped} 张损坏图片" if skipped else ""
        self._status.showMessage(f"分类完成，共 {total_classified} 张{skip_msg}", 8000)

    def _on_classify_stopped(self, classified: int, skipped: int) -> None:
        preset_name = self._classify_cache.preset_name or self._get_current_preset_name()
        self._classify_page.show_paused_state(self._classify_cache, preset_name)
        skip_msg = f"，已跳过 {skipped} 张损坏图片" if skipped else ""
        self._status.showMessage(f"分类已暂停，已分类 {classified} 张{skip_msg}，可点击「继续分类」", 8000)

    def _on_classify_failed(self, msg: str) -> None:
        if self._classify_cache.total_classified > 0:
            self._classify_cache.mark_complete()
            self._classify_page.show_results(self._classify_cache)
        else:
            self._classify_cache.mark_complete()
            self._classify_page.show_empty_state()
        QMessageBox.warning(self, "分类失败", msg)

    def _on_pause_classify(self) -> None:
        if self._classify_worker:
            self._classify_worker.pause()
            self._classify_page.show_paused()
            self._classify_cache.save()

    def _on_resume_classify(self) -> None:
        if self._classify_worker and self._classify_worker.isRunning():
            self._classify_worker.resume()
            self._classify_page.show_resumed()
        else:
            self._do_classify(clear_cache=False)

    def _on_clear_classify_cache(self) -> None:
        reply = QMessageBox.question(
            self, "确认清除", "确定要清除所有分类缓存吗？\n\n这将删除分类缓存数据库，清除后需要重新执行分类。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._classify_cache.clear()
        self._classify_cache.load()
        self._classify_page.show_empty_state()
        from services.model_manager import ModelManager
        ModelManager().clear_tag_probs_cache()
        self._status.showMessage("分类缓存和标签缓存已清除", 4000)

    def _on_clear_all_caches(self) -> None:
        if self._classify_worker and self._classify_worker.isRunning():
            self._classify_worker.request_stop()
            self._classify_worker.wait(3000)
            self._classify_worker = None
        self._cache_mgr.delete_all()
        self._classify_cache.clear()
        from services.model_manager import ModelManager
        ModelManager().clear_tag_probs_cache()
        import shutil
        from paths import THUMBNAIL_DIR
        if os.path.isdir(THUMBNAIL_DIR):
            shutil.rmtree(THUMBNAIL_DIR, ignore_errors=True)
            os.makedirs(THUMBNAIL_DIR, exist_ok=True)
        self._classify_page.show_empty_state()
        self._start.refresh_cache_list(self._cache_mgr)
        self._status.showMessage("所有缓存已清除（图库、分类、标签、缩略图）", 4000)

    def _on_category_clicked(self, category: str) -> None:
        images = self._classify_cache.get_images_by_category().get(category, [])
        if not images:
            QMessageBox.information(self, category, "该分类下暂无图片。")
            return
        from models.gallery import Gallery
        root = Gallery(path="", name=category)
        root.images = images
        self._gallery.set_root_gallery(root)
        self._gallery.set_return_target("classify")
        self._stack.setCurrentWidget(self._gallery)

    def closeEvent(self, event) -> None:
        self._stop_workers()
        for model_id, info in list(self._active_downloads.items()):
            info["cancel_event"].set()
        for model_id, info in list(self._active_downloads.items()):
            t = info.get("thread")
            if t and t.is_alive():
                t.join(timeout=3)
        self._active_downloads.clear()
        if self._classify_worker and self._classify_worker.isRunning():
            self._classify_cache.mark_incomplete()
            self._classify_cache.save()
            self._classify_worker.request_stop()
            self._classify_worker.wait(5000)
        self._classify_cache.close()
        if self._quantize_worker and self._quantize_worker.isRunning():
            self._quantize_worker.requestInterruption()
            self._quantize_worker.wait(3000)
            model_id = self._quantize_model_id
            if model_id:
                from services.models_registry import scan_installed_models
                for m in scan_installed_models():
                    if m["model_id"] == model_id:
                        int8_path = m.get("int8_path", "")
                        if int8_path and os.path.isfile(int8_path):
                            fp32_path = m["onnx_path"]
                            if os.path.isfile(fp32_path):
                                try:
                                    os.remove(int8_path)
                                except OSError:
                                    pass
                        break
                self._quantize_model_id = None
        from paths import cleanup_on_exit
        cleanup_on_exit()
        super().closeEvent(event)
