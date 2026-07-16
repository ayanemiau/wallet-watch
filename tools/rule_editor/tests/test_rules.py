"""Rule format + engine tests. All data here is synthetic — never real merchants."""

import sys
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parent.parent
REPO = TOOL.parent.parent

sys.path.insert(0, str(TOOL))

from rules import (  # noqa: E402
    Condition, Rule, categorize_row, first_match, guard_path, load_rules,
    match_rule, save_rules, validate_rule,
)


def cond(column="original_description", op="contains", value="FAKE COFFEE CO"):
    return Condition(column=column, op=op, value=value)


def row(**kw):
    base = {"date": "2026-01-05", "amount": "-12.50", "account": "Fake Card",
            "is_reference": "0", "original_description": "FAKE COFFEE CO #123",
            "corrected_description": "", "category": "", "tags": ""}
    base.update(kw)
    return base


# --- load / save ---


def test_round_trip_preserves_order_and_fields(tmp_path):
    path = tmp_path / "rules" / "keywords.yaml"
    rules = [
        Rule(category="Coffee", match="any", conditions=[cond(), cond(value="SAMPLE ROASTERS")]),
        Rule(category="Groceries", match="all",
             conditions=[cond(value="FAKE GROCER"), cond(column="amount", op="lt", value="0")]),
    ]
    save_rules(path, rules)
    assert load_rules(path) == rules


def test_missing_file_is_empty_rule_list(tmp_path):
    # first run is normal, not an error
    assert load_rules(tmp_path / "rules" / "keywords.yaml") == []


def test_empty_file_is_empty_rule_list(tmp_path):
    path = tmp_path / "keywords.yaml"
    path.write_text("")
    assert load_rules(path) == []


def test_malformed_yaml_names_the_file(tmp_path):
    path = tmp_path / "keywords.yaml"
    path.write_text("rules: [oops\n")
    with pytest.raises(SystemExit, match="invalid YAML"):
        load_rules(path)


def test_bad_rule_names_the_rule_index(tmp_path):
    path = tmp_path / "keywords.yaml"
    path.write_text("version: 1\nrules:\n  - category: Coffee\n  - match: all\n")
    with pytest.raises(SystemExit, match="rule 2: rule missing category"):
        load_rules(path)


def test_unsupported_version_is_rejected(tmp_path):
    path = tmp_path / "keywords.yaml"
    path.write_text("version: 99\nrules: []\n")
    with pytest.raises(SystemExit, match="unsupported format version"):
        load_rules(path)


def test_yaml_scalars_are_coerced_to_str(tmp_path):
    # `value: 0` parses as int; every comparison here is str/Decimal based
    path = tmp_path / "keywords.yaml"
    path.write_text("version: 1\nrules:\n  - category: Refunds\n    match: all\n"
                    "    conditions:\n      - column: amount\n        op: gt\n        value: 0\n")
    assert load_rules(path)[0].conditions[0].value == "0"


def test_save_keeps_previous_version_as_bak(tmp_path):
    path = tmp_path / "keywords.yaml"
    save_rules(path, [Rule(category="First", conditions=[cond()])])
    assert not path.with_suffix(".yaml.bak").exists()   # nothing to back up yet

    save_rules(path, [Rule(category="Second", conditions=[cond()])])
    assert load_rules(path)[0].category == "Second"
    assert load_rules(path.with_suffix(".yaml.bak"))[0].category == "First"


def test_save_leaves_no_temp_files_behind(tmp_path):
    path = tmp_path / "keywords.yaml"
    save_rules(path, [Rule(category="Coffee", conditions=[cond()])])
    assert sorted(p.name for p in tmp_path.iterdir()) == ["keywords.yaml"]


def test_save_creates_parent_dir(tmp_path):
    path = tmp_path / "rules" / "keywords.yaml"
    save_rules(path, [Rule(category="Coffee", conditions=[cond()])])
    assert path.is_file()


def test_saved_file_keeps_a_header_comment(tmp_path):
    path = tmp_path / "keywords.yaml"
    save_rules(path, [Rule(category="Coffee", conditions=[cond()])])
    assert path.read_text().startswith("# wallet-watch categorization rules")


# --- in-repo guard ---
#
# The rule set is a list of real merchants, so a real keywords.yaml must never
# land in the repo tree. gitignore stops the commit but not the working-tree
# file, so this guard is the primary defense — it gets tests, not just a comment.


def test_guard_rejects_path_inside_repo():
    with pytest.raises(SystemExit, match="refusing to touch a rules file inside the repo"):
        guard_path(REPO / "rules" / "keywords.yaml")


def test_guard_rejects_traversal_back_into_repo(tmp_path):
    # resolve() must happen before the check, or ".." walks straight past it
    with pytest.raises(SystemExit, match="refusing to touch"):
        guard_path(REPO / "tools" / ".." / "rules" / "keywords.yaml")


def test_guard_blocks_both_load_and_save():
    sneaky = REPO / "tools" / "rule_editor" / "keywords.yaml"
    with pytest.raises(SystemExit, match="refusing to touch"):
        save_rules(sneaky, [Rule(category="Coffee", conditions=[cond()])])
    with pytest.raises(SystemExit, match="refusing to touch"):
        load_rules(sneaky)
    assert not sneaky.exists()


def test_guard_allows_path_outside_repo(tmp_path):
    path = tmp_path / "rules" / "keywords.yaml"
    save_rules(path, [Rule(category="Coffee", conditions=[cond()])])
    assert load_rules(path)[0].category == "Coffee"


# --- validation ---


def test_valid_rule_has_no_problems():
    assert validate_rule(Rule(category="Coffee", conditions=[cond()])) == []


def test_validate_flags_empty_category_and_no_conditions():
    problems = validate_rule(Rule(category="  ", conditions=[]))
    assert "category is empty" in problems
    assert "rule has no conditions" in problems


def test_validate_flags_unknown_op_and_bad_match():
    problems = validate_rule(Rule(category="X", match="either", conditions=[cond(op="sounds_like")]))
    assert any("unknown op" in p for p in problems)
    assert any("match must be one of" in p for p in problems)


def test_validate_flags_non_numeric_operand_for_numeric_op():
    problems = validate_rule(
        Rule(category="X", conditions=[cond(column="amount", op="gt", value="lots")]))
    assert any("needs a number" in p for p in problems)


def test_validate_flags_invalid_regex():
    problems = validate_rule(Rule(category="X", conditions=[cond(op="regex", value="FAKE(")]))
    assert any("invalid regex" in p for p in problems)


def test_validate_flags_empty_value():
    problems = validate_rule(Rule(category="X", conditions=[cond(value="")]))
    assert any("value is empty" in p for p in problems)


# --- matching ---


@pytest.mark.parametrize("op,value,expected", [
    ("contains", "COFFEE", True),
    ("contains", "TEA", False),
    ("not_contains", "TEA", True),
    ("not_contains", "COFFEE", False),
    ("equals", "FAKE COFFEE CO #123", True),
    ("equals", "FAKE COFFEE CO", False),
    ("not_equals", "FAKE COFFEE CO", True),
    ("starts_with", "FAKE", True),
    ("starts_with", "COFFEE", False),
    ("ends_with", "#123", True),
    ("ends_with", "FAKE", False),
    ("regex", r"FAKE\s+COFFEE", True),
    ("regex", r"^COFFEE", False),
])
def test_string_ops(op, value, expected):
    rule = Rule(category="X", conditions=[cond(op=op, value=value)])
    assert match_rule(rule, row()) is expected


@pytest.mark.parametrize("op,value,expected", [
    ("gt", "-20", True),
    ("gt", "-5", False),
    ("gte", "-12.50", True),
    ("lt", "0", True),
    ("lt", "-20", False),
    ("lte", "-12.50", True),
])
def test_numeric_ops_compare_as_decimals(op, value, expected):
    # "-12.50" vs "-5": a string compare would get this backwards
    rule = Rule(category="X", conditions=[cond(column="amount", op=op, value=value)])
    assert match_rule(rule, row()) is expected


def test_string_matching_is_case_insensitive():
    rule = Rule(category="X", conditions=[cond(value="fake coffee")])
    assert match_rule(rule, row(original_description="FAKE COFFEE CO")) is True
    assert match_rule(rule, row(original_description="Fake Coffee Co")) is True


def test_regex_is_case_insensitive_too():
    rule = Rule(category="X", conditions=[cond(op="regex", value="fake.*co")])
    assert match_rule(rule, row()) is True


def test_match_all_requires_every_condition():
    rule = Rule(category="X", match="all",
                conditions=[cond(value="FAKE COFFEE"), cond(column="amount", op="lt", value="0")])
    assert match_rule(rule, row()) is True
    assert match_rule(rule, row(amount="10.00")) is False


def test_match_any_requires_one_condition():
    rule = Rule(category="X", match="any",
                conditions=[cond(value="SAMPLE ROASTERS"), cond(value="FAKE COFFEE")])
    assert match_rule(rule, row()) is True
    assert match_rule(rule, row(original_description="FAKE GROCER")) is False


def test_rule_with_no_conditions_matches_nothing():
    # all([]) is True — an empty rule would otherwise swallow every row
    assert match_rule(Rule(category="X", conditions=[]), row()) is False


def test_unknown_column_never_matches():
    rule = Rule(category="X", conditions=[cond(column="not_a_column", value="anything")])
    assert match_rule(rule, row()) is False


def test_non_numeric_cell_does_not_raise():
    # a real export can carry "" or "pending"; one odd row must not kill a run
    rule = Rule(category="X", conditions=[cond(column="amount", op="lt", value="0")])
    assert match_rule(rule, row(amount="")) is False
    assert match_rule(rule, row(amount="pending")) is False


# --- first match wins ---


def test_categorize_row_returns_first_match():
    rules = [
        Rule(category="Coffee", conditions=[cond(value="FAKE")]),
        Rule(category="Groceries", conditions=[cond(value="FAKE")]),
    ]
    assert categorize_row(rules, row()) == "Coffee"


def test_categorize_row_skips_non_matching_rules():
    rules = [
        Rule(category="Groceries", conditions=[cond(value="FAKE GROCER")]),
        Rule(category="Coffee", conditions=[cond(value="FAKE COFFEE")]),
    ]
    assert categorize_row(rules, row()) == "Coffee"


def test_categorize_row_returns_none_when_nothing_matches():
    rules = [Rule(category="Groceries", conditions=[cond(value="FAKE GROCER")])]
    assert categorize_row(rules, row()) is None


def test_categorize_row_with_no_rules():
    assert categorize_row([], row()) is None


def test_first_match_returns_the_first_index():
    rules = [
        Rule(category="Groceries", conditions=[cond(value="FAKE GROCER")]),
        Rule(category="Coffee", conditions=[cond(value="FAKE")]),
        Rule(category="Anything", conditions=[cond(value="FAKE")]),
    ]
    assert first_match(rules, row()) == 1


def test_first_match_is_none_when_nothing_matches():
    assert first_match([Rule(category="X", conditions=[cond(value="NOPE")])], row()) is None
    assert first_match([], row()) is None
