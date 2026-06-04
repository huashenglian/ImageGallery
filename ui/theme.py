from __future__ import annotations

from PySide6.QtGui import QIcon, QPixmap, QPainter, QColor
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtCore import QSize, QByteArray

DEFAULT_BASE = "#1a1a2e"

SVG_FOLDER = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="{color}">'
    '<path d="M10 4H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V8'
    'c0-1.1-.9-2-2-2h-8l-2-2z"/></svg>'
)
SVG_IMAGE = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="{color}">'
    '<path d="M21 19V5c0-1.1-.9-2-2-2H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14'
    'c1.1 0 2-.9 2-2zM8.5 13.5l2.5 3.01L14.5 12l4.5 6H5l3.5-4.5z"/></svg>'
)
SVG_BACK = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="{color}">'
    '<path d="M20 11H7.83l5.59-5.59L12 4l-8 8 8 8 1.41-1.41L7.83 13H20v-2z"/></svg>'
)
SVG_SEARCH = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="{color}">'
    '<path d="M15.5 14h-.79l-.28-.27A6.47 6.47 0 0016 9.5 6.5 6.5 0 109.5 16'
    'c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01'
    ' 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z"/></svg>'
)
SVG_SETTINGS = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="{color}">'
    '<path d="M19.14 12.94c.04-.3.06-.61.06-.94 0-.32-.02-.64-.07-.94l2.03-1.58'
    'a.49.49 0 00.12-.61l-1.92-3.32a.49.49 0 00-.59-.22l-2.39.96c-.5-.38-1.03-'
    '.7-1.62-.94l-.36-2.54a.484.484 0 00-.48-.41h-3.84c-.24 0-.43.17-.47.41l-'
    '.36 2.54c-.59.24-1.13.57-1.62.94l-2.39-.96a.49.49 0 00-.59.22L2.74 8.87c'
    '-.12.21-.08.47.12.61l2.03 1.58c-.05.3-.07.62-.07.94s.02.64.07.94l-2.03 1.'
    '58a.49.49 0 00-.12.61l1.92 3.32c.12.22.37.29.59.22l2.39-.96c.5.38 1.03.7 '
    '1.62.94l.36 2.54c.05.24.24.41.48.41h3.84c.24 0 .44-.17.47-.41l.36-2.54c.5'
    '9-.24 1.13-.56 1.62-.94l2.39.96c.22.08.47 0 .59-.22l1.92-3.32c.12-.22.07-'
    '.47-.12-.61l-2.01-1.58zM12 15.6A3.6 3.6 0 1115.6 12 3.6 3.6 0 0112 15.6z"/></svg>'
)
SVG_HOME = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="{color}">'
    '<path d="M10 20v-6h4v6h5v-8h3L12 3 2 12h3v8z"/></svg>'
)


def svg_icon(svg_str: str, size: int = 16) -> QIcon:
    renderer = QSvgRenderer(QByteArray(svg_str.encode("utf-8")))
    pix = QPixmap(QSize(size, size))
    pix.fill()
    painter = QPainter(pix)
    renderer.render(painter)
    painter.end()
    return QIcon(pix)


def _is_light(base: str) -> bool:
    c = QColor(base)
    return (0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()) >= 128


def _text(base: str) -> str:
    return "#1a1a1a" if _is_light(base) else "#e0e0e0"


def _text_dim(base: str) -> str:
    return "#666666" if _is_light(base) else "#999999"


def _surface(base: str) -> str:
    c = QColor(base)
    h, s, v, a = c.getHsvF()
    if _is_light(base):
        v = max(v - 0.10, 0.0)
    else:
        v = min(v + 0.10, 1.0)
    s = max(s - 0.05, 0.0)
    return QColor.fromHsvF(h, s, v, a).name()


def _border(base: str) -> str:
    c = QColor(base)
    h, s, v, a = c.getHsvF()
    if _is_light(base):
        v = max(v - 0.22, 0.0)
    else:
        v = min(v + 0.22, 1.0)
    s = max(s - 0.10, 0.0)
    return QColor.fromHsvF(h, s, v, a).name()


def _hover(base: str) -> str:
    c = QColor(base)
    h, s, v, a = c.getHsvF()
    if _is_light(base):
        v = max(v - 0.05, 0.0)
    else:
        v = min(v + 0.05, 1.0)
    return QColor.fromHsvF(h, s, v, a).name()


def _accent(base: str) -> str:
    c = QColor(base)
    h, s, v, a = c.getHsvF()
    if s < 0.10:
        h = 0.60
        s = 0.70
    else:
        s = max(s, 0.65)
    v = 0.55 if _is_light(base) else 0.85
    return QColor.fromHsvF(h % 1.0, min(s, 1.0), min(v, 1.0), a).name()


def _accent_dark(base: str) -> str:
    c = QColor(_accent(base))
    h, s, v, a = c.getHsvF()
    v = max(v - 0.15, 0.0)
    return QColor.fromHsvF(h, s, v, a).name()


def build_qss(base: str = DEFAULT_BASE) -> str:
    bg = base
    surf = _surface(base)
    bdr = _border(base)
    hov = _hover(base)
    acc = _accent(base)
    acc_dk = _accent_dark(base)
    txt = _text(base)
    txt_dim = _text_dim(base)
    dis_bg = "#cccccc" if _is_light(base) else "#2a2a4a"
    dis_fg = "#999999" if _is_light(base) else "#666666"
    return f"""
QMainWindow{{background:{bg};color:{txt};font-size:13px;}}
QWidget{{background:{bg};color:{txt};font-size:13px;}}
QFrame#cacheCard{{background:{surf};border:1px solid {bdr};border-radius:8px;}}
QFrame#cacheCard:hover{{background:{hov};}}
QMenuBar{{background:{surf};color:{txt};border-bottom:1px solid {bdr};}}
QMenuBar::item:selected{{background:{acc};border-radius:4px;}}
QMenu{{background:{surf};color:{txt};border:1px solid {bdr};}}
QMenu::item:selected{{background:{acc};}}
QPushButton{{background:{bdr};color:{txt};border:none;border-radius:8px;
  padding:6px 16px;font-size:13px;}}
QPushButton:hover{{background:{acc};}}
QPushButton:pressed{{background:{acc_dk};}}
QPushButton:disabled{{background:{dis_bg};color:{dis_fg};}}
QLineEdit{{background:{surf};color:{txt};border:1px solid {bdr};
  border-radius:6px;padding:6px;}}
QLineEdit:focus{{border-color:{acc};}}
QComboBox{{background:{surf};color:{txt};border:1px solid {bdr};
  border-radius:6px;padding:4px 8px;}}
QComboBox::drop-down{{border:none;}}
QComboBox QAbstractItemView{{background:{surf};color:{txt};
  selection-background-color:{acc};}}
QSpinBox{{background:{surf};color:{txt};border:1px solid {bdr};
  border-radius:6px;padding:4px;}}
QProgressBar{{background:{surf};border-radius:4px;text-align:center;color:{txt};}}
QProgressBar::chunk{{background:{acc};border-radius:4px;}}
QScrollArea{{border:none;background:{bg};}}
QScrollBar:vertical{{background:{bg};width:8px;border-radius:4px;}}
QScrollBar::handle:vertical{{background:{bdr};border-radius:4px;min-height:30px;}}
QScrollBar::handle:vertical:hover{{background:{acc};}}
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{{height:0;}}
QScrollBar:horizontal{{height:0;}}
QStatusBar{{background:{surf};color:{txt_dim};border-top:1px solid {bdr};}}
QMainWindow QLabel{{background:transparent;}}
QGroupBox{{border:1px solid {bdr};border-radius:8px;margin-top:8px;
  padding-top:16px;color:{txt};}}
QGroupBox::title{{subcontrol-origin:margin;left:10px;padding:0 4px;}}
QDialogButtonBox QPushButton{{min-width:80px;}}
QMessageBox{{background:{bg};}}
QInputDialog{{background:{bg};}}
QDialog{{background:{bg};}}
"""


def build_tile_qss(base: str = DEFAULT_BASE, object_id: str = "tile") -> str:
    surf = _surface(base)
    bdr = _border(base)
    hov = _hover(base)
    acc = _accent(base)
    return f"""
#{object_id} {{background:{surf};border-radius:12px;border:1px solid {bdr};}}
#{object_id}:hover {{border-color:{acc};background:{hov};}}
#{object_id}[selected="true"] {{border:2px solid #4a90d9;}}
#{object_id}[selected="true"]:hover {{border:2px solid #4a90d9;background:{hov};}}
"""


def build_breadcrumb_qss(base: str = DEFAULT_BASE) -> str:
    surf = _surface(base)
    bdr = _border(base)
    acc = _accent(base)
    return f"""
QWidget{{background:{surf};border:1px solid {bdr};border-radius:4px;}}
QPushButton{{border:none;padding:2px 4px;font-size:12px;color:{acc};
  background:transparent;}}
QPushButton:hover{{text-decoration:underline;}}
"""
