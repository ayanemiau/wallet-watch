"""Phase 4 resolve — the review file schema + I/O (the reusable library).

`scripts/resolve_batch.py` writes the whole categorized batch to a
`review_<runtime>.csv` for the operator to approve/correct in `tools/review_approver`.
A review row is a `Transaction` plus two workflow columns:

  - resolved_by : where the category came from — "hard" (Phase 3 hard filter,
                  trusted), "desc-map", "cat-map", "agent", or "none" (unmatched,
                  the operator fills it in). Splits the two approver tabs:
                  "hard" rows are "everything else", the rest are the review inbox.
  - approved    : hard rows default to 1 (trusted); everything else starts 0 and
                  the operator flips it. Commit is blocked while any 0 remains
                  (see all_approved / plan.md §6.3).

The human never edits `category`; corrections go to `category_override` and
`corrected_description` (both `Transaction` fields), keeping raw data isolated.
Those ride along automatically here because the row columns are Transaction's
`FIELDNAMES` plus the two workflow columns. This module is pure I/O.
"""

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

from schema import (FIELDNAMES, Transaction, decode_bool, encode_bool, from_row,
                    to_row)

RESOLVED_BY = "resolved_by"
APPROVED = "approved"

# Transaction columns first (so a Transaction round-trips), then the two workflow
# columns the review file adds.
REVIEW_FIELDNAMES = FIELDNAMES + [RESOLVED_BY, APPROVED]

# resolved_by values
BY_HARD = "hard"           # Phase 3 hard-filter hit — trusted, pre-approved
BY_DESC_MAP = "desc-map"   # path 1: description_map hit, re-matched by rules
BY_CAT_MAP = "cat-map"     # path 2: category_map hit
BY_AGENT = "agent"         # LLM proposal (deferred)
BY_NONE = "none"           # nothing matched — operator fills it in


@dataclass
class ReviewRow:
    """One review-file row: a Transaction plus the approval workflow columns."""

    txn: Transaction
    resolved_by: str = BY_NONE
    approved: bool = False


def to_review_dict(row: ReviewRow) -> Dict[str, str]:
    d = to_row(row.txn)
    d[RESOLVED_BY] = row.resolved_by
    d[APPROVED] = encode_bool(row.approved)
    return d


def from_review_dict(d: Dict[str, str]) -> ReviewRow:
    return ReviewRow(
        txn=from_row(d),
        resolved_by=d.get(RESOLVED_BY, BY_NONE) or BY_NONE,
        approved=decode_bool(d.get(APPROVED, "")),
    )


def write_review(path: Path, rows: List[ReviewRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=REVIEW_FIELDNAMES)
        w.writeheader()
        for row in rows:
            w.writerow(to_review_dict(row))


def read_review(path: Path) -> List[ReviewRow]:
    with path.open(newline="") as fh:
        return [from_review_dict(row) for row in csv.DictReader(fh)]


def all_approved(rows: List[ReviewRow]) -> bool:
    """The GATE 2 gate: commit may proceed only once this is true."""
    return all(row.approved for row in rows)
