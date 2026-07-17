"""Lookup + norm_key tests (plan.md §6.2). All descriptions here are synthetic."""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))

from resolve_lookup import Lookup, norm_key  # noqa: E402


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
