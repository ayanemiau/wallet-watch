"""Preview diff/grouping tests. All data here is synthetic — never real merchants."""

import sys
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(TOOL))

from preview import (  # noqa: E402
    build_entries, changed, group_by_category, uncategorized,
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
