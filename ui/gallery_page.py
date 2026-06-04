from __future__ import annotations

import os
import shutil
from collections import OrderedDict
from functools import partial
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from PySide6.QtCore import Qt, Signal, QPropertyAnimation, QEasingCurve, QSize, QSettings, QPoint, QRect, QEvent
from PySide6.QtGui import QPixmap, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QRubberBand,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from models.gallery import Gallery
from models.gallery_view import (
    DisplayImage,
    build_gallery_view_rows,
    count_sub_galleries_recursive,
    count_images_recursive,
    effective_flatten_depth,
    flatten_depth_label,
)
from services.reveal_path import reveal_in_file_manager
from services.thumbnail_cache import ThumbnailCache
from ui.image_preview import ImagePreviewDialog
from ui.properties_dialog import ImagePropertiesDialog, FolderPropertiesDialog
from ui.theme import build_tile_qss, build_breadcrumb_qss, DEFAULT_BASE, SVG_HOME, svg_icon, _surface, _border, _hover, _accent, _text, _text_dim
from workers.thumb_worker import ThumbSignals, ThumbTask, global_thumb_pool
from services.model_manager import ModelManager
from services.classify_cache import ClassifyCache
from services.models_registry import get_enabled_models

PAGE_SIZE = 42


class _PixmapLRUCache:
    def __init__(self, max_size: int = 100) -> None:
        self._max_size = max(1, max_size)
        self._cache: OrderedDict[str, QPixmap] = OrderedDict()

    def put(self, key: str, pixmap: QPixmap) -> None:
        if key in self._cache:
            self._cache.move_to_end(key)
            return
        self._cache[key] = pixmap
        self._cache.move_to_end(key)
        self._evict()

    def get(self, key: str) -> QPixmap | None:
        pix = self._cache.get(key)
        if pix is not None:
            self._cache.move_to_end(key)
        return pix

    def set_max_size(self, max_size: int) -> None:
        self._max_size = max(1, max_size)
        self._evict()

    def clear(self) -> None:
        self._cache.clear()

    def __len__(self) -> int:
        return len(self._cache)

    def _evict(self) -> None:
        while len(self._cache) > self._max_size:
            self._cache.popitem(last=False)

_model_mgr_instance: ModelManager | None = None


def _get_model_mgr() -> ModelManager:
    global _model_mgr_instance
    if _model_mgr_instance is None:
        _model_mgr_instance = ModelManager()
    return _model_mgr_instance

SIZE_PRESETS: Dict[str, int] = {
    "超大图标": 256,
    "大图标": 160,
    "中等图标": 96,
    "小图标": 64,
}

DEFAULT_SIZE_NAME = "大图标"

TILE_MARGIN = 6
SCROLL_BOTTOM_THRESHOLD = 300


def _first_preview_image(gallery: Gallery) -> Optional[str]:
    if gallery.images:
        return gallery.images[0]
    for sub in gallery.sub_galleries:
        found = _first_preview_image(sub)
        if found:
            return found
    return None


class _Tile(QFrame):
    clicked = Signal()
    double_clicked = Signal()

    def __init__(self, caption: str, tile_size: int, base: str = DEFAULT_BASE, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tile_size = tile_size
        self._base = base
        self._selected = False
        self.setObjectName("imgTile")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._style_sheet = build_tile_qss(base, "imgTile")
        self.setStyleSheet(self._style_sheet)
        self._thumb = QLabel()
        self._thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._thumb.setFixedSize(tile_size, tile_size)
        self._thumb.setObjectName("thumbPlaceholder")
        self._thumb.setStyleSheet(f"QLabel#thumbPlaceholder{{background:{_border(base)};border-radius:8px;}}")
        self._thumb.setText("…")
        cap = QLabel(caption)
        cap.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cap.setWordWrap(True)
        cap.setMaximumWidth(tile_size + TILE_MARGIN * 2)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(TILE_MARGIN, TILE_MARGIN, TILE_MARGIN, TILE_MARGIN)
        lay.addWidget(self._thumb)
        lay.addWidget(cap)
        self.setFixedWidth(tile_size + TILE_MARGIN * 2)
        self._anim = QPropertyAnimation(self, b"windowOpacity")
        self._anim.setDuration(120)

    def set_selected(self, selected: bool) -> None:
        if self._selected == selected:
            return
        self._selected = selected
        self.setProperty("selected", selected)
        self.style().unpolish(self)
        self.style().polish(self)

    def enterEvent(self, event) -> None:  # noqa: ANN001
        self._anim.setStartValue(1.0)
        self._anim.setEndValue(0.88)
        self._anim.start()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: ANN001
        self._anim.setStartValue(0.88)
        self._anim.setEndValue(1.0)
        self._anim.start()
        super().leaveEvent(event)

    def set_pixmap(self, pix: QPixmap) -> None:
        if pix.isNull():
            return
        self._thumb.setPixmap(
            pix.scaled(
                self._thumb.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        self._thumb.setText("")

    def mousePressEvent(self, event) -> None:  # noqa: ANN001
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: ANN001
        if event.button() == Qt.MouseButton.LeftButton:
            self.double_clicked.emit()
        super().mouseDoubleClickEvent(event)


class _SubGalleryTile(QFrame):
    clicked = Signal()
    double_clicked = Signal()

    def __init__(self, gallery: Gallery, caption: str, tile_size: int, base: str = DEFAULT_BASE, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.gallery = gallery
        self._tile_size = tile_size
        self._base = base
        self._selected = False
        self.setObjectName("subTile")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._style_sheet = build_tile_qss(base, "subTile")
        self.setStyleSheet(self._style_sheet)
        self._container = QWidget()
        self._container.setFixedSize(tile_size, tile_size)

        sub_n = count_sub_galleries_recursive(gallery)
        img_n = count_images_recursive(gallery)
        self._has_nested = sub_n > 0

        if self._has_nested:
            off2 = max(1, int(tile_size * 0.1375))
            sz2 = max(1, int(tile_size * 0.7375))
            off1 = max(1, int(tile_size * 0.06875))
            sz1 = max(1, int(tile_size * 0.8625))
            back2 = QFrame(self._container)
            back2.setGeometry(off2, off2, sz2, sz2)
            back2.setObjectName("stackBack2")
            back2.setStyleSheet(
                f"QFrame#stackBack2{{background:{_surface(base)};border-radius:10px;border:1px solid {_border(base)};}}"
            )
            back1 = QFrame(self._container)
            back1.setGeometry(off1, off1, sz1, sz1)
            back1.setObjectName("stackBack1")
            back1.setStyleSheet(
                f"QFrame#stackBack1{{background:{_hover(base)};border-radius:10px;border:1px solid {_border(base)};}}"
            )

        self._thumb = QLabel(self._container)
        self._thumb.setObjectName("subThumbPlaceholder")
        if self._has_nested:
            t_off = max(0, int(tile_size * 0.03125))
            t_sz = max(1, int(tile_size * 0.9375))
            self._thumb.setGeometry(t_off, t_off, t_sz, t_sz)
        else:
            self._thumb.setGeometry(0, 0, tile_size, tile_size)
        self._thumb.setStyleSheet(f"QLabel#subThumbPlaceholder{{background:{_border(base)};border-radius:8px;}}")
        self._thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._thumb.setText("…")

        badge_font = max(8, min(12, tile_size // 16))
        self._badge = QLabel(self._container)
        self._badge.setText(f"子图库 {sub_n} · 图片 {img_n}")
        self._badge.setStyleSheet(
            f"color:#eee;background:rgba(0,0,0,165);padding:3px 6px;"
            f"border-radius:6px;font-size:{badge_font}px;font-weight:bold;"
        )
        self._badge.adjustSize()
        bw = self._badge.sizeHint().width()
        bh = self._badge.sizeHint().height()
        self._badge.setGeometry(tile_size - bw - 4, tile_size - bh - 4, bw + 4, bh + 2)

        cap = QLabel(caption)
        cap.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cap.setWordWrap(True)
        cap.setMaximumWidth(tile_size + TILE_MARGIN * 2)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(TILE_MARGIN, TILE_MARGIN, TILE_MARGIN, TILE_MARGIN)
        lay.addWidget(self._container)
        lay.addWidget(cap)
        self.setFixedWidth(tile_size + TILE_MARGIN * 2)
        self._thumb.raise_()
        self._badge.raise_()

    def set_selected(self, selected: bool) -> None:
        if self._selected == selected:
            return
        self._selected = selected
        self.setProperty("selected", selected)
        self.style().unpolish(self)
        self.style().polish(self)

    def set_pixmap(self, pix: QPixmap) -> None:
        if pix.isNull():
            return
        self._thumb.setPixmap(
            pix.scaled(
                self._thumb.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        self._thumb.setText("")

    def mousePressEvent(self, event) -> None:  # noqa: ANN001
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: ANN001
        if event.button() == Qt.MouseButton.LeftButton:
            self.double_clicked.emit()
        super().mouseDoubleClickEvent(event)


_ThumbTile = Union[_Tile, _SubGalleryTile]


class GalleryPage(QWidget):
    home_requested = Signal()

    def __init__(self, thumb_cache: ThumbnailCache, base: str = DEFAULT_BASE, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._cache = thumb_cache
        self._thumb_signals = ThumbSignals(self)
        self._thumb_signals.ready.connect(self._on_thumb_ready)
        self._thumb_signals.error.connect(self._on_thumb_error)

        self._tree_root: Optional[Gallery] = None
        self._nav_stack: List[Gallery] = []
        self._return_target: str = "start"
        self._flatten_depth: int = -1
        self._loaded_count = 0
        self._display_rows: List[Tuple[str, Any]] = []
        self._tiles: List[_ThumbTile] = []
        self._tile_by_source: Dict[str, _ThumbTile] = {}
        self._tile_size = SIZE_PRESETS[DEFAULT_SIZE_NAME]
        self._columns = 6
        self._loading = False
        self._base = base
        self._sort_ascending = True
        self._selected_tiles: set = set()
        self._anchor_tile: _ThumbTile | None = None
        self._rubber_band: QRubberBand | None = None
        self._rubber_origin = QPoint()
        self._rubber_active = False
        self._pixmap_cache = _PixmapLRUCache(100)

        self._back_btn = QPushButton("返回")
        self._back_btn.clicked.connect(self._go_back)

        self._home_icon_btn = QPushButton()
        self._home_icon_btn.setIcon(svg_icon(SVG_HOME.format(color=_accent(self._base)), 20))
        self._home_icon_btn.setIconSize(QSize(20, 20))
        self._home_icon_btn.setFixedSize(36, 36)
        self._home_icon_btn.setToolTip("返回启动页")
        self._home_icon_btn.setStyleSheet(
            f"QPushButton{{background:transparent;border:none;border-radius:6px;}}"
            f"QPushButton:hover{{background:{_accent(self._base)};border-radius:6px;}}"
        )
        self._home_icon_btn.clicked.connect(self.home_requested.emit)

        self._title = QLabel("")
        self._title.setStyleSheet(f"font-size:16px;font-weight:bold;color:{_text(self._base)};")

        self._depth_spin = QSpinBox()
        self._depth_spin.setMinimum(-1)
        self._depth_spin.setMaximum(99)
        self._depth_spin.setValue(-1)
        self._depth_spin.setSpecialValueText("层级 (-1)")
        self._depth_spin.setToolTip(
            "图库深度展平：-1=完整层级；0=根目录展平全部图片；"
            "N=保留前 N 层文件夹，更深层图片展平到第 N 层显示。"
        )
        self._depth_spin.valueChanged.connect(self._on_depth_changed)
        self._depth_label = QLabel("")
        self._depth_label.setStyleSheet(f"color:{_text_dim(self._base)};font-size:11px;")

        self._size_combo = QComboBox()
        for name in SIZE_PRESETS:
            self._size_combo.addItem(name, SIZE_PRESETS[name])
        self._size_combo.setCurrentText(DEFAULT_SIZE_NAME)
        self._size_combo.setToolTip("切换缩略图大小")
        self._size_combo.currentIndexChanged.connect(self._on_size_changed)

        self._sort_combo = QComboBox()
        self._sort_combo.addItems(["名称", "分辨率", "格式", "创建时间", "修改时间"])
        self._sort_combo.setToolTip("排序方式")
        self._sort_combo.currentIndexChanged.connect(self._on_sort_changed)

        self._sort_order_btn = QPushButton("↑ 正序")
        self._sort_order_btn.setToolTip("当前：正序（点击切换为倒序）")
        self._sort_order_btn.clicked.connect(self._on_sort_order_toggled)

        top = QHBoxLayout()
        top.addWidget(self._back_btn)
        top.addWidget(self._home_icon_btn)
        top.addWidget(self._title, 1)
        top.addWidget(QLabel("图标:"))
        top.addWidget(self._size_combo)
        top.addWidget(QLabel("排序:"))
        top.addWidget(self._sort_combo)
        top.addWidget(self._sort_order_btn)
        top.addWidget(QLabel("展平 depth:"))
        top.addWidget(self._depth_spin)

        self._breadcrumb_layout = QHBoxLayout()
        self._breadcrumb_layout.setContentsMargins(4, 2, 4, 2)
        self._breadcrumb_layout.setSpacing(0)
        self._breadcrumb_widget = QWidget()
        self._breadcrumb_widget.setLayout(self._breadcrumb_layout)
        self._breadcrumb_widget.setStyleSheet(build_breadcrumb_qss(self._base))

        depth_row = QHBoxLayout()
        depth_row.addWidget(self._depth_label, 1)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.verticalScrollBar().valueChanged.connect(self._on_scroll)

        self._content = QWidget()
        self._content_lay = QVBoxLayout(self._content)
        self._content_lay.setContentsMargins(0, 0, 0, 0)
        self._content_lay.setSpacing(0)

        self._grid_container = QWidget()
        self._grid = QGridLayout(self._grid_container)
        self._grid.setContentsMargins(TILE_MARGIN, TILE_MARGIN, TILE_MARGIN, TILE_MARGIN)
        self._grid.setSpacing(TILE_MARGIN)
        self._content_lay.addWidget(self._grid_container)

        self._loading_label = QLabel("加载中…")
        self._loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._loading_label.setStyleSheet(f"color:{_text_dim(self._base)};padding:8px;")
        self._loading_label.setVisible(False)
        self._content_lay.addWidget(self._loading_label)

        self._content_lay.addStretch(1)

        self._scroll.setWidget(self._content)
        self._scroll.viewport().installEventFilter(self)

        root = QVBoxLayout(self)
        root.addLayout(top)
        root.addWidget(self._breadcrumb_widget)
        root.addLayout(depth_row)
        root.addWidget(self._scroll, 1)

    def _calc_columns(self) -> int:
        avail = self._scroll.viewport().width() - 2 * TILE_MARGIN
        cell_w = self._tile_size + 2 * TILE_MARGIN + TILE_MARGIN
        if cell_w <= 0:
            return 1
        return max(1, avail // cell_w)

    def _update_pixmap_cache_size(self) -> None:
        vp_h = self._scroll.viewport().height()
        cell_h = self._tile_size + 2 * TILE_MARGIN
        visible_rows = max(1, vp_h // cell_h) if cell_h > 0 else 1
        max_size = visible_rows * self._columns * 2
        self._pixmap_cache.set_max_size(max(20, max_size))

    def _rebuild_breadcrumb(self) -> None:
        while self._breadcrumb_layout.count():
            item = self._breadcrumb_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        for i, gallery in enumerate(self._nav_stack):
            if i > 0:
                sep = QLabel(" › ")
                sep.setStyleSheet(f"color:{_text_dim(self._base)};font-size:13px;padding:0 1px;")
                self._breadcrumb_layout.addWidget(sep)

            is_last = i == len(self._nav_stack) - 1
            name = gallery.name or Path(gallery.path).name or gallery.path
            btn = QPushButton(name)
            btn.setFlat(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor if not is_last else Qt.CursorShape.ArrowCursor)
            btn.setStyleSheet(
                f"QPushButton{{border:none;padding:2px 4px;font-size:12px;"
                f"color:{_text(self._base) if is_last else _accent(self._base)};"
                f"font-weight:{'bold' if is_last else 'normal'};}}"
                f"QPushButton:hover{{{'text-decoration:underline;' if not is_last else ''}}}"
            )
            if not is_last:
                idx = i
                btn.clicked.connect(lambda checked, x=idx: self._jump_to_nav(x))
            self._breadcrumb_layout.addWidget(btn)

        self._breadcrumb_layout.addStretch(1)

    def _jump_to_nav(self, index: int) -> None:
        if 0 <= index < len(self._nav_stack) - 1:
            self._nav_stack = self._nav_stack[: index + 1]
            self._refresh_view()

    def set_return_target(self, target: str) -> None:
        self._return_target = target

    @property
    def viewing_category(self) -> str | None:
        if self._return_target == "classify" and self._nav_stack:
            return self._nav_stack[0].name
        return None

    def add_image(self, image_path: str) -> None:
        if not self._tree_root:
            return
        self._tree_root.images.append(image_path)
        self._current().images.append(image_path)
        disp = DisplayImage(path=image_path, display_name=Path(image_path).name)
        self._display_rows.append(("img", disp))
        tile = _Tile(disp.display_name, self._tile_size, self._base)
        self._tile_by_source[disp.path] = tile
        global_thumb_pool().start(
            ThumbTask(disp.path, self._cache, self._thumb_signals)
        )
        tile.clicked.connect(partial(self._on_tile_clicked, tile))
        tile.double_clicked.connect(partial(self._open_preview, disp.path))
        tile.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        tile.customContextMenuRequested.connect(
            partial(self._on_img_menu, tile, disp.path)
        )
        self._tiles.append(tile)
        r, c = divmod(len(self._tiles) - 1, self._columns)
        self._grid.addWidget(tile, r, c)
        self._loaded_count += 1

    def set_root_gallery(self, gallery: Gallery) -> None:
        self._tree_root = gallery
        self._nav_stack = [gallery]
        self._flatten_depth = self._depth_spin.value()
        self._refresh_view()

    @property
    def flatten_depth(self) -> int:
        return self._flatten_depth

    def set_flatten_depth(self, depth: int) -> None:
        self._depth_spin.blockSignals(True)
        self._depth_spin.setValue(max(-1, depth))
        self._depth_spin.blockSignals(False)
        self._flatten_depth = self._depth_spin.value()
        self._refresh_view()

    def set_tile_size(self, size: int) -> None:
        if size == self._tile_size:
            return
        idx = self._size_combo.findData(size)
        if idx >= 0:
            self._size_combo.blockSignals(True)
            self._size_combo.setCurrentIndex(idx)
            self._size_combo.blockSignals(False)
        self._tile_size = size
        new_tag = f"{size}x{size}"
        self._cache.purge_other_sizes(new_tag)
        self._refresh_view()

    def set_base(self, base: str) -> None:
        self._base = base
        self._home_icon_btn.setIcon(svg_icon(SVG_HOME.format(color=_accent(base)), 20))
        self._home_icon_btn.setStyleSheet(
            f"QPushButton{{background:transparent;border:none;border-radius:6px;}}"
            f"QPushButton:hover{{background:{_accent(base)};border-radius:6px;}}"
        )
        self._title.setStyleSheet(f"font-size:16px;font-weight:bold;color:{_text(base)};")
        self._breadcrumb_widget.setStyleSheet(build_breadcrumb_qss(base))
        self._depth_label.setStyleSheet(f"color:{_text_dim(base)};font-size:11px;")
        self._loading_label.setStyleSheet(f"color:{_text_dim(base)};padding:8px;")
        self._refresh_view()

    def _on_depth_changed(self, value: int) -> None:
        self._flatten_depth = value
        self._refresh_view()

    def _on_size_changed(self) -> None:
        size = self._size_combo.currentData()
        if size and size != self._tile_size:
            self._tile_size = size
            self._refresh_view()

    def _on_sort_changed(self) -> None:
        self._refresh_view()

    def _on_sort_order_toggled(self) -> None:
        self._sort_ascending = not self._sort_ascending
        if self._sort_ascending:
            self._sort_order_btn.setText("↑ 正序")
            self._sort_order_btn.setToolTip("当前：正序（点击切换为倒序）")
        else:
            self._sort_order_btn.setText("↓ 倒序")
            self._sort_order_btn.setToolTip("当前：倒序（点击切换为正序）")
        self._refresh_view()

    def _on_tile_clicked(self, tile: _ThumbTile) -> None:
        modifiers = QApplication.keyboardModifiers()
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            self._toggle_selection(tile)
        elif modifiers & Qt.KeyboardModifier.ShiftModifier and self._anchor_tile is not None:
            self._select_range(self._anchor_tile, tile)
        else:
            self._select_single(tile)

    def _select_single(self, tile: _ThumbTile) -> None:
        self._clear_selection()
        tile.set_selected(True)
        self._selected_tiles.add(tile)
        self._anchor_tile = tile

    def _select_range(self, from_tile: _ThumbTile, to_tile: _ThumbTile) -> None:
        self._clear_selection()
        try:
            idx_from = self._tiles.index(from_tile)
            idx_to = self._tiles.index(to_tile)
        except ValueError:
            return
        if idx_from > idx_to:
            idx_from, idx_to = idx_to, idx_from
        for i in range(idx_from, idx_to + 1):
            t = self._tiles[i]
            t.set_selected(True)
            self._selected_tiles.add(t)

    def _toggle_selection(self, tile: _ThumbTile) -> None:
        if tile._selected:  # noqa: SLF001
            tile.set_selected(False)
            self._selected_tiles.discard(tile)
        else:
            tile.set_selected(True)
            self._selected_tiles.add(tile)
            self._anchor_tile = tile

    def _clear_selection(self) -> None:
        from shiboken6 import isValid
        for t in list(self._selected_tiles):
            if isValid(t):
                t.set_selected(False)
        self._selected_tiles.clear()

    def _get_selected_paths(self) -> List[str]:
        paths: List[str] = []
        for tile in self._selected_tiles:
            if isinstance(tile, _Tile):
                for i, (kind, data) in enumerate(self._display_rows):
                    if kind == "img" and i < len(self._tiles) and self._tiles[i] is tile:
                        paths.append(data.path if isinstance(data, DisplayImage) else str(data))
                        break
            elif isinstance(tile, _SubGalleryTile):
                paths.append(tile.gallery.path)
        return paths

    def _get_selected_galleries(self) -> List[Gallery]:
        gals: List[Gallery] = []
        for tile in self._selected_tiles:
            if isinstance(tile, _SubGalleryTile):
                gals.append(tile.gallery)
        return gals

    def keyPressEvent(self, event) -> None:  # noqa: ANN001
        if event.key() == Qt.Key.Key_Escape:
            self._clear_selection()
        super().keyPressEvent(event)

    def _tile_at_viewport_pos(self, pos: QPoint) -> _ThumbTile | None:
        for tile in self._tiles:
            tile_rect = QRect(tile.mapTo(self._scroll.viewport(), QPoint(0, 0)), tile.size())
            if tile_rect.contains(pos):
                return tile
        return None

    def eventFilter(self, obj, event) -> bool:  # noqa: ANN001
        if obj is not self._scroll.viewport():
            return super().eventFilter(obj, event)
        et = event.type()
        if et == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            vp_pos = event.position().toPoint()
            tile = self._tile_at_viewport_pos(vp_pos)
            if tile is None:
                self._clear_selection()
                self._rubber_origin = vp_pos
                if self._rubber_band is None:
                    self._rubber_band = QRubberBand(QRubberBand.Shape.Rectangle, self._scroll.viewport())
                self._rubber_band.setGeometry(QRect(self._rubber_origin, QSize()).normalized())
                self._rubber_band.show()
                self._rubber_active = True
                return True
        elif et == QEvent.Type.MouseMove and self._rubber_active and self._rubber_band:
            self._rubber_band.setGeometry(
                QRect(self._rubber_origin, event.position().toPoint()).normalized()
            )
            return True
        elif et == QEvent.Type.MouseButtonRelease and self._rubber_active:
            self._rubber_band.hide()
            self._rubber_active = False
            rect = QRect(self._rubber_origin, event.position().toPoint()).normalized()
            modifiers = QApplication.keyboardModifiers()
            if not (modifiers & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)):
                self._clear_selection()
            for tile in self._tiles:
                tile_rect = QRect(tile.mapTo(self._scroll.viewport(), QPoint(0, 0)), tile.size())
                if rect.intersects(tile_rect):
                    tile.set_selected(True)
                    self._selected_tiles.add(tile)
            return True
        return super().eventFilter(obj, event)

    def _fill_image_metadata(self, disp: DisplayImage) -> None:
        try:
            stat = os.stat(disp.path)
            disp.created_time = stat.st_ctime
            disp.modified_time = stat.st_mtime
        except OSError:
            pass
        try:
            from PIL import Image
            old_limit = Image.MAX_IMAGE_PIXELS
            Image.MAX_IMAGE_PIXELS = None
            try:
                with Image.open(disp.path) as img:
                    disp.resolution = img.size
                    disp.format = img.format
            finally:
                Image.MAX_IMAGE_PIXELS = old_limit
        except Exception:
            pass

    def _fill_and_sort_rows(self) -> None:
        for kind, data in self._display_rows:
            if kind == "img" and isinstance(data, DisplayImage):
                self._fill_image_metadata(data)
        sub_rows = [(k, d) for k, d in self._display_rows if k == "sub"]
        img_rows = [(k, d) for k, d in self._display_rows if k == "img"]
        sort_key = self._sort_combo.currentText()
        if sort_key == "创建时间":
            sub_rows.sort(key=lambda x: os.path.getctime(x[1].path) if os.path.isdir(x[1].path) else 0.0)
        elif sort_key == "修改时间":
            sub_rows.sort(key=lambda x: os.path.getmtime(x[1].path) if os.path.isdir(x[1].path) else 0.0)
        else:
            sub_rows.sort(key=lambda x: (x[1].name or Path(x[1].path).name or "").lower())
        if sort_key == "分辨率":
            img_rows.sort(key=lambda x: (x[1].resolution[0] * x[1].resolution[1]) if x[1].resolution else 0)
        elif sort_key == "格式":
            img_rows.sort(key=lambda x: (x[1].format or "").lower())
        elif sort_key == "创建时间":
            img_rows.sort(key=lambda x: x[1].created_time or 0.0)
        elif sort_key == "修改时间":
            img_rows.sort(key=lambda x: x[1].modified_time or 0.0)
        else:
            img_rows.sort(key=lambda x: x[1].display_name.lower())
        if not self._sort_ascending:
            sub_rows.reverse()
            img_rows.reverse()
        self._display_rows = sub_rows + img_rows

    def _current(self) -> Gallery:
        return self._nav_stack[-1]

    def _stack_depth(self) -> int:
        return len(self._nav_stack) - 1

    def _go_back(self) -> None:
        if len(self._nav_stack) > 1:
            self._nav_stack.pop()
            self._refresh_view()
        else:
            self.home_requested.emit()

    def _enter_sub(self, sub: Gallery) -> None:
        self._nav_stack.append(sub)
        self._refresh_view()

    def _current_image_paths(self) -> List[str]:
        paths: List[str] = []
        for kind, data in self._display_rows:
            if kind == "img":
                if isinstance(data, DisplayImage):
                    paths.append(data.path)
                else:
                    paths.append(str(data))
        return paths

    def _refresh_view(self) -> None:
        self._clear_grid()
        self._tile_by_source.clear()
        self._tiles.clear()
        self._loaded_count = 0
        self._loading = False
        cur = self._current()
        root = self._tree_root or cur

        mode = flatten_depth_label(self._flatten_depth, root)
        eff = effective_flatten_depth(self._flatten_depth, root)
        self._depth_label.setText(
            f"视图：{mode} · 当前层级 {self._stack_depth()}"
            + (f" · 有效 depth={eff}" if self._flatten_depth >= 0 else "")
        )

        title = cur.name or cur.path or "图库"
        self._title.setText(title)
        self._back_btn.setEnabled(True)

        self._rebuild_breadcrumb()

        self._display_rows = build_gallery_view_rows(
            cur,
            self._stack_depth(),
            self._flatten_depth,
            root,
        )
        self._fill_and_sort_rows()
        self._columns = self._calc_columns()
        self._update_pixmap_cache_size()
        self._append_page()

    def _clear_grid(self) -> None:
        self._selected_tiles.clear()
        self._anchor_tile = None
        self._pixmap_cache.clear()
        while self._grid.count():
            item = self._grid.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

    def _relayout_grid(self) -> None:
        widgets: List[QWidget] = []
        while self._grid.count():
            item = self._grid.takeAt(0)
            w = item.widget()
            if w:
                widgets.append(w)
        self.setUpdatesEnabled(False)
        for i, w in enumerate(widgets):
            r, c = divmod(i, self._columns)
            self._grid.addWidget(w, r, c)
        self.setUpdatesEnabled(True)

    def _append_page(self) -> None:
        if self._loading:
            return
        start, end = self._loaded_count, min(
            self._loaded_count + PAGE_SIZE, len(self._display_rows)
        )
        if start >= end:
            self._loading_label.setVisible(False)
            return

        self._loading = True
        self._loading_label.setVisible(True)

        for i in range(start, end):
            kind, data = self._display_rows[i]
            if kind == "sub":
                gallery: Gallery = data
                tile: _ThumbTile = _SubGalleryTile(
                    gallery, gallery.name or Path(gallery.path).name, self._tile_size, self._base
                )
                preview = _first_preview_image(gallery)
                if preview:
                    self._tile_by_source[preview] = tile
                    global_thumb_pool().start(
                        ThumbTask(preview, self._cache, self._thumb_signals)
                    )
                elif gallery.sub_galleries:
                    tile._thumb.setText("相册")  # noqa: SLF001
                else:
                    tile._thumb.setText("空")  # noqa: SLF001
                tile.clicked.connect(partial(self._on_tile_clicked, tile))
                tile.double_clicked.connect(partial(self._enter_sub, gallery))
                tile.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
                tile.customContextMenuRequested.connect(
                    partial(self._on_sub_menu, tile, gallery)
                )
            else:
                if isinstance(data, DisplayImage):
                    disp = data
                else:
                    p = str(data)
                    disp = DisplayImage(path=p, display_name=Path(p).name)
                caption = disp.display_name
                if disp.source_folder:
                    caption = f"{disp.display_name}\n[{disp.source_folder}]"
                tile = _Tile(caption, self._tile_size, self._base)
                self._tile_by_source[disp.path] = tile
                global_thumb_pool().start(
                    ThumbTask(disp.path, self._cache, self._thumb_signals)
                )
                tile.clicked.connect(partial(self._on_tile_clicked, tile))
                tile.double_clicked.connect(partial(self._open_preview, disp.path))
                tile.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
                tile.customContextMenuRequested.connect(
                    partial(self._on_img_menu, tile, disp.path)
                )
            self._tiles.append(tile)
            r, c = divmod(len(self._tiles) - 1, self._columns)
            self._grid.addWidget(tile, r, c)

        self._loaded_count = end
        self._loading = False
        self._loading_label.setVisible(self._loaded_count < len(self._display_rows))

    def _on_scroll(self, value: int) -> None:
        vbar = self._scroll.verticalScrollBar()
        if vbar.maximum() - value <= SCROLL_BOTTOM_THRESHOLD:
            if self._loaded_count < len(self._display_rows):
                self._append_page()

    def resizeEvent(self, event) -> None:  # noqa: ANN001
        super().resizeEvent(event)
        new_cols = self._calc_columns()
        if new_cols != self._columns and self._tiles:
            self._columns = new_cols
            self._update_pixmap_cache_size()
            self._relayout_grid()

    def _on_sub_menu(self, tile: _ThumbTile, gallery: Gallery, pos) -> None:  # noqa: ANN001
        self._folder_menu(tile.mapToGlobal(pos), gallery)

    def _on_img_menu(self, tile: _Tile, path: str, pos) -> None:  # noqa: ANN001
        self._image_menu(tile.mapToGlobal(pos), path)

    def _on_thumb_ready(self, src: str, thumb: str) -> None:
        tile = self._tile_by_source.get(src)
        if tile:
            pix = QPixmap(thumb)
            if not pix.isNull():
                self._pixmap_cache.put(src, pix)
                tile.set_pixmap(pix)

    def _on_thumb_error(self, src: str, _msg: str) -> None:
        tile = self._tile_by_source.get(src)
        if tile:
            tile._thumb.setText("!")  # noqa: SLF001

    def _open_preview(self, path: str) -> None:
        paths = self._current_image_paths()
        if path not in paths:
            paths = [path] + paths
        idx = paths.index(path) if path in paths else 0
        ImagePreviewDialog(paths, idx, self).exec()

    def _reveal(self, path: str) -> None:
        ok, err = reveal_in_file_manager(path)
        if not ok:
            QMessageBox.warning(self, "无法打开", err)

    def _show_image_tags(self, path: str) -> None:
        enabled = get_enabled_models()
        if not enabled:
            QMessageBox.warning(self, "提示", "当前未启用任何分类模型，无法查看标签。")
            return

        model_id = enabled[0]["model_id"]
        model_mgr = _get_model_mgr()
        classify_cache = ClassifyCache()
        classify_cache.load()

        current_category = None
        entry = classify_cache.get(path)
        if entry:
            current_category = entry.get("category")

        from ui.image_tags_dialog import ImageTagsDialog
        dlg = ImageTagsDialog(
            image_path=path,
            model_id=model_id,
            model_manager=model_mgr,
            current_category=current_category,
            parent=self,
        )
        dlg.exec()

        cache_limit = QSettings("ImageGallery", "ImageGallery").value(
            "classify/tag_cache_limit", 3, type=int
        )
        model_mgr.evict_oldest_tag_cache(cache_limit)

    def _remove_path_from_source_tree(self, path: str) -> None:
        if not self._tree_root:
            return

        def walk(g: Gallery) -> None:
            if path in g.images:
                g.images = [x for x in g.images if x != path]
            for sub in g.sub_galleries:
                walk(sub)

        walk(self._tree_root)

    def _folder_menu(self, pos, gallery: Gallery) -> None:
        if len(self._selected_tiles) > 1:
            self._multi_select_menu(pos)
            return
        menu = QMenu(self)
        menu.addAction("在文件管理器中打开", lambda: self._reveal(gallery.path))
        sub_gallery_count = count_sub_galleries_recursive(gallery)
        image_count = count_images_recursive(gallery)
        menu.addAction("属性", lambda: FolderPropertiesDialog(gallery.path, sub_gallery_count, image_count, self).exec())
        menu.addAction("删除文件夹…", partial(self._delete_folder, gallery))
        menu.exec(pos)

    def _image_menu(self, pos, path: str) -> None:
        if len(self._selected_tiles) > 1:
            self._multi_select_menu(pos)
            return
        menu = QMenu(self)
        menu.addAction("预览", partial(self._open_preview, path))
        menu.addAction("默认查看器打开", lambda: os.startfile(path) if os.path.isfile(path) else None)
        menu.addAction("属性", lambda: ImagePropertiesDialog(path, self).exec())
        menu.addAction("图片标签", partial(self._show_image_tags, path))
        menu.addAction("在文件管理器中显示", lambda: self._reveal(path))
        menu.addAction("删除图片…", partial(self._delete_image, path))
        menu.exec(pos)

    def _multi_select_menu(self, pos) -> None:
        count = len(self._selected_tiles)
        menu = QMenu(self)
        menu.addAction(f"删除所选 {count} 个项目…", self._multi_delete_selected)
        menu.exec(pos)

    def _multi_delete_selected(self) -> None:
        count = len(self._selected_tiles)
        if QMessageBox.question(
            self, "确认删除",
            f"是否确认删除 {count} 个项目？\n此操作不可撤销。"
        ) != QMessageBox.StandardButton.Yes:
            return
        paths = self._get_selected_paths()
        galleries = self._get_selected_galleries()
        errors: List[str] = []
        for p in paths:
            if not os.path.isfile(p):
                errors.append(f"文件不存在: {p}")
                continue
            try:
                Path(p).unlink()
            except OSError as exc:
                errors.append(f"{p}: {exc}")
        for g in galleries:
            if not os.path.isdir(g.path):
                errors.append(f"文件夹不存在: {g.path}")
                continue
            try:
                shutil.rmtree(g.path)
            except OSError as exc:
                errors.append(f"{g.path}: {exc}")
        for p in paths:
            self._remove_path_from_source_tree(p)
        for g in galleries:
            parent = self._current()
            parent.sub_galleries = [x for x in parent.sub_galleries if x.path != g.path]
            if self._tree_root:
                def walk(gal: Gallery) -> None:
                    gal.sub_galleries = [x for x in gal.sub_galleries if x.path != g.path]
                    for sub in gal.sub_galleries:
                        walk(sub)
                walk(self._tree_root)
            if g in self._nav_stack:
                while self._nav_stack and self._nav_stack[-1].path != g.path:
                    self._nav_stack.pop()
                if self._nav_stack and self._nav_stack[-1].path == g.path:
                    self._nav_stack.pop()
        self._clear_selection()
        self._refresh_view()
        if errors:
            QMessageBox.warning(self, "部分失败", "\n".join(errors[:5]))

    def _delete_image(self, path: str) -> None:
        if (
            QMessageBox.question(self, "确认", f"删除文件？\n{path}")
            != QMessageBox.StandardButton.Yes
        ):
            return
        try:
            Path(path).unlink()
        except OSError as exc:
            QMessageBox.warning(self, "失败", str(exc))
            return
        self._remove_path_from_source_tree(path)

        vbar = self._scroll.verticalScrollBar()
        old_pos = vbar.value()

        idx = None
        for i, (kind, data) in enumerate(self._display_rows):
            if kind == "img":
                img_path = data.path if isinstance(data, DisplayImage) else str(data)
                if img_path == path:
                    idx = i
                    break

        if idx is not None and idx < len(self._tiles):
            self._display_rows.pop(idx)
            tile = self._tiles.pop(idx)
            keys_to_remove = [k for k, v in self._tile_by_source.items() if v is tile]
            for k in keys_to_remove:
                del self._tile_by_source[k]
            self._grid.removeWidget(tile)
            tile.deleteLater()
            self._loaded_count = min(self._loaded_count, len(self._display_rows))
            self._relayout_grid()
            if self._loaded_count < len(self._display_rows):
                self._append_page()

        vbar.setValue(old_pos)

    def _delete_folder(self, gallery: Gallery) -> None:
        if (
            QMessageBox.question(self, "确认", f"删除整个文件夹？\n{gallery.path}")
            != QMessageBox.StandardButton.Yes
        ):
            return
        try:
            shutil.rmtree(gallery.path)
        except OSError as exc:
            QMessageBox.warning(self, "失败", str(exc))
            return
        parent = self._current()
        parent.sub_galleries = [g for g in parent.sub_galleries if g.path != gallery.path]
        if self._tree_root:
            def walk(g: Gallery) -> None:
                g.sub_galleries = [x for x in g.sub_galleries if x.path != gallery.path]
                for sub in g.sub_galleries:
                    walk(sub)

            walk(self._tree_root)
        if gallery in self._nav_stack:
            while self._nav_stack and self._nav_stack[-1].path != gallery.path:
                self._nav_stack.pop()
            if self._nav_stack and self._nav_stack[-1].path == gallery.path:
                self._nav_stack.pop()
        self._refresh_view()
