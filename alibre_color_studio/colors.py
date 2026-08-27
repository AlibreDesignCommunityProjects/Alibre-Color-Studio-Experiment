"""Color conversion between Alibre's packed integers and RGB tuples.

**AlibreX uses two opposite byte orders, depending on the surface.** This is
not documented anywhere -- the CHM describes only the part convention -- and
getting it wrong silently reverses the red and blue channels, so a bronze
renders as blue. Verified on Alibre 29.1 by writing a raw value and rendering
the result:

============================================  =================  ============
Surface                                       ``0xFF0000`` is    Layout
============================================  =================  ============
``IADPartSession.Color`` / ``EdgeColor``      blue               ``0x00BBGGRR``
``IADPartFeature.FaceColor`` / ``EdgeColor``  **red**            ``0xAARRGGBB``
``IADFace.Color``                             red (read-only)    ``0xAARRGGBB``
``IADOccurrence.Color``                       *see below*        ``0x00BBGGRR``
============================================  =================  ============

The part layout is Win32 ``COLORREF``, which the CHM corroborates twice: the
``Color`` sample builds values with VB's ``RGB(r, g, b)`` (``r + g*256 +
b*65536``) and decodes red from the low byte, and ``EdgeColor`` is described as
"a BGR int". The feature layout is plain .NET ARGB and has no sample in the
docs at all, which is exactly why following them produced reversed colors.

``IADOccurrence.Color`` is **inferred, not verified**: the CHM's occurrence
sample passes ``RGB(255, 255, 0)`` and calls it yellow, which only holds under
COLORREF. That matches the part surface, so occurrences are treated as COLORREF.
If assembly component colors ever come out channel-reversed, flip the one
entry for ``"occurrence"`` in :data:`LAYOUT_BY_KIND` and nothing else.
"""
from __future__ import annotations

RGB = tuple[int, int, int]

_CHANNELS = 0xFFFFFF

LAYOUT_BY_KIND = {
    "part": "colorref",
    "feature": "argb",
    "face": "argb",
    "occurrence": "colorref",
}

def _clamp8(value: int) -> int:
    return 0 if value < 0 else (255 if value > 255 else int(value))

def pack_colorref(rgb: RGB) -> int:
    """``(r, g, b)`` -> ``0x00BBGGRR`` (red in the low byte)."""
    r, g, b = (_clamp8(c) for c in rgb)
    return r | (g << 8) | (b << 16)

def unpack_colorref(value: int) -> RGB:
    """``0x00BBGGRR`` -> ``(r, g, b)``."""
    v = int(value) & _CHANNELS
    return (v & 0xFF, (v >> 8) & 0xFF, (v >> 16) & 0xFF)

def pack_argb(rgb: RGB) -> int:
    """``(r, g, b)`` -> ``0x00RRGGBB`` (red high).

    The alpha byte is left clear: Alibre stamps it to ``0xFF`` on write, and
    setting the high bit ourselves would make the value negative as a signed
    .NET ``int``.
    """
    r, g, b = (_clamp8(c) for c in rgb)
    return (r << 16) | (g << 8) | b

def unpack_argb(value: int) -> RGB:
    """``0xAARRGGBB`` -> ``(r, g, b)``, discarding alpha and the sign bit."""
    v = int(value) & _CHANNELS
    return ((v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF)

def pack_for(kind: str, rgb: RGB) -> int:
    """Pack ``rgb`` the way the ``kind`` of target expects it."""
    if LAYOUT_BY_KIND.get(kind, "colorref") == "argb":
        return pack_argb(rgb)
    return pack_colorref(rgb)

def unpack_for(kind: str, value: int) -> RGB:
    """Unpack a value read from a ``kind`` of target."""
    if LAYOUT_BY_KIND.get(kind, "colorref") == "argb":
        return unpack_argb(value)
    return unpack_colorref(value)

def to_hex(rgb: RGB) -> str:
    """Render ``(r, g, b)`` as a ``#RRGGBB`` string."""
    r, g, b = (_clamp8(c) for c in rgb)
    return f"#{r:02X}{g:02X}{b:02X}"

def from_hex(text: str) -> RGB | None:
    """Parse ``#RRGGBB`` / ``RRGGBB`` / ``#RGB``. Returns None if unparseable."""
    s = text.strip().lstrip("#")
    if len(s) == 3 and all(c in "0123456789abcdefABCDEF" for c in s):
        s = "".join(c * 2 for c in s)
    if len(s) != 6:
        return None
    try:
        v = int(s, 16)
    except ValueError:
        return None
    return ((v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF)

def to_dpg(rgb: RGB) -> list[int]:
    """DearPyGui color widgets take ``[r, g, b, a]`` on a 0-255 scale."""
    r, g, b = (_clamp8(c) for c in rgb)
    return [r, g, b, 255]

def from_dpg(value) -> RGB:
    """Read an ``(r, g, b, a)`` value off a DearPyGui color widget.

    Expects the **0-255** scale, which is what ``dpg.get_value`` returns.
    Values are rounded rather than truncated: truncating loses a step on
    nearly every channel.

    Do not pass a color callback's ``app_data`` here -- DearPyGui delivers
    that on a 0-1 scale instead (see ``ColorStudio._on_picker``). No
    auto-detection is attempted on purpose: "all channels <= 1" is genuinely
    ambiguous between near-black on one scale and full intensity on the other,
    and guessing would turn a deliberate near-black into white.
    """
    seq = list(value)[:3]
    while len(seq) < 3:
        seq.append(0)
    return tuple(_clamp8(round(float(c))) for c in seq)

PALETTES: dict[str, list[tuple[str, RGB]]] = {
    "Metals": [
        ("Aluminum", (0xD6, 0xD9, 0xDC)),
        ("Anodized", (0x8A, 0x93, 0x9B)),
        ("Stainless", (0xC0, 0xC5, 0xC9)),
        ("Mild steel", (0x7D, 0x84, 0x8C)),
        ("Cast iron", (0x4A, 0x4E, 0x54)),
        ("Brass", (0xC8, 0xA3, 0x4A)),
        ("Bronze", (0xA1, 0x72, 0x3A)),
        ("Copper", (0xB5, 0x6A, 0x3C)),
        ("Titanium", (0x9A, 0x9C, 0xA0)),
        ("Zinc plate", (0xBE, 0xC6, 0xCB)),
    ],
    "Plastics": [
        ("ABS black", (0x2B, 0x2D, 0x30)),
        ("ABS white", (0xEC, 0xEE, 0xF0)),
        ("Nylon", (0xE3, 0xDC, 0xC8)),
        ("Delrin", (0xF1, 0xF1, 0xEE)),
        ("PTFE", (0xFA, 0xFA, 0xF7)),
        ("Polycarb", (0xC7, 0xDA, 0xE0)),
        ("PVC gray", (0x9E, 0xA5, 0xA8)),
        ("Rubber", (0x33, 0x35, 0x38)),
        ("PLA red", (0xC4, 0x3B, 0x3B)),
        ("PLA teal", (0x2E, 0x9E, 0x9E)),
    ],
    "Signal": [
        ("Safety red", (0xC8, 0x20, 0x22)),
        ("Safety orange", (0xE8, 0x6A, 0x11)),
        ("Safety yellow", (0xF2, 0xC0, 0x1E)),
        ("Safety green", (0x22, 0x8B, 0x45)),
        ("Safety blue", (0x1D, 0x5D, 0xA8)),
        ("Violet", (0x6C, 0x4A, 0xB6)),
        ("Magenta", (0xC0, 0x3A, 0x8C)),
        ("Cyan", (0x1F, 0xA8, 0xC0)),
        ("Charcoal", (0x3A, 0x3D, 0x42)),
        ("Bone", (0xEA, 0xE4, 0xD6)),
    ],
    "Neutrals": [
        ("White", (0xFF, 0xFF, 0xFF)),
        ("Paper", (0xF4, 0xF4, 0xF2)),
        ("Light gray", (0xD8, 0xD8, 0xD8)),
        ("Silver", (0xBD, 0xBD, 0xBD)),
        ("Gray", (0x9A, 0x9A, 0x9A)),
        ("Slate", (0x6B, 0x70, 0x76)),
        ("Graphite", (0x45, 0x48, 0x4C)),
        ("Ink", (0x26, 0x28, 0x2B)),
        ("Near black", (0x14, 0x15, 0x17)),
        ("Black", (0x00, 0x00, 0x00)),
    ],
}
