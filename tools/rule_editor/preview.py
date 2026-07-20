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
from decimal import Decimal, InvalidOperation
from typing import Dict, List, Optional, Set, Tuple

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
    """Categorized rows grouped by category, sorted alphabetically by name.

    The editor lists these in a Categories dropdown, and with ~20 hierarchical
    `<main>/<sub>` names alphabetical is what you can scan. casefold() so
    `Food/Dining` and `food/snacks` order naturally; alphabetical order is also
    inherently stable, so the group order never reshuffles between refreshes.
    """
    groups: Dict[str, List[PreviewEntry]] = {}
    for entry in entries:
        if entry.new is not None:
            groups.setdefault(entry.new, []).append(entry)
    return sorted(groups.items(), key=lambda kv: kv[0].casefold())


# --- panel filtering & sorting -------------------------------------------
#
# A pure view transform the editor lays over the current snapshot: filter by
# account and/or amount, then sort by any column. It never touches the rules or
# the categories — only which rows show and in what order.

# amount-filter kinds; the order matches the radio options in the editor's UI
AMOUNT_ALL, AMOUNT_LT100, AMOUNT_MID, AMOUNT_GT500, AMOUNT_CUSTOM = (
    "all", "lt100", "100to500", "gt500", "custom")


def cell_amount(entry: PreviewEntry) -> Optional[Decimal]:
    """The row's amount as a Decimal, or None for a non-numeric cell.

    Mirrors the rule engine's numeric convention (rules.py): a blank or
    "pending" amount simply drops out of a numeric filter rather than raising.
    """
    try:
        return Decimal(entry.row.get("amount", ""))
    except InvalidOperation:
        return None


def distinct_accounts(rows: List[Dict[str, str]]) -> List[str]:
    """Sorted unique account names across all preview rows (tab-independent)."""
    return sorted({r.get("account", "") for r in rows})


def amount_ok(value: Optional[Decimal], kind: str,
              lo: Optional[Decimal], hi: Optional[Decimal]) -> bool:
    """Whether an amount passes the amount filter, compared on MAGNITUDE.

    Spend is stored negative, so the operator's "< 100" / "> 500" mean the size
    of the transaction, not the signed value; abs() gives that.
    """
    if kind == AMOUNT_ALL:
        return True
    if value is None:
        return False
    m = abs(value)
    if kind == AMOUNT_LT100:
        return m < 100
    if kind == AMOUNT_MID:
        return 100 <= m <= 500
    if kind == AMOUNT_GT500:
        return m > 500
    if kind == AMOUNT_CUSTOM:
        return (lo is None or m >= lo) and (hi is None or m <= hi)
    return True


def sort_value(entry: PreviewEntry, column: str):
    """A comparable key for a display column. None (blank amount/rule) sorts last.

    Amount sorts on the SIGNED Decimal (natural ledger order, unlike the
    magnitude-based filter); text columns casefold; dates are ISO strings that
    already sort chronologically.
    """
    row = entry.row
    if column == "amount":
        return cell_amount(entry)
    if column == "rule":
        return entry.rule_index
    if column == "date":
        return row.get("date", "")
    if column == "account":
        return row.get("account", "").casefold()
    if column == "description":
        return row.get("original_description", "").casefold()
    if column == "from":
        return (entry.old or "").casefold()
    if column == "to":
        return (entry.new or "").casefold()
    return ""


def account_counts(entries: List[PreviewEntry]) -> Dict[str, int]:
    """How many of these entries each account holds — for the filter's `(n)` labels."""
    counts: Dict[str, int] = {}
    for e in entries:
        a = e.row.get("account", "")
        counts[a] = counts.get(a, 0) + 1
    return counts


def amount_counts(entries: List[PreviewEntry],
                  lo: Optional[Decimal], hi: Optional[Decimal]) -> Dict[str, int]:
    """How many entries fall in each amount kind — for the filter's `(n)` labels.

    Counts the full entry list (not the already-filtered view), so the numbers show
    the distribution to pick from. Custom uses the current lo/hi bounds.
    """
    vals = [cell_amount(e) for e in entries]
    kinds = (AMOUNT_ALL, AMOUNT_LT100, AMOUNT_MID, AMOUNT_GT500, AMOUNT_CUSTOM)
    return {k: sum(1 for v in vals if amount_ok(v, k, lo, hi)) for k in kinds}


def rule_categories(rules: List[Rule]) -> List[str]:
    """Distinct non-empty categories across the rules, alphabetical (casefold).

    The candidate list for the rule-search dropdown: every category some rule
    already assigns, deduped. Recomputed on demand so a name just typed on one
    rule is offered immediately.
    """
    return sorted({r.category for r in rules if r.category}, key=str.casefold)


def match_categories(cats: List[str], query: str) -> List[str]:
    """Case-insensitive SUBSTRING filter; an empty query returns all (order kept)."""
    q = query.casefold()
    if not q:
        return list(cats)
    return [c for c in cats if q in c.casefold()]


def view_entries(entries: List[PreviewEntry],
                 accounts: Optional[Set[str]],
                 amount: Optional[Tuple[str, Optional[Decimal], Optional[Decimal]]],
                 sort_col: Optional[str],
                 sort_asc: bool) -> List[PreviewEntry]:
    """Filter (account AND amount) then sort.

    accounts=None means every account; amount=None means no amount filter;
    sort_col=None keeps source order. Rows whose sort key is None (a blank
    amount or an unmatched rule) always land last, in either direction.
    """
    out = entries
    if accounts is not None:
        out = [e for e in out if e.row.get("account", "") in accounts]
    if amount is not None:
        kind, lo, hi = amount
        out = [e for e in out if amount_ok(cell_amount(e), kind, lo, hi)]
    if sort_col:
        present = [e for e in out if sort_value(e, sort_col) is not None]
        missing = [e for e in out if sort_value(e, sort_col) is None]
        present.sort(key=lambda e: sort_value(e, sort_col), reverse=not sort_asc)
        out = present + missing
    return out
