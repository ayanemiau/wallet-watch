"""Review-file schema + I/O tests (plan.md §6.3). Synthetic data only."""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))

from resolve_review import (APPROVED, BY_CAT_MAP, BY_DESC_MAP, BY_HARD,  # noqa: E402
                            REVIEW_FIELDNAMES, RESOLVED_BY, ReviewRow, all_approved,
                            from_review_dict, read_review, to_review_dict, write_review)
from schema import CategorySource, FIELDNAMES, Transaction  # noqa: E402


def txn(**kw) -> Transaction:
    base = dict(date="2026-01-05", amount="-6.00", account="Fake Card",
                original_description="MYSTERY LLC")
    base.update(kw)
    return Transaction(**base)


def test_review_fieldnames_extend_transaction_columns():
    assert REVIEW_FIELDNAMES == FIELDNAMES + [RESOLVED_BY, APPROVED]
    assert "category_override" in REVIEW_FIELDNAMES   # human column rides along


def test_row_round_trips_including_workflow_columns():
    row = ReviewRow(txn(category="Coffee", category_source=CategorySource.FILTER_RULES,
                        corrected_description="Blue Bottle Coffee",
                        category_override="Cafes"),
                    resolved_by=BY_DESC_MAP, approved=True)
    assert from_review_dict(to_review_dict(row)) == row


def test_hard_row_round_trips():
    row = ReviewRow(txn(category="Coffee"), resolved_by=BY_HARD, approved=True)
    assert from_review_dict(to_review_dict(row)) == row


def test_approved_defaults_to_zero_on_disk():
    d = to_review_dict(ReviewRow(txn(), resolved_by=BY_CAT_MAP))
    assert d[APPROVED] == "0"          # 1/0, never true/false
    assert d[RESOLVED_BY] == BY_CAT_MAP


def test_transaction_reads_back_unchanged_from_a_review_row():
    t = txn(category="Transfers", category_source=CategorySource.DICT_MATCH,
            category_override="Friends")
    assert from_review_dict(to_review_dict(ReviewRow(t, resolved_by=BY_CAT_MAP))).txn == t


def test_write_then_read(tmp_path):
    rows = [ReviewRow(txn(), resolved_by="none"),
            ReviewRow(txn(category="Coffee", category_source=CategorySource.FILTER_RULES),
                      resolved_by=BY_DESC_MAP, approved=True)]
    path = tmp_path / "batch" / "review_20260101_000000.csv"
    write_review(path, rows)
    assert read_review(path) == rows
    assert path.read_text().splitlines()[0] == ",".join(REVIEW_FIELDNAMES)


def test_all_approved_gate():
    a = ReviewRow(txn(), approved=True)
    b = ReviewRow(txn(), approved=False)
    assert all_approved([a, a]) is True
    assert all_approved([a, b]) is False
    assert all_approved([]) is True     # nothing to approve -> commit may proceed
