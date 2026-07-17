"""Resolver tests (plan.md §6.1–6.2). All merchants/descriptions are synthetic."""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))

from resolve_lookup import Lookup  # noqa: E402
from resolve_review import BY_CAT_MAP, BY_DESC_MAP, BY_NONE  # noqa: E402
from resolver import Resolver, unmatched  # noqa: E402
from rules import Condition, Rule  # noqa: E402
from schema import Transaction  # noqa: E402


def txn(desc="SQ *BLUE BOTTLE #99", category="", **kw) -> Transaction:
    base = dict(date="2026-01-05", amount="-6.00", account="Fake Card",
                original_description=desc, category=category)
    base.update(kw)
    return Transaction(**base)


def coffee_rules():
    # a keyword rule authored against original_description (Phase 3 convention)
    return [Rule(category="Coffee", match="all",
                 conditions=[Condition("original_description", "contains", "blue bottle")])]


def desc_map(desc="SQ *BLUE BOTTLE #1", value="Blue Bottle Coffee") -> Lookup:
    lk = Lookup()
    lk.put(desc, value)
    return lk


def cat_map(desc="ZELLE TO SAM 8842", value="Transfers") -> Lookup:
    lk = Lookup()
    lk.put(desc, value)
    return lk


# --- path 1: updated description, re-matched by rules ---


def test_description_map_hit_rematches_to_method_1():
    r = Resolver(coffee_rules(), desc_map(), Lookup())
    (row,) = r.resolve([txn()])
    assert row.resolved_by == BY_DESC_MAP
    assert row.txn.category == "Coffee"
    assert row.txn.categorize_method == 1
    assert row.txn.corrected_description == "Blue Bottle Coffee"
    assert row.txn.original_description == "SQ *BLUE BOTTLE #99"   # unchanged
    assert row.approved is False                                  # always needs approval


def test_description_map_hit_but_no_rule_falls_through():
    # corrected description recorded, but nothing categorizes it -> manual
    r = Resolver([], desc_map(), Lookup())
    (row,) = r.resolve([txn()])
    assert row.resolved_by == BY_NONE
    assert row.txn.corrected_description == "Blue Bottle Coffee"  # fix kept
    assert row.txn.category == ""
    assert row.txn.categorize_method == 0


# --- path 2: direct category ---


def test_category_map_hit_is_method_2():
    r = Resolver(coffee_rules(), Lookup(), cat_map())
    (row,) = r.resolve([txn(desc="ZELLE TO SAM 0001")])
    assert row.resolved_by == BY_CAT_MAP
    assert row.txn.category == "Transfers"
    assert row.txn.categorize_method == 2
    assert row.approved is False


def test_description_map_wins_over_category_map():
    # both maps hit the same merchant; the re-matchable description path wins
    dm = desc_map(desc="SQ *BLUE BOTTLE #1", value="Blue Bottle Coffee")
    cm = cat_map(desc="SQ *BLUE BOTTLE #1", value="Misc")
    (row,) = Resolver(coffee_rules(), dm, cm).resolve([txn()])
    assert row.resolved_by == BY_DESC_MAP
    assert row.txn.category == "Coffee"


def test_desc_map_no_rule_then_falls_to_category_map():
    dm = desc_map(desc="SQ *BLUE BOTTLE #1", value="Bluebottle")  # no rule matches this
    cm = cat_map(desc="SQ *BLUE BOTTLE #1", value="Coffee")
    (row,) = Resolver([], dm, cm).resolve([txn()])
    assert row.resolved_by == BY_CAT_MAP
    assert row.txn.category == "Coffee"
    assert row.txn.categorize_method == 2
    assert row.txn.corrected_description == "Bluebottle"          # path-1 fix still kept


# --- no match ---


def test_no_map_hit_is_none_and_uncategorized():
    (row,) = Resolver(coffee_rules(), Lookup(), Lookup()).resolve([txn(desc="MYSTERY LLC")])
    assert row.resolved_by == BY_NONE
    assert row.txn.category == ""


def test_inputs_not_mutated():
    original = txn()
    Resolver(coffee_rules(), desc_map(), Lookup()).resolve([original])
    assert original.category == ""
    assert original.corrected_description == ""
    assert original.categorize_method == 0


def test_order_preserved_and_one_row_out_per_row_in():
    rows = [txn(desc="SQ *BLUE BOTTLE #1"), txn(desc="MYSTERY LLC"),
            txn(desc="ZELLE TO SAM 5")]
    out = Resolver(coffee_rules(), desc_map(), cat_map()).resolve(rows)
    assert [r.resolved_by for r in out] == [BY_DESC_MAP, BY_NONE, BY_CAT_MAP]


# --- unmatched() helper ---


def test_unmatched_filters_out_categorized_rows():
    rows = [txn(category="Coffee"), txn(desc="MYSTERY LLC"), txn(category="Rent")]
    assert [t.original_description for t in unmatched(rows)] == ["MYSTERY LLC"]
