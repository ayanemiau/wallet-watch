"""schema round-trip tests, focused on the category_source enum + category_override
(plan.md §6.4). All data here is synthetic."""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))

from schema import (CategorySource, FIELDNAMES, Transaction, decode_source,  # noqa: E402
                    effective_category, encode_source, from_row, to_row)


def txn(**kw) -> Transaction:
    base = dict(date="2026-01-05", amount="-12.50", account="Fake Card",
                original_description="FAKE COFFEE CO #123")
    base.update(kw)
    return Transaction(**base)


# --- category_source enum ---


def test_category_source_is_a_fieldname_before_tags():
    assert "category_source" in FIELDNAMES
    assert FIELDNAMES.index("category_source") < FIELDNAMES.index("tags")
    assert FIELDNAMES.index("category") < FIELDNAMES.index("category_source")


def test_default_source_is_none():
    assert txn().category_source is CategorySource.NONE


@pytest.mark.parametrize("source", list(CategorySource))
def test_round_trips_through_a_row(source):
    original = txn(category="Coffee", category_source=source)
    assert from_row(to_row(original)) == original


def test_kebab_values_on_disk():
    # self-documenting kebab values, consistent with resolved_by / account types
    assert encode_source(CategorySource.FILTER_RULES) == "filter-rules"
    assert encode_source(CategorySource.DICT_MATCH) == "dict-match"
    assert encode_source(CategorySource.LLM_LABEL) == "llm-label"
    assert encode_source(CategorySource.HUMAN_REVIEW) == "human-review"
    assert encode_source(CategorySource.NONE) == ""
    # the written cell is the kebab string, never the member repr
    assert to_row(txn(category="Coffee",
                      category_source=CategorySource.HUMAN_REVIEW))["category_source"] \
        == "human-review"


def test_decode_source():
    assert decode_source("dict-match") is CategorySource.DICT_MATCH
    assert decode_source("") is CategorySource.NONE


def test_missing_column_reads_as_none():
    # a CSV written before the column existed still loads (tolerate missing)
    row = to_row(txn())
    del row["category_source"]
    assert from_row(row).category_source is CategorySource.NONE


def test_decode_source_rejects_unknown_token():
    with pytest.raises(ValueError):
        decode_source("banana")


# --- category_override (human override column) ---


def test_category_override_between_category_and_source():
    assert FIELDNAMES.index("category") < FIELDNAMES.index("category_override")
    assert FIELDNAMES.index("category_override") < FIELDNAMES.index("category_source")


def test_category_override_round_trips():
    original = txn(category="Coffee", category_override="Cafes")
    assert from_row(to_row(original)) == original


def test_missing_override_column_reads_as_empty():
    row = to_row(txn(category="Coffee"))
    del row["category_override"]
    assert from_row(row).category_override == ""


def test_effective_category_prefers_override():
    assert effective_category(txn(category="Coffee", category_override="Cafes")) == "Cafes"
    assert effective_category(txn(category="Coffee")) == "Coffee"
    assert effective_category(txn()) == ""
