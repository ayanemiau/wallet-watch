"""review_model + review-file round-trip tests. Synthetic data only, no window.

conftest.py puts lib/ and the tool dir on sys.path; nothing here imports
dearpygui or approver.py (the UI file is never imported by the suite).
"""

import copy
import sys
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TOOL))

from resolve_review import (BY_CAT_MAP, BY_DESC_MAP, BY_HARD, BY_NONE, ReviewRow,  # noqa: E402
                            read_review, write_review)
from review_model import (candidates, counts, is_dirty, is_uncategorized,  # noqa: E402
                          match_candidates, needs_review, split_tabs)
from schema import CategorySource, Transaction  # noqa: E402


def txn(desc="MYSTERY LLC", category="", override="", **kw) -> Transaction:
    base = dict(date="2026-01-05", amount="-6.00", account="Fake Card",
                original_description=desc, category=category, category_override=override)
    base.update(kw)
    return Transaction(**base)


def sample():
    return [
        ReviewRow(txn("STARBUCKS", category="Coffee",
                      category_source=CategorySource.FILTER_RULES), BY_HARD, approved=True),
        ReviewRow(txn("SQ *BLUE BOTTLE", category="Coffee",
                      category_source=CategorySource.FILTER_RULES), BY_DESC_MAP, approved=False),
        ReviewRow(txn("ZELLE 1", category="Transfers",
                      category_source=CategorySource.DICT_MATCH), BY_CAT_MAP, approved=False),
        ReviewRow(txn("WHO KNOWS"), BY_NONE, approved=False),
    ]


# --- tab split ---


def test_needs_review_is_everything_but_hard():
    rows = sample()
    assert [needs_review(r) for r in rows] == [False, True, True, True]


def test_split_tabs_preserves_order():
    inbox, rest = split_tabs(sample())
    assert [r.txn.original_description for r in inbox] == \
        ["SQ *BLUE BOTTLE", "ZELLE 1", "WHO KNOWS"]
    assert [r.txn.original_description for r in rest] == ["STARBUCKS"]


# --- uncategorized respects the override ---


def test_is_uncategorized_true_when_no_category_anywhere():
    assert is_uncategorized(ReviewRow(txn("WHO KNOWS"))) is True


def test_override_makes_a_row_categorized():
    assert is_uncategorized(ReviewRow(txn("WHO KNOWS", override="Gifts"))) is False


def test_machine_category_makes_a_row_categorized():
    assert is_uncategorized(ReviewRow(txn("STARBUCKS", category="Coffee"))) is False


# --- counts ---


def test_counts():
    c = counts(sample())
    assert (c.total, c.hard, c.inbox) == (4, 1, 3)
    assert c.pending == 3                # the three non-hard rows start unapproved
    assert c.uncategorized == 1          # only WHO KNOWS has no category


# --- dirty ---


def test_is_dirty_detects_edits():
    rows = sample()
    base = copy.deepcopy(rows)
    assert is_dirty(rows, base) is False
    rows[3].txn.category_override = "Gifts"
    assert is_dirty(rows, base) is True


# --- round-trip through the review file (the save/reload contract) ---


def test_round_trip_persists_edits_and_isolates_raw(tmp_path):
    rows = sample()
    # the operator's two edits + an approval
    rows[3].txn.category_override = "Gifts"
    rows[3].txn.corrected_description = "A Friend"
    rows[3].approved = True

    path = tmp_path / "batch" / "review_20260101_000000.csv"
    write_review(path, rows)
    back = read_review(path)

    assert back == rows                                   # ReviewRow equality (incl. txn)
    edited = back[3]
    assert edited.txn.category_override == "Gifts"
    assert edited.txn.corrected_description == "A Friend"
    assert edited.approved is True
    assert edited.txn.original_description == "WHO KNOWS"  # raw untouched
    assert edited.txn.category == ""                       # machine column untouched


def test_header_has_override_column(tmp_path):
    path = tmp_path / "review.csv"
    write_review(path, sample())
    header = path.read_text().splitlines()[0]
    assert "category_override" in header
    assert "resolved_by" in header and "approved" in header


# --- category autocomplete candidates ---


def test_candidates_distinct_sorted_from_both_columns():
    rows = [
        ReviewRow(txn(category="Coffee")),
        ReviewRow(txn(category="rent")),
        ReviewRow(txn(category="Coffee")),           # dup
        ReviewRow(txn(category="", override="Reimbursed")),  # from the override column
        ReviewRow(txn(category="", override="")),    # nothing to contribute
    ]
    # distinct, case-insensitive alphabetical, empties dropped, both columns pooled
    assert candidates(rows) == ["Coffee", "Reimbursed", "rent"]


def test_candidates_empty_when_no_categories():
    assert candidates([ReviewRow(txn()), ReviewRow(txn())]) == []


def test_match_candidates_is_case_insensitive_substring():
    cands = ["Coffee", "Groceries", "Grocery Run", "Rent"]
    assert match_candidates(cands, "gr") == ["Groceries", "Grocery Run"]
    assert match_candidates(cands, "ERY") == ["Grocery Run"]  # 'ery' only in Grocery, not Groceries
    assert match_candidates(cands, "ent") == ["Rent"]         # substring, not prefix-only


def test_match_candidates_empty_query_returns_all():
    cands = ["Coffee", "Rent"]
    assert match_candidates(cands, "") == cands


def test_match_candidates_is_not_fuzzy():
    # a subsequence that is not a contiguous substring must NOT match
    assert match_candidates(["Grocery"], "grcy") == []
    assert match_candidates(["Grocery"], "xyz") == []
