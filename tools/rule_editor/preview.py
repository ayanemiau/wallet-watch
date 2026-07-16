"""What the rule table does to a set of transactions.

Answers the two questions the editor's footer counts can't: which rows does my
unsaved edit move, and what actually landed in each category. Pure — no UI, no
DearPyGui — so it is testable without a window.

`old` comes from the rules as SAVED on disk, `new` from the rules as edited.
The transaction's own `category` column is deliberately ignored: a
normalized.csv has it empty (categorization is a later phase), so the only
meaningful "before" is what the saved rules produce.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from rules import Rule, first_match


@dataclass
class PreviewEntry:
    """One transaction, categorized under both rule sets."""

    row: Dict[str, str]

    # category under the saved rules; None = no rule matched
    old: Optional[str]

    # category under the rules currently in the editor
    new: Optional[str]

    # index into the edited rules of the rule that won, or None
    rule_index: Optional[int]


def build_entries(baseline: List[Rule], live: List[Rule],
                  rows: List[Dict[str, str]]) -> List[PreviewEntry]:
    """Categorize every row under both rule sets. Source order is preserved."""
    entries = []
    for row in rows:
        old_i = first_match(baseline, row)
        new_i = first_match(live, row)
        entries.append(PreviewEntry(
            row=row,
            old=baseline[old_i].category if old_i is not None else None,
            new=live[new_i].category if new_i is not None else None,
            rule_index=new_i,
        ))
    return entries


def changed(entries: List[PreviewEntry]) -> List[PreviewEntry]:
    """Rows whose category the unsaved edits move.

    A row no rule has ever matched has old == new == None and is NOT a change.
    On a first run the saved rules are empty, so every categorized row shows up
    here as None -> X; that is correct, not a special case.
    """
    return [e for e in entries if e.old != e.new]


def uncategorized(entries: List[PreviewEntry]) -> List[PreviewEntry]:
    """Rows the edited rules leave uncategorized — the worklist for new rules."""
    return [e for e in entries if e.new is None]


def group_by_category(entries: List[PreviewEntry]) -> List[Tuple[str, List[PreviewEntry]]]:
    """Categorized rows grouped by category, biggest group first.

    Ties break on name so tab order is stable between refreshes — otherwise
    two equal-sized categories could swap places on every keystroke.
    """
    groups: Dict[str, List[PreviewEntry]] = {}
    for entry in entries:
        if entry.new is not None:
            groups.setdefault(entry.new, []).append(entry)
    return sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))
