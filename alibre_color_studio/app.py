"""Alibre Color Studio -- a DearPyGui front end for Alibre Design colors.

Three panes: what you're coloring (left), the color itself (center),
palettes and actions (right). Everything Alibre-facing lives in
:mod:`.backend`; this module is UI and wiring only.
"""
from __future__ import annotations

import time
from dataclasses import replace
from typing import Any

import dearpygui.dearpygui as dpg

from . import colors, dpi, theme
from .backend import Backend, ColorState, Document, Target

APP_TITLE = "Alibre Color Studio"
MAX_RECENTS = 14

LIVE_APPLY_INTERVAL = 0.12

FOLLOW_POLL_INTERVAL = 0.35

DOC_POLL_INTERVAL = 0.6

class ColorStudio:
    def __init__(self) -> None:
        self.backend = Backend()
        self.documents: list[Document] = []
        self.doc: Document | None = None
        self.targets: list[Target] = []
        self.targets_by_key: dict[str, Target] = {}
        self.selected_keys: list[str] = []
        self.recents: list[colors.RGB] = []

        self.rgb: colors.RGB = (200, 200, 200)
        self._syncing = False
        self._live_dirty = False
        self._live_last = 0.0
        self._follow_last = 0.0
        self._follow_signature: tuple[str, ...] | None = None
        self._doc_poll_last = 0.0
        self._row_chips: dict[str, int] = {}
        self._expanded: set[str] = set()
        self._faces_cache: dict[str, list] = {}
        self._face_rows: dict[tuple[str, int], tuple[int, int]] = {}
        self._face_labels: dict[tuple[str, int], str] = {}
        self._controller_cache: dict | None = None
        self._part_released = False
        self._swatch_themes: list[int] = []

        self.t_accent = 0
        self.t_danger = 0

        dpi.enable()
        self.scale = dpi.scale_factor()
        self.px = dpi.Scaler(self.scale)

    def run(self) -> None:
        px = self.px
        dpg.create_context()
        theme.build(self.scale)
        self.t_accent = theme.accent_button()
        self.t_danger = theme.danger_button()
        self._install_font()

        self._build_ui()

        dpg.create_viewport(
            title=APP_TITLE,
            width=px(1280),
            height=px(840),
            min_width=px(1020),
            min_height=px(680),
            clear_color=theme.BG_ROOT,
        )
        dpg.setup_dearpygui()
        dpg.show_viewport()
        dpg.set_primary_window("root", True)
        dpg.set_viewport_resize_callback(lambda *_: self._layout())
        self._layout()

        self._do_connect(announce=False)

        while dpg.is_dearpygui_running():
            self._poll_active_document()
            self._poll_alibre_selection()
            self._pump_live_apply()
            dpg.render_dearpygui_frame()

        dpg.destroy_context()

    def _install_font(self) -> None:
        """Rasterize a real TTF at the scaled pixel size.

        Scaling DearPyGui's built-in 96-DPI bitmap font with
        ``set_global_font_scale`` produces soft, smeared glyphs. Baking a
        system TTF at ``BASE_FONT_SIZE * scale`` gives true HiDPI text. The
        built-in font plus a global scale is kept as the fallback, so a
        machine with no readable system font still gets correctly sized text.
        """
        path = dpi.font_path()
        size = int(round(dpi.BASE_FONT_SIZE * self.scale))
        if path is None:
            dpg.set_global_font_scale(self.scale * 1.15)
            return
        try:
            with dpg.font_registry():
                font = dpg.add_font(path, size)
            dpg.bind_font(font)
            dpg.set_global_font_scale(1.0)
        except Exception:
            dpg.set_global_font_scale(self.scale * 1.15)

    def _build_ui(self) -> None:
        px = self.px
        with dpg.window(tag="root", no_scrollbar=True):
            self._build_header()
            dpg.add_spacer(height=px(2))
            with dpg.group(horizontal=True, tag="body"):
                self._build_targets_pane()
                self._build_color_pane()
                self._build_palette_pane()
            dpg.add_spacer(height=px(2))
            self._build_status_bar()

    def _build_header(self) -> None:
        px = self.px
        with dpg.child_window(tag="header", height=px(64), border=False):
            with dpg.group(horizontal=True):
                dpg.add_text(APP_TITLE)
                dpg.add_spacer(width=px(6))
                dpg.add_text("|", color=theme.BORDER)
                dpg.add_spacer(width=px(6))
                dpg.add_text("Disconnected", tag="conn_pill", color=theme.WARN)
                dpg.add_spacer(width=px(14))

                dpg.add_button(label="Connect", tag="btn_connect", callback=self._on_connect)
                with dpg.tooltip("btn_connect"):
                    dpg.add_text("Bind to the running Alibre Design instance.\n"
                                 "Use this again after restarting Alibre.")

                dpg.add_text("Document", color=theme.TEXT_DIM)
                dpg.add_combo(
                    items=[],
                    tag="doc_combo",
                    width=px(330),
                    callback=self._on_document_changed,
                )
                dpg.add_button(label="Rescan", tag="btn_rescan", callback=self._on_rescan)
                with dpg.tooltip("btn_rescan"):
                    dpg.add_text("Re-read the list of open documents and their contents.\n"
                                 "Run this after adding features or components in Alibre.")

    def _build_targets_pane(self) -> None:
        px = self.px
        with dpg.child_window(tag="pane_targets", width=px(360), border=True):
            dpg.add_text("WHAT TO COLOR", color=theme.TEXT_DIM)
            dpg.add_separator()
            dpg.add_input_text(
                tag="target_filter",
                hint="Filter by name...",
                width=-1,
                callback=lambda *_: self._render_target_rows(),
            )
            with dpg.group(horizontal=True):
                dpg.add_button(
                    label="Use Alibre selection",
                    tag="btn_pull",
                    width=px(-70),
                    callback=self._on_pull_selection,
                )
                with dpg.tooltip("btn_pull"):
                    dpg.add_text(
                        "Read whatever is selected in Alibre's window.\n\n"
                        "Selected faces resolve to the feature that owns them --\n"
                        "AlibreX exposes face color as read-only, so a face takes\n"
                        "its color from its feature, exactly as Alibre's own UI does."
                    )
                dpg.add_button(label="All", tag="btn_all", width=-1, callback=self._on_select_all)
                with dpg.tooltip("btn_all"):
                    dpg.add_text("Select every row currently listed.")

            dpg.add_checkbox(
                tag="chk_follow_doc",
                label="Follow active document",
                default_value=True,
                callback=lambda *_: self._sync_active_document(force=True),
            )
            with dpg.tooltip("chk_follow_doc"):
                dpg.add_text(
                    "Track whichever document is frontmost in Alibre.\n\n"
                    "Without this, clicking a face in one document while the\n"
                    "dropdown points at another reads an empty selection --\n"
                    "which looks exactly like face coloring being broken."
                )

            dpg.add_checkbox(
                tag="chk_follow",
                label="Follow Alibre selection",
                callback=self._on_follow_toggle,
            )
            with dpg.tooltip("chk_follow"):
                dpg.add_text(
                    "Keep watching what you pick in Alibre and mirror it here,\n"
                    "so you can click a face over there and color it here\n"
                    "without pressing anything in between."
                )

            dpg.add_spacer(height=px(2))
            dpg.add_text("", tag="sel_summary", color=theme.TEXT_DIM)
            dpg.add_separator()
            with dpg.child_window(tag="target_list", border=False):
                dpg.add_text("Not connected.", tag="target_empty", color=theme.TEXT_DIM)

    def _build_color_pane(self) -> None:
        px = self.px
        with dpg.child_window(tag="pane_color", width=px(430), border=True):
            dpg.add_text("COLOR", color=theme.TEXT_DIM)
            dpg.add_separator()

            dpg.add_color_picker(
                tag="picker",
                default_value=colors.to_dpg(self.rgb),
                picker_mode=dpg.mvColorPicker_wheel,
                alpha_bar=False,
                no_alpha=True,
                display_rgb=True,
                no_side_preview=False,
                width=px(340),
                callback=self._on_picker,
            )

            with dpg.group(horizontal=True):
                dpg.add_text("Hex")
                dpg.add_input_text(
                    tag="hex_input",
                    width=px(110),
                    default_value=colors.to_hex(self.rgb),
                    on_enter=True,
                    callback=self._on_hex,
                )
                dpg.add_button(
                    label="Read from target",
                    tag="btn_reread",
                    callback=self._on_reread,
                )
                with dpg.tooltip("btn_reread"):
                    dpg.add_text("Load the color the selected target currently has in Alibre.")

            for ch, label, initial in (
                ("r", "R", self.rgb[0]), ("g", "G", self.rgb[1]), ("b", "B", self.rgb[2])
            ):
                dpg.add_slider_int(
                    tag=f"slider_{ch}",
                    label=label,
                    default_value=initial,
                    min_value=0,
                    max_value=255,
                    width=px(250),
                    callback=self._on_rgb_slider,
                )

            dpg.add_spacer(height=px(4))
            dpg.add_separator()
            dpg.add_text("FINISH", color=theme.TEXT_DIM)

            dpg.add_slider_int(
                tag="slider_transparency",
                label="Transparency",
                default_value=0,
                min_value=0,
                max_value=99,
                width=px(250),
                callback=self._mark_live_dirty,
            )
            with dpg.tooltip("slider_transparency"):
                dpg.add_text("0 = opaque, 99 = maximum transparency.\n"
                             "Parts and assembly components only.")

            dpg.add_slider_int(
                tag="slider_opacity",
                label="Opacity",
                default_value=100,
                min_value=0,
                max_value=100,
                width=px(250),
                callback=self._mark_live_dirty,
            )
            with dpg.tooltip("slider_opacity"):
                dpg.add_text("Features carry opacity rather than transparency.\n"
                             "100 = fully opaque.")

            dpg.add_slider_int(
                tag="slider_reflectivity",
                label="Reflectivity",
                default_value=0,
                min_value=0,
                max_value=100,
                width=px(250),
                callback=self._mark_live_dirty,
            )
            with dpg.tooltip("slider_reflectivity"):
                dpg.add_text("0 = matte, 100 = fully glossy -- the same scale as Alibre's dialog.")

            dpg.add_spacer(height=px(4))
            with dpg.group(horizontal=True):
                dpg.add_checkbox(
                    tag="chk_edge",
                    label="Also set edge color",
                    callback=self._on_edge_toggle,
                )
                dpg.add_color_edit(
                    tag="edge_color",
                    default_value=[55, 55, 55, 255],
                    no_alpha=True,
                    no_inputs=True,
                    no_label=True,
                    width=px(40),
                    enabled=False,
                    callback=self._mark_live_dirty,
                )
            with dpg.tooltip("chk_edge"):
                dpg.add_text("Edge color is available on parts and features, not on\n"
                             "assembly components.")

            dpg.add_checkbox(
                tag="chk_use_part",
                label="Feature inherits the part color",
                callback=self._on_use_part_toggle,
            )
            with dpg.tooltip("chk_use_part"):
                dpg.add_text(
                    "While a feature inherits, its own face color is ignored.\n"
                    "Applying a color to a feature clears this automatically."
                )

    def _build_palette_pane(self) -> None:
        px = self.px
        with dpg.child_window(tag="pane_palette", width=-1, border=True):
            dpg.add_text("EDITING LIVE", color=theme.OK, tag="live_banner")
            with dpg.tooltip("live_banner"):
                dpg.add_text(
                    "Whatever is selected in the tree updates in Alibre as you\n"
                    "change the color. Undo steps back one edit at a time."
                )
            dpg.add_separator()

            with dpg.group(horizontal=True):
                dpg.add_button(
                    label="Undo",
                    tag="btn_undo",
                    width=px(-4),
                    height=px(32),
                    callback=self._on_undo,
                )
                dpg.bind_item_theme("btn_undo", self.t_accent)
            with dpg.tooltip("btn_undo"):
                dpg.add_text("Step back one edit, restoring color and finish.")

            dpg.add_button(
                label="Reset feature to part color",
                tag="btn_reset_feature",
                width=-1,
                callback=self._on_reset_to_part,
            )
            dpg.bind_item_theme("btn_reset_feature", self.t_danger)
            with dpg.tooltip("btn_reset_feature"):
                dpg.add_text("Hand the selected features back to the part's own color.")

            dpg.add_button(
                label="Diagnose face selection",
                tag="btn_diagnose",
                width=-1,
                callback=self._on_diagnose,
            )
            with dpg.tooltip("btn_diagnose"):
                dpg.add_text(
                    "Reports every step from Alibre's selection to the feature\n"
                    "being colored -- use this if a pick does nothing."
                )

            dpg.add_spacer(height=px(6))
            dpg.add_separator()
            dpg.add_text("PALETTES", color=theme.TEXT_DIM)
            with dpg.tab_bar(tag="palette_tabs"):
                for group_name, entries in colors.PALETTES.items():
                    with dpg.tab(label=group_name):
                        self._build_swatch_grid(entries, per_row=5)

            dpg.add_spacer(height=px(6))
            dpg.add_separator()
            dpg.add_text("RECENT", color=theme.TEXT_DIM)
            dpg.add_group(tag="recents_row", horizontal=True)

    def _build_swatch_grid(self, entries: list[tuple[str, colors.RGB]], per_row: int) -> None:
        px = self.px
        for start in range(0, len(entries), per_row):
            with dpg.group(horizontal=True):
                for name, rgb in entries[start:start + per_row]:
                    btn = dpg.add_color_button(
                        default_value=colors.to_dpg(rgb),
                        width=px(40),
                        height=px(30),
                        no_border=False,
                        no_drag_drop=True,
                        callback=self._on_swatch,
                        user_data=rgb,
                    )
                    with dpg.tooltip(btn):
                        dpg.add_text(f"{name}   {colors.to_hex(rgb)}")

    def _build_status_bar(self) -> None:
        px = self.px
        with dpg.child_window(tag="statusbar", height=px(40), border=True):
            dpg.add_text("Starting up...", tag="status_text", color=theme.TEXT_DIM)

    def _layout(self) -> None:
        """Size the three panes to the viewport.

        The viewport reports physical pixels, so the min/max pane widths are
        design-time sizes that have to be scaled before they are compared
        against it -- otherwise the side panes stay 96-DPI narrow on a HiDPI
        display and the center pane swallows the extra room.
        """
        px = self.px
        if not dpg.is_dearpygui_running() and not dpg.does_item_exist("pane_targets"):
            return
        width = max(dpg.get_viewport_client_width(), px(900))
        height = max(dpg.get_viewport_client_height(), px(620))

        left = max(px(300), min(px(380), int(width * 0.28)))
        center = max(px(400), min(px(460), int(width * 0.34)))
        body_h = height - px(64) - px(40) - px(46)

        for tag, w in (("pane_targets", left), ("pane_color", center), ("pane_palette", -1)):
            if dpg.does_item_exist(tag):
                dpg.configure_item(tag, width=w, height=body_h)

        if self.targets and dpg.does_item_exist("target_list"):
            self._render_target_rows()

    def _on_connect(self, *_: Any) -> None:
        self._do_connect(announce=True)

    def _do_connect(self, announce: bool) -> None:
        if self.backend.connect():
            version = self.backend.version()
            dpg.set_value("conn_pill", f"Connected  {version}".strip())
            dpg.configure_item("conn_pill", color=theme.OK)
            self._reload_documents()
            self._status(f"Connected to Alibre. {len(self.documents)} colorable document(s).", "ok")
        else:
            dpg.set_value("conn_pill", "Disconnected")
            dpg.configure_item("conn_pill", color=theme.WARN)
            self.documents = []
            self.doc = None
            self.targets = []
            self.targets_by_key = {}
            self.selected_keys = []
            dpg.configure_item("doc_combo", items=[], default_value="")
            self._render_target_rows()
            message = self.backend.error or "Alibre Design is not running."
            self._status(message if announce else f"{message} Start Alibre, then press Connect.", "warn")

    def _on_rescan(self, *_: Any) -> None:
        if not self.backend.connected:
            self._status("Not connected to Alibre.", "warn")
            return
        self._reload_documents()
        self._status("Rescanned open documents.", "ok")

    def _reload_documents(self) -> None:
        self.documents = self.backend.refresh_documents()
        labels = [d.label for d in self.documents]
        dpg.configure_item("doc_combo", items=labels)
        if not self.documents:
            dpg.set_value("doc_combo", "")
            self.doc = None
            self._reload_targets()
            return
        index = self.backend.active_document_index()
        index = min(index, len(self.documents) - 1)
        dpg.set_value("doc_combo", labels[index])
        self.doc = self.documents[index]
        self._reload_targets()

    def _on_document_changed(self, _sender: Any, value: str) -> None:
        match = next((d for d in self.documents if d.label == value), None)
        if match is None:
            self._status(f"Document {value!r} is no longer open. Press Rescan.", "warn")
            return
        self.doc = match
        self.backend.clear_undo()
        self._reload_targets()
        self._status(f"Switched to {value}.", "info")

    def _reload_targets(self) -> None:
        self._expanded = set()
        self._faces_cache = {}
        self.targets = self.backend.targets(self.doc) if self.doc else []
        self.targets_by_key = {t.key: t for t in self.targets}
        self.selected_keys = [self.targets[0].key] if self.targets else []
        self._render_target_rows()
        self._load_state_from_selection()

    def _render_target_rows(self) -> None:
        """Draw the part -> feature -> face tree.

        Hand-rolled from selectables rather than ``add_tree_node``: that widget
        takes no ``callback``, and its ``toggled_open`` handler does not fire,
        so lazy expansion through it silently never ran. Here the arrow button
        owns expand/collapse and the label owns selection, both via ordinary
        callbacks.
        """
        px = self.px
        dpg.delete_item("target_list", children_only=True)
        self._row_chips = {}
        self._face_rows = {}
        self._controller_cache = None

        if not self.backend.connected:
            dpg.add_text("Not connected. Press Connect.", parent="target_list",
                         color=theme.TEXT_DIM)
            self._update_selection_summary()
            return
        if not self.doc:
            dpg.add_text(
                "No part or assembly is open in Alibre.",
                parent="target_list", color=theme.TEXT_DIM, wrap=px(310),
            )
            self._update_selection_summary()
            return

        needle = (dpg.get_value("target_filter") or "").strip().lower()
        pane_w = dpg.get_item_width("pane_targets") or px(360)
        part_faces = self.backend.part_face_count(self.doc)

        shown = 0
        for target in self.targets:
            if needle and needle not in target.label.lower() \
                    and needle not in target.detail.lower():
                continue
            shown += 1
            expandable = target.kind == "feature" and target.face_count > 0
            expanded = target.key in self._expanded
            self._draw_target_row(target, part_faces, pane_w, expandable, expanded)
            if expandable and expanded:
                self._draw_face_rows(target, pane_w)

        if shown == 0:
            dpg.add_text("Nothing matches that filter.", parent="target_list",
                         color=theme.TEXT_DIM)
        self._update_selection_summary()

    def _draw_target_row(self, target: Target, part_faces: int, pane_w: int,
                         expandable: bool, expanded: bool) -> None:
        px = self.px
        state = self.backend.read(target)
        indent = px(22) if expandable else px(22)
        label_w = max(px(90), pane_w - indent - px(20) - px(46))

        suffix = self._row_suffix(target, part_faces)
        label = f"{target.label}{suffix}"
        max_chars = max(12, int(label_w / (dpi.BASE_FONT_SIZE * self.scale * 0.5)))
        if len(label) > max_chars:
            keep = max(8, max_chars - len(suffix) - 1)
            label = f"{target.label[:keep].rstrip()}\u2026{suffix}"

        with dpg.group(horizontal=True, parent="target_list"):
            if expandable:
                dpg.add_button(
                    label="\u25BC" if expanded else "\u25B6",
                    width=px(20), height=px(20),
                    callback=self._on_toggle_expand,
                    user_data=target.key,
                )
            else:
                dpg.add_spacer(width=px(20))

            chip = dpg.add_color_button(
                default_value=colors.to_dpg(state.rgb),
                width=px(18), height=px(18), no_drag_drop=True,
                callback=self._on_row_clicked, user_data=target.key,
            )
            self._row_chips[target.key] = chip

            row = dpg.add_selectable(
                label=label,
                default_value=target.key in self.selected_keys,
                tag=f"row::{target.key}",
                width=label_w,
                callback=self._on_row_clicked,
                user_data=target.key,
            )
            with dpg.tooltip(row):
                dpg.add_text(f"{target.label.strip()}\n{target.detail}")
                if expandable:
                    dpg.add_text("Arrow expands to this feature's faces.",
                                 color=theme.TEXT_DIM)

    def _draw_face_rows(self, target: Target, pane_w: int) -> None:
        """List one feature's faces, read fresh from Alibre."""
        px = self.px
        faces = self._faces_cache.get(target.key)
        if faces is None:
            faces = self.backend.feature_faces(
                target, controller_map=self._controller_map()
            )
            self._faces_cache[target.key] = faces

        if not faces:
            with dpg.group(horizontal=True, parent="target_list"):
                dpg.add_spacer(width=px(58))
                dpg.add_text("no faces reported", color=theme.WARN)
            return

        label_w = max(px(80), pane_w - px(58) - px(46))
        for face in faces:
            with dpg.group(horizontal=True, parent="target_list"):
                dpg.add_spacer(width=px(40))
                face_chip = dpg.add_color_button(
                    default_value=colors.to_dpg(face.rgb),
                    width=px(14), height=px(14), no_drag_drop=True,
                    callback=self._on_face_clicked,
                    user_data=(target.key, face.index),
                )
                owner = self.targets_by_key.get(face.controller or "")
                text = f"{face.label}   {colors.to_hex(face.rgb)}"
                if owner is not None:
                    text += f"   -> {owner.label.strip()}"
                item = dpg.add_selectable(
                    label=text,
                    width=label_w,
                    callback=self._on_face_clicked,
                    user_data=(target.key, face.index),
                )
                self._face_rows[(target.key, face.index)] = (face_chip, item)
                self._face_labels[(target.key, face.index)] = face.label
                with dpg.tooltip(item):
                    dpg.add_text(
                        f"{face.label} of {target.label.strip()}\n"
                        f"Current color {colors.to_hex(face.rgb)}"
                    )
                    if owner is not None:
                        dpg.add_text(
                            f"\nThis face's color comes from {owner.label.strip()},\n"
                            "which overrides the feature above. Clicking edits\n"
                            f"that override -- {owner.face_count} face(s) only.",
                            color=theme.OK,
                        )
                    else:
                        dpg.add_text(
                            f"\nEditing repaints all {target.face_count} faces of\n"
                            "this feature. To recolor just this one, select it in\n"
                            "Alibre and use Part Tools > Face Color to create a\n"
                            "Face Color feature -- it then appears here.",
                            color=theme.TEXT_DIM,
                        )

    def _controller_map(self) -> dict:
        """Face key -> controlling feature index, cached for this render pass.

        Walking it is one COM call per face of every feature, so it is built at
        most once per tree rebuild and only when a feature is expanded.
        """
        if self._controller_cache is None and self.doc is not None:
            self._controller_cache = self.backend.face_to_feature_index(self.doc)
        return self._controller_cache or {}

    def _row_suffix(self, target: Target, part_faces: int) -> str:
        """Trailing detail for a row, including its share of the part's faces.

        The feature type is dropped when the name already carries it --
        "Extrusion<1> \u00b7 Extrusion, 6/10 faces" wastes the width that the name
        itself needs, and the name is what the user is actually reading.
        """
        parts: list[str] = []
        if target.detail and target.detail.lower() not in target.label.lower():
            parts.append(target.detail)
        if target.kind == "feature" and target.face_count:
            if part_faces and target.face_count >= part_faces:
                parts.append(f"all {part_faces} faces")
            elif part_faces:
                parts.append(f"{target.face_count}/{part_faces} faces")
            else:
                plural = "s" if target.face_count != 1 else ""
                parts.append(f"{target.face_count} face{plural}")
        return f"  \u00b7  {', '.join(parts)}" if parts else ""

    def _refresh_face_swatches(self, keys: list[str], rgb: colors.RGB) -> None:
        """Repaint the visible face rows of the features just edited.

        Updated optimistically rather than re-read: every face of a feature
        takes that feature's FaceColor, and re-reading each face over COM on
        every live-apply tick would stall the drag. The cache is dropped too,
        so the next expand reads the model rather than this assumption.
        """
        swatch = colors.to_dpg(rgb)
        hexed = colors.to_hex(rgb)
        for (key, index), (chip, label) in self._face_rows.items():
            if key not in keys:
                continue
            if dpg.does_item_exist(chip):
                dpg.configure_item(chip, default_value=swatch)
            if dpg.does_item_exist(label):
                name = self._face_labels.get((key, index), "Face")
                dpg.configure_item(label, label=f"{name}   {hexed}")
        for key in keys:
            self._faces_cache.pop(key, None)

    def _on_toggle_expand(self, _sender: Any, _value: Any, key: str) -> None:
        """Expand or collapse one feature's face list."""
        if key in self._expanded:
            self._expanded.discard(key)
        else:
            self._expanded.add(key)
            self._faces_cache.pop(key, None)
        self._render_target_rows()

    def _on_face_clicked(self, _sender: Any, _value: Any, payload: tuple) -> None:
        """Pick a face: target its feature and highlight the face in Alibre."""
        key, face_index = payload
        target = self.targets_by_key.get(key)
        if target is None or not self.doc:
            return
        controller_key = key
        for face in self._faces_cache.get(key) or []:
            if face.index == face_index and face.controller in self.targets_by_key:
                controller_key = face.controller
                break
        key = controller_key
        target = self.targets_by_key[key]
        self.selected_keys = [key]
        self._sync_row_selection()
        self._update_selection_summary()
        self._load_state_from_selection()

        ok = self.backend.select_face_in_alibre(self.doc, target, face_index)
        self._follow_signature = tuple(self.selected_keys)
        where = "highlighted in Alibre" if ok else "could not be highlighted"
        self._status(
            f"{target.label.strip()} / face {face_index + 1} {where}. "
            f"Editing recolors all {target.face_count} faces of this feature.",
            "info",
        )

    def _sync_row_selection(self) -> None:
        """Push ``selected_keys`` onto whichever rows currently exist."""
        for target in self.targets:
            row = f"row::{target.key}"
            if dpg.does_item_exist(row):
                dpg.set_value(row, target.key in self.selected_keys)

    def _on_row_clicked(self, _sender: Any, _value: Any, key: str) -> None:
        """Click selects; ctrl-click or shift-click extends the selection."""
        extend = (
            dpg.is_key_down(dpg.mvKey_LControl)
            or dpg.is_key_down(dpg.mvKey_RControl)
            or dpg.is_key_down(dpg.mvKey_LShift)
            or dpg.is_key_down(dpg.mvKey_RShift)
        )
        if extend:
            if key in self.selected_keys:
                self.selected_keys.remove(key)
            else:
                self.selected_keys.append(key)
        else:
            self.selected_keys = [key]

        for target in self.targets:
            row = f"row::{target.key}"
            if dpg.does_item_exist(row):
                dpg.set_value(row, target.key in self.selected_keys)

        self._update_selection_summary()
        self._load_state_from_selection()

    def _on_select_all(self, *_: Any) -> None:
        needle = (dpg.get_value("target_filter") or "").strip().lower()
        self.selected_keys = [
            t.key
            for t in self.targets
            if not needle or needle in t.label.lower() or needle in t.detail.lower()
        ]
        for target in self.targets:
            row = f"row::{target.key}"
            if dpg.does_item_exist(row):
                dpg.set_value(row, target.key in self.selected_keys)
        self._update_selection_summary()
        self._load_state_from_selection()

    def _on_pull_selection(self, *_: Any) -> None:
        if not self.doc:
            self._status("No document to read a selection from.", "warn")
            return
        keys, notes = self.backend.selection_target_keys(self.doc, self.targets)
        if keys:
            self.selected_keys = keys
            for target in self.targets:
                row = f"row::{target.key}"
                if dpg.does_item_exist(row):
                    dpg.set_value(row, target.key in self.selected_keys)
            self._update_selection_summary()
            self._load_state_from_selection()
            summary = f"Matched {len(keys)} target(s) from Alibre's selection."
            if notes:
                summary += "  " + notes[0]
            self._status(summary, "ok")
        else:
            self._status(notes[0] if notes else "Nothing usable is selected.", "warn")

    def _update_selection_summary(self) -> None:
        count = len(self.selected_keys)
        if count == 0:
            text = "Nothing selected"
        elif count == 1:
            target = self.targets_by_key.get(self.selected_keys[0])
            text = f"1 selected — {target.label.strip()}" if target else "1 selected"
        else:
            text = f"{count} selected  (ctrl-click to add or remove)"
        dpg.set_value("sel_summary", text)

    def _set_rgb(self, rgb: colors.RGB, source: str = "") -> None:
        """Push ``rgb`` into every color widget, without re-entering callbacks."""
        self.rgb = rgb
        self._syncing = True
        try:
            if source != "picker":
                dpg.set_value("picker", colors.to_dpg(rgb))
            if source != "hex":
                dpg.set_value("hex_input", colors.to_hex(rgb))
            if source != "slider":
                for ch, value in zip("rgb", rgb):
                    dpg.set_value(f"slider_{ch}", value)
        finally:
            self._syncing = False

    def _on_picker(self, _sender: Any, _value: Any) -> None:
        """Read the wheel. Deliberately ignores the callback's ``app_data``.

        DearPyGui is inconsistent about color scale: ``set_value``/``get_value``
        speak 0-255 (mvPyUtils.cpp divides Python input by 255 on the way in),
        but a color widget's callback hands the raw internal ImGui floats to
        ToPyColor with no conversion, so ``app_data`` arrives as 0-1. Trusting
        it turns 162 into 1, i.e. every dragged color collapses to near-black.
        ``get_value`` is the accessor that always speaks 0-255.
        """
        if self._syncing:
            return
        self._set_rgb(colors.from_dpg(dpg.get_value("picker")), source="picker")
        self._mark_live_dirty()

    def _on_hex(self, _sender: Any, value: str) -> None:
        if self._syncing:
            return
        parsed = colors.from_hex(value)
        if parsed is None:
            self._status(f"{value!r} is not a valid hex color.", "warn")
            dpg.set_value("hex_input", colors.to_hex(self.rgb))
            return
        self._set_rgb(parsed, source="hex")
        self._mark_live_dirty()

    def _on_rgb_slider(self, *_: Any) -> None:
        if self._syncing:
            return
        rgb = tuple(int(dpg.get_value(f"slider_{c}")) for c in "rgb")
        self._set_rgb(rgb, source="slider")
        self._mark_live_dirty()

    def _on_swatch(self, _sender: Any, _value: Any, rgb: colors.RGB) -> None:
        self._set_rgb(rgb)
        self._mark_live_dirty()

    def _on_edge_toggle(self, _sender: Any, value: bool) -> None:
        dpg.configure_item("edge_color", enabled=value)
        self._mark_live_dirty()

    def _on_use_part_toggle(self, *_: Any) -> None:
        self._mark_live_dirty()

    def _on_reread(self, *_: Any) -> None:
        self._load_state_from_selection()
        self._status("Loaded the target's current color.", "info")

    def _load_state_from_selection(self) -> None:
        """Mirror the first selected target's state into the widgets."""
        self._part_released = False
        target = self._active_target()
        if target is None:
            for tag in ("slider_transparency", "slider_opacity", "slider_reflectivity",
                        "chk_use_part", "chk_edge"):
                dpg.configure_item(tag, enabled=False)
            return

        state = self.backend.read(target)
        self._set_rgb(state.rgb)

        is_feature = target.kind == "feature"
        is_part = target.kind == "part"

        dpg.configure_item("slider_transparency", enabled=state.transparency is not None)
        dpg.set_value("slider_transparency", state.transparency or 0)

        dpg.configure_item("slider_opacity", enabled=state.opacity is not None)
        dpg.set_value("slider_opacity", 100 if state.opacity is None else state.opacity)

        dpg.configure_item("slider_reflectivity", enabled=state.reflectivity is not None)
        dpg.set_value("slider_reflectivity", state.reflectivity or 0)

        dpg.configure_item("chk_edge", enabled=state.edge_rgb is not None)
        if state.edge_rgb is not None:
            dpg.set_value("edge_color", colors.to_dpg(state.edge_rgb))
        else:
            dpg.set_value("chk_edge", False)
            dpg.configure_item("edge_color", enabled=False)

        dpg.configure_item("chk_use_part", enabled=is_feature)
        dpg.set_value("chk_use_part", bool(state.use_part_color) if is_feature else False)
        dpg.configure_item("btn_reset_feature", enabled=is_feature)
        del is_part

    def _active_target(self) -> Target | None:
        for key in self.selected_keys:
            target = self.targets_by_key.get(key)
            if target is not None:
                return target
        return None

    def _compose_state(self, target: Target) -> ColorState:
        """Build the state to write, keeping only fields ``target`` supports."""
        current = self.backend.read(target)
        state = ColorState(rgb=self.rgb)

        if dpg.get_value("chk_edge") and current.edge_rgb is not None:
            state = replace(state, edge_rgb=colors.from_dpg(dpg.get_value("edge_color")))
        if current.transparency is not None:
            state = replace(state, transparency=int(dpg.get_value("slider_transparency")))
        if current.reflectivity is not None:
            state = replace(state, reflectivity=int(dpg.get_value("slider_reflectivity")))
        if current.opacity is not None:
            state = replace(state, opacity=int(dpg.get_value("slider_opacity")))
        if target.kind == "feature":
            state = replace(state, use_part_color=False)
        return state

    def _apply_to_selection(self, record_undo: bool) -> tuple[int, list[str]]:
        if not self.doc:
            return 0, ["No document."]
        applied = 0
        problems: list[str] = []
        for key in self.selected_keys:
            target = self.targets_by_key.get(key)
            if target is None:
                continue
            issues = self.backend.apply(
                target, self._compose_state(target), self.doc, record_undo=record_undo
            )
            problems.extend(f"{target.label.strip()} -> {p}" for p in issues)
            applied += 1
            chip = self._row_chips.get(key)
            if chip is not None and dpg.does_item_exist(chip):
                dpg.configure_item(chip, default_value=colors.to_dpg(self.rgb))
        if applied:
            self._refresh_face_swatches(list(self.selected_keys), self.rgb)
        return applied, problems

    def _on_reset_to_part(self, *_: Any) -> None:
        if not self.doc:
            return
        reset = 0
        for key in self.selected_keys:
            target = self.targets_by_key.get(key)
            if target is None or target.kind != "feature":
                continue
            current = self.backend.read(target)
            self.backend.apply(
                target, replace(current, use_part_color=True), self.doc, record_undo=True
            )
            reset += 1
        if reset:
            self._render_target_rows()
            self._load_state_from_selection()
            self._status(f"{reset} feature(s) now inherit the part color.", "ok")
        else:
            self._status("Select one or more features first.", "warn")

    def _on_undo(self, *_: Any) -> None:
        if not self.doc:
            return
        message = self.backend.undo_last(self.targets_by_key, self.doc)
        self._render_target_rows()
        self._load_state_from_selection()
        self._status(message, "info")

    def _poll_active_document(self) -> None:
        """Throttled check for document switches and newly added features."""
        if not dpg.get_value("chk_follow_doc"):
            return
        now = time.monotonic()
        if now - self._doc_poll_last < DOC_POLL_INTERVAL:
            return
        self._doc_poll_last = now
        if self._sync_active_document():
            return
        if self.doc is not None and self.backend.feature_count(self.doc) != sum(
            1 for t in self.targets if t.kind == "feature"
        ):
            self._reload_targets()
            self._status("Feature list changed in Alibre - tree refreshed.", "info")

    def _sync_active_document(self, force: bool = False) -> bool:
        """Switch to whatever document is frontmost in Alibre.

        Returns True if the document changed. Cheap enough to run on the poll
        tick: it is one ``TopmostSession.Identifier`` read, and only rebuilds
        the target list when the GUID actually differs.
        """
        if not self.backend.connected or not self.documents:
            return False
        if not force and not dpg.get_value("chk_follow_doc"):
            return False

        top = self.backend.safe_topmost_identifier()
        if not top:
            return False
        if self.doc is not None and self.doc.identifier == top:
            return False
        match = next((d for d in self.documents if d.identifier == top), None)
        if match is None:
            return False

        self.doc = match
        dpg.set_value("doc_combo", match.label)
        self.backend.clear_undo()
        self._follow_signature = None
        self._reload_targets()
        self._status(f"Following Alibre: now on {match.label}.", "info")
        return True

    def _on_diagnose(self, *_: Any) -> None:
        """Print why face coloring is or isn't finding anything."""
        if not self.backend.connected:
            self._status("Not connected to Alibre.", "warn")
            return
        if not self.doc:
            self._status("No document is open.", "warn")
            return
        lines = self.backend.diagnose_selection(self.doc, self.targets)
        report = "\n".join(lines)
        print("\n=== Selection diagnostics ===\n" + report + "\n", flush=True)
        if dpg.does_item_exist("diag_window"):
            dpg.delete_item("diag_window")
        with dpg.window(
            tag="diag_window",
            label="Selection diagnostics",
            width=self.px(760),
            height=self.px(520),
            pos=(self.px(120), self.px(90)),
        ):
            dpg.add_text("Copy this and send it over if face coloring misbehaves.",
                         color=theme.TEXT_DIM)
            dpg.add_separator()
            dpg.add_input_text(
                multiline=True, readonly=True, default_value=report,
                width=-1, height=-1,
            )
        self._status(f"Diagnostics: {len(lines)} lines (also printed to console).", "info")

    def _on_follow_toggle(self, _sender: Any, value: bool) -> None:
        if value:
            self._follow_signature = None
            self._poll_alibre_selection(force=True)
            self._status("Following Alibre's selection.", "info")
        else:
            self._status("Stopped following Alibre's selection.", "info")

    def _poll_alibre_selection(self, force: bool = False) -> None:
        """Mirror Alibre's selection into the row list, when following.

        Polled rather than event-driven: AlibreX's event manager reports
        selection changes only for some object kinds, and a cheap read of
        ``SelectedObjects`` a few times a second is both simpler and more
        reliable than a partial event feed.
        """
        if not force and not dpg.get_value("chk_follow"):
            return
        if not self.doc or not self.backend.connected:
            return
        now = time.monotonic()
        if not force and now - self._follow_last < FOLLOW_POLL_INTERVAL:
            return
        self._follow_last = now

        keys, notes = self.backend.selection_target_keys(self.doc, self.targets)
        signature = tuple(keys)
        if signature == self._follow_signature:
            return
        self._follow_signature = signature
        if not keys:
            return

        self.selected_keys = list(keys)
        for target in self.targets:
            row = f"row::{target.key}"
            if dpg.does_item_exist(row):
                dpg.set_value(row, target.key in self.selected_keys)
        self._update_selection_summary()
        self._load_state_from_selection()
        message = f"Following Alibre: {len(keys)} target(s)."
        if notes:
            message += "  " + notes[0]
        self._status(message, "info")

    def _mark_live_dirty(self, *_: Any) -> None:
        """Queue a write. There is no Apply step -- edits are always live."""
        self._live_dirty = True

    def _release_part_overrides(self) -> int:
        """Hand features back to the part before coloring the part itself.

        A feature with ``UsePartColor`` false keeps painting its own
        ``FaceColor``, so writing the part color alone changes nothing you can
        see. Done once per selection rather than per write, since it only
        matters the first time the part is edited.
        """
        if self._part_released or not self.doc:
            return 0
        target = self._active_target()
        if target is None or target.kind != "part":
            return 0
        self._part_released = True

        overriding = self.backend.features_overriding_part(self.targets)
        for feature in overriding:
            state = replace(self.backend.read(feature), use_part_color=True)
            self.backend.apply(feature, state, self.doc, record_undo=True)
        return len(overriding)

    def _pump_live_apply(self) -> None:
        """Flush a pending live-apply, at most once per interval."""
        if not self._live_dirty:
            return
        now = time.monotonic()
        if now - self._live_last < LIVE_APPLY_INTERVAL:
            return
        self._live_dirty = False
        self._live_last = now
        if not self.selected_keys or not self.doc:
            return

        first = self.backend.peek_undo()
        record = not (first and first.target_key in self.selected_keys and first.doc_index == self.doc.index)
        if record:
            self.backend.begin_group()
        cleared = self._release_part_overrides()
        applied, _ = self._apply_to_selection(record_undo=record)
        if not applied:
            return
        if cleared:
            self._render_target_rows()
        target = self._active_target()
        if target is not None and target.kind == "feature" and self.doc:
            total = self.backend.part_face_count(self.doc)
            scope = (
                f"all {total} faces"
                if total and target.face_count >= total
                else f"{target.face_count}/{total} faces" if total
                else f"{target.face_count} faces"
            )
            self._status(
                f"{colors.to_hex(self.rgb)} -> {target.label.strip()} ({scope}).", "ok"
            )
        else:
            label = target.label.strip() if target else f"{applied} target(s)"
            self._status(f"{colors.to_hex(self.rgb)} -> {label}.", "ok")

    def _remember(self, rgb: colors.RGB) -> None:
        px = self.px
        if rgb in self.recents:
            self.recents.remove(rgb)
        self.recents.insert(0, rgb)
        del self.recents[MAX_RECENTS:]
        dpg.delete_item("recents_row", children_only=True)
        for value in self.recents:
            with dpg.group(parent="recents_row", horizontal=True):
                btn = dpg.add_color_button(
                    default_value=colors.to_dpg(value),
                    width=px(26),
                    height=px(22),
                    no_drag_drop=True,
                    callback=self._on_swatch,
                    user_data=value,
                )
                with dpg.tooltip(btn):
                    dpg.add_text(colors.to_hex(value))

    def _status(self, message: str, level: str = "info") -> None:
        palette = {"ok": theme.OK, "warn": theme.WARN, "info": theme.TEXT_DIM}
        dpg.set_value("status_text", message)
        dpg.configure_item("status_text", color=palette.get(level, theme.TEXT_DIM))

def main() -> int:
    ColorStudio().run()
    return 0
