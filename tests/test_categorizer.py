"""Categorizer (tier 3a) tests. All data here is synthetic — never real merchants.

These exercise lib/categorizer.py over the same lib/rules.py engine the editor's
preview uses, so passing here is the pipeline half of the "editor and pipeline
agree" contract (the engine itself is covered by tools/rule_editor/tests).
"""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
LIB = REPO / "lib"
SCRIPTS = REPO / "scripts"

sys.path.insert(0, str(LIB))
sys.path.insert(0, str(SCRIPTS))

from categorizer import CATEGORIZER_VERSION, Categorizer  # noqa: E402
from rules import Condition, Rule, save_rules  # noqa: E402
from schema import CategorySource, Transaction  # noqa: E402


def txn(**kw) -> Transaction:
    base = dict(date="2026-01-05", amount="-12.50", account="Fake Card",
                original_description="FAKE COFFEE CO #123")
    base.update(kw)
    return Transaction(**base)


def write_rules(tmp_path: Path, rules) -> str:
    path = tmp_path / "rules" / "keywords.yaml"
    save_rules(path, rules)
    return str(path)


def coffee_rule(category="Coffee", value="FAKE COFFEE"):
    return Rule(category=category, match="all",
                conditions=[Condition("original_description", "contains", value)])


# --- basic application ---


def test_fills_category_from_matching_rule(tmp_path):
    cfg = write_rules(tmp_path, [coffee_rule()])
    (result,) = Categorizer().apply_rules([txn()], cfg)
    assert result.category == "Coffee"


def test_unmatched_row_keeps_empty_category(tmp_path):
    cfg = write_rules(tmp_path, [coffee_rule(value="FAKE GROCER")])
    (result,) = Categorizer().apply_rules([txn()], cfg)
    assert result.category == ""


def test_hard_filter_hit_stamps_filter_rules(tmp_path):
    # a rule match is the Phase 3 hard filter (plan.md §6.4): category_source
    # becomes FILTER_RULES, overwriting any prior source on the input row
    cfg = write_rules(tmp_path, [coffee_rule()])
    (result,) = Categorizer().apply_rules(
        [txn(category_source=CategorySource.DICT_MATCH)], cfg)
    assert result.category == "Coffee"
    assert result.category_source is CategorySource.FILTER_RULES


def test_first_matching_rule_wins(tmp_path):
    cfg = write_rules(tmp_path, [
        coffee_rule(category="Coffee", value="FAKE"),
        coffee_rule(category="Groceries", value="FAKE"),
    ])
    (result,) = Categorizer().apply_rules([txn()], cfg)
    assert result.category == "Coffee"


def test_any_vs_all_match_modes(tmp_path):
    any_rule = Rule(category="Drinks", match="any", conditions=[
        Condition("original_description", "contains", "SAMPLE ROASTERS"),
        Condition("original_description", "contains", "FAKE COFFEE"),
    ])
    cfg = write_rules(tmp_path, [any_rule])
    assert Categorizer().apply_rules([txn()], cfg)[0].category == "Drinks"
    # neither condition holds -> no match
    other = txn(original_description="FAKE GROCER")
    assert Categorizer().apply_rules([other], cfg)[0].category == ""


def test_matches_on_non_description_columns(tmp_path):
    # a numeric amount rule proves the engine sees to_row's full column set,
    # not just the description
    cfg = write_rules(tmp_path, [
        Rule(category="Refund", match="all",
             conditions=[Condition("amount", "gt", "0")]),
    ])
    assert Categorizer().apply_rules([txn(amount="50.00")], cfg)[0].category == "Refund"
    assert Categorizer().apply_rules([txn(amount="-50.00")], cfg)[0].category == ""


# --- everything but category is preserved ---


def test_other_columns_unchanged(tmp_path):
    cfg = write_rules(tmp_path, [coffee_rule()])
    original = txn(is_reference=True, corrected_description="corrected",
                   tags=["trip"], category="")
    (result,) = Categorizer().apply_rules([original], cfg)
    assert result.category == "Coffee"
    # a hit sets only category + category_source; every other field is identical
    from dataclasses import replace
    assert replace(result, category="",
                   category_source=CategorySource.NONE) == original


def test_input_transactions_not_mutated(tmp_path):
    cfg = write_rules(tmp_path, [coffee_rule()])
    original = txn()
    Categorizer().apply_rules([original], cfg)
    assert original.category == ""


def test_order_is_preserved(tmp_path):
    cfg = write_rules(tmp_path, [coffee_rule()])
    rows = [txn(original_description="FAKE COFFEE 1"),
            txn(original_description="FAKE GROCER"),
            txn(original_description="FAKE COFFEE 2")]
    result = Categorizer().apply_rules(rows, cfg)
    assert [r.category for r in result] == ["Coffee", "", "Coffee"]


# --- edge cases ---


def test_empty_rule_table_leaves_all_uncategorized(tmp_path):
    cfg = write_rules(tmp_path, [])
    result = Categorizer().apply_rules([txn(), txn()], cfg)
    assert all(r.category == "" for r in result)


def test_empty_transaction_list(tmp_path):
    cfg = write_rules(tmp_path, [coffee_rule()])
    assert Categorizer().apply_rules([], cfg) == []


def test_missing_rule_file_is_a_clean_error(tmp_path):
    # load_rules treats a missing file as an empty rule list (first run), so this
    # is not an error — everything stays uncategorized
    missing = str(tmp_path / "rules" / "keywords.yaml")
    result = Categorizer().apply_rules([txn()], missing)
    assert result[0].category == ""


def test_categorizer_version_is_exposed():
    assert isinstance(CATEGORIZER_VERSION, int)
