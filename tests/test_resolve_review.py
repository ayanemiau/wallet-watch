"""Review-inbox schema + I/O tests (plan.md §6.3). Synthetic data only."""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))

from resolve_review import (APPROVED, BY_CAT_MAP, BY_DESC_MAP, INBOX_FIELDNAMES,  # noqa: E402
                            RESOLVED_BY, InboxRow, all_approved, from_inbox_dict,
                            read_inbox, to_inbox_dict, write_inbox)
from schema import FIELDNAMES, Transaction  # noqa: E402


def txn(**kw) -> Transaction:
    base = dict(date="2026-01-05", amount="-6.00", account="Fake Card",
                original_description="MYSTERY LLC")
    base.update(kw)
    return Transaction(**base)


def test_inbox_fieldnames_extend_transaction_columns():
    assert INBOX_FIELDNAMES == FIELDNAMES + [RESOLVED_BY, APPROVED]


def test_row_round_trips_including_workflow_columns():
    row = InboxRow(txn(category="Coffee", categorize_method=1,
                       corrected_description="Blue Bottle Coffee"),
                   resolved_by=BY_DESC_MAP, approved=True)
    back = from_inbox_dict(to_inbox_dict(row))
    assert back == row


def test_approved_defaults_to_zero_on_disk():
    d = to_inbox_dict(InboxRow(txn(), resolved_by=BY_CAT_MAP))
    assert d[APPROVED] == "0"          # 1/0, never true/false
    assert d[RESOLVED_BY] == BY_CAT_MAP


def test_transaction_reads_back_unchanged_from_an_inbox_row():
    t = txn(category="Transfers", categorize_method=2)
    assert from_inbox_dict(to_inbox_dict(InboxRow(t, resolved_by=BY_CAT_MAP))).txn == t


def test_write_then_read(tmp_path):
    rows = [InboxRow(txn(), resolved_by="none"),
            InboxRow(txn(category="Coffee", categorize_method=1),
                     resolved_by=BY_DESC_MAP, approved=True)]
    path = tmp_path / "batch" / "review_inbox_20260101_000000.csv"
    write_inbox(path, rows)
    assert read_inbox(path) == rows
    assert path.read_text().splitlines()[0] == ",".join(INBOX_FIELDNAMES)


def test_all_approved_gate():
    a = InboxRow(txn(), approved=True)
    b = InboxRow(txn(), approved=False)
    assert all_approved([a, a]) is True
    assert all_approved([a, b]) is False
    assert all_approved([]) is True     # nothing to approve -> commit may proceed
