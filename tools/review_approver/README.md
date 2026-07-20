# Review approver

A DearPyGui tool for **GATE 2** of Phase 4: work through the transactions the
resolver couldn't hard-match, approve them, and fill in or correct categories —
then save.

**This is a standalone tool, not part of the pipeline.** `scripts/resolve_batch.py`
writes a `review_<runtime>.csv`; this tool is the interactive front-end over it. The
two share `lib/resolve_review.py`, so they read and write the file identically.

## Run it

```
pip install -r tools/review_approver/requirements.txt
python tools/review_approver/approver.py --data-dir ../wallet-watch-data
```

Input resolution, in order: `--input <review.csv>` wins; else `--batch-dir`'s newest
`review_*.csv`; else the newest `review_*.csv` in the latest-start batch under
`<data-root>/batch/` (`--data-dir`, then `$WALLET_WATCH_DATA_DIR`). The file is read
and validated **before** the window opens, so a bad path is a plain CLI error.

**Light / dark theme.** `--theme auto|light|dark` (default `auto`) picks the palette at
launch — `auto` follows the macOS appearance setting. A `☾ Dark` / `☀ Light` button in the
toolbar toggles it live thereafter. The palette and the detection live in
`tools/tool_theme.py`, shared with `tools/rule_editor`.

## It's real data

A `review_*.csv` is real transaction data — it lives in the external data root, never
in this repo (see `CLAUDE.md`). The tool only ever reads/writes the file you point it
at under the data root.

## The review file

One CSV holding **every** row of the batch: the `Transaction` columns plus two
workflow columns written by `resolve_batch`.

| column | who writes it | meaning |
| --- | --- | --- |
| `original_description` | machine (raw) | never edited — the source description |
| `corrected_description` | **you** | a clean rewrite of a cryptic description ("updated description") |
| `category` | machine, then **you** | the **learned** category — machine-set (hard filter → dict/LLM), correct it here; reused to label future transactions |
| `category_override` | **you** | a **one-off** special case; wins over `category` downstream but is never learned |
| `resolved_by` | machine | `hard` / `desc-map` / `cat-map` / `agent` / `none` — how the category was proposed |
| `approved` | **you** | `1` once you've signed off; hard rows start `1`, the rest `0` |

Isolation: you edit `corrected_description`, `category`, and `category_override` — never a
**raw** field (`original_description`, `amount`, `date`, `account`). The effective category
downstream is `category_override or category`.

`category` vs `category_override`: correct a wrong/blank category in **`category`** — that
is the value that gets learned, so the same merchant auto-resolves next time. Reach for
**`category_override`** only when one transaction needs a special category that should *not*
generalize (it wins for this row and is never fed back into the maps).

## Approving

Two tabs, split by `resolved_by`:

- **Review inbox** — everything that isn't a trusted hard-filter hit (unmatched, or
  dict/LLM-suggested). These start unapproved; emptying this queue is GATE 2.
- **Everything else** — the hard-filter rows, pre-approved. Open it to fix a category you
  disagree with (edit `category`) or to set a one-off `override`.

Per row there's a select checkbox; **Select all** / **Unselect all** act on the
current tab. **Approve selected** / **Unapprove selected** flip the `approved` mark on
every checked row. Type into **updated description**, **category**, or **override** to
correct a row; edits mark the file unsaved. **Save** writes the file back in place
(and closing with unsaved edits rescues them to `review_*.csv.autosave`).

The **category** and **override** boxes are free-text but offer a candidate list of the
categories already in the file: click an empty box to see them all (alphabetical), or type
to filter by substring (case-insensitive). Click a suggestion to fill the box, or just keep
typing to enter a new one.

Only the active tab's table is built, so switching tabs is what pays for rendering.
Drag the column edges to rebalance, or resize the window.

## Layout

| File | What |
| --- | --- |
| `review_model.py` | Tab split, counts, dirty check over `List[ReviewRow]`. No UI. |
| `approver.py` | DearPyGui UI + CLI entry point. |
| `tests/` | `pytest tools/review_approver/tests/` |

The file I/O lives in `lib/resolve_review.py` (`read_review`/`write_review`), the
schema in `lib/schema.py`. `review_model.py` holds no UI code and `approver.py` holds
no file or counting logic, so the tests cover what matters without ever opening a
window.

## Why DearPyGui

Same story as `tools/rule_editor`: Tkinter is stdlib but the Tk 8.5 bundled with
macOS system Python can't open a window on macOS 26 (`macOS 26 or later required, have
instead 16`), and the fix is a whole second Python. DearPyGui is a pip wheel that runs
on the system interpreter as-is, and gives us rounded corners besides.
