"""Phase 4 resolve — the review inbox schema + I/O (the reusable library).

Every row Phase 4 touches (every non-hard-matched transaction) is written to a
batch's `review_inbox_<runtime>.csv` for manual approval. An inbox row is a
`Transaction` plus two workflow columns:

  - resolved_by : where the (proposed) category came from — "desc-map",
                  "cat-map", "agent", or "none" — so the operator can triage and
                  batch-approve by source.
  - approved    : 0 by default; the operator (or the review_approver UI) flips it
                  to 1. Commit is blocked while any 0 remains (plan.md §6.3).

The columns are Transaction's `FIELDNAMES` plus those two, so a Transaction reads
back out of an inbox row unchanged. This module is pure I/O; the logic that fills
`resolved_by`/`category` lives in `lib/resolver.py`.
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
# columns the inbox adds.
INBOX_FIELDNAMES = FIELDNAMES + [RESOLVED_BY, APPROVED]

# resolved_by values
BY_DESC_MAP = "desc-map"   # path 1: description_map hit, re-matched by rules
BY_CAT_MAP = "cat-map"     # path 2: category_map hit
BY_AGENT = "agent"         # LLM proposal (deferred)
BY_NONE = "none"           # nothing matched — operator fills it in


@dataclass
class InboxRow:
    """One review-inbox row: a Transaction plus the approval workflow columns."""

    txn: Transaction
    resolved_by: str = BY_NONE
    approved: bool = False


def to_inbox_dict(row: InboxRow) -> Dict[str, str]:
    d = to_row(row.txn)
    d[RESOLVED_BY] = row.resolved_by
    d[APPROVED] = encode_bool(row.approved)
    return d


def from_inbox_dict(d: Dict[str, str]) -> InboxRow:
    return InboxRow(
        txn=from_row(d),
        resolved_by=d.get(RESOLVED_BY, BY_NONE) or BY_NONE,
        approved=decode_bool(d.get(APPROVED, "")),
    )


def write_inbox(path: Path, rows: List[InboxRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=INBOX_FIELDNAMES)
        w.writeheader()
        for row in rows:
            w.writerow(to_inbox_dict(row))


def read_inbox(path: Path) -> List[InboxRow]:
    with path.open(newline="") as fh:
        return [from_inbox_dict(row) for row in csv.DictReader(fh)]


def all_approved(rows: List[InboxRow]) -> bool:
    """The GATE 2 gate: commit may proceed only once this is true."""
    return all(row.approved for row in rows)
