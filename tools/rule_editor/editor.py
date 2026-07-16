"""Interactive editor for the tier 3a rule table (rules/keywords.yaml).

A DearPyGui UI over rules.py: edit ordered first-match-wins rules, see live
match counts against a normalized.csv, and write the YAML only when Save is
pressed.

The rules file lives in the external data root, never in this repo — a real
keywords.yaml lists the operator's actual merchants (see CLAUDE.md). rules.py
enforces that on every read and write.

Run:  python tools/rule_editor/editor.py --data-dir ../wallet-watch-data
See tools/rule_editor/README.md.
"""

import argparse
import copy
import csv
import os
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import dearpygui.dearpygui as dpg

from preview import build_entries, changed, group_by_category, uncategorized
from rules import (
    DEFAULT_COLUMNS, MATCH_MODES, OPS, Condition, Rule, load_rules, match_rule,
    save_rules, validate_rule,
)


def _rgb(value: str) -> List[int]:
    value = value.lstrip("#")
    return [int(value[i:i + 2], 16) for i in (0, 2, 4)]


# Claude-ish warm palette.
BG = _rgb("#FAF9F5")
SURFACE = _rgb("#FFFFFF")
TEXT = _rgb("#3D3929")
MUTED = _rgb("#83827D")
ACCENT = _rgb("#D97757")
ACCENT_ACTIVE = _rgb("#C4623F")
BORDER = _rgb("#E5E4DF")
DANGER = _rgb("#B54A34")
WHITE = [255, 255, 255]
# a touch darker than BORDER so the table header reads as a header, not a row
TABLE_HEADER = _rgb("#EAE8E1")

MAIN = "main_window"
RULES_BOX = "rules_box"
SAVE_BTN = "save_button"
PREVIEW_BTN = "preview_button"
DIRTY_TXT = "dirty_text"
FOOTER_TXT = "footer_text"
ALERT = "alert_modal"
PANEL = "panel"
# A custom tab strip, not dpg.tab_bar: the built-in bar has no scroll fitting
# policy in the 1.10 API, so with many categories it shrinks every tab to an
# unreadable ~23px nub. This is a horizontally-scrolling row of buttons instead.
PANEL_TABSTRIP = "panel_tabstrip"
PANEL_CONTENT = "panel_content"
TABSTRIP_H = 52

# Rebuild the panel this long after the last keystroke rather than on each one.
# Measured on a real 1396-row csv: the pure diff is ~2ms, but building the table
# widgets costs 28-45ms, which is a visible hitch on every character — and the
# first letter of a keyword is exactly when it matches the most rows. Waiting
# for a pause makes typing free and still feels live.
PANEL_DEBOUNCE_S = 0.2

# Transactions panel. 680 leaves ~700 for the rules column at the default 1400
# viewport, which is what a card needs once laid out tightly.
PANEL_W = 680

# Rule card geometry. Cards get an EXPLICIT height because dearpygui's
# child_window autosize_y is broken for stacked siblings: the first card
# expands to fill the scroll area and every one after it collapses to ~4px.
# These track the theme below — a widget is font (17) + 2x FramePadding.y (5),
# and ROW_GAP is ItemSpacing.y.
ROW_H = 27
ROW_GAP = 6
CARD_PAD_Y = 12

# tab keys; categories use CAT_PREFIX + name so they can't collide with these
KEY_CHANGED = "__changed__"
KEY_UNCAT = "__uncategorized__"
CAT_PREFIX = "cat:"

DASH = "—"

# Description cells get a hover tooltip with the full text when they're long
# enough to clip in their column. get_text_size is None before the first frame
# and the panel builds synchronously on toggle, so the clip point is estimated
# in characters from the column weights rather than measured in pixels.
DESC_CELL_INDEX = 3          # date, amount, account, description, ...
DESC_PX_PER_CHAR = 8         # ~17px font measures ~8px/char (probed)
DESC_TOOLTIP_WRAP = 420

EMPTY_MESSAGE = {
    KEY_CHANGED: "no changes vs the saved rules",
    KEY_UNCAT: "every row matches a rule",
}

# candidates tried in order; DPG's built-in font is tiny on a retina display
FONT_CANDIDATES = [
    "/System/Library/Fonts/SFNS.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/Library/Fonts/Arial.ttf",
]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--data-dir", type=Path, default=None,
                   help="data root holding rules/keywords.yaml; "
                        "defaults to $WALLET_WATCH_DATA_DIR")
    p.add_argument("--rules", type=Path, default=None,
                   help="explicit path to a rules yaml (overrides --data-dir)")
    p.add_argument("--preview-csv", type=Path, default=None,
                   help="normalized.csv to count matches against; "
                        "defaults to the newest batch/*/normalized.csv under the data root")
    return p.parse_args()


def resolve_data_dir(arg: Optional[Path]) -> Optional[Path]:
    # --data-dir, then $WALLET_WATCH_DATA_DIR. Unlike the pipeline this may come
    # back None: --rules alone is a complete answer, so the caller decides
    # whether a missing root is fatal.
    if arg:
        return arg
    env = os.environ.get("WALLET_WATCH_DATA_DIR")
    return Path(env) if env else None


def resolve_rules_path(args: argparse.Namespace, data_dir: Optional[Path]) -> Path:
    if args.rules:
        return args.rules
    if data_dir is None:
        raise SystemExit("no data root: pass --data-dir or set WALLET_WATCH_DATA_DIR "
                         "(or point --rules at a rules yaml)")
    if not data_dir.is_dir():
        raise SystemExit(f"data root does not exist: {data_dir}")
    return data_dir / "rules" / "keywords.yaml"


def resolve_preview_csv(args: argparse.Namespace, data_dir: Optional[Path]) -> Optional[Path]:
    if args.preview_csv:
        if not args.preview_csv.is_file():
            raise SystemExit(f"preview csv not found: {args.preview_csv}")
        return args.preview_csv
    if data_dir is None:
        return None
    # batch ids are YYYYMMDD-YYYYMMDD, so a name sort is chronological
    found = sorted((data_dir / "batch").glob("*/normalized.csv"))
    return found[-1] if found else None


def load_preview(path: Optional[Path]) -> Tuple[List[Dict[str, str]], List[str]]:
    """Read the preview CSV read-only into memory. Returns (rows, columns)."""
    # No preview is a normal state: the editor must work before any batch has
    # been normalized. Counts are simply hidden.
    if path is None:
        return [], list(DEFAULT_COLUMNS)
    with path.open(newline="") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
        # the CSV header IS the column list — more accurate than any constant,
        # and it keeps this tool decoupled from src/schema.py
        columns = list(reader.fieldnames or DEFAULT_COLUMNS)
    return rows, columns


def init_theme() -> str:
    with dpg.theme() as theme:
        with dpg.theme_component(dpg.mvAll):
            dpg.add_theme_color(dpg.mvThemeCol_WindowBg, BG)
            dpg.add_theme_color(dpg.mvThemeCol_ChildBg, SURFACE)
            dpg.add_theme_color(dpg.mvThemeCol_PopupBg, SURFACE)
            dpg.add_theme_color(dpg.mvThemeCol_Text, TEXT)
            dpg.add_theme_color(dpg.mvThemeCol_Border, BORDER)
            dpg.add_theme_color(dpg.mvThemeCol_FrameBg, BG)
            dpg.add_theme_color(dpg.mvThemeCol_FrameBgHovered, BORDER)
            dpg.add_theme_color(dpg.mvThemeCol_FrameBgActive, BORDER)
            dpg.add_theme_color(dpg.mvThemeCol_Button, BG)
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, BORDER)
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, BORDER)
            dpg.add_theme_color(dpg.mvThemeCol_Header, BORDER)
            dpg.add_theme_color(dpg.mvThemeCol_HeaderHovered, BORDER)
            dpg.add_theme_color(dpg.mvThemeCol_HeaderActive, BORDER)
            dpg.add_theme_color(dpg.mvThemeCol_ScrollbarBg, BG)
            dpg.add_theme_color(dpg.mvThemeCol_ScrollbarGrab, BORDER)
            dpg.add_theme_color(dpg.mvThemeCol_ScrollbarGrabHovered, MUTED)
            dpg.add_theme_color(dpg.mvThemeCol_Separator, BORDER)
            # Tables default to ImGui's dark palette, so without these the
            # preview header renders black on the light theme. Header text stays
            # TEXT (dark), so the header needs a light fill; rows alternate
            # surface/bg for a subtle zebra with row_background=True.
            dpg.add_theme_color(dpg.mvThemeCol_TableHeaderBg, TABLE_HEADER)
            dpg.add_theme_color(dpg.mvThemeCol_TableBorderStrong, BORDER)
            dpg.add_theme_color(dpg.mvThemeCol_TableBorderLight, BORDER)
            dpg.add_theme_color(dpg.mvThemeCol_TableRowBg, SURFACE)
            dpg.add_theme_color(dpg.mvThemeCol_TableRowBgAlt, BG)
            dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 6)
            dpg.add_theme_style(dpg.mvStyleVar_ChildRounding, 10)
            dpg.add_theme_style(dpg.mvStyleVar_PopupRounding, 8)
            dpg.add_theme_style(dpg.mvStyleVar_FramePadding, 8, 5)
            dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing, 8, 6)
            dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, 16, 12)
            dpg.add_theme_style(dpg.mvStyleVar_ChildBorderSize, 1)
    dpg.bind_theme(theme)
    return theme


def accent_theme() -> str:
    with dpg.theme() as theme:
        with dpg.theme_component(dpg.mvButton):
            dpg.add_theme_color(dpg.mvThemeCol_Button, ACCENT)
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, ACCENT_ACTIVE)
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, ACCENT_ACTIVE)
            dpg.add_theme_color(dpg.mvThemeCol_Text, WHITE)
        # greyed out until something actually differs from disk
        with dpg.theme_component(dpg.mvButton, enabled_state=False):
            dpg.add_theme_color(dpg.mvThemeCol_Button, BORDER)
            dpg.add_theme_color(dpg.mvThemeCol_Text, MUTED)
    return theme


def tab_themes() -> Tuple[str, str]:
    """(active, inactive) themes for the tab-strip buttons."""
    with dpg.theme() as active:
        with dpg.theme_component(dpg.mvButton):
            dpg.add_theme_color(dpg.mvThemeCol_Button, ACCENT)
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, ACCENT_ACTIVE)
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, ACCENT_ACTIVE)
            dpg.add_theme_color(dpg.mvThemeCol_Text, WHITE)
    with dpg.theme() as inactive:
        with dpg.theme_component(dpg.mvButton):
            dpg.add_theme_color(dpg.mvThemeCol_Button, SURFACE)
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, BORDER)
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, BORDER)
            dpg.add_theme_color(dpg.mvThemeCol_Text, MUTED)
    return active, inactive


def init_font() -> None:
    # DPG's built-in font is tiny on a retina display. A missing font must never
    # be fatal — fall back to scaling the built-in one.
    for candidate in FONT_CANDIDATES:
        if not Path(candidate).is_file():
            continue
        try:
            with dpg.font_registry():
                dpg.bind_font(dpg.add_font(candidate, 17))
            return
        except Exception:
            continue
    dpg.set_global_font_scale(1.3)


def alert(title: str, message: str) -> None:
    if dpg.does_item_exist(ALERT):
        dpg.delete_item(ALERT)
    with dpg.window(label=title, modal=True, tag=ALERT, width=620, height=320,
                    pos=(180, 160), no_resize=True):
        dpg.add_text(message, wrap=580)
        dpg.add_spacer(height=8)
        dpg.add_button(label="OK", width=90, callback=lambda: dpg.delete_item(ALERT))


class RuleEditor:
    def __init__(self, rules_path: Path, rules: List[Rule],
                 rows: List[Dict[str, str]], columns: List[str]):
        self.rules_path = rules_path
        self.rows, self.columns = rows, columns

        # The model is a plain List[Rule]; widgets are rendered from it and
        # write back through callbacks. Structural edits re-render; typing
        # never does, or the focused input would die mid-keystroke.
        # Files are read before the window exists (see main), so a guard trip
        # or a malformed yaml is a clean CLI error, not a half-drawn window.
        self.rules: List[Rule] = rules
        self.baseline: List[Rule] = copy.deepcopy(self.rules)
        self.count_tags: List[int] = []
        self.accent = accent_theme()
        self.tab_active, self.tab_inactive = tab_themes()
        # ROW_H is derived from the theme's font size, but init_font falls back
        # to a scaled built-in font when the system ones are missing. Measure a
        # real row on the first frame rather than trust the arithmetic — cards
        # have no scrollbar, so a short card clips instead of complaining.
        self.row_h = ROW_H
        self.calibrated = False

        # transactions panel state
        self.panel_open = False
        self.active_tab = KEY_CHANGED
        self.tab_buttons: Dict[str, int] = {}   # tab key -> strip button id
        self.tab_entries: Dict[str, List] = {}  # tab key -> rows to render
        # rules as of the last panel render; None = never rendered
        self.panel_snapshot: Optional[List[Rule]] = None
        # when the panel first went out of date; None = up to date. tick() turns
        # this into a rebuild once typing pauses.
        self.panel_dirty_at: Optional[float] = None

    # --- layout ---

    def build(self) -> None:
        with dpg.window(tag=MAIN):
            with dpg.group(horizontal=True):
                dpg.add_text(str(self.rules_path))
                dpg.add_text("", tag=DIRTY_TXT, color=ACCENT)
                dpg.add_spacer(width=12)
                # read-only, so unlike Save it doesn't depend on dirty state —
                # but with no csv there is nothing to show
                dpg.add_button(label="Preview", tag=PREVIEW_BTN, width=100,
                               enabled=bool(self.rows), callback=self.toggle_panel)
                dpg.add_button(label="Save", tag=SAVE_BTN, width=90, enabled=False,
                               callback=self.save)
                dpg.bind_item_theme(SAVE_BTN, self.accent)
            dpg.add_separator()
            with dpg.group(horizontal=True):
                # negative height = fill the window minus the footer; scrolls itself
                dpg.add_child_window(tag=RULES_BOX, width=-1, height=-38, border=False)
                with dpg.child_window(tag=PANEL, width=PANEL_W, height=-38, show=False):
                    # horizontal_scrollbar: the tab strip scrolls sideways when
                    # there are more categories than fit; the content area below
                    # holds only the active tab's table.
                    dpg.add_child_window(tag=PANEL_TABSTRIP, height=TABSTRIP_H,
                                         horizontal_scrollbar=True, border=False)
                    dpg.add_child_window(tag=PANEL_CONTENT, height=-1, border=False)
            dpg.add_text("", tag=FOOTER_TXT, color=MUTED)
        dpg.set_primary_window(MAIN, True)
        self.render()

    # --- rendering ---

    def render(self, *_) -> None:
        """Rebuild every card from the model. Called only on structural edits."""
        dpg.delete_item(RULES_BOX, children_only=True)
        self.count_tags = []

        for i, rule in enumerate(self.rules):
            self._render_rule(i, rule)

        dpg.add_button(label="+ rule", parent=RULES_BOX, callback=self.add_rule, width=110)
        # structural edits and saves land here, so the panel follows them for
        # free; only typing leaves it stale (see _refresh_stale)
        self._render_panel()
        self.refresh()

    def _schedule_render(self) -> None:
        # Never restructure inside the callback of a widget being deleted —
        # defer a frame so DPG has finished with the item that fired.
        dpg.set_frame_callback(dpg.get_frame_count() + 1, self.render)

    def _card_height(self, rule: Rule) -> int:
        # header row + one row per condition + the "+ condition" row.
        rows = 2 + len(rule.conditions)
        return 2 * CARD_PAD_Y + rows * self.row_h + (rows - 1) * ROW_GAP + 2

    def _calibrate(self) -> None:
        """Measure a real rendered row once, and re-render if ROW_H was wrong."""
        self.calibrated = True
        cards = [k for k in dpg.get_item_children(RULES_BOX, 1)
                 if dpg.get_item_type(k) == "mvAppItemType::mvChildWindow"]
        if not cards:
            return
        groups = dpg.get_item_children(cards[0], 1)
        if not groups:
            return
        measured = int(dpg.get_item_rect_size(groups[0])[1])
        if measured > 0 and abs(measured - self.row_h) > 1:
            self.row_h = measured
            self.render()

    def _render_rule(self, index: int, rule: Rule) -> None:
        with dpg.child_window(parent=RULES_BOX, autosize_x=True, border=True,
                              height=self._card_height(rule), no_scrollbar=True):
            with dpg.group(horizontal=True):
                dpg.add_text(f"{index + 1}", color=MUTED)
                dpg.add_button(label="^", width=26, user_data=index,
                               callback=lambda s, a, u: self.move(u, -1))
                dpg.add_button(label="v", width=26, user_data=index,
                               callback=lambda s, a, u: self.move(u, 1))
                dpg.add_input_text(default_value=rule.category, width=170, user_data=index,
                                   hint="category", callback=self._on_category)
                dpg.add_combo(items=list(MATCH_MODES), default_value=rule.match, width=70,
                              user_data=index, callback=self._on_match)
                dpg.add_spacer(width=4)
                self.count_tags.append(dpg.add_text("", color=MUTED))
                dpg.add_spacer(width=4)
                dpg.add_button(label="x", width=26, user_data=index,
                               callback=lambda s, a, u: self.delete_rule(u))

            for j, cond in enumerate(rule.conditions):
                self._render_condition(index, j, cond)

            with dpg.group(horizontal=True):
                dpg.add_spacer(width=20)
                dpg.add_button(label="+ condition", width=110, user_data=index,
                               callback=lambda s, a, u: self.add_condition(u))

    def _render_condition(self, ri: int, ci: int, cond: Condition) -> None:
        with dpg.group(horizontal=True):
            dpg.add_spacer(width=20)
            # DPG combos aren't editable, so a column already in the yaml that
            # this CSV doesn't have still has to be selectable — otherwise
            # opening the editor would silently rewrite it. Union it in.
            items = sorted(set(self.columns) | {cond.column}) if cond.column else self.columns
            dpg.add_combo(items=items, default_value=cond.column, width=180,
                          user_data=(ri, ci, "column"), callback=self._on_cond)
            dpg.add_combo(items=list(OPS), default_value=cond.op, width=115,
                          user_data=(ri, ci, "op"), callback=self._on_cond)
            dpg.add_input_text(default_value=cond.value, width=230, hint="keyword",
                               user_data=(ri, ci, "value"), callback=self._on_cond)
            dpg.add_button(label="x", width=26, user_data=(ri, ci),
                           callback=lambda s, a, u: self.delete_condition(*u))

    # --- transactions panel ---

    def toggle_panel(self, *_) -> None:
        self.panel_open = not self.panel_open
        dpg.configure_item(PANEL, show=self.panel_open)
        # the rules column gives up exactly the panel's width plus the gap
        dpg.configure_item(RULES_BOX, width=-(PANEL_W + 8) if self.panel_open else -1)
        if self.panel_open:
            self._render_panel()
        self.refresh()

    def tick(self) -> None:
        """Called once per frame from the render loop. Fires the debounce."""
        if not self.calibrated:
            self._calibrate()
        if self.panel_dirty_at is None:
            return
        if time.perf_counter() - self.panel_dirty_at < PANEL_DEBOUNCE_S:
            return
        self.panel_dirty_at = None
        self._render_panel()

    def _tab_specs(self) -> List[Tuple[str, str, List]]:
        """(key, label, entries) for every tab, in display order."""
        entries = build_entries(self.baseline, self.rules, self.rows)
        changed_rows = changed(entries)
        uncat_rows = uncategorized(entries)
        specs = [
            (KEY_CHANGED, f"Changed ({len(changed_rows)})", changed_rows),
            (KEY_UNCAT, f"Uncategorized ({len(uncat_rows)})", uncat_rows),
        ]
        # biggest categories leftmost; ties break on name so tabs don't reshuffle
        for name, rows in group_by_category(entries):
            specs.append((CAT_PREFIX + name, f"{name} ({len(rows)})", rows))
        return specs

    def _render_panel(self) -> None:
        if not self.panel_open:
            return
        specs = self._tab_specs()
        self.tab_entries = {key: rows for key, _, rows in specs}
        keys = [key for key, _, _ in specs]
        # the active tab can vanish when a category stops existing
        if self.active_tab not in keys:
            self.active_tab = keys[0]

        # rebuild the tab strip: one button per tab in a horizontal row that
        # overflows into the strip's own horizontal scrollbar
        dpg.delete_item(PANEL_TABSTRIP, children_only=True)
        self.tab_buttons = {}
        with dpg.group(horizontal=True, parent=PANEL_TABSTRIP):
            for key, label, _ in specs:
                btn = dpg.add_button(label=label, user_data=key, callback=self._on_tab)
                dpg.bind_item_theme(
                    btn, self.tab_active if key == self.active_tab else self.tab_inactive)
                self.tab_buttons[key] = btn

        self._render_content()
        self.panel_snapshot = copy.deepcopy(self.rules)
        self.panel_dirty_at = None

    def _render_content(self) -> None:
        # Only the active tab's table exists at a time. dpg's table clipper skips
        # drawing off-screen rows but not their widget creation, so building
        # every tab would mean tens of thousands of text items on a 1400-row csv.
        dpg.delete_item(PANEL_CONTENT, children_only=True)
        self._render_table(PANEL_CONTENT, self.active_tab,
                           self.tab_entries.get(self.active_tab, []))

    def _on_tab(self, sender, app_data, user_data) -> None:
        if user_data == self.active_tab:
            return
        # Only re-theme the two affected buttons and swap the content — never
        # rebuild the strip here, which would delete the button mid-callback.
        prev = self.tab_buttons.get(self.active_tab)
        if prev is not None:
            dpg.bind_item_theme(prev, self.tab_inactive)
        dpg.bind_item_theme(sender, self.tab_active)
        self.active_tab = user_data
        self._render_content()

    def _columns(self, key: str) -> List[Tuple[str, float]]:
        # `from -> to` is noise on tabs where nothing changed; the description
        # carries the signal, so it takes the weight everywhere.
        if key == KEY_CHANGED:
            return [("date", 0.8), ("amount", 0.6), ("account", 0.8),
                    ("description", 2.6), ("from", 0.9), ("to", 0.9), ("rule", 0.5)]
        if key == KEY_UNCAT:
            return [("date", 0.8), ("amount", 0.6), ("account", 0.8), ("description", 3.8)]
        return [("date", 0.8), ("amount", 0.6), ("account", 0.8),
                ("description", 3.2), ("rule", 0.5)]

    def _cells(self, key: str, entry) -> List[str]:
        row = entry.row
        cells = [row.get("date", ""), row.get("amount", ""), row.get("account", ""),
                 row.get("original_description", "")]
        # rule cards are numbered from 1, so "#3" points straight at one
        rule = f"#{entry.rule_index + 1}" if entry.rule_index is not None else DASH
        if key == KEY_CHANGED:
            return cells + [entry.old or DASH, entry.new or DASH, rule]
        if key == KEY_UNCAT:
            return cells
        return cells + [rule]

    def _desc_clip_chars(self, key: str) -> int:
        # Rough chars that fit in this tab's description column. The panel width
        # is taken as nominal; resizing wider just shows a few needless
        # tooltips, narrower risks missing one — both minor. Bias toward showing.
        cols = self._columns(key)
        total = sum(w for _, w in cols)
        desc_w = dict(cols).get("description", total)
        usable = PANEL_W - 32     # scrollbar + cell padding, approx
        return int(usable * desc_w / total / DESC_PX_PER_CHAR) - 2

    def _render_table(self, parent: int, key: str, entries: List) -> None:
        if not entries:
            dpg.add_text(EMPTY_MESSAGE.get(key, "no rows"), parent=parent, color=MUTED)
            return
        clip_at = self._desc_clip_chars(key)
        with dpg.table(parent=parent, header_row=True, scrollY=True, freeze_rows=1,
                       row_background=True, resizable=True, borders_innerV=True,
                       borders_innerH=True, policy=dpg.mvTable_SizingStretchProp,
                       height=-1):
            for label, weight in self._columns(key):
                dpg.add_table_column(label=label, init_width_or_weight=weight)
            for entry in entries:
                cells = self._cells(key, entry)
                with dpg.table_row():
                    for ci, cell in enumerate(cells):
                        txt = dpg.add_text(cell)
                        # full text on hover only when it's too long to fit,
                        # so short descriptions don't pop a redundant tooltip
                        if ci == DESC_CELL_INDEX and len(cell) > clip_at:
                            with dpg.tooltip(txt):
                                dpg.add_text(cell, wrap=DESC_TOOLTIP_WRAP)

    def _arm_panel(self) -> None:
        # Re-arm from the LAST edit, so a burst of typing collapses into one
        # rebuild once it stops. Structural edits re-render outright and clear
        # the snapshot, so they never reach here.
        if not self.panel_open or self.panel_snapshot is None:
            return
        if self.rules != self.panel_snapshot:
            self.panel_dirty_at = time.perf_counter()

    # --- model edits (no re-render: keep focus while typing) ---

    def _on_category(self, sender, app_data, user_data) -> None:
        self.rules[user_data].category = app_data
        self.refresh()

    def _on_match(self, sender, app_data, user_data) -> None:
        self.rules[user_data].match = app_data
        self.refresh()

    def _on_cond(self, sender, app_data, user_data) -> None:
        ri, ci, field = user_data
        setattr(self.rules[ri].conditions[ci], field, app_data)
        self.refresh()

    # --- structural edits (re-render) ---

    def add_rule(self, *_) -> None:
        self.rules.append(Rule(category="", match="all",
                               conditions=[Condition(column="original_description",
                                                     op="contains", value="")]))
        self._schedule_render()

    def delete_rule(self, index: int) -> None:
        del self.rules[index]
        self._schedule_render()

    def move(self, index: int, delta: int) -> None:
        target = index + delta
        if not 0 <= target < len(self.rules):
            return
        self.rules[index], self.rules[target] = self.rules[target], self.rules[index]
        self._schedule_render()

    def add_condition(self, index: int) -> None:
        self.rules[index].conditions.append(
            Condition(column="original_description", op="contains", value=""))
        self._schedule_render()

    def delete_condition(self, ri: int, ci: int) -> None:
        del self.rules[ri].conditions[ci]
        self._schedule_render()

    # --- live state ---

    def is_dirty(self) -> bool:
        # dataclass equality: structural compare against what's on disk
        return self.rules != self.baseline

    def refresh(self) -> None:
        """Update dirty state + match counts. Never rebuilds widgets."""
        dirty = self.is_dirty()
        dpg.set_value(DIRTY_TXT, "* unsaved" if dirty else "")
        dpg.configure_item(SAVE_BTN, enabled=dirty)
        self._refresh_counts()
        self._arm_panel()

    def _refresh_counts(self) -> None:
        if not self.rows:
            dpg.set_value(FOOTER_TXT, "no preview csv — match counts unavailable")
            return

        won, matched, uncategorized = self._tally()
        for i, tag in enumerate(self.count_tags):
            if i >= len(won):
                continue
            # "rows won" (first match), not "rows matched": a rule that matches
            # 50 rows but wins none is fully shadowed by one above it, which is
            # exactly the failure plain-text editing hides.
            if won[i] == 0 and matched[i] > 0:
                dpg.set_value(tag, f"0 of {matched[i]} · shadowed")
                dpg.configure_item(tag, color=DANGER)
            else:
                dpg.set_value(tag, f"{won[i]} rows")
                dpg.configure_item(tag, color=MUTED)

        total = len(self.rows)
        dpg.set_value(FOOTER_TXT,
                      f"{total - uncategorized} / {total} rows matched  -  "
                      f"{uncategorized} uncategorized")

    def _tally(self) -> Tuple[List[int], List[int], int]:
        won = [0] * len(self.rules)
        matched = [0] * len(self.rules)
        uncategorized = 0
        for row in self.rows:
            first = None
            for i, rule in enumerate(self.rules):
                # every rule is tested against every row (no short-circuit) so
                # `matched` can expose shadowing; ~1300 rows makes this free
                if match_rule(rule, row):
                    matched[i] += 1
                    if first is None:
                        first = i
            if first is None:
                uncategorized += 1
            else:
                won[first] += 1
        return won, matched, uncategorized

    # --- save ---

    def save(self, *_) -> None:
        problems = []
        for i, rule in enumerate(self.rules, start=1):
            for problem in validate_rule(rule):
                problems.append(f"rule {i} ({rule.category or 'unnamed'}): {problem}")
        if problems:
            alert("Cannot save", "Fix these first:\n\n" + "\n".join(problems))
            return
        try:
            save_rules(self.rules_path, self.rules)
        except SystemExit as e:
            # the in-repo guard raises SystemExit; inside a GUI callback that
            # would be swallowed, so surface it as a modal instead
            alert("Cannot save", str(e))
            return
        except OSError as e:
            alert("Cannot save", f"{self.rules_path}: {e}")
            return
        # reload from disk so the baseline is what was actually written
        self.rules = load_rules(self.rules_path)
        self.baseline = copy.deepcopy(self.rules)
        self._schedule_render()

    def on_exit(self) -> None:
        # DPG cannot veto a viewport close, so a "discard changes?" prompt is
        # not possible. Rescue the work instead of dropping it silently.
        if not self.is_dirty():
            return
        rescue = self.rules_path.with_suffix(self.rules_path.suffix + ".autosave")
        try:
            save_rules(rescue, self.rules)
        except (SystemExit, OSError) as e:
            print(f"unsaved changes lost: {e}", file=sys.stderr)
            return
        print(f"closed with unsaved changes -> {rescue}", file=sys.stderr)


def main() -> None:
    args = parse_args()
    data_dir = resolve_data_dir(args.data_dir)
    rules_path = resolve_rules_path(args, data_dir)
    preview = resolve_preview_csv(args, data_dir)

    # Read everything BEFORE opening a window: the in-repo guard and any yaml
    # error must surface as a plain CLI failure, not from behind a GUI.
    rules = load_rules(rules_path)
    rows, columns = load_preview(preview)
    print(f"editing {rules_path} ({len(rules)} rules)", file=sys.stderr)
    print(f"preview: {preview or 'none — match counts hidden'}", file=sys.stderr)

    dpg.create_context()
    init_theme()
    init_font()
    editor = RuleEditor(rules_path, rules, rows, columns)
    editor.build()
    # clear_color is 0-255, like every other dpg colour.
    # 1400 leaves the rules column ~700 with the panel open, which fits a card.
    dpg.create_viewport(title="wallet-watch — categorization rules",
                        width=1400, height=820, clear_color=BG + [255])
    dpg.setup_dearpygui()
    dpg.show_viewport()

    # The manual loop (rather than start_dearpygui) exists to give tick() a
    # per-frame hook for the panel debounce. It also lets on_exit run as a plain
    # statement instead of a viewport callback.
    while dpg.is_dearpygui_running():
        editor.tick()
        dpg.render_dearpygui_frame()
    editor.on_exit()
    dpg.destroy_context()


if __name__ == "__main__":
    main()
