"""High-DPI support.

DearPyGui 2.x has no DPI handling of its own: it creates a DPI-unaware
window, so on a 200% display Windows bitmap-stretches the result and every
glyph goes soft. The fix has three parts, and all three are needed --
doing only the first gives a crisp but half-size UI:

1. Declare the process **per-monitor DPI aware** before any window exists,
   so Windows hands us real pixels instead of stretching them.
2. Scale every dimension we ask for -- window size, paddings, widget
   widths -- by the monitor's scale factor.
3. Rasterize the font at ``base_size * scale`` rather than scaling a
   96-DPI bitmap font up, which is what keeps text sharp.

:func:`enable` must run before ``dpg.create_viewport``.
"""
from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes

_PER_MONITOR_AWARE_V2 = -4
_PER_MONITOR_AWARE = -3
_SYSTEM_AWARE = -2

BASE_DPI = 96.0

MIN_SCALE, MAX_SCALE = 1.0, 4.0

BASE_FONT_SIZE = 15

SCALE_ENV_VAR = "ALIBRE_COLOR_UI_SCALE"

_FONT_CANDIDATES = (
    r"C:\Windows\Fonts\segoeui.ttf",
    r"C:\Windows\Fonts\SegoeUI.ttf",
    r"C:\Windows\Fonts\tahoma.ttf",
    r"C:\Windows\Fonts\arial.ttf",
    r"C:\Windows\Fonts\verdana.ttf",
)

def enable() -> bool:
    """Mark the process per-monitor DPI aware. True if a mode was set.

    Tries newest API first and falls back; on anything but Windows, or if
    awareness was already established (some hosts set it for us), this is a
    harmless no-op.
    """
    if sys.platform != "win32":
        return False

    user32 = ctypes.windll.user32
    setter = getattr(user32, "SetProcessDpiAwarenessContext", None)
    if setter is not None:
        setter.argtypes = [wintypes.HANDLE]
        setter.restype = wintypes.BOOL
        for context in (_PER_MONITOR_AWARE_V2, _PER_MONITOR_AWARE, _SYSTEM_AWARE):
            try:
                if setter(wintypes.HANDLE(context)):
                    return True
            except Exception:
                pass

    try:
        if ctypes.windll.shcore.SetProcessDpiAwareness(2) == 0:
            return True
    except Exception:
        pass

    try:
        return bool(ctypes.windll.user32.SetProcessDPIAware())
    except Exception:
        return False

def _monitor_dpi() -> float:
    """DPI of the monitor under the cursor, falling back to the system DPI."""
    user32 = ctypes.windll.user32

    try:
        point = wintypes.POINT()
        if user32.GetCursorPos(ctypes.byref(point)):
            monitor = user32.MonitorFromPoint(point, 2)
            dpi_x, dpi_y = ctypes.c_uint(), ctypes.c_uint()
            if ctypes.windll.shcore.GetDpiForMonitor(
                monitor, 0, ctypes.byref(dpi_x), ctypes.byref(dpi_y)
            ) == 0 and dpi_x.value:
                return float(dpi_x.value)
    except Exception:
        pass

    try:
        value = user32.GetDpiForSystem()
        if value:
            return float(value)
    except Exception:
        pass

    try:
        dc = user32.GetDC(None)
        value = ctypes.windll.gdi32.GetDeviceCaps(dc, 88)
        user32.ReleaseDC(None, dc)
        if value:
            return float(value)
    except Exception:
        pass

    return BASE_DPI

def scale_factor() -> float:
    """The UI scale to apply: 1.0 at 100%, 2.0 at 200%."""
    override = os.environ.get(SCALE_ENV_VAR, "").strip()
    if override:
        try:
            return max(MIN_SCALE, min(MAX_SCALE, float(override)))
        except ValueError:
            pass
    if sys.platform != "win32":
        return 1.0
    return max(MIN_SCALE, min(MAX_SCALE, _monitor_dpi() / BASE_DPI))

def font_path() -> str | None:
    """First readable UI font on this machine, or None to keep DPG's own."""
    for candidate in _FONT_CANDIDATES:
        if os.path.isfile(candidate):
            return candidate
    return None

class Scaler:
    """Converts design-time pixels into physical pixels for one scale factor."""

    __slots__ = ("scale",)

    def __init__(self, scale: float) -> None:
        self.scale = scale

    def __call__(self, value: float) -> int:
        """Scale a length.

        DearPyGui reads a negative width as "fill the parent, less N pixels",
        so the magnitude is a real pixel margin and scales like any other --
        except ``-1``, the bare "fill" sentinel, which must stay exactly -1.
        """
        if value in (0, -1):
            return int(value)
        magnitude = int(round(abs(value) * self.scale))
        return -magnitude if value < 0 else magnitude
