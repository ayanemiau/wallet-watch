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
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

# The tier-3a rule engine (rules.py) is the pipeline's canonical copy in lib/;
# both this editor and lib/categorizer.py import it so they categorize
# identically. Put lib/ on the path before importing it (preview.py imports it
# too). See plan.md §5.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "lib"))
# tools/ holds tool_theme (the palette + macOS light/dark detection shared with
# tools/review_approver).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import dearpygui.dearpygui as dpg  # noqa: E402

import preview  # noqa: E402
import tool_theme  # noqa: E402
from preview import build_entries, changed, group_by_category, uncategorized  # noqa: E402
from rules import (  # noqa: E402
    DEFAULT_COLUMNS, MATCH_MODES, OPS, Condition, Rule, load_rules, match_rule,
    save_rules, validate_rule,
)


# The Claude-ish warm palette lives in tool_theme (shared with review_approver).
# Load the light values into module scope as the bare names the theme builders
# and inline `color=` args reference (BG, SURFACE, TEXT, MUTED, ACCENT,
# ACCENT_ACTIVE, BORDER, DANGER, WHITE, TABLE_HEADER). main() re-applies the
# resolved palette before building, and apply_theme() swaps it live on toggle.
globals().update(tool_theme.LIGHT)

MAIN = "main_window"
RULES_BOX = "rules_box"
SAVE_BTN = "save_button"
PREVIEW_BTN = "preview_button"
THEME_BTN = "theme_button"
DIRTY_TXT = "dirty_text"
FOOTER_TXT = "footer_text"
ALERT = "alert_modal"
PANEL = "panel"
# The tab strip holds two fixed buttons (Changed, Uncategorized) plus a single
# Categories combo. It replaced a per-category row of buttons: the operator uses
# ~20 `<main>/<sub>` categories, and a scrolling row of that many tabs is worse
# to navigate than a dropdown.
PANEL_TABSTRIP = "panel_tabstrip"
# The filter bar sits between the tab strip and the table: Account / Amount
# multi-select popups + a header-click sort, all a pure view over the snapshot.
PANEL_FILTERBAR = "panel_filterbar"
FILTER_COUNT_TXT = "filter_count_text"
PANEL_CONTENT = "panel_content"
TABSTRIP_H = 52
FILTERBAR_H = 40

# Rule search: an input whose text filters the rule cards by category, with a
# floating autocomplete dropdown of existing categories (mirrors the category
# autocomplete in tools/review_approver).
RULE_SEARCH = "rule_search"
SUGGEST = "rule_suggest"        # the shared floating candidate list
SUGGEST_W = 240
SUGGEST_H = 260                 # bounded; the list scrolls past this
SUGGEST_MAX = 60                # cap the rendered rows

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

# Amount-filter radio options (label shown, kind from preview.py). Order matters:
# it's the on-screen order and the radio's item list.
AMOUNT_OPTIONS = [
    ("All", preview.AMOUNT_ALL),
    ("< 100", preview.AMOUNT_LT100),
    ("100–500", preview.AMOUNT_MID),
    ("> 500", preview.AMOUNT_GT500),
    ("Custom", preview.AMOUNT_CUSTOM),
]
AMOUNT_KIND_BY_LABEL = {label: kind for label, kind in AMOUNT_OPTIONS}
AMOUNT_LABEL_BY_KIND = {kind: label for label, kind in AMOUNT_OPTIONS}


def _parse_decimal(text: str) -> Optional[Decimal]:
    """A custom-range bound: blank or non-numeric means an open (unset) bound."""
    text = text.strip()
    if not text:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None

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
                   help="normalized CSV to count matches against; "
                        "defaults to the newest batch/*/normalized_*.csv under the data root")
    p.add_argument("--theme", choices=["auto", "light", "dark"], default="auto",
                   help="auto follows the macOS setting; the in-app button toggles after launch")
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
    # normalize writes normalized_<YYYYMMDD>_<HHMMSS>.csv per run; batch ids are
    # YYYYMMDD-YYYYMMDD, so a path sort orders by batch then run timestamp and
    # the last is the newest run of the newest batch.
    found = sorted((data_dir / "batch").glob("*/normalized_*.csv"))
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
        # and it keeps this tool decoupled from lib/schema.py
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


def combo_tab_themes() -> Tuple[str, str]:
    """(active, inactive) themes for the Categories combo in the tab strip.

    A combo needs its own theme: tab_themes targets mvButton, which does not
    touch a combo's box. Only the closed box is coloured here (FrameBg + the
    dropdown arrow) — Text is left at the default dark on purpose. Theming a
    combo also themes its popup list, and white-on-surface items would vanish.
    """
    with dpg.theme() as active:
        with dpg.theme_component(dpg.mvCombo):
            dpg.add_theme_color(dpg.mvThemeCol_FrameBg, ACCENT)
            dpg.add_theme_color(dpg.mvThemeCol_FrameBgHovered, ACCENT_ACTIVE)
            dpg.add_theme_color(dpg.mvThemeCol_FrameBgActive, ACCENT_ACTIVE)
            dpg.add_theme_color(dpg.mvThemeCol_Button, ACCENT_ACTIVE)
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, ACCENT_ACTIVE)
    with dpg.theme() as inactive:
        with dpg.theme_component(dpg.mvCombo):
            dpg.add_theme_color(dpg.mvThemeCol_FrameBg, SURFACE)
            dpg.add_theme_color(dpg.mvThemeCol_FrameBgHovered, BORDER)
    return active, inactive


def init_font() -> None:
    # DPG's built-in font is tiny on a retina display. A missing font must never
    # be fatal — fall back to scaling the built-in one.
    # A font loads only basic Latin by default, so glyphs beyond it render as a
    # "not found" box (a question-mark-looking tofu). Load the few we use: the
    # down-triangle on the filter buttons and the en/em dashes.
    extra = [0x2013, 0x2014, 0x25BC]
    for candidate in FONT_CANDIDATES:
        if not Path(candidate).is_file():
            continue
        try:
            with dpg.font_registry():
                with dpg.font(candidate, 17) as font:
                    dpg.add_font_range_hint(dpg.mvFontRangeHint_Default)
                    dpg.add_font_chars(extra)
                dpg.bind_font(font)
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
        # rule index -> its count-text id; a dict (not a list) so the cards can be
        # rendered filtered without the index mapping drifting off the model.
        self.count_tags: Dict[int, int] = {}
        self.accent = accent_theme()
        self.tab_active, self.tab_inactive = tab_themes()
        self.combo_active, self.combo_inactive = combo_tab_themes()
        # ROW_H is derived from the theme's font size, but init_font falls back
        # to a scaled built-in font when the system ones are missing. Measure a
        # real row on the first frame rather than trust the arithmetic — cards
        # have no scrollbar, so a short card clips instead of complaining.
        self.row_h = ROW_H
        self.calibrated = False

        # theming: main() sets theme_name (the resolved "light"/"dark") and
        # global_theme (the bound theme id) so apply_theme can rebuild + rebind
        # on the toggle. The per-item themes above already read the palette main
        # applied before this object was constructed.
        self.theme_name = "light"
        self.global_theme: Optional[int] = None

        # transactions panel state. The panel is always visible when a CSV is
        # loaded (never toggled); Preview is a manual refresh, not a show/hide.
        self.panel_shown = bool(self.rows)
        self.active_tab = KEY_CHANGED
        self.tab_buttons: Dict[str, int] = {}   # fixed tab key -> strip button id
        self.cat_combo: Optional[int] = None    # the Categories combo, or None
        self.cat_choice_keys: Dict[str, str] = {}  # combo label -> "cat:<name>"
        self.tab_entries: Dict[str, List] = {}  # tab key -> rows to render
        # rules as of the last Preview; None = never previewed. Drives the stale
        # marker: when self.rules diverges from this, a refresh is pending.
        self.panel_snapshot: Optional[List[Rule]] = None

        # panel filter/sort — a pure view over the snapshot (see preview.py),
        # session-only, invisible to categorization. Accounts are tab-independent
        # (from all rows), so the filter bar is built once and never rebuilt.
        self.accounts: List[str] = preview.distinct_accounts(self.rows)
        self.account_selected: Set[str] = set(self.accounts)   # all checked
        self.amount_kind: str = preview.AMOUNT_ALL
        self.amount_lo: Optional[Decimal] = None
        self.amount_hi: Optional[Decimal] = None
        # sort is native (click a header): _on_sort reorders these rows in place
        self._sort_cols: Dict[int, str] = {}   # table column id -> label
        self._row_entries: List[Tuple[int, "PreviewEntry"]] = []  # (row id, entry)
        # filter-bar widget ids, set in _build_filterbar (needed by Select all /
        # Clear to push values back into the checkboxes and radio)
        self._account_checks: Dict[str, int] = {}
        self._amount_radio: Optional[int] = None
        self._amount_min: Optional[int] = None
        self._amount_max: Optional[int] = None
        # radio labels carry a per-option `(n)` count, so map a clicked label back
        # to its kind by position rather than by text
        self._amount_labels: List[str] = [label for label, _ in AMOUNT_OPTIONS]

        # rule search: rule_query filters which cards render; the ac_* state drives
        # the floating category autocomplete. input_text has no per-keystroke
        # callback, so tick() polls the focused box each frame (as review_approver).
        self.rule_query = ""
        self.ac_input: Optional[int] = None       # the search box while its list is open
        self.ac_query: Optional[str] = None       # last-seen text, for change detection
        self.ac_shown = False                     # is the dropdown visible
        self.ac_pos: Optional[Tuple[int, int]] = None   # where we placed it (viewport coords)
        self.ac_grace: Optional[int] = None       # frame to auto-close at after a blur
        self.ac_registry: Optional[int] = None    # the focus-handler registry

    # --- layout ---

    def build(self) -> None:
        with dpg.window(tag=MAIN):
            with dpg.group(horizontal=True):
                dpg.add_text(str(self.rules_path))
                dpg.add_text("", tag=DIRTY_TXT, color=ACCENT)
                dpg.add_spacer(width=12)
                # A manual refresh of the always-visible panel, not a toggle:
                # the panel shows the last previewed snapshot until this is
                # clicked. Read-only, so unlike Save it ignores dirty state —
                # but with no csv there is nothing to preview.
                dpg.add_button(label="Preview", tag=PREVIEW_BTN, width=110,
                               enabled=bool(self.rows), callback=self.refresh_preview)
                dpg.add_button(label="Save", tag=SAVE_BTN, width=90, enabled=False,
                               callback=self.save)
                dpg.bind_item_theme(SAVE_BTN, self.accent)
                dpg.add_button(label=self._theme_label(), tag=THEME_BTN, width=90,
                               callback=self.toggle_theme)
            dpg.add_separator()
            with dpg.group(horizontal=True):
                # filter the rule cards by category; the floating list under the
                # box autocompletes existing categories (see _bind_autocomplete)
                dpg.add_text("Filter rules by category:", color=MUTED)
                dpg.add_input_text(tag=RULE_SEARCH, hint="type or pick a category", width=240)
                dpg.add_button(label="Clear", width=70, callback=self._clear_rule_search)
            with dpg.group(horizontal=True):
                # the rules column yields the panel's width plus the gap whenever
                # the panel is shown (i.e. whenever a csv is loaded)
                rules_w = -(PANEL_W + 8) if self.panel_shown else -1
                dpg.add_child_window(tag=RULES_BOX, width=rules_w, height=-38, border=False)
                with dpg.child_window(tag=PANEL, width=PANEL_W, height=-38,
                                      show=self.panel_shown):
                    # the strip holds the two fixed tab buttons + the Categories
                    # combo; the filter bar the Account/Amount popups + row count;
                    # the content area below holds only the active table
                    dpg.add_child_window(tag=PANEL_TABSTRIP, height=TABSTRIP_H,
                                         border=False)
                    dpg.add_child_window(tag=PANEL_FILTERBAR, height=FILTERBAR_H,
                                         border=False)
                    dpg.add_child_window(tag=PANEL_CONTENT, height=-1, border=False)
            dpg.add_text("", tag=FOOTER_TXT, color=MUTED)
        dpg.set_primary_window(MAIN, True)
        # the autocomplete list floats over everything, hidden until the search box
        # is focused; no_focus_on_appearing keeps the keyboard in the box so typing
        # keeps filtering.
        with dpg.window(tag=SUGGEST, show=False, no_title_bar=True, no_move=True,
                        no_resize=True, no_collapse=True, no_focus_on_appearing=True,
                        no_scrollbar=False, width=SUGGEST_W, height=SUGGEST_H):
            pass
        self._bind_autocomplete(RULE_SEARCH)
        self.render()
        # the filter bar is static (accounts don't change), so build it once
        if self.panel_shown:
            self._build_filterbar()
        # populate the panel's initial snapshot so it isn't blank on launch
        self._render_panel()

    # --- theming ---

    def _theme_label(self) -> str:
        # the button shows what you'll switch TO
        return "Light" if self.theme_name == "dark" else "Dark"

    def toggle_theme(self, *_) -> None:
        self.apply_theme("light" if self.theme_name == "dark" else "dark")

    def apply_theme(self, name: str) -> None:
        """Swap the whole UI to the light/dark palette live.

        DPG doesn't cascade a palette change to existing widgets, so we reassign
        the module colour constants, rebuild + rebind every theme item, update
        the viewport background (baked in at creation, not the theme), and poke
        the few persistent widgets that carry an inline colour. render() rebuilds
        the cards and the panel rebuilds its table, so their colours follow.
        """
        self.theme_name = name
        globals().update(tool_theme.palette(name))
        # global theme: rebuild + rebind, delete the old to avoid a per-toggle leak
        if self.global_theme is not None:
            dpg.delete_item(self.global_theme)
        self.global_theme = init_theme()
        dpg.set_viewport_clear_color(BG + [255])
        # per-item themes: delete olds, rebuild from the new globals, re-bind
        for t in (self.accent, self.tab_active, self.tab_inactive,
                  self.combo_active, self.combo_inactive):
            dpg.delete_item(t)
        self.accent = accent_theme()
        self.tab_active, self.tab_inactive = tab_themes()
        self.combo_active, self.combo_inactive = combo_tab_themes()
        dpg.bind_item_theme(SAVE_BTN, self.accent)
        # persistent inline-coloured toolbar/footer texts
        dpg.configure_item(DIRTY_TXT, color=ACCENT)
        dpg.configure_item(FOOTER_TXT, color=MUTED)
        dpg.configure_item(THEME_BTN, label=self._theme_label())
        self.render()                 # rebuilds cards; calls refresh() (recolours counts)
        if self.panel_shown:          # tab themes + empty-msg MUTED live in the panel
            self._paint_tabs()
            self._render_content()

    # --- rendering ---

    def render(self, *_) -> None:
        """Rebuild every card from the model. Called only on structural edits.

        This deliberately does NOT refresh the panel: the panel is a snapshot
        the user refreshes with Preview, so no edit — structural or typed —
        rebuilds it. refresh() only updates the dirty state and the stale marker.
        """
        dpg.delete_item(RULES_BOX, children_only=True)
        self.count_tags = {}

        for i, rule in enumerate(self.rules):
            if self._rule_visible(rule):
                self._render_rule(i, rule)

        dpg.add_button(label="+ rule", parent=RULES_BOX, callback=self.add_rule, width=110)
        self.refresh()

    def _rule_visible(self, rule: Rule) -> bool:
        # filter by the search box's category substring. An uncategorized rule
        # always shows, so adding a rule while a filter is active isn't a no-op.
        q = self.rule_query.casefold()
        return not q or not rule.category or q in rule.category.casefold()

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
                self.count_tags[index] = dpg.add_text("", color=MUTED)
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

    def refresh_preview(self, *_) -> None:
        """Recompute the panel against the current rules. The Preview button.

        This is the only thing that rebuilds the panel — editing rules never
        does. It resnapshots, which clears the stale marker via refresh().
        """
        self._render_panel()
        self.refresh()

    def tick(self) -> None:
        """Called once per frame from the render loop; drives first-frame
        calibration and the rule-search autocomplete."""
        if not self.calibrated:
            self._calibrate()
        self._ac_tick()

    # --- rule search / category autocomplete (mirrors review_approver) ----

    def _bind_autocomplete(self, item: int) -> None:
        # Open on a real mouse CLICK, not on focus. A structural edit (+ rule /
        # + condition) deletes the clicked button, and DPG then shifts keyboard
        # focus to the nearest input — this search box — which a focus handler
        # would read as "opened", popping the list unbidden. A click can't be
        # synthesised that way, and "click to open" is the intended behaviour.
        with dpg.item_handler_registry() as reg:
            dpg.add_item_clicked_handler(callback=self._ac_open, user_data=item)
        dpg.bind_item_handler_registry(item, reg)
        self.ac_registry = reg

    def _ac_open(self, sender, app_data, user_data) -> None:
        item = user_data
        # clicking the box while its list is already open is a no-op; _ac_tick
        # owns refiltering from there.
        if self.ac_input == item:
            return
        self.ac_input = item
        # anchor the list just under the box; remember where — the hit-test reads
        # this, never the window's (fragile) render state.
        x, y = dpg.get_item_rect_min(item)
        h = dpg.get_item_rect_size(item)[1]
        self.ac_pos = (int(x), int(y + h))
        self.ac_grace = None
        dpg.configure_item(SUGGEST, pos=list(self.ac_pos))
        value = dpg.get_value(item)
        self.ac_query = value
        self._ac_populate(value)

    def _ac_populate(self, query: str) -> None:
        matches = preview.match_categories(preview.rule_categories(self.rules), query)
        dpg.delete_item(SUGGEST, children_only=True)
        if not matches:
            dpg.configure_item(SUGGEST, show=False)
            self.ac_shown = False
            return
        for m in matches[:SUGGEST_MAX]:
            dpg.add_selectable(label=m, parent=SUGGEST, user_data=m, callback=self._ac_pick)
        dpg.configure_item(SUGGEST, show=True)
        self.ac_shown = True

    def _ac_pick(self, sender, app_data, user_data) -> None:
        if self.ac_input is None:
            return
        category = user_data
        dpg.set_value(self.ac_input, category)   # set_value skips the callback
        self._ac_close()
        self._apply_rule_search(category)        # show that category's rules

    def _ac_close(self) -> None:
        if dpg.does_item_exist(SUGGEST):
            dpg.configure_item(SUGGEST, show=False)
            dpg.delete_item(SUGGEST, children_only=True)
        self.ac_input = None
        self.ac_query = None
        self.ac_shown = False
        self.ac_pos = None
        self.ac_grace = None

    def _suggest_hovered(self) -> bool:
        # keep the list open while the mouse is over it, so a click on a suggestion
        # (which blurs the box) doesn't race the dismissal. Avoids reading the
        # floating window's rect (can be missing mid-frame); hit-tests the position
        # we set it to instead.
        if not self.ac_shown:
            return False
        try:
            if dpg.is_item_hovered(SUGGEST):
                return True
        except Exception:
            pass
        if self.ac_pos is None:
            return False
        try:
            mx, my = dpg.get_mouse_pos(local=False)
        except Exception:
            return False
        x, y = self.ac_pos
        return x <= mx <= x + SUGGEST_W and y <= my <= y + SUGGEST_H

    def _apply_rule_search(self, text: str) -> None:
        # the box's text is the filter; re-render the cards when it changes
        if text == self.rule_query:
            return
        self.rule_query = text
        self.render()

    def _clear_rule_search(self, *_) -> None:
        dpg.set_value(RULE_SEARCH, "")
        self.ac_query = ""
        self._ac_close()
        self._apply_rule_search("")

    def _ac_tick(self) -> None:
        if self.ac_input is None:
            return
        if not dpg.does_item_exist(self.ac_input):
            self._ac_close()
            return
        if dpg.is_item_focused(self.ac_input):
            self.ac_grace = None                       # active box — cancel pending close
            cur = dpg.get_value(self.ac_input)
            if cur != self.ac_query:                   # refilter as you type
                self.ac_query = cur
                self._ac_populate(cur)
                self._apply_rule_search(cur)
            return
        # focus left the box: keep the list open while the mouse is over it, and
        # give a few frames' grace so a click on a suggestion lands first.
        if self._suggest_hovered():
            self.ac_grace = None
            return
        if self.ac_grace is None:
            self.ac_grace = dpg.get_frame_count() + 6
        elif dpg.get_frame_count() >= self.ac_grace:
            self._ac_close()

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
        if not self.rows:
            return
        specs = self._tab_specs()
        self.tab_entries = {key: rows for key, _, rows in specs}
        keys = [key for key, _, _ in specs]
        # the active category tab can vanish when its category stops existing
        if self.active_tab not in keys:
            self.active_tab = keys[0]

        # the two fixed tabs are buttons; the categories (already alphabetical)
        # collapse into one dropdown so ~20 of them stay navigable
        fixed = specs[:2]     # Changed, Uncategorized
        cats = specs[2:]

        dpg.delete_item(PANEL_TABSTRIP, children_only=True)
        self.tab_buttons = {}
        self.cat_combo = None
        self.cat_choice_keys = {}
        with dpg.group(horizontal=True, parent=PANEL_TABSTRIP):
            for key, label, _ in fixed:
                self.tab_buttons[key] = dpg.add_button(
                    label=label, user_data=key, callback=self._on_tab)
            if cats:
                # combo label -> tab key, so the callback maps a selection back
                # without parsing the " (n)" count off the label
                self.cat_choice_keys = {label: key for key, label, _ in cats}
                # show the active category's label if a category is active,
                # otherwise a bare "Categories" prompt
                active_label = next((lbl for k, lbl, _ in cats if k == self.active_tab),
                                    "Categories")
                self.cat_combo = dpg.add_combo(
                    items=[lbl for _, lbl, _ in cats], default_value=active_label,
                    width=200, callback=self._on_cat_select)

        self._paint_tabs()
        self._render_content()
        self.panel_snapshot = copy.deepcopy(self.rules)

    def _paint_tabs(self) -> None:
        """Active theme on the strip element for the active tab, inactive on the rest."""
        for key, btn in self.tab_buttons.items():
            dpg.bind_item_theme(
                btn, self.tab_active if key == self.active_tab else self.tab_inactive)
        if self.cat_combo is not None:
            cat_active = self.active_tab.startswith(CAT_PREFIX)
            dpg.bind_item_theme(
                self.cat_combo, self.combo_active if cat_active else self.combo_inactive)

    # --- filter bar (account / amount) ---------------------------------

    def _build_filterbar(self) -> None:
        """Build the Account/Amount popups + row count once. Static — accounts
        come from all rows, not the active tab, so this never rebuilds."""
        with dpg.group(horizontal=True, parent=PANEL_FILTERBAR):
            amt_btn = dpg.add_button(label="Amount ▼", width=110)
            with dpg.popup(amt_btn, mousebutton=dpg.mvMouseButton_Left):
                self._amount_radio = dpg.add_radio_button(
                    items=[label for label, _ in AMOUNT_OPTIONS],
                    default_value=AMOUNT_LABEL_BY_KIND[self.amount_kind],
                    callback=self._on_amount_kind)
                dpg.add_separator()
                dpg.add_text("Custom range (magnitude):", color=MUTED)
                with dpg.group(horizontal=True):
                    # on_enter so we filter on commit, not on every keystroke
                    self._amount_min = dpg.add_input_text(
                        hint="min", width=90, on_enter=True,
                        user_data="lo", callback=self._on_amount_bound)
                    dpg.add_text("–")
                    self._amount_max = dpg.add_input_text(
                        hint="max", width=90, on_enter=True,
                        user_data="hi", callback=self._on_amount_bound)

            acct_btn = dpg.add_button(label="Account ▼", width=110)
            with dpg.popup(acct_btn, mousebutton=dpg.mvMouseButton_Left):
                with dpg.group(horizontal=True):
                    dpg.add_button(label="Select all", callback=self._select_all_accounts)
                    dpg.add_button(label="Unselect all", callback=self._unselect_all_accounts)
                dpg.add_separator()
                self._account_checks = {}
                for acct in self.accounts:
                    label = acct or "(blank)"
                    self._account_checks[acct] = dpg.add_checkbox(
                        label=label, default_value=True,
                        user_data=acct, callback=self._on_account_toggle)

            dpg.add_button(label="Clear", width=70, callback=self._clear_filters)
            dpg.add_text("", tag=FILTER_COUNT_TXT, color=MUTED)

    def _on_account_toggle(self, sender, checked, account) -> None:
        if checked:
            self.account_selected.add(account)
        else:
            self.account_selected.discard(account)
        self._apply_view()

    def _select_all_accounts(self, *_) -> None:
        self.account_selected = set(self.accounts)
        for cid in self._account_checks.values():
            dpg.set_value(cid, True)
        self._apply_view()

    def _unselect_all_accounts(self, *_) -> None:
        self.account_selected = set()
        for cid in self._account_checks.values():
            dpg.set_value(cid, False)
        self._apply_view()

    def _on_amount_kind(self, sender, label, user_data=None) -> None:
        # labels carry a `(n)` count, so map back to the kind by position
        try:
            idx = self._amount_labels.index(label)
        except ValueError:
            return
        self.amount_kind = AMOUNT_OPTIONS[idx][1]
        self._apply_view()

    def _on_amount_bound(self, sender, text, which) -> None:
        # editing a bound implies a custom range; the radio selection is synced by
        # _update_filter_counts (which owns the counted labels)
        value = _parse_decimal(text)
        if which == "lo":
            self.amount_lo = value
        else:
            self.amount_hi = value
        self.amount_kind = preview.AMOUNT_CUSTOM
        self._apply_view()

    def _clear_filters(self, *_) -> None:
        self.account_selected = set(self.accounts)
        for cid in self._account_checks.values():
            dpg.set_value(cid, True)
        self.amount_kind = preview.AMOUNT_ALL
        self.amount_lo = self.amount_hi = None
        if self._amount_min is not None:
            dpg.set_value(self._amount_min, "")
        if self._amount_max is not None:
            dpg.set_value(self._amount_max, "")
        self._apply_view()   # _update_filter_counts re-selects the radio to "All"

    def _apply_view(self, *_) -> None:
        # every filter/sort change funnels here; only the table is rebuilt, so the
        # open popup (a child of a filter-bar button) is untouched and stays open
        self._render_content()

    def _update_filter_counts(self) -> None:
        """Label each Account/Amount option with how many active-tab rows it holds.

        Counts are over the FULL tab entries, not the filtered view, so they show
        the distribution to pick from and don't shift as you toggle other options.
        """
        if not self._account_checks or self._amount_radio is None:
            return
        entries = self.tab_entries.get(self.active_tab, [])
        acc = preview.account_counts(entries)
        for acct, cid in self._account_checks.items():
            name = acct or "(blank)"
            dpg.configure_item(cid, label=f"{name} ({acc.get(acct, 0)})")
        amt = preview.amount_counts(entries, self.amount_lo, self.amount_hi)
        self._amount_labels = [f"{base} ({amt[kind]})" for base, kind in AMOUNT_OPTIONS]
        dpg.configure_item(self._amount_radio, items=self._amount_labels)
        sel = [kind for _, kind in AMOUNT_OPTIONS].index(self.amount_kind)
        dpg.set_value(self._amount_radio, self._amount_labels[sel])   # skips the callback

    # --- sort (click a column header) ----------------------------------

    def _on_sort(self, sender, app_data, user_data=None) -> None:
        # sender is the table. We REORDER the existing rows in place rather than
        # rebuilding the table: a rebuild draws the fresh rows over the old ones
        # for a frame, which showed up as ghosted/doubled text. app_data is
        # [[column_id, direction], ...]; empty on the tristate "off" click, which
        # restores the original (build) order. direction is +1 asc / -1 desc.
        if not self._row_entries:
            return
        if not app_data:
            order = [row_id for row_id, _ in self._row_entries]
        else:
            col_id, direction = app_data[0]
            column = self._sort_cols.get(col_id)
            ascending = direction > 0
            # blanks (empty amount / no rule) sort last in either direction
            present = [(r, e) for r, e in self._row_entries
                       if preview.sort_value(e, column) is not None]
            missing = [(r, e) for r, e in self._row_entries
                       if preview.sort_value(e, column) is None]
            present.sort(key=lambda re: preview.sort_value(re[1], column),
                         reverse=not ascending)
            order = [r for r, _ in present] + [r for r, _ in missing]
        dpg.reorder_items(sender, 1, order)   # slot 1 = the table's rows

    def _render_content(self, *_) -> None:
        # Only the active tab's table exists at a time. dpg's table clipper skips
        # drawing off-screen rows but not their widget creation, so building
        # every tab would mean tens of thousands of text items on a 1400-row csv.
        # Filtering rebuilds the table (source order); sorting reorders in place
        # (_on_sort), so a rebuild here never re-sorts — the sort simply resets.
        dpg.delete_item(PANEL_CONTENT, children_only=True)
        entries = self.tab_entries.get(self.active_tab, [])
        self._update_filter_counts()
        amount = ((self.amount_kind, self.amount_lo, self.amount_hi)
                  if self.amount_kind != preview.AMOUNT_ALL else None)
        # None = every account (no-op filter); anything narrower filters
        accounts = (self.account_selected
                    if set(self.account_selected) != set(self.accounts) else None)
        view = preview.view_entries(entries, accounts, amount, None, True)
        self._render_table(PANEL_CONTENT, self.active_tab, view)
        if dpg.does_item_exist(FILTER_COUNT_TXT):
            dpg.set_value(FILTER_COUNT_TXT, f"showing {len(view)} of {len(entries)}")

    def _on_tab(self, sender, app_data, user_data) -> None:
        if user_data == self.active_tab:
            return
        # Repaint the strip and swap the content — never rebuild the strip here,
        # which would delete the button mid-callback. Switching tabs reads the
        # already-computed tab_entries, so it never sneaks in a live refresh.
        self.active_tab = user_data
        self._paint_tabs()
        self._render_content()

    def _on_cat_select(self, sender, app_data, user_data) -> None:
        key = self.cat_choice_keys.get(app_data)
        if key is None or key == self.active_tab:
            return
        self.active_tab = key
        self._paint_tabs()
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
            # distinguish "nothing here" from "a filter hid everything"
            hidden = bool(self.tab_entries.get(key))
            msg = "no rows match the filter" if hidden else EMPTY_MESSAGE.get(key, "no rows")
            dpg.add_text(msg, parent=parent, color=MUTED)
            return
        clip_at = self._desc_clip_chars(key)
        # sortable: clicking a header fires _on_sort, which reorders the rows in
        # place (see _on_sort). _sort_cols maps a column id to its label;
        # _row_entries pairs each row id with its entry so the sort has typed keys.
        self._sort_cols = {}
        self._row_entries = []
        with dpg.table(parent=parent, header_row=True, scrollY=True, freeze_rows=1,
                       row_background=True, resizable=True, borders_innerV=True,
                       borders_innerH=True, policy=dpg.mvTable_SizingStretchProp,
                       sortable=True, sort_tristate=True, callback=self._on_sort,
                       height=-1):
            for label, weight in self._columns(key):
                col_id = dpg.add_table_column(label=label, init_width_or_weight=weight)
                self._sort_cols[col_id] = label
            for entry in entries:
                cells = self._cells(key, entry)
                with dpg.table_row() as row_id:
                    self._row_entries.append((row_id, entry))
                    for ci, cell in enumerate(cells):
                        txt = dpg.add_text(cell)
                        # full text on hover only when it's too long to fit,
                        # so short descriptions don't pop a redundant tooltip
                        if ci == DESC_CELL_INDEX and len(cell) > clip_at:
                            with dpg.tooltip(txt):
                                dpg.add_text(cell, wrap=DESC_TOOLTIP_WRAP)

    def _refresh_stale(self) -> None:
        # The panel is a snapshot from the last Preview. When the rules have
        # since diverged, flag that a refresh is pending by accenting the Preview
        # button rather than silently showing stale categories. The accent colour
        # carries the signal on its own, so the label stays a plain "Preview".
        if not self.panel_shown or self.panel_snapshot is None:
            return
        stale = self.rules != self.panel_snapshot
        dpg.bind_item_theme(PREVIEW_BTN, self.accent if stale else 0)

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
        """Update dirty state, match counts, and the panel's stale marker.

        Never rebuilds widgets: the rule cards and the panel are left as they
        are — only labels, Save's enabled-state, and the stale marker change.
        """
        dirty = self.is_dirty()
        dpg.set_value(DIRTY_TXT, "* unsaved" if dirty else "")
        dpg.configure_item(SAVE_BTN, enabled=dirty)
        self._refresh_counts()
        self._refresh_stale()

    def _refresh_counts(self) -> None:
        if not self.rows:
            dpg.set_value(FOOTER_TXT, "no preview csv — match counts unavailable")
            return

        won, matched, uncategorized = self._tally()
        for i, tag in self.count_tags.items():
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
    # Resolve + apply the palette BEFORE any theme or widget is built, so
    # init_theme() and the editor's per-item themes read the right colours.
    theme_name = tool_theme.resolve_theme(args.theme)
    globals().update(tool_theme.palette(theme_name))
    theme_id = init_theme()
    init_font()
    editor = RuleEditor(rules_path, rules, rows, columns)
    editor.theme_name = theme_name
    editor.global_theme = theme_id
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
