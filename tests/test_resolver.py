"""Resolver tests (plan.md §6.1–6.2). All merchants/descriptions are synthetic."""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))

from resolve_lookup import CategoryLookup, Lookup, norm_key  # noqa: E402
from resolve_review import BY_CAT_MAP, BY_DESC_MAP, BY_HARD, BY_NONE  # noqa: E402
from resolver import Resolver, unmatched  # noqa: E402
from rules import Condition, Rule  # noqa: E402
from schema import CategorySource, Transaction  # noqa: E402


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


def cat_map(desc="ZELLE TO SAM 8842", value="Transfers") -> CategoryLookup:
    # a merchant-wildcard entry (matches any amount)
    return CategoryLookup({norm_key(desc): value})


# --- path 1: updated description, re-matched by rules ---


def test_description_map_hit_rematches_to_filter_rules():
    r = Resolver(coffee_rules(), desc_map(), CategoryLookup())
    (row,) = r.resolve([txn()])
    assert row.resolved_by == BY_DESC_MAP
    assert row.txn.category == "Coffee"
    assert row.txn.category_source is CategorySource.FILTER_RULES
    assert row.txn.corrected_description == "Blue Bottle Coffee"
    assert row.txn.original_description == "SQ *BLUE BOTTLE #99"   # unchanged
    assert row.approved is False                                  # always needs approval


def test_description_map_hit_but_no_rule_falls_through():
    # corrected description recorded, but nothing categorizes it -> manual
    r = Resolver([], desc_map(), CategoryLookup())
    (row,) = r.resolve([txn()])
    assert row.resolved_by == BY_NONE
    assert row.txn.corrected_description == "Blue Bottle Coffee"  # fix kept
    assert row.txn.category == ""
    assert row.txn.category_source is CategorySource.NONE


# --- path 2: direct category ---


def test_category_map_hit_is_dict_match():
    r = Resolver(coffee_rules(), Lookup(), cat_map())
    (row,) = r.resolve([txn(desc="ZELLE TO SAM 0001")])
    assert row.resolved_by == BY_CAT_MAP
    assert row.txn.category == "Transfers"
    assert row.txn.category_source is CategorySource.DICT_MATCH
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
    assert row.txn.category_source is CategorySource.DICT_MATCH
    assert row.txn.corrected_description == "Bluebottle"          # path-1 fix still kept


def test_category_map_is_amount_aware():
    # same merchant, different category depending on the amount
    cm = CategoryLookup(
        wildcard={norm_key("SQ TAXI"): "Rideshare"},
        by_amount={(norm_key("SQ TAXI"), "-40"): "Airport"},
    )
    r = Resolver([], Lookup(), cm)
    (small,) = r.resolve([txn(desc="SQ TAXI 88", amount="-8.00")])
    (big,) = r.resolve([txn(desc="SQ TAXI 88", amount="-40.00")])
    assert small.txn.category == "Rideshare"     # unseen amount -> wildcard
    assert big.txn.category == "Airport"         # exact-amount override wins
    assert big.txn.category_source is CategorySource.DICT_MATCH


# --- no match ---


def test_no_map_hit_is_none_and_uncategorized():
    (row,) = Resolver(coffee_rules(), Lookup(), CategoryLookup()).resolve([txn(desc="MYSTERY LLC")])
    assert row.resolved_by == BY_NONE
    assert row.txn.category == ""


def test_inputs_not_mutated():
    original = txn()
    Resolver(coffee_rules(), desc_map(), CategoryLookup()).resolve([original])
    assert original.category == ""
    assert original.corrected_description == ""
    assert original.category_source is CategorySource.NONE


def test_order_preserved_and_one_row_out_per_row_in():
    rows = [txn(desc="SQ *BLUE BOTTLE #1"), txn(desc="MYSTERY LLC"),
            txn(desc="ZELLE TO SAM 5")]
    out = Resolver(coffee_rules(), desc_map(), cat_map()).resolve(rows)
    assert [r.resolved_by for r in out] == [BY_DESC_MAP, BY_NONE, BY_CAT_MAP]


# --- unmatched() helper ---


def test_unmatched_filters_out_categorized_rows():
    rows = [txn(category="Coffee"), txn(desc="MYSTERY LLC"), txn(category="Rent")]
    assert [t.original_description for t in unmatched(rows)] == ["MYSTERY LLC"]


# --- resolve_all (full batch: hard rows pass through pre-approved) ---


def test_resolve_all_passes_hard_rows_through_pre_approved():
    r = Resolver(coffee_rules(), Lookup(), CategoryLookup())
    hard = txn(desc="STARBUCKS", category="Coffee",
               category_source=CategorySource.FILTER_RULES)
    (row,) = r.resolve_all([hard])
    assert row.resolved_by == BY_HARD
    assert row.approved is True
    assert row.txn.category == "Coffee"                            # untouched
    assert row.txn.category_source is CategorySource.FILTER_RULES  # preserved


def test_resolve_all_interleaves_and_preserves_order():
    rows = [
        txn(desc="STARBUCKS", category="Coffee"),   # hard
        txn(desc="SQ *BLUE BOTTLE #1"),             # desc-map -> Coffee
        txn(desc="ZELLE TO SAM 5"),                 # cat-map -> Transfers
        txn(desc="MYSTERY LLC"),                    # none
    ]
    out = Resolver(coffee_rules(), desc_map(), cat_map()).resolve_all(rows)
    assert [r.resolved_by for r in out] == [BY_HARD, BY_DESC_MAP, BY_CAT_MAP, BY_NONE]
    assert [r.approved for r in out] == [True, False, False, False]


def test_resolver_never_sets_category_override():
    rows = [txn(desc="STARBUCKS", category="Coffee"), txn(desc="SQ *BLUE BOTTLE #1"),
            txn(desc="ZELLE 5"), txn(desc="MYSTERY LLC")]
    out = Resolver(coffee_rules(), desc_map(), cat_map()).resolve_all(rows)
    assert all(r.txn.category_override == "" for r in out)
