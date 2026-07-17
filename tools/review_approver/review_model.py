"""Pure, no-UI logic for the review approver — the testable half.

Mirrors tools/rule_editor/preview.py: all tab-splitting / counting / dirty logic
lives here so the tests exercise it without opening a DearPyGui window. Operates
on already-loaded `List[ReviewRow]` (see lib/resolve_review.py); imports nothing
from dearpygui and does no file I/O (read_review / write_review live in the lib).
"""

from dataclasses import dataclass
from typing import List, Tuple

from resolve_review import BY_HARD, ReviewRow
from schema import effective_category


def needs_review(row: ReviewRow) -> bool:
    """A "Review inbox" row: anything that is not a trusted hard-filter hit."""
    return row.resolved_by != BY_HARD


def is_uncategorized(row: ReviewRow) -> bool:
    """No category will commit for this row — neither machine nor human set one."""
    return not effective_category(row.txn)


def split_tabs(rows: List[ReviewRow]) -> Tuple[List[ReviewRow], List[ReviewRow]]:
    """(review inbox, everything else), each preserving input order."""
    inbox = [r for r in rows if needs_review(r)]
    rest = [r for r in rows if not needs_review(r)]
    return inbox, rest


@dataclass
class Counts:
    total: int          # every row
    hard: int           # trusted hard-filter rows (the "everything else" tab)
    inbox: int          # rows needing review
    pending: int        # rows not yet approved — blocks commit
    uncategorized: int  # rows with no effective category


def counts(rows: List[ReviewRow]) -> Counts:
    inbox, rest = split_tabs(rows)
    return Counts(
        total=len(rows),
        hard=len(rest),
        inbox=len(inbox),
        pending=sum(1 for r in rows if not r.approved),
        uncategorized=sum(1 for r in rows if is_uncategorized(r)),
    )


def is_dirty(rows: List[ReviewRow], baseline: List[ReviewRow]) -> bool:
    """True if the in-memory rows differ from the on-load snapshot."""
    return rows != baseline


# --- category autocomplete (candidate word list) ---


def candidates(rows: List[ReviewRow]) -> List[str]:
    """Distinct non-empty category values in the file, alphabetical (casefold).

    Pooled from BOTH `category` and `category_override` — every category-like value
    the reviewer might want to reuse. Recomputed on demand, so a name just typed on
    one row is immediately offered on the next.
    """
    seen = {value
            for r in rows
            for value in (r.txn.category, r.txn.category_override)
            if value}
    return sorted(seen, key=str.casefold)


def match_candidates(cands: List[str], query: str) -> List[str]:
    """Case-insensitive SUBSTRING filter (not prefix, not fuzzy).

    An empty query returns all candidates (order preserved).
    """
    q = query.casefold()
    if not q:
        return list(cands)
    return [c for c in cands if q in c.casefold()]
