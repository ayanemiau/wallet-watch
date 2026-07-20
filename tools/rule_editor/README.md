# Rule editor

An interactive editor for the tier 3a rule table (`rules/keywords.yaml`) — see `plan.md` §5.

**This is a standalone tool, not part of the pipeline.** It's one optional way to maintain
`keywords.yaml`; editing the file by hand stays perfectly valid, and nothing in `lib/`
depends on this directory. The contract between the two is the *file format* below.

Rules are ordered and **first match wins**, so a rule's real effect depends on every rule
above it — which is exactly what's invisible in a text editor. The editor shows, live, how
many rows each rule actually wins, and flags rules that are fully shadowed by an earlier one.

## Run it

```sh
pip install -r tools/rule_editor/requirements.txt
python tools/rule_editor/editor.py --data-dir ../wallet-watch-data
```

The rules file resolves to `<data-root>/rules/keywords.yaml`, where the root is `--data-dir`,
then `$WALLET_WATCH_DATA_DIR`, then it fails fast. `--rules PATH` points at one explicitly.

Match counts need a normalized CSV. By default it picks the newest
`<data-root>/batch/*/normalized_*.csv` (normalize writes one per run); `--preview-csv PATH`
overrides. With no CSV the editor
still works — counts are just hidden. The CSV is read **read-only**, once, and never copied
or written anywhere.

**Light / dark theme.** `--theme auto|light|dark` (default `auto`) picks the palette at
launch — `auto` follows the macOS appearance setting. A `☾ Dark` / `☀ Light` button in the
toolbar toggles it live thereafter (macOS can't be auto-followed mid-session). The palette
and the detection live in `tools/tool_theme.py`, shared with `tools/review_approver`.

Starting from scratch:

```sh
cp tools/rule_editor/keywords.example.yaml "$WALLET_WATCH_DATA_DIR/rules/keywords.yaml"
```

## Your rules are real data

A real `keywords.yaml` is a list of your actual merchants — it leaks spending just like a
bank export, which is why `CLAUDE.md` names it directly. It lives in the external data root
with everything else.

`rules.py` **enforces** this: any rules path resolving inside this repo is a hard error, on
read and write alike. `.gitignore`'s `rules/` entry is the second line of defense, not the
first — it stops a commit, but not the file existing in your working tree. The one in-repo
exception is `keywords.example.yaml`, which is synthetic and never a write target.

## The format

```yaml
version: 1
rules:
  - category: Groceries
    match: all              # all | any
    conditions:
      - column: original_description
        op: contains
        value: FAKE GROCER
      - column: amount
        op: lt
        value: "0"
```

A rule is a list of conditions on transaction columns, combined by one `match` operator,
mapping to one category. The first rule that matches a transaction wins; a rule with no
conditions matches nothing.

**Columns** are whatever the preview CSV's header says (its header *is* the column list). With
no CSV loaded, the dropdown falls back to the `Transaction` fields: `date`, `amount`,
`account`, `is_reference`, `original_description`, `corrected_description`, `category`,
`tags`. A column already named in the YAML is always offered too, even if the current CSV
lacks it — so opening the editor never silently rewrites a rule. Naming a *brand new* column
the CSV doesn't have means hand-editing the YAML; the dropdown can't invent one.

**String ops** — `contains`, `not_contains`, `equals`, `not_equals`, `starts_with`,
`ends_with`, `regex`. All are **case-insensitive**, always: bank descriptions have
inconsistent casing, so a case-sensitive default would make every rule a bug report.

**Numeric ops** — `gt`, `gte`, `lt`, `lte`. Operands compare as decimals, so `-9.99 < -5`
works out right where a string compare wouldn't. A non-numeric cell (`""`, `pending`) simply
doesn't match rather than raising — one odd row shouldn't take down a run.

## Editing

Changes stay in memory until you press **Save**; the button stays greyed out until something
actually differs from what's on disk. Save validates every rule first and refuses if any is
incomplete. Writes are atomic (temp file + rename) and the previous version is kept as
`keywords.yaml.bak` — `rules/` is gitignored, so there's no git history to recover this file
from.

If you close the window with unsaved changes they're written to `keywords.yaml.autosave`
rather than lost. DearPyGui can't intercept a window close to ask "discard changes?", so the
tool rescues the work instead of prompting. Nothing is overwritten: inspect the autosave and
copy it over `keywords.yaml` yourself if you want it.

Reorder rules with **▲▼**. Order is the semantics here, so it's worth being deliberate: if a
rule reports `shadowed`, some rule above it already claims every row it would match.

**Filter rules by category.** The search box above the rules narrows the cards to the rules
of one category — useful once the table runs to ~20 categories. Click the empty box to see
every category currently in use; type to narrow that list by substring; pick one (or just
leave your typed text) and the cards collapse to the matching rules. Uncategorized rules
always stay visible, so adding a rule while filtered isn't a dead end. **Clear** resets it.
(The autocomplete mirrors the category picker in `tools/review_approver`.)

The footer's uncategorized count is the M2 progress metric (`plan.md` §6 targets ~80%
auto-categorized).

## The transactions panel

The panel sits to the right of the rules whenever a preview CSV is loaded (without one there's
nothing to show, so it's hidden and the rules take the full width). It shows the actual rows,
not just counts:

- **Changed** — rows whose category your unsaved edits move, as `from → to`. This diffs
  against the rules **as saved on disk**, so it's empty right after a Save, and *everything*
  shows as `— → X` before your first save (no saved rules means no previous categories).
- **Uncategorized** — rows no rule matches. This is the worklist: it's what you read to decide
  which rule to write next.
- **Categories** — a dropdown, because a real rule set runs to ~20 `<main>/<sub>` categories
  and that many tabs are worse to navigate than a list. Pick a category to see what landed
  there, listed **alphabetically**. The `rule` column gives the winning rule's number (`#3`,
  matching the numbered cards on the left), so a row that arrived via the wrong rule is visible
  rather than merely present.

**Preview is a manual refresh, not a toggle.** The panel does not follow your edits live: it
shows the snapshot from the last time you pressed **Preview**, and while your rules have
changed since then the button flags it with a `●` marker. This keeps typing and reordering
free of the 28–45ms table rebuild a live panel would pay on every keystroke — worst exactly
when you type a keyword's first letter and it still matches everything — and lets you make a
few edits before judging their combined effect. Click **Preview** when you want the panel to
catch up. (Save doesn't auto-refresh either, so the marker stays lit after a save until you
press Preview.)

**Filtering & sorting the panel.** A filter bar above the table narrows what you're reading —
handy on a long Uncategorized worklist. It's a pure view over the current Preview snapshot: it
never changes your rules, the match counts, or the footer's uncategorized metric.

- **Sort** — click a column header to sort A→Z, click again for Z→A, a third time to clear
  (the header shows the sort arrow). Rows reorder in place. Amount sorts numerically, not
  lexically, so `-500` orders below `-5`.
- **Account ▼** — a checkbox per account with **Select all / Unselect all**; the table shows
  only the checked accounts.
- **Amount ▼** — presets **< 100 / 100–500 / > 500** or a **Custom** min–max, compared on the
  **magnitude** of the amount (spend is stored negative, so `< 100` means "under $100 in
  size"). The custom bounds are inclusive; leave one blank for an open end.
- Each Account and Amount option carries a `(n)` count — how many rows in the current tab it
  holds. Counts are over the whole tab (not the already-filtered view), so they show the
  distribution to pick from and don't shift as you toggle other options.
- **Clear** resets everything, and *showing N of M* tracks how much the filter hides.

Filters (account + amount) combine with AND and persist as you switch tabs; a sort applies to
whatever tab has that column (the `from`/`to` columns only exist on Changed).

A description too long for its column gets the full text in a hover tooltip. Only long ones —
whether a description clips depends on the tab (the Changed tab's description column is
narrower than Uncategorized's), so the threshold adapts per tab and short descriptions don't
pop a needless tooltip.

Only the visible tab is built, so switching tabs — or picking a category — is what pays for a
table, not the refresh itself.

The window defaults to 1400px wide so the rules keep their room alongside the panel; drag the
column edges in the table to rebalance, or resize the window.

**Caveat: saving drops comments.** PyYAML doesn't round-trip them, so hand-written comments in
`keywords.yaml` are lost the first time the editor saves — worth knowing, since hand-editing
is a supported workflow. Only the managed header survives. If comment preservation ever
matters, swap `ruamel.yaml` in behind `load_rules`/`save_rules` in `rules.py`; nothing else
changes.

## Layout

| File | What |
| --- | --- |
| `rules.py` | The format, the match engine, the in-repo guard. No UI, no `lib/` imports. |
| `preview.py` | What the rules do to a set of rows: the diff and the grouping. No UI. |
| `editor.py` | DearPyGui UI + CLI entry point. |
| `keywords.example.yaml` | Synthetic sample doubling as format docs. |
| `tests/` | `pytest tools/rule_editor/tests/` |

`rules.py` and `preview.py` hold no UI code, and `editor.py` holds no file, matching, or diff
logic. That's why the tests cover the parts that matter without ever opening a window — and
why swapping the whole UI toolkit once cost nothing outside `editor.py`.

## Why DearPyGui

Tkinter was the obvious pick — stdlib, nothing to install. It doesn't work here: the Tk 8.5
bundled with macOS's system Python can't open a window on macOS 26 (`macOS 26 or later
required, have instead 16`), and the fix is installing a whole second Python via homebrew.
DearPyGui is a pip wheel that runs on the system interpreter as-is, so it's both the
lower-friction option and the better-looking one. It also gives us rounded corners, which
Tk can't do.
