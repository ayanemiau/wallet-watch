"""Preview diff/grouping tests. All data here is synthetic — never real merchants."""

import sys
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(TOOL))

from decimal import Decimal  # noqa: E402

from preview import (  # noqa: E402
    AMOUNT_ALL, AMOUNT_CUSTOM, AMOUNT_GT500, AMOUNT_LT100, AMOUNT_MID,
    PreviewEntry, account_counts, amount_counts, amount_ok, build_entries,
    cell_amount, changed, distinct_accounts, group_by_category, match_categories,
    rule_categories, sort_value, uncategorized, view_entries,
)
from rules import Condition, Rule  # noqa: E402


def rule(category, value, column="original_description", op="contains"):
    return Rule(category=category, match="all",
                conditions=[Condition(column=column, op=op, value=value)])


def row(desc, amount="-5.00"):
    return {"date": "2026-01-07", "amount": amount, "account": "Fake Card",
            "is_reference": "0", "original_description": desc,
            "corrected_description": "", "category": "", "tags": ""}


ROWS = [
    row("FAKE COFFEE CO"),
    row("SAMPLE ROASTERS #4"),
    row("FAKE GROCER"),
    row("MYSTERY MERCHANT"),
]


# --- build_entries ---


def test_build_entries_pairs_old_and_new():
    baseline = [rule("Coffee", "FAKE COFFEE")]
    live = [rule("Coffee", "FAKE COFFEE"), rule("Groceries", "FAKE GROCER")]
    entries = build_entries(baseline, live, ROWS)

    assert [(e.old, e.new) for e in entries] == [
        ("Coffee", "Coffee"),        # unchanged
        (None, None),                # matched by neither
        (None, "Groceries"),         # newly categorized
        (None, None),                # matched by neither
    ]


def test_build_entries_preserves_source_order_and_rows():
    entries = build_entries([], [rule("Coffee", "FAKE COFFEE")], ROWS)
    assert [e.row for e in entries] == ROWS


def test_rule_index_names_the_winning_rule():
    live = [rule("Groceries", "FAKE GROCER"), rule("Coffee", "FAKE COFFEE")]
    entries = build_entries([], live, ROWS)
    assert entries[0].rule_index == 1      # coffee row won by rule 2
    assert entries[2].rule_index == 0      # grocer row won by rule 1
    assert entries[3].rule_index is None   # nothing matched


def test_rule_index_is_the_first_match_not_any_match():
    # both rules match; the earlier one wins
    live = [rule("Everything", "FAKE"), rule("Coffee", "FAKE COFFEE")]
    entries = build_entries([], live, ROWS)
    assert entries[0].rule_index == 0
    assert entries[0].new == "Everything"


def test_empty_rows():
    assert build_entries([], [rule("Coffee", "FAKE")], []) == []


# --- changed ---


def test_empty_baseline_makes_every_categorized_row_a_change():
    # the first-run state: rules.yaml doesn't exist yet
    entries = build_entries([], [rule("Coffee", "FAKE COFFEE")], ROWS)
    assert [(e.old, e.new) for e in changed(entries)] == [(None, "Coffee")]


def test_row_matched_by_neither_ruleset_is_not_a_change():
    # None == None must not read as a change
    entries = build_entries([], [rule("Coffee", "NOTHING MATCHES THIS")], ROWS)
    assert changed(entries) == []


def test_identical_rulesets_produce_no_changes():
    rules = [rule("Coffee", "FAKE COFFEE"), rule("Groceries", "FAKE GROCER")]
    entries = build_entries(rules, list(rules), ROWS)
    assert changed(entries) == []


def test_renaming_a_category_marks_exactly_the_affected_rows():
    baseline = [rule("Coffee", "FAKE COFFEE"), rule("Groceries", "FAKE GROCER")]
    live = [rule("Coffee & Tea", "FAKE COFFEE"), rule("Groceries", "FAKE GROCER")]
    result = changed(build_entries(baseline, live, ROWS))
    assert [(e.old, e.new) for e in result] == [("Coffee", "Coffee & Tea")]


def test_reordering_so_a_different_rule_wins_is_a_change():
    specific = rule("Coffee", "FAKE COFFEE")
    catch_all = rule("Everything", "FAKE")
    baseline = [specific, catch_all]
    live = [catch_all, specific]          # catch-all now shadows the specific rule
    result = changed(build_entries(baseline, live, ROWS))
    assert [(e.old, e.new) for e in result] == [("Coffee", "Everything")]


def test_deleting_a_rule_uncategorizes_its_rows():
    baseline = [rule("Coffee", "FAKE COFFEE")]
    result = changed(build_entries(baseline, [], ROWS))
    assert [(e.old, e.new) for e in result] == [("Coffee", None)]


# --- uncategorized ---


def test_uncategorized_lists_rows_no_rule_matches():
    entries = build_entries([], [rule("Coffee", "FAKE COFFEE")], ROWS)
    descs = [e.row["original_description"] for e in uncategorized(entries)]
    assert descs == ["SAMPLE ROASTERS #4", "FAKE GROCER", "MYSTERY MERCHANT"]


def test_uncategorized_is_everything_when_there_are_no_rules():
    entries = build_entries([], [], ROWS)
    assert len(uncategorized(entries)) == len(ROWS)


# --- group_by_category ---


def test_group_by_category_sorts_alphabetically():
    # count order (Zebra 3, Alpha 1) is the reverse of alpha order, so this
    # would fail under the old biggest-first sort
    live = [rule("Alpha", "FAKE COFFEE"), rule("Zebra", "E")]  # "E" catches the rest
    groups = group_by_category(build_entries([], live, ROWS))
    assert [(name, len(rows)) for name, rows in groups] == [("Alpha", 1), ("Zebra", 3)]


def test_group_by_category_sorts_case_insensitively():
    # casefold, so lowercase 'apple' sorts before 'Zebra'; a plain str sort
    # would put all-caps first and flip them
    live = [rule("Zebra", "FAKE COFFEE"), rule("apple", "FAKE GROCER")]
    groups = group_by_category(build_entries([], live, ROWS))
    assert [name for name, _ in groups] == ["apple", "Zebra"]


def test_group_by_category_excludes_uncategorized_rows():
    groups = group_by_category(build_entries([], [rule("Coffee", "FAKE COFFEE")], ROWS))
    assert [name for name, _ in groups] == ["Coffee"]
    assert len(groups[0][1]) == 1


def test_group_by_category_is_empty_without_rules():
    assert group_by_category(build_entries([], [], ROWS)) == []


def test_buckets_partition_every_row():
    live = [rule("Coffee", "FAKE COFFEE"), rule("Groceries", "FAKE GROCER")]
    entries = build_entries([], live, ROWS)
    grouped = sum(len(rows) for _, rows in group_by_category(entries))
    assert grouped + len(uncategorized(entries)) == len(entries)


def test_two_rules_sharing_a_category_land_in_one_group():
    live = [rule("Coffee", "FAKE COFFEE"), rule("Coffee", "SAMPLE ROASTERS")]
    groups = group_by_category(build_entries([], live, ROWS))
    assert [(name, len(rows)) for name, rows in groups] == [("Coffee", 2)]


# --- panel filter / sort (view_entries + helpers) ---


def entry(amount="-5.00", account="Fake Card", desc="X", old=None, new=None, ri=None):
    return PreviewEntry(row={"date": "2026-01-07", "amount": amount, "account": account,
                             "original_description": desc},
                        old=old, new=new, rule_index=ri)


def test_distinct_accounts_sorted_unique_incl_blank():
    rows = [{"account": "chase"}, {"account": "apple"}, {"account": "chase"}, {"account": ""}]
    assert distinct_accounts(rows) == ["", "apple", "chase"]


def test_cell_amount_parses_or_none():
    assert cell_amount(entry("-12.34")) == Decimal("-12.34")
    assert cell_amount(entry("")) is None          # blank drops out
    assert cell_amount(entry("pending")) is None   # non-numeric never raises


def test_amount_ok_uses_magnitude():
    # spend is negative; the operator means the size of the transaction
    assert amount_ok(Decimal("-9.99"), AMOUNT_LT100, None, None) is True
    assert amount_ok(Decimal("-120"), AMOUNT_MID, None, None) is True
    assert amount_ok(Decimal("-120"), AMOUNT_GT500, None, None) is False
    assert amount_ok(Decimal("-750"), AMOUNT_GT500, None, None) is True
    # boundaries: 100–500 is inclusive; <100 is strict
    assert amount_ok(Decimal("-100"), AMOUNT_MID, None, None) is True
    assert amount_ok(Decimal("-100"), AMOUNT_LT100, None, None) is False


def test_amount_ok_all_and_none():
    assert amount_ok(None, AMOUNT_ALL, None, None) is True       # ALL ignores value
    assert amount_ok(None, AMOUNT_LT100, None, None) is False    # a blank fails a real filter


def test_amount_ok_custom_bounds_inclusive_and_open():
    assert amount_ok(Decimal("-120"), AMOUNT_CUSTOM, Decimal("50"), Decimal("200")) is True
    assert amount_ok(Decimal("-40"), AMOUNT_CUSTOM, Decimal("50"), Decimal("200")) is False
    assert amount_ok(Decimal("-250"), AMOUNT_CUSTOM, Decimal("50"), Decimal("200")) is False
    # None bounds are open on that side
    assert amount_ok(Decimal("-9999"), AMOUNT_CUSTOM, Decimal("50"), None) is True
    assert amount_ok(Decimal("-5"), AMOUNT_CUSTOM, None, Decimal("200")) is True


def test_sort_value_types():
    assert sort_value(entry("-500"), "amount") == Decimal("-500")   # signed, not magnitude
    assert sort_value(entry(""), "amount") is None                  # blank sorts last
    assert sort_value(entry(ri=2), "rule") == 2
    assert sort_value(entry(ri=None), "rule") is None
    assert sort_value(entry(account="ZChase"), "account") == "zchase"   # casefold
    assert sort_value(entry(old="Food", new="Rent"), "from") == "food"
    assert sort_value(entry(old="Food", new="Rent"), "to") == "rent"


def test_view_account_filter():
    es = [entry(account="chase"), entry(account="apple"), entry(account="chase")]
    kept = view_entries(es, {"chase"}, None, None, True)
    assert [e.row["account"] for e in kept] == ["chase", "chase"]
    # None means every account (no-op)
    assert view_entries(es, None, None, None, True) == es
    # empty selection hides everything
    assert view_entries(es, set(), None, None, True) == []


def test_view_amount_and_account_compose_and():
    es = [entry("-9", account="chase"), entry("-900", account="chase"),
          entry("-900", account="apple")]
    kept = view_entries(es, {"chase"}, (AMOUNT_GT500, None, None), None, True)
    assert len(kept) == 1 and kept[0].row["account"] == "chase" and kept[0].row["amount"] == "-900"


def test_view_sort_amount_numeric_not_lexical():
    es = [entry("-500"), entry("-5"), entry("-100")]
    asc = view_entries(es, None, None, "amount", True)
    assert [e.row["amount"] for e in asc] == ["-500", "-100", "-5"]
    desc = view_entries(es, None, None, "amount", False)
    assert [e.row["amount"] for e in desc] == ["-5", "-100", "-500"]


def test_view_sort_puts_blanks_last_both_directions():
    es = [entry("-5"), entry(""), entry("-100")]
    asc = view_entries(es, None, None, "amount", True)
    desc = view_entries(es, None, None, "amount", False)
    assert asc[-1].row["amount"] == "" and desc[-1].row["amount"] == ""


def test_view_no_sort_preserves_source_order():
    es = [entry("-5"), entry("-500"), entry("-100")]
    assert view_entries(es, None, None, None, True) == es


# --- rule search (category candidates) ---


def test_rule_categories_distinct_nonempty_sorted():
    rules = [rule("Zebra", "A"), rule("apple", "B"), rule("apple", "C"),
             rule("", "D")]  # duplicate + an uncategorized rule
    # casefold sort, no blanks, deduped
    assert rule_categories(rules) == ["apple", "Zebra"]


def test_rule_categories_empty_when_no_categories():
    assert rule_categories([rule("", "A")]) == []


def test_match_categories_substring_case_insensitive():
    cats = ["Food/Dining", "Food/Snacks", "Rent", "Transit"]
    assert match_categories(cats, "food") == ["Food/Dining", "Food/Snacks"]
    assert match_categories(cats, "IT") == ["Transit"]   # substring, not prefix
    assert match_categories(cats, "") == cats            # empty -> all, order kept
    assert match_categories(cats, "zzz") == []


# --- filter option counts ---


def test_account_counts_tallies_per_account():
    es = [entry(account="chase"), entry(account="apple"), entry(account="chase"),
          entry(account="")]
    assert account_counts(es) == {"chase": 2, "apple": 1, "": 1}


def test_amount_counts_by_magnitude_bucket():
    es = [entry("-9"), entry("-250"), entry("-750"), entry("-120"), entry("")]
    c = amount_counts(es, None, None)
    assert c[AMOUNT_ALL] == 5                 # every entry, incl. the blank
    assert c[AMOUNT_LT100] == 1               # -9
    assert c[AMOUNT_MID] == 2                 # -250, -120
    assert c[AMOUNT_GT500] == 1               # -750
    assert c[AMOUNT_CUSTOM] == 4              # open bounds -> every numeric (blank drops)


def test_amount_counts_custom_uses_bounds():
    es = [entry("-9"), entry("-250"), entry("-750"), entry("-120")]
    assert amount_counts(es, Decimal("100"), Decimal("300"))[AMOUNT_CUSTOM] == 2  # -250, -120
