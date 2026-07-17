"""Lookup + norm_key tests (plan.md §6.2). All descriptions here are synthetic."""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))

from resolve_lookup import (CategoryLookup, Lookup, build_category_map,  # noqa: E402
                            build_description_map, build_lookup, canon_amount, norm_key)
from schema import CategorySource, Transaction  # noqa: E402


def txn(desc, amount="-5.00", category="", **kw) -> Transaction:
    base = dict(date="2026-01-05", amount=amount, account="Fake",
                original_description=desc, category=category)
    base.update(kw)
    return Transaction(**base)


# --- norm_key ---


def test_norm_key_strips_digits_punctuation_and_case():
    assert norm_key("SQ *BLUE BOTTLE #4471") == "sq blue bottle"


def test_norm_key_collapses_merchant_variants_to_one_key():
    assert norm_key("SQ *BLUE BOTTLE #4471") == norm_key("sq  *blue bottle  #0093")


def test_norm_key_empty_and_all_noise():
    assert norm_key("") == ""
    assert norm_key("#123 ***") == ""


def test_norm_key_keeps_non_ascii_letters():
    # casefold, but a non-ASCII merchant name must not vanish entirely
    assert norm_key("Café Ünç* 88") == "café ünç"


# --- Lookup ---


def test_put_and_get_normalize_the_query():
    lk = Lookup()
    lk.put("SQ *BLUE BOTTLE #4471", "Blue Bottle Coffee")
    # a different store number for the same merchant still hits
    assert lk.get("SQ *BLUE BOTTLE #0093") == "Blue Bottle Coffee"
    assert "SQ *BLUE BOTTLE #0093" in lk
    assert len(lk) == 1


def test_get_miss_is_none():
    assert Lookup().get("anything") is None
    assert "anything" not in Lookup()


def test_put_overwrites_same_merchant():
    lk = Lookup()
    lk.put("MERCHANT #1", "A")
    lk.put("MERCHANT #2", "B")
    assert lk.get("MERCHANT #9") == "B"
    assert len(lk) == 1


def test_load_missing_file_is_empty(tmp_path):
    lk = Lookup.load(tmp_path / "nope.csv")
    assert len(lk) == 0


def test_save_then_load_round_trips(tmp_path):
    lk = Lookup()
    lk.put("SQ *BLUE BOTTLE #1", "Blue Bottle Coffee")
    lk.put("TST* TAQUERIA 22", "Tacos")
    path = tmp_path / "lookup" / "description_map.csv"
    lk.save(path)

    back = Lookup.load(path)
    assert back.get("sq blue bottle #77") == "Blue Bottle Coffee"
    assert back.get("TST* TAQUERIA 99") == "Tacos"
    assert len(back) == 2


def test_saved_file_is_key_sorted_with_header(tmp_path):
    lk = Lookup()
    lk.put("ZED", "z")
    lk.put("ABLE", "a")
    path = tmp_path / "m.csv"
    lk.save(path)
    lines = path.read_text().splitlines()
    assert lines[0] == "key,value"
    assert lines[1].startswith("able,")   # sorted before "zed"


# --- canon_amount ---


def test_canon_amount_collapses_value_equal_amounts():
    assert canon_amount("-9.90") == canon_amount("-9.9") == canon_amount("-9.900")
    assert canon_amount("100.00") == canon_amount("100") == "100"
    assert canon_amount("-0.00") == canon_amount("0") == "0"


def test_canon_amount_non_numeric_is_none():
    assert canon_amount("") is None
    assert canon_amount("pending") is None


# --- CategoryLookup ---


def test_category_lookup_wildcard_matches_any_amount():
    cl = CategoryLookup({norm_key("COFFEE HUT"): "Coffee"})
    assert cl.get("COFFEE HUT #9", "-3.00") == "Coffee"
    assert cl.get("coffee hut", "-99.00") == "Coffee"


def test_category_lookup_exact_amount_overrides_wildcard():
    cl = CategoryLookup(
        wildcard={norm_key("SQ TAXI"): "Rideshare"},
        by_amount={(norm_key("SQ TAXI"), "-40"): "Airport"},
    )
    assert cl.get("SQ TAXI 12", "-8.00") == "Rideshare"     # unseen amount
    assert cl.get("SQ TAXI 12", "-40.00") == "Airport"      # exact-amount override
    assert cl.get("UNKNOWN LLC", "-1.00") is None


def test_category_lookup_round_trips_three_columns(tmp_path):
    cl = CategoryLookup(
        wildcard={"sq taxi": "Rideshare"},
        by_amount={("sq taxi", "-40"): "Airport"},
    )
    path = tmp_path / "lookup" / "category_map.csv"
    cl.save(path)
    assert path.read_text().splitlines()[0] == "key,amount,value"
    back = CategoryLookup.load(path)
    assert back.get("SQ TAXI 9", "-8.00") == "Rideshare"
    assert back.get("SQ TAXI 9", "-40.00") == "Airport"


# --- build_category_map (the learning) ---


def test_build_ignores_amount_when_category_is_constant():
    # scenario 1: same merchant, different amounts, one category -> wildcard only
    history = [txn("COFFEE HUT 1", "-3.00", category="Coffee"),
               txn("COFFEE HUT 2", "-4.50", category="Coffee")]
    cm = build_category_map(history)
    assert cm.get("COFFEE HUT 9", "-9.99") == "Coffee"     # any amount


def test_build_pins_amount_when_category_depends_on_it():
    # scenario 2: category is a function of the amount (digit suffixes normalize away)
    history = [txn("SQ TAXI 1", "-8.00", category="Rideshare"),
               txn("SQ TAXI 2", "-8.00", category="Rideshare"),
               txn("SQ TAXI 3", "-40.00", category="Airport")]
    cm = build_category_map(history)
    assert cm.get("SQ TAXI 9", "-8.00") == "Rideshare"
    assert cm.get("SQ TAXI 9", "-40.00") == "Airport"


def test_build_most_recent_wins():
    # same merchant AND amount, later categorization overrides the earlier one
    history = [txn("COFFEE HUT 1", "-3.00", category="Coffee"),
               txn("COFFEE HUT 2", "-3.00", category="Cafe")]      # newer, same amount
    cm = build_category_map(history)
    assert cm.get("COFFEE HUT 9", "-3.00") == "Cafe"
    assert cm.get("COFFEE HUT 9", "-9.99") == "Cafe"              # wildcard is the recent one


def test_build_skips_override_only_rows():
    # category_override must never be learned; a row with only an override contributes nothing
    history = [txn("SPECIAL LLC", "-5.00", category="", category_override="Gift")]
    cm = build_category_map(history)
    assert cm.get("SPECIAL LLC", "-5.00") is None
    assert len(cm) == 0


def test_build_description_map_last_wins():
    history = [txn("SQ *BLUE BOTTLE 1", corrected_description="Blue Bottle"),
               txn("SQ *BLUE BOTTLE 2", corrected_description="Blue Bottle Coffee")]
    dm = build_description_map(history)
    assert dm.get("SQ *BLUE BOTTLE 9") == "Blue Bottle Coffee"


def test_build_lookup_returns_both_maps():
    history = [txn("COFFEE HUT 1", "-3.00", category="Coffee",
                   corrected_description="Coffee Hut")]
    dm, cm = build_lookup(history)
    assert isinstance(dm, Lookup) and isinstance(cm, CategoryLookup)
    assert cm.get("COFFEE HUT 2", "-3.00") == "Coffee"
    assert dm.get("COFFEE HUT 2") == "Coffee Hut"
