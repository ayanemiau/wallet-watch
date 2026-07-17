"""schema round-trip tests, focused on the categorize_method column (plan.md §6.4).
All data here is synthetic."""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))

from schema import (FIELDNAMES, Transaction, decode_method, encode_method,  # noqa: E402
                    from_row, to_row)


def txn(**kw) -> Transaction:
    base = dict(date="2026-01-05", amount="-12.50", account="Fake Card",
                original_description="FAKE COFFEE CO #123")
    base.update(kw)
    return Transaction(**base)


def test_categorize_method_is_a_fieldname_before_tags():
    assert "categorize_method" in FIELDNAMES
    assert FIELDNAMES.index("categorize_method") < FIELDNAMES.index("tags")
    assert FIELDNAMES.index("category") < FIELDNAMES.index("categorize_method")


def test_default_method_is_zero():
    assert txn().categorize_method == 0


@pytest.mark.parametrize("method", [0, 1, 2])
def test_round_trips_through_a_row(method):
    original = txn(category="Coffee", categorize_method=method)
    assert from_row(to_row(original)) == original


def test_missing_column_reads_as_zero():
    # a CSV written before the column existed still loads (tolerate missing)
    row = to_row(txn())
    del row["categorize_method"]
    assert from_row(row).categorize_method == 0


def test_encode_decode_method():
    assert encode_method(2) == "2"
    assert decode_method("2") == 2
    assert decode_method("") == 0


def test_decode_method_rejects_non_integer():
    with pytest.raises(ValueError):
        decode_method("nope")
