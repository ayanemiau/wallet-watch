"""Interactive approver for a Phase 4 review file (review_<runtime>.csv).

A DearPyGui UI over lib/resolve_review.py: work through the transactions Phase 4
resolved, in two tabs — "Review inbox" (unmatched / dict- / LLM-suggested rows)
and "Everything else" (trusted hard-filter rows, still correctable). Select rows
(single, multi, or select-all) and approve them; correct the learned `category`,
set a one-off `category_override`, or rewrite a cryptic description into
`corrected_description`. Save writes the file back in place.

Two category columns, deliberately distinct: `category` is the learnable value
(machine-set, then human-correctable — a correction stamps category_source=human-review
and is reused for future transactions via committed history), while `category_override`
is a one-off that wins downstream (`effective_category = category_override or
category`) but is never learned. Isolation: the human edits only those two plus
`corrected_description` — every raw field (`original_description`, amount, date,
account) is untouched.

The review file lives in the external data root, never in this repo — it is real
transaction data (see CLAUDE.md).

Run:  python tools/review_approver/approver.py --data-dir ../wallet-watch-data
See tools/review_approver/README.md.
"""

import argparse
import copy
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

# lib/ holds the review-file I/O (resolve_review) and schema; both this tool and
# scripts/resolve_batch.py import them so they read/write identically. Put lib/
# on the path before importing (review_model imports them too).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "lib"))

import dearpygui.dearpygui as dpg  # noqa: E402

from review_model import (candidates, counts, is_dirty, match_candidates,  # noqa: E402
                          split_tabs)
from resolve_review import ReviewRow, read_review, write_review  # noqa: E402
from schema import CategorySource  # noqa: E402

# a batch dir is named by its date range, YYYYMMDD-YYYYMMDD
BATCH_ID_RE = re.compile(r"\d{8}-\d{8}")


def _rgb(value: str) -> List[int]:
    value = value.lstrip("#")
    return [int(value[i:i + 2], 16) for i in (0, 2, 4)]


# Claude-ish warm palette (shared with tools/rule_editor).
BG = _rgb("#FAF9F5")
SURFACE = _rgb("#FFFFFF")
TEXT = _rgb("#3D3929")
MUTED = _rgb("#83827D")
ACCENT = _rgb("#D97757")
ACCENT_ACTIVE = _rgb("#C4623F")
BORDER = _rgb("#E5E4DF")
OK = _rgb("#617A5C")
WHITE = [255, 255, 255]
TABLE_HEADER = _rgb("#EAE8E1")

MAIN = "main_window"
TABLE = "review_table"
HEADER_TXT = "header_text"
DIRTY_TXT = "dirty_text"
SAVE_BTN = "save_button"
TAB_INBOX_BTN = "tab_inbox_button"
TAB_REST_BTN = "tab_rest_button"
ALERT = "alert_modal"
SUGGEST = "suggest_window"        # shared floating candidate-list dropdown

CHECK = "✓"
DASH = "—"

# category autocomplete: the suggestion list floats under the focused input.
SUGGEST_W = 240
SUGGEST_H = 260          # bounded; the list scrolls past this
SUGGEST_MAX = 60         # cap the rendered rows

# original_description cells get a hover tooltip when long enough to clip.
DESC_CLIP = 34
DESC_TOOLTIP_WRAP = 420

FONT_CANDIDATES = [
    "/System/Library/Fonts/SFNS.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/Library/Fonts/Arial.ttf",
]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--batch-dir", type=Path, default=None,
                   help="batch workspace holding review_*.csv; defaults to the "
                        "latest-start batch under <data-root>/batch/")
    p.add_argument("--data-dir", type=Path, default=None,
                   help="data root under which to find the batch; defaults to "
                        "$WALLET_WATCH_DATA_DIR")
    p.add_argument("--input", type=Path, default=None,
                   help="review CSV to approve; defaults to the newest "
                        "review_*.csv in the batch")
    return p.parse_args()


def resolve_data_dir(arg: Optional[Path]) -> Optional[Path]:
    # A given --input is a complete answer, so the data root may be absent here;
    # resolve_input enforces that at least one of them pins a file.
    data_dir = arg or (Path(os.environ["WALLET_WATCH_DATA_DIR"])
                       if os.environ.get("WALLET_WATCH_DATA_DIR") else None)
    if data_dir is not None and not data_dir.is_dir():
        raise SystemExit(f"data root does not exist: {data_dir}")
    return data_dir


def latest_batch_dir(data_dir: Path) -> Path:
    batch_root = data_dir / "batch"
    if not batch_root.is_dir():
        raise SystemExit(f"no batch dir under data root: {batch_root}")
    batches = sorted(d for d in batch_root.iterdir()
                     if d.is_dir() and BATCH_ID_RE.fullmatch(d.name))
    if not batches:
        raise SystemExit(f"no batches found in {batch_root} (expected YYYYMMDD-YYYYMMDD dirs)")
    return batches[-1]


def latest_review(batch_dir: Path) -> Path:
    # resolve_batch writes review_<YYYYMMDD>_<HHMMSS>.csv; a name sort orders by
    # run timestamp, so the last is the newest run.
    found = sorted(batch_dir.glob("review_*.csv"))
    if not found:
        raise SystemExit(f"no review_*.csv in batch: {batch_dir} (run resolve_batch first)")
    return found[-1]


def resolve_input(args: argparse.Namespace, data_dir: Optional[Path]) -> Path:
    if args.input is not None:
        if not args.input.is_file():
            raise SystemExit(f"review file not found: {args.input}")
        return args.input
    if args.batch_dir is not None:
        return latest_review(args.batch_dir)
    if data_dir is None:
        raise SystemExit("no data root: pass --data-dir, set WALLET_WATCH_DATA_DIR, "
                         "or point --input at a review_*.csv")
    batch_dir = latest_batch_dir(data_dir)
    print(f"using latest batch: {batch_dir.name}", file=sys.stderr)
    return latest_review(batch_dir)


# --- theme / font (shared look with tools/rule_editor) ---


def init_theme() -> None:
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
            dpg.add_theme_color(dpg.mvThemeCol_ScrollbarBg, BG)
            dpg.add_theme_color(dpg.mvThemeCol_ScrollbarGrab, BORDER)
            dpg.add_theme_color(dpg.mvThemeCol_ScrollbarGrabHovered, MUTED)
            dpg.add_theme_color(dpg.mvThemeCol_Separator, BORDER)
            dpg.add_theme_color(dpg.mvThemeCol_TableHeaderBg, TABLE_HEADER)
            dpg.add_theme_color(dpg.mvThemeCol_TableBorderStrong, BORDER)
            dpg.add_theme_color(dpg.mvThemeCol_TableBorderLight, BORDER)
            dpg.add_theme_color(dpg.mvThemeCol_TableRowBg, SURFACE)
            dpg.add_theme_color(dpg.mvThemeCol_TableRowBgAlt, BG)
            dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 6)
            dpg.add_theme_style(dpg.mvStyleVar_ChildRounding, 10)
            dpg.add_theme_style(dpg.mvStyleVar_FramePadding, 8, 5)
            dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing, 8, 6)
            dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, 16, 12)
    dpg.bind_theme(theme)


def accent_theme() -> str:
    with dpg.theme() as theme:
        with dpg.theme_component(dpg.mvButton):
            dpg.add_theme_color(dpg.mvThemeCol_Button, ACCENT)
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, ACCENT_ACTIVE)
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, ACCENT_ACTIVE)
            dpg.add_theme_color(dpg.mvThemeCol_Text, WHITE)
        with dpg.theme_component(dpg.mvButton, enabled_state=False):
            dpg.add_theme_color(dpg.mvThemeCol_Button, BORDER)
            dpg.add_theme_color(dpg.mvThemeCol_Text, MUTED)
    return theme


def tab_themes() -> Tuple[str, str]:
    """(active, inactive) themes for the two tab buttons."""
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
    with dpg.window(label=title, modal=True, tag=ALERT, width=620, height=200,
                    pos=(180, 160), no_resize=True):
        dpg.add_text(message, wrap=580)
        dpg.add_spacer(height=8)
        dpg.add_button(label="OK", width=90, callback=lambda: dpg.delete_item(ALERT))


# columns: (label, weight). The checkbox column is first; three columns carry the
# human's edits — updated description, category (learned & reused), and override
# (one-off, never learned).
COLUMNS = [
    ("", 0.4), ("date", 1.1), ("amount", 0.9), ("account", 1.4),
    ("original description", 2.5), ("updated description", 2.2),
    ("category ¹", 1.6), ("override ²", 1.5), ("source", 1.0), ("ok", 0.6),
]

# footer legend explaining the two editable category columns
COLUMN_LEGEND = ("¹ category — machine-set, correct it here; learned & reused for future "
                 "transactions    ²  override — a one-off special case; wins over category, "
                 "never learned")


class ReviewApprover:
    """The DearPyGui approver over a loaded review file (one file, two tabs)."""

    def __init__(self, input_path: Path, rows: List[ReviewRow]):
        self.input_path = input_path
        self.rows = rows                                  # master list, edited in place
        self.baseline = copy.deepcopy(rows)               # for the dirty check
        self.active = "inbox"                             # or "rest"
        self.selected: Set[int] = set()                   # master indices
        self.check_items: Dict[int, int] = {}             # master idx -> checkbox id (active tab)
        self.status_items: Dict[int, int] = {}            # master idx -> ✓/— text id (active tab)
        self.accent = accent_theme()
        self.tab_active, self.tab_inactive = tab_themes()
        # category autocomplete session (None when the dropdown is closed)
        self.ac_input: Optional[int] = None               # focused input widget id
        self.ac_target: Optional[Tuple[int, str]] = None  # (master idx, field name)
        self.ac_query: Optional[str] = None               # last-seen text, for change detection
        self.ac_shown = False                             # is the dropdown currently visible
        self.ac_registries: List[int] = []                # per-input focus handler registries

    # --- tab membership -------------------------------------------------

    def _tab_indices(self, which: str) -> List[int]:
        # master indices of the rows shown in a tab, in order
        inbox, _ = split_tabs(self.rows)
        inbox_ids = {id(r) for r in inbox}
        want_inbox = which == "inbox"
        return [i for i, r in enumerate(self.rows)
                if (id(r) in inbox_ids) == want_inbox]

    # --- build ----------------------------------------------------------

    def build(self) -> None:
        with dpg.window(tag=MAIN):
            with dpg.group(horizontal=True):
                dpg.add_text("", tag=HEADER_TXT)
                dpg.add_text("", tag=DIRTY_TXT, color=MUTED)
            dpg.add_spacer(height=4)
            with dpg.group(horizontal=True):
                inbox, rest = split_tabs(self.rows)
                dpg.add_button(label=f"Review inbox ({len(inbox)})", tag=TAB_INBOX_BTN,
                               width=200, callback=lambda: self._switch("inbox"))
                dpg.add_button(label=f"Everything else ({len(rest)})", tag=TAB_REST_BTN,
                               width=220, callback=lambda: self._switch("rest"))
                dpg.add_spacer(width=24)
                dpg.add_button(label="Select all", callback=self._select_all)
                dpg.add_button(label="Unselect all", callback=self._unselect_all)
                dpg.add_button(label="Approve selected",
                               callback=lambda: self._set_selected_approved(True))
                dpg.add_button(label="Unapprove selected",
                               callback=lambda: self._set_selected_approved(False))
                save = dpg.add_button(label="Save", tag=SAVE_BTN, width=110,
                                      callback=self.save)
                dpg.bind_item_theme(save, self.accent)
            dpg.add_text(COLUMN_LEGEND, color=MUTED)
            dpg.add_spacer(height=6)
            dpg.add_group(tag=TABLE)                       # table container, rebuilt per tab
        dpg.set_primary_window(MAIN, True)
        # the shared category-autocomplete dropdown: one floating window, hidden
        # until an input is focused, repositioned under whichever input is active.
        # no_focus_on_appearing keeps the keyboard in the input so typing filters.
        with dpg.window(tag=SUGGEST, show=False, no_title_bar=True, no_move=True,
                        no_resize=True, no_collapse=True, no_focus_on_appearing=True,
                        no_scrollbar=False, width=SUGGEST_W, height=SUGGEST_H):
            pass
        self._render_tab()
        self.refresh()

    def _bind_tabs(self) -> None:
        dpg.bind_item_theme(TAB_INBOX_BTN,
                            self.tab_active if self.active == "inbox" else self.tab_inactive)
        dpg.bind_item_theme(TAB_REST_BTN,
                            self.tab_active if self.active == "rest" else self.tab_inactive)

    def _switch(self, which: str) -> None:
        self.active = which
        self._render_tab()
        self.refresh()

    def _render_tab(self) -> None:
        # rebuild only the active tab's table; selection persists via self.selected
        self._ac_close()               # old input ids are about to be deleted
        for reg in self.ac_registries:  # drop the previous tab's focus handlers
            if dpg.does_item_exist(reg):
                dpg.delete_item(reg)
        self.ac_registries.clear()
        dpg.delete_item(TABLE, children_only=True)
        self.check_items.clear()
        self.status_items.clear()
        self._bind_tabs()

        indices = self._tab_indices(self.active)
        if not indices:
            dpg.add_text("nothing here", parent=TABLE, color=MUTED)
            return

        with dpg.table(parent=TABLE, header_row=True, scrollY=True, freeze_rows=1,
                       row_background=True, resizable=True, borders_innerV=True,
                       borders_innerH=True, policy=dpg.mvTable_SizingStretchProp,
                       height=-1):
            for label, weight in COLUMNS:
                dpg.add_table_column(label=label, init_width_or_weight=weight)
            for idx in indices:
                self._render_row(idx)

    def _render_row(self, idx: int) -> None:
        row = self.rows[idx]
        txn = row.txn
        with dpg.table_row():
            self.check_items[idx] = dpg.add_checkbox(
                default_value=idx in self.selected, user_data=idx,
                callback=self._on_select)
            dpg.add_text(txn.date)
            dpg.add_text(txn.amount)
            dpg.add_text(txn.account)
            desc = dpg.add_text(txn.original_description)
            if len(txn.original_description) > DESC_CLIP:
                with dpg.tooltip(desc):
                    dpg.add_text(txn.original_description, wrap=DESC_TOOLTIP_WRAP)
            dpg.add_input_text(default_value=txn.corrected_description, width=-1,
                               hint="rewrite", user_data=idx, callback=self._on_corrected)
            cat_id = dpg.add_input_text(default_value=txn.category, width=-1,
                                        hint="category", user_data=idx,
                                        callback=self._on_category)
            self._bind_autocomplete(cat_id, idx, "category")
            ovr_id = dpg.add_input_text(default_value=txn.category_override, width=-1,
                                        hint="one-off", user_data=idx,
                                        callback=self._on_override)
            self._bind_autocomplete(ovr_id, idx, "category_override")
            dpg.add_text(row.resolved_by, color=MUTED)
            self.status_items[idx] = dpg.add_text(
                CHECK if row.approved else DASH,
                color=OK if row.approved else MUTED)

    # --- edit callbacks (never rebuild widgets — only refresh derived UI) ---

    def _on_corrected(self, sender, app_data, user_data) -> None:
        self.rows[user_data].txn.corrected_description = app_data
        self.refresh()

    def _on_category(self, sender, app_data, user_data) -> None:
        # correcting the learned category: stamp category_source=HUMAN_REVIEW so
        # the value no longer claims a machine provenance. Empty -> NONE.
        txn = self.rows[user_data].txn
        txn.category = app_data
        txn.category_source = (CategorySource.HUMAN_REVIEW if app_data
                               else CategorySource.NONE)
        self.refresh()

    def _on_override(self, sender, app_data, user_data) -> None:
        # the one-off override never touches method — it is a separate layer that
        # wins via effective_category and is never learned.
        self.rows[user_data].txn.category_override = app_data
        self.refresh()

    def _on_select(self, sender, app_data, user_data) -> None:
        if app_data:
            self.selected.add(user_data)
        else:
            self.selected.discard(user_data)

    # --- category autocomplete (候选词列表) ------------------------------
    #
    # A shared floating window lists existing categories under the focused
    # category/override input. Focusing an input opens it (empty -> all,
    # alphabetical); typing filters by substring — but input_text fires no
    # per-keystroke callback, so tick() polls the focused value each frame.

    def _bind_autocomplete(self, item: int, idx: int, field: str) -> None:
        with dpg.item_handler_registry() as reg:
            dpg.add_item_focus_handler(callback=self._ac_open,
                                       user_data=(item, idx, field))
        dpg.bind_item_handler_registry(item, reg)
        self.ac_registries.append(reg)

    def _ac_open(self, sender, app_data, user_data) -> None:
        item, idx, field = user_data
        # the focus handler is level-triggered (fires every frame while focused);
        # only (re)open on a NEW focus, else tick() owns refiltering.
        if self.ac_input == item:
            return
        self.ac_input = item
        self.ac_target = (idx, field)
        # anchor the dropdown just under the focused input
        x, y = dpg.get_item_rect_min(item)
        h = dpg.get_item_rect_size(item)[1]
        dpg.configure_item(SUGGEST, pos=[int(x), int(y + h)])
        value = dpg.get_value(item)
        self.ac_query = value
        self._ac_populate(value)

    def _ac_populate(self, query: str) -> None:
        matches = match_candidates(candidates(self.rows), query)
        dpg.delete_item(SUGGEST, children_only=True)
        if not matches:
            dpg.configure_item(SUGGEST, show=False)
            self.ac_shown = False
            return
        for m in matches[:SUGGEST_MAX]:
            dpg.add_selectable(label=m, parent=SUGGEST, user_data=m,
                               callback=self._ac_pick)
        dpg.configure_item(SUGGEST, show=True)
        self.ac_shown = True

    def _ac_pick(self, sender, app_data, user_data) -> None:
        if self.ac_input is None or self.ac_target is None:
            return
        idx, field = self.ac_target
        value = user_data
        dpg.set_value(self.ac_input, value)                # set_value skips the callback
        (self._on_category if field == "category" else self._on_override)(None, value, idx)
        self._ac_close()

    def _ac_close(self) -> None:
        if dpg.does_item_exist(SUGGEST):
            dpg.configure_item(SUGGEST, show=False)
            dpg.delete_item(SUGGEST, children_only=True)
        self.ac_input = None
        self.ac_target = None
        self.ac_query = None
        self.ac_shown = False

    def _mouse_in_suggest(self) -> bool:
        # keeps the list open while the mouse is over it, so clicking a suggestion
        # (which blurs the input) doesn't race the tick() dismissal.
        if not self.ac_shown:
            return False
        mx, my = dpg.get_mouse_pos(local=False)
        x, y = dpg.get_item_rect_min(SUGGEST)
        w, h = dpg.get_item_rect_size(SUGGEST)
        return x <= mx <= x + w and y <= my <= y + h

    # --- selection / approval ------------------------------------------

    def _select_all(self) -> None:
        for idx, item in self.check_items.items():
            self.selected.add(idx)
            dpg.set_value(item, True)

    def _unselect_all(self) -> None:
        for idx, item in self.check_items.items():
            self.selected.discard(idx)
            dpg.set_value(item, False)

    def _set_selected_approved(self, approved: bool) -> None:
        for idx in self.selected:
            self.rows[idx].approved = approved
            item = self.status_items.get(idx)          # only visible-tab rows have one
            if item is not None:
                dpg.set_value(item, CHECK if approved else DASH)
                dpg.configure_item(item, color=OK if approved else MUTED)
        self.refresh()

    # --- derived UI -----------------------------------------------------

    def refresh(self) -> None:
        c = counts(self.rows)
        dpg.set_value(HEADER_TXT,
                      f"{c.total} rows  ·  inbox {c.inbox} "
                      f"({c.pending} awaiting approval, {c.uncategorized} uncategorized)"
                      f"  ·  {c.hard} hard-filter")
        dirty = self.is_dirty()
        dpg.set_value(DIRTY_TXT, "● unsaved" if dirty else "")
        dpg.configure_item(SAVE_BTN, enabled=dirty)

    def is_dirty(self) -> bool:
        return is_dirty(self.rows, self.baseline)

    def tick(self) -> None:
        # drives the category autocomplete: dismiss when focus leaves (and the
        # mouse isn't over the list), else refilter as the focused value changes.
        if self.ac_input is None:
            return
        if not dpg.does_item_exist(self.ac_input):
            self._ac_close()
            return
        if not dpg.is_item_focused(self.ac_input) and not self._mouse_in_suggest():
            self._ac_close()
            return
        cur = dpg.get_value(self.ac_input)
        if cur != self.ac_query:
            self.ac_query = cur
            self._ac_populate(cur)

    # --- save / exit ----------------------------------------------------

    def save(self) -> None:
        if not self.is_dirty():
            return
        try:
            write_review(self.input_path, self.rows)
        except OSError as e:
            alert("Cannot save", f"{self.input_path}: {e}")
            return
        self.baseline = copy.deepcopy(self.rows)
        self.refresh()

    def on_exit(self) -> None:
        # DPG cannot veto a viewport close, so rescue unsaved work rather than
        # dropping it (mirrors tools/rule_editor).
        if not self.is_dirty():
            return
        rescue = self.input_path.with_suffix(self.input_path.suffix + ".autosave")
        try:
            write_review(rescue, self.rows)
        except OSError as e:
            print(f"unsaved changes lost: {e}", file=sys.stderr)
            return
        print(f"closed with unsaved changes -> {rescue}", file=sys.stderr)


def main() -> None:
    args = parse_args()
    data_dir = resolve_data_dir(args.data_dir)
    input_path = resolve_input(args, data_dir)

    # Read the file BEFORE opening a window so a bad path/CSV surfaces as a plain
    # CLI error, not from behind the GUI.
    rows = read_review(input_path)
    pending = sum(1 for r in rows if not r.approved)
    print(f"approving {input_path} ({len(rows)} rows, {pending} awaiting approval)",
          file=sys.stderr)

    dpg.create_context()
    init_theme()
    init_font()
    approver = ReviewApprover(input_path, rows)
    approver.build()
    dpg.create_viewport(title="wallet-watch — review approver",
                        width=1500, height=820, clear_color=BG + [255])
    dpg.setup_dearpygui()
    dpg.show_viewport()

    while dpg.is_dearpygui_running():
        approver.tick()
        dpg.render_dearpygui_frame()
    approver.on_exit()
    dpg.destroy_context()


if __name__ == "__main__":
    main()
