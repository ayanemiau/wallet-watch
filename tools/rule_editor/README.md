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
`<data-root>/batch/*/normalized.csv`; `--preview-csv PATH` overrides. With no CSV the editor
still works — counts are just hidden. The CSV is read **read-only**, once, and never copied
or written anywhere.

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

The footer's uncategorized count is the M2 progress metric (`plan.md` §6 targets ~80%
auto-categorized).

## The transactions panel

**Preview** toggles a panel showing the actual rows, not just counts. It needs a preview CSV,
so the button is disabled without one. Three kinds of tab:

- **Changed** — rows whose category your unsaved edits move, as `from → to`. This diffs
  against the rules **as saved on disk**, so it's empty right after a Save, and *everything*
  shows as `— → X` before your first save (no saved rules means no previous categories).
- **Uncategorized** — rows no rule matches. This is the worklist: it's what you read to decide
  which rule to write next.
- **One tab per category** — what actually landed there, biggest category first. The `rule`
  column gives the winning rule's number (`#3`, matching the numbered cards on the left), so
  a row that arrived via the wrong rule is visible rather than merely present.

The tab strip scrolls sideways when there are more categories than fit, so a tab keeps its
full label instead of shrinking to an unreadable nub. Scroll it with the horizontal scrollbar
or shift-mouse-wheel.

A description too long for its column gets the full text in a hover tooltip. Only long ones —
whether a description clips depends on the tab (the Changed tab's description column is
narrower than Uncategorized's), so the threshold adapts per tab and short descriptions don't
pop a needless tooltip.

The panel follows your edits automatically — no refresh button. Structural changes (adding,
deleting, reordering) rebuild it at once; typing rebuilds it a beat after you stop. That pause
is deliberate: the diff itself costs ~2ms on 1400 rows, but building the table widgets costs
28–45ms, which would hitch every keystroke — worst exactly when you type a keyword's first
letter and it still matches everything.

Only the visible tab is built, so switching tabs is what pays for a tab, not opening the panel.

The window defaults to 1400px wide so the rules keep their room with the panel open; drag the
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
