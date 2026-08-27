"""DearPyGui theming: a flat dark shell with one accent color.

Kept separate from ``app`` so the visual language lives in one place --
every color the chrome uses is a named constant below, nothing is
hard-coded at the call site.
"""
from __future__ import annotations

import dearpygui.dearpygui as dpg

BG_ROOT = (18, 19, 22, 255)
BG_PANEL = (25, 27, 31, 255)
BG_RAISED = (33, 36, 42, 255)
BG_HOVER = (44, 48, 56, 255)
BG_ACTIVE = (55, 60, 70, 255)

BORDER = (48, 52, 60, 255)
TEXT = (226, 229, 234, 255)
TEXT_DIM = (138, 145, 156, 255)

ACCENT = (70, 143, 235, 255)
ACCENT_HOVER = (92, 161, 244, 255)
ACCENT_ACTIVE = (54, 122, 210, 255)

DANGER = (206, 84, 84, 255)
DANGER_HOVER = (224, 104, 104, 255)

OK = (86, 176, 118, 255)
WARN = (214, 168, 74, 255)

def build(scale: float = 1.0) -> int:
    """Create and return the global theme tag.

    ``scale`` multiplies every metric (padding, rounding, scrollbar width) so
    the chrome grows with the display -- ImGui style vars are raw pixels and
    would otherwise stay 96-DPI sized on a HiDPI screen.
    """
    def s(value: float) -> float:
        return value * scale

    with dpg.theme() as global_theme:
        with dpg.theme_component(dpg.mvAll):
            dpg.add_theme_color(dpg.mvThemeCol_WindowBg, BG_ROOT)
            dpg.add_theme_color(dpg.mvThemeCol_ChildBg, BG_PANEL)
            dpg.add_theme_color(dpg.mvThemeCol_PopupBg, BG_RAISED)
            dpg.add_theme_color(dpg.mvThemeCol_MenuBarBg, BG_PANEL)
            dpg.add_theme_color(dpg.mvThemeCol_Border, BORDER)
            dpg.add_theme_color(dpg.mvThemeCol_BorderShadow, (0, 0, 0, 0))
            dpg.add_theme_color(dpg.mvThemeCol_Text, TEXT)
            dpg.add_theme_color(dpg.mvThemeCol_TextDisabled, TEXT_DIM)

            dpg.add_theme_color(dpg.mvThemeCol_FrameBg, BG_RAISED)
            dpg.add_theme_color(dpg.mvThemeCol_FrameBgHovered, BG_HOVER)
            dpg.add_theme_color(dpg.mvThemeCol_FrameBgActive, BG_ACTIVE)

            dpg.add_theme_color(dpg.mvThemeCol_Button, BG_RAISED)
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, BG_HOVER)
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, BG_ACTIVE)

            dpg.add_theme_color(dpg.mvThemeCol_Header, BG_HOVER)
            dpg.add_theme_color(dpg.mvThemeCol_HeaderHovered, BG_ACTIVE)
            dpg.add_theme_color(dpg.mvThemeCol_HeaderActive, ACCENT_ACTIVE)

            dpg.add_theme_color(dpg.mvThemeCol_Tab, BG_PANEL)
            dpg.add_theme_color(dpg.mvThemeCol_TabHovered, BG_HOVER)
            dpg.add_theme_color(dpg.mvThemeCol_TabActive, BG_RAISED)

            dpg.add_theme_color(dpg.mvThemeCol_SliderGrab, ACCENT)
            dpg.add_theme_color(dpg.mvThemeCol_SliderGrabActive, ACCENT_HOVER)
            dpg.add_theme_color(dpg.mvThemeCol_CheckMark, ACCENT)
            dpg.add_theme_color(dpg.mvThemeCol_Separator, BORDER)
            dpg.add_theme_color(dpg.mvThemeCol_ScrollbarBg, BG_PANEL)
            dpg.add_theme_color(dpg.mvThemeCol_ScrollbarGrab, BG_RAISED)
            dpg.add_theme_color(dpg.mvThemeCol_ScrollbarGrabHovered, BG_HOVER)
            dpg.add_theme_color(dpg.mvThemeCol_ScrollbarGrabActive, BG_ACTIVE)

            dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, s(14), s(12))
            dpg.add_theme_style(dpg.mvStyleVar_FramePadding, s(10), s(6))
            dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing, s(9), s(7))
            dpg.add_theme_style(dpg.mvStyleVar_ItemInnerSpacing, s(7), s(5))
            dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, s(6))
            dpg.add_theme_style(dpg.mvStyleVar_ChildRounding, s(8))
            dpg.add_theme_style(dpg.mvStyleVar_PopupRounding, s(8))
            dpg.add_theme_style(dpg.mvStyleVar_GrabRounding, s(5))
            dpg.add_theme_style(dpg.mvStyleVar_ScrollbarRounding, s(8))
            dpg.add_theme_style(dpg.mvStyleVar_TabRounding, s(6))
            dpg.add_theme_style(dpg.mvStyleVar_ChildBorderSize, 1)
            dpg.add_theme_style(dpg.mvStyleVar_ScrollbarSize, s(12))

    dpg.bind_theme(global_theme)
    return global_theme

def accent_button() -> int:
    """Theme for the one primary action on screen."""
    with dpg.theme() as t:
        with dpg.theme_component(dpg.mvAll):
            dpg.add_theme_color(dpg.mvThemeCol_Button, ACCENT)
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, ACCENT_HOVER)
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, ACCENT_ACTIVE)
            dpg.add_theme_color(dpg.mvThemeCol_Text, (255, 255, 255, 255))
    return t

def danger_button() -> int:
    with dpg.theme() as t:
        with dpg.theme_component(dpg.mvAll):
            dpg.add_theme_color(dpg.mvThemeCol_Button, (58, 38, 40, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, DANGER)
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, DANGER_HOVER)
            dpg.add_theme_color(dpg.mvThemeCol_Text, (238, 200, 200, 255))
    return t
