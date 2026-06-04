from __future__ import annotations

import sys

_W = 480
_H = 240
_CLR_BG = 0x002E1A1A
_CLR_TEXT = 0x00E0E0E0
_CLR_ACCENT = 0x006045E9
_CLR_DIM = 0x00999999

_WS_POPUP = 0x80000000
_WS_VISIBLE = 0x10000000
_SS_BITMAP = 0x0E
_WS_EX_TOPMOST = 0x00000008
_WS_EX_TOOLWINDOW = 0x00000080
_STM_SETIMAGE = 0x0172
_IMAGE_BITMAP = 0
_DT_CENTER = 0x01
_DT_VCENTER = 0x04
_DT_SINGLELINE = 0x20
_PM_REMOVE = 0x0001


def _setup_restypes(user32, gdi32):
    import ctypes
    from ctypes import wintypes

    user32.GetDC.restype = wintypes.HDC
    user32.ReleaseDC.restype = ctypes.c_int
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.SendMessageW.restype = ctypes.c_void_p
    user32.PeekMessageW.restype = wintypes.BOOL
    user32.UpdateWindow.restype = wintypes.BOOL
    user32.SetWindowRgn.restype = ctypes.c_int
    user32.GetSystemMetrics.restype = ctypes.c_int
    user32.DestroyWindow.restype = wintypes.BOOL

    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.DeleteObject.restype = wintypes.BOOL
    gdi32.DeleteDC.restype = wintypes.BOOL
    gdi32.CreateSolidBrush.restype = wintypes.HBRUSH
    gdi32.CreatePen.restype = wintypes.HPEN
    gdi32.CreateFontW.restype = wintypes.HFONT
    gdi32.CreateRoundRectRgn.restype = wintypes.HRGN
    gdi32.SetBkMode.restype = ctypes.c_int
    gdi32.SetTextColor.restype = wintypes.COLORREF
    gdi32.MoveToEx.restype = wintypes.BOOL
    gdi32.LineTo.restype = wintypes.BOOL


def show_splash():
    if sys.platform != "win32":
        return None

    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32

    _setup_restypes(user32, gdi32)

    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", wintypes.LONG),
            ("top", wintypes.LONG),
            ("right", wintypes.LONG),
            ("bottom", wintypes.LONG),
        ]

    sw = user32.GetSystemMetrics(0)
    sh = user32.GetSystemMetrics(1)
    x = (sw - _W) // 2
    y = (sh - _H) // 2

    hdc_desk = user32.GetDC(None)
    if not hdc_desk:
        return None

    hbmp = None
    hdc_mem = None
    hbmp_old = None
    _gdi_objs = []

    try:
        hdc_mem = gdi32.CreateCompatibleDC(hdc_desk)
        if not hdc_mem:
            user32.ReleaseDC(None, hdc_desk)
            return None

        hbmp = gdi32.CreateCompatibleBitmap(hdc_desk, _W, _H)
        if not hbmp:
            gdi32.DeleteDC(hdc_mem)
            user32.ReleaseDC(None, hdc_desk)
            return None

        hbmp_old = gdi32.SelectObject(hdc_mem, hbmp)

        hbr = gdi32.CreateSolidBrush(_CLR_BG)
        _gdi_objs.append(hbr)
        rc = RECT(0, 0, _W, _H)
        user32.FillRect(hdc_mem, ctypes.byref(rc), hbr)

        hpen = gdi32.CreatePen(0, 3, _CLR_ACCENT)
        _gdi_objs.append(hpen)
        hpen_old = gdi32.SelectObject(hdc_mem, hpen)
        gdi32.MoveToEx(hdc_mem, 0, 1, None)
        gdi32.LineTo(hdc_mem, _W, 1)
        gdi32.SelectObject(hdc_mem, hpen_old)

        hf1 = gdi32.CreateFontW(-37, 0, 0, 0, 700, 0, 0, 0, 1, 0, 0, 5, 0, "Segoe UI")
        if hf1:
            _gdi_objs.append(hf1)
            hf1_old = gdi32.SelectObject(hdc_mem, hf1)
            user32.SetBkMode(hdc_mem, 1)
            user32.SetTextColor(hdc_mem, _CLR_TEXT)
            r1 = RECT(0, 60, _W, 108)
            user32.DrawTextW(hdc_mem, "ImageGallery", -1, ctypes.byref(r1),
                             _DT_CENTER | _DT_VCENTER | _DT_SINGLELINE)
            gdi32.SelectObject(hdc_mem, hf1_old)

        hpen2 = gdi32.CreatePen(0, 2, _CLR_ACCENT)
        _gdi_objs.append(hpen2)
        hpen2_old = gdi32.SelectObject(hdc_mem, hpen2)
        gdi32.MoveToEx(hdc_mem, 170, 116, None)
        gdi32.LineTo(hdc_mem, 310, 116)
        gdi32.SelectObject(hdc_mem, hpen2_old)

        hf2 = gdi32.CreateFontW(-16, 0, 0, 0, 400, 0, 0, 0, 1, 0, 0, 5, 0, "Segoe UI")
        if hf2:
            _gdi_objs.append(hf2)
            hf2_old = gdi32.SelectObject(hdc_mem, hf2)
            user32.SetTextColor(hdc_mem, _CLR_DIM)
            try:
                from app_meta import APP_VERSION
                ver = f"v{APP_VERSION}  \u52a0\u8f7d\u4e2d\u2026"
            except ImportError:
                ver = "\u52a0\u8f7d\u4e2d\u2026"
            r2 = RECT(0, 128, _W, 158)
            user32.DrawTextW(hdc_mem, ver, -1, ctypes.byref(r2),
                             _DT_CENTER | _DT_VCENTER | _DT_SINGLELINE)
            gdi32.SelectObject(hdc_mem, hf2_old)

        for obj in _gdi_objs:
            gdi32.DeleteObject(obj)
        _gdi_objs.clear()

        gdi32.SelectObject(hdc_mem, hbmp_old)
        hbmp_old = None
        gdi32.DeleteDC(hdc_mem)
        hdc_mem = None
        user32.ReleaseDC(None, hdc_desk)
        hdc_desk = None

        hwnd = user32.CreateWindowExW(
            _WS_EX_TOPMOST | _WS_EX_TOOLWINDOW,
            "STATIC", "",
            _WS_POPUP | _WS_VISIBLE | _SS_BITMAP,
            x, y, _W, _H,
            0, 0, None, 0,
        )
        if not hwnd:
            gdi32.DeleteObject(hbmp)
            return None

        prev_bmp = user32.SendMessageW(hwnd, _STM_SETIMAGE, _IMAGE_BITMAP, hbmp)
        if prev_bmp:
            gdi32.DeleteObject(prev_bmp)

        hrgn = gdi32.CreateRoundRectRgn(0, 0, _W + 1, _H + 1, 16, 16)
        if hrgn:
            user32.SetWindowRgn(hwnd, hrgn, True)

        msg = wintypes.MSG()
        for _ in range(20):
            if not user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, _PM_REMOVE):
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        user32.UpdateWindow(hwnd)

        return (hwnd, hbmp)

    except Exception:
        for obj in _gdi_objs:
            try:
                gdi32.DeleteObject(obj)
            except Exception:
                pass
        if hdc_mem:
            if hbmp_old is not None:
                try:
                    gdi32.SelectObject(hdc_mem, hbmp_old)
                except Exception:
                    pass
            try:
                gdi32.DeleteDC(hdc_mem)
            except Exception:
                pass
        if hbmp:
            try:
                gdi32.DeleteObject(hbmp)
            except Exception:
                pass
        if hdc_desk:
            try:
                user32.ReleaseDC(None, hdc_desk)
            except Exception:
                pass
        return None


def close_splash(handle):
    if handle is None:
        return
    if sys.platform != "win32":
        return

    import ctypes

    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32

    try:
        hwnd, hbmp = handle
        user32.DestroyWindow(hwnd)
        gdi32.DeleteObject(hbmp)
    except Exception:
        pass
