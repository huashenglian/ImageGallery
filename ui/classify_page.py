from __future__ import annotations

import time

from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from services.classify_cache import ClassifyCache
from services.thumbnail_cache import ThumbnailCache
from ui.theme import (
    build_tile_qss,
    DEFAULT_BASE,
    SVG_HOME,
    svg_icon,
    _surface,
    _border,
    _hover,
    _accent,
    _text,
    _text_dim,
)
from workers.thumb_worker import ThumbSignals, ThumbTask, global_thumb_pool

CATEGORY_ICONS = {
    "人物": "👤",
    "女性": "👩",
    "男性": "👨",
    "多人": "👥",
    "风景": "🏔",
    "动物": "🐱",
    "食物": "🍔",
    "建筑": "🏛",
    "车辆": "🚗",
    "截图/文字": "📄",
    "文档/文字": "📄",
    "其他": "📦",
}

_ThumbTile = QWidget


class _CategoryCard(QFrame):
    clicked = Signal()

    def __init__(self, category: str, count: int, base: str = DEFAULT_BASE, parent=None):
        super().__init__(parent)
        self.setObjectName("categoryCard")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(build_tile_qss(base, "categoryCard"))
        self.setFixedSize(140, 120)
        self._count = count

        icon = QLabel(CATEGORY_ICONS.get(category, "📦"))
        icon.setStyleSheet("font-size:36px;")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)

        name = QLabel(category)
        name.setStyleSheet(f"font-size:14px;font-weight:bold;color:{_text(base)};")
        name.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._cnt_label = QLabel(f"{count} 张")
        self._cnt_label.setStyleSheet(f"font-size:11px;color:{_text_dim(base)};")
        self._cnt_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 12, 8, 8)
        lay.addWidget(icon)
        lay.addWidget(name)
        lay.addWidget(self._cnt_label)

    def increment_count(self) -> None:
        self._count += 1
        self._cnt_label.setText(f"{self._count} 张")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class ClassifyPage(QWidget):
    start_classify_requested = Signal()
    reclassify_requested = Signal()
    pause_classify_requested = Signal()
    resume_classify_requested = Signal()
    category_clicked = Signal(str)
    clear_classify_cache_requested = Signal()
    home_requested = Signal()

    def __init__(self, thumb_cache: ThumbnailCache, base: str = DEFAULT_BASE, parent=None):
        super().__init__(parent)
        self._base = base
        self._cache = thumb_cache
        self._thumb_signals = ThumbSignals(self)
        self._thumb_signals.ready.connect(self._on_thumb_ready)
        self._thumb_signals.error.connect(self._on_thumb_error)
        self._tile_by_source: dict[str, _ThumbTile] = {}
        self._category_cards: dict[str, _CategoryCard] = {}

        self._back_btn = QPushButton("返回")
        self._back_btn.clicked.connect(self.home_requested.emit)

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

        self._title = QLabel("自动分类")
        self._title.setStyleSheet(f"font-size:16px;font-weight:bold;color:{_text(self._base)};")

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._content = QWidget()
        self._content_lay = QVBoxLayout(self._content)
        self._content_lay.setContentsMargins(0, 0, 0, 0)

        self._empty_widget = QWidget()
        empty_lay = QVBoxLayout(self._empty_widget)
        empty_lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_label = QLabel("📂")
        icon_label.setStyleSheet("font-size:48px;")
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_lay.addWidget(icon_label)
        empty_title = QLabel("暂无分类结果")
        empty_title.setStyleSheet(f"font-size:18px;font-weight:bold;color:{_text(self._base)};")
        empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_lay.addWidget(empty_title)
        self._start_btn = QPushButton("开始自动分类")
        self._start_btn.clicked.connect(self.start_classify_requested.emit)
        empty_lay.addWidget(self._start_btn, alignment=Qt.AlignmentFlag.AlignCenter)
        desc = QLabel("将对所有已缓存图库的图片进行内容识别分类")
        desc.setStyleSheet(f"color:{_text_dim(self._base)};font-size:12px;")
        desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_lay.addWidget(desc)

        self._results_widget = QWidget()
        self._results_lay = QVBoxLayout(self._results_widget)
        self._results_lay.setContentsMargins(12, 12, 12, 12)

        self._progress = QProgressBar()
        self._progress.setVisible(False)
        self._results_lay.addWidget(self._progress)

        self._stats_label = QLabel("")
        self._stats_label.setStyleSheet(f"color:{_text_dim(self._base)};font-size:12px;")
        self._stats_label.setVisible(False)
        self._results_lay.addWidget(self._stats_label)

        self._current_file_label = QLabel("")
        self._current_file_label.setWordWrap(True)
        self._current_file_label.setStyleSheet(f"color:{_text_dim(self._base)};font-size:11px;")
        self._current_file_label.setVisible(False)
        self._results_lay.addWidget(self._current_file_label)

        ctrl_row = QHBoxLayout()
        self._pause_btn = QPushButton("暂停分类")
        self._pause_btn.clicked.connect(self._on_pause_clicked)
        self._pause_btn.setVisible(False)
        self._reclassify_btn = QPushButton("重新分类")
        self._reclassify_btn.clicked.connect(self.reclassify_requested.emit)
        self._reclassify_btn.setVisible(False)
        self._clear_cache_btn = QPushButton("清除分类缓存")
        self._clear_cache_btn.clicked.connect(self.clear_classify_cache_requested.emit)
        self._clear_cache_btn.setVisible(False)
        ctrl_row.addStretch()
        ctrl_row.addWidget(self._pause_btn)
        ctrl_row.addWidget(self._reclassify_btn)
        ctrl_row.addWidget(self._clear_cache_btn)
        ctrl_row.addStretch()
        self._results_lay.addLayout(ctrl_row)

        self._grid_container = QWidget()
        self._grid = QGridLayout(self._grid_container)
        self._grid.setSpacing(12)
        self._results_lay.addWidget(self._grid_container)

        self._info_label = QLabel("")
        self._info_label.setStyleSheet(f"color:{_text_dim(self._base)};font-size:11px;padding:8px;")
        self._results_lay.addWidget(self._info_label)

        self._results_lay.addStretch()

        self._scroll.setWidget(self._content)

        top = QHBoxLayout()
        top.addWidget(self._back_btn)
        top.addWidget(self._home_icon_btn)
        top.addWidget(self._title, 1)

        root = QVBoxLayout(self)
        root.addLayout(top)
        root.addWidget(self._scroll, 1)

        self._show_empty()

    def _show_empty(self):
        while self._content_lay.count():
            item = self._content_lay.takeAt(0)
            if item.widget():
                item.widget().setParent(None)
        self._content_lay.addWidget(self._empty_widget)

    def _show_results(self):
        while self._content_lay.count():
            item = self._content_lay.takeAt(0)
            if item.widget():
                item.widget().setParent(None)
        self._content_lay.addWidget(self._results_widget)

    def set_base(self, base: str):
        self._base = base
        self._title.setStyleSheet(f"font-size:16px;font-weight:bold;color:{_text(base)};")
        self._home_icon_btn.setIcon(svg_icon(SVG_HOME.format(color=_accent(base)), 20))
        self._home_icon_btn.setStyleSheet(
            f"QPushButton{{background:transparent;border:none;border-radius:6px;}}"
            f"QPushButton:hover{{background:{_accent(base)};border-radius:6px;}}"
        )

    def _set_buttons_for_classifying(self):
        self._progress.setVisible(True)
        self._stats_label.setVisible(True)
        self._current_file_label.setVisible(True)
        self._pause_btn.setVisible(True)
        self._pause_btn.setText("暂停分类")
        self._reclassify_btn.setVisible(False)
        self._clear_cache_btn.setVisible(False)

    def _set_buttons_for_paused(self):
        self._progress.setVisible(True)
        self._stats_label.setVisible(True)
        self._current_file_label.setVisible(False)
        self._pause_btn.setVisible(True)
        self._pause_btn.setText("继续分类")
        self._reclassify_btn.setVisible(True)
        self._clear_cache_btn.setVisible(True)

    def _set_buttons_for_completed(self):
        self._progress.setVisible(False)
        self._stats_label.setVisible(False)
        self._current_file_label.setVisible(False)
        self._pause_btn.setVisible(False)
        self._reclassify_btn.setVisible(True)
        self._clear_cache_btn.setVisible(True)

    def show_empty_state(self):
        self._show_empty()

    def show_classifying(self, classify_cache: ClassifyCache | None = None, preset_name: str | None = None):
        self._set_buttons_for_classifying()
        self._progress.setRange(0, 0)
        self._stats_label.setText("准备分类…")
        self._current_file_label.setText("")
        if classify_cache and classify_cache.total_classified > 0:
            self._build_grid_nonzero(classify_cache)
            self._info_label.setText(f"分类进行中  ·  已分类: {classify_cache.total_classified} 张")
        else:
            self._clear_grid()
            self._info_label.setText("分类进行中")
        self._show_results()

    def show_classifying_state(self, classify_cache: ClassifyCache, preset_name: str | None = None):
        self._set_buttons_for_classifying()
        if classify_cache.total_classified > 0:
            self._build_grid_nonzero(classify_cache)
        else:
            self._clear_grid()
        self._info_label.setText(f"分类进行中  ·  已分类: {classify_cache.total_classified} 张")
        self._show_results()

    def show_paused_state(self, classify_cache: ClassifyCache, preset_name: str | None = None):
        self._set_buttons_for_paused()
        if classify_cache.total_classified > 0:
            self._build_grid_nonzero(classify_cache)
        else:
            self._clear_grid()
        cur = classify_cache.progress_current
        tot = classify_cache.progress_total
        if tot > 0:
            pct = int(cur / tot * 100)
            self._progress.setRange(0, tot)
            self._progress.setValue(min(cur, tot))
            self._stats_label.setText(f"已处理: {cur} / {tot} 张 ({pct}%)")
        else:
            self._progress.setVisible(False)
            self._stats_label.setVisible(False)
        self._info_label.setText(
            f"分类已暂停  ·  已分类: {classify_cache.total_classified} 张"
            + (f"  ·  已跳过: {classify_cache.skipped} 张" if classify_cache.skipped else "")
        )
        self._show_results()

    def update_progress(self, current: int, total: int, current_file: str):
        if total > 0:
            self._progress.setRange(0, total)
            self._progress.setValue(min(current, total))
            pct = int(current / total * 100)
            self._stats_label.setText(f"已处理: {current} / {total} 张 ({pct}%)")
        else:
            self._progress.setRange(0, 0)
            self._stats_label.setText(f"已处理: {current} 张")
        self._current_file_label.setText(current_file)
        self._info_label.setText(f"分类进行中  ·  已分类: {current} 张")

    def show_paused(self):
        self._set_buttons_for_paused()

    def show_resumed(self):
        self._set_buttons_for_classifying()

    def show_results(self, classify_cache: ClassifyCache):
        self._set_buttons_for_completed()
        self._build_grid_nonzero(classify_cache)
        ts = classify_cache.classified_at
        time_str = time.strftime("%m-%d %H:%M", time.localtime(ts)) if ts else "未知"
        self._info_label.setText(
            f"最后分类: {time_str}  ·  总分类图片: {classify_cache.total_classified} 张"
            + (f"  ·  已跳过: {classify_cache.skipped} 张" if classify_cache.skipped else "")
        )
        self._show_results()

    def _clear_grid(self):
        self._category_cards.clear()
        while self._grid.count():
            item = self._grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _build_grid_nonzero(self, classify_cache: ClassifyCache, preset_name: str | None = None):
        self._category_cards.clear()
        while self._grid.count():
            item = self._grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        counts = classify_cache.get_category_counts()
        nonzero = {cat: cnt for cat, cnt in counts.items() if cnt > 0}
        cols = 4
        for i, (cat, cnt) in enumerate(sorted(nonzero.items(), key=lambda x: -x[1])):
            card = _CategoryCard(cat, cnt, self._base)
            card.clicked.connect(lambda c=cat: self.category_clicked.emit(c))
            r, c_idx = divmod(i, cols)
            self._grid.addWidget(card, r, c_idx)
            self._category_cards[cat] = card

    def increment_category(self, category: str) -> None:
        card = self._category_cards.get(category)
        if card:
            card.increment_count()
        else:
            count = len(self._category_cards)
            card = _CategoryCard(category, 1, self._base)
            card.clicked.connect(lambda c=category: self.category_clicked.emit(c))
            cols = 4
            r, c_idx = divmod(count, cols)
            self._grid.addWidget(card, r, c_idx)
            self._category_cards[category] = card

    def _on_pause_clicked(self):
        if self._pause_btn.text() == "暂停分类":
            self.pause_classify_requested.emit()
        else:
            self.resume_classify_requested.emit()

    def _on_thumb_ready(self, src, thumb):
        tile = self._tile_by_source.get(src)
        if tile:
            pix = QPixmap(thumb)
            if not pix.isNull():
                tile.set_pixmap(pix)

    def _on_thumb_error(self, src, msg):
        tile = self._tile_by_source.get(src)
        if tile:
            tile._thumb.setText("!")
