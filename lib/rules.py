"""Rule table format + match engine for categorization tier 3a.

Defines the `rules/keywords.yaml` format, loads/saves it, and evaluates it:
a rule is a list of conditions on transaction columns combined by one
operator (all/any), mapping to one category. Rules are ordered and the FIRST
matching rule wins.

This is the **single canonical engine** for tier 3a: both the pipeline
(`lib/categorizer.py`) and the interactive editor (`tools/rule_editor/`) import
it, so they categorize identically by construction. The engine works on plain
`Dict[str, str]` rows (exactly what `schema.to_row()` produces and what a
`csv.DictReader` yields), so it stays independent of the `Transaction`
dataclass and tolerant of a CSV written by an older schema.

See plan.md §5 (tier 3a).
"""

import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Dict, List, Optional

import yaml
from schema import FIELDNAMES

# bumped only on a breaking format change; readers reject what they don't know
FORMAT_VERSION = 1

# Rewritten on every save. PyYAML does not round-trip comments, so any comment
# a human adds to keywords.yaml is lost the first time this tool saves it —
# this header is the one comment that survives (see README).
HEADER = ("# wallet-watch categorization rules (tier 3a) — managed by tools/rule_editor.\n"
          "# Order matters: the FIRST rule that matches a transaction wins.\n"
          "# Editing by hand is fine, but comments are dropped when the editor saves.\n")

MATCH_MODES = ("all", "any")

# Ops taking a plain string operand; matching is always case-insensitive
# (bank descriptions have inconsistent casing — a case-sensitive default would
# make every rule a bug report).
STRING_OPS = ("contains", "not_contains", "equals", "not_equals",
              "starts_with", "ends_with", "regex")

# Ops comparing operands as decimals. Non-numeric cell values never match
# rather than raising: a real export can carry a stray "" or "pending" amount,
# and one bad row must not take down the whole run.
NUMERIC_OPS = ("gt", "gte", "lt", "lte")

OPS = STRING_OPS + NUMERIC_OPS

# Fallback column list, used by the editor only when no preview CSV is loaded to
# supply real headers. These are schema.py's Transaction fields (the CSV column
# contract); if a real preview CSV is loaded, its own header wins anyway.
DEFAULT_COLUMNS = list(FIELDNAMES)


@dataclass
class Condition:
    """One keyword test against one column."""

    column: str
    op: str
    value: str = ""


@dataclass
class Rule:
    """Conditions -> a category. Combined by `match`; first matching rule wins."""

    category: str
    match: str = "all"
    conditions: List[Condition] = field(default_factory=list)


# --- in-repo guard ---
#
# A real keywords.yaml IS real data: the rule set is a list of the operator's
# actual merchants, so it leaks spending just as an export does (CLAUDE.md).
# It belongs in the external data root. .gitignore's `rules/` entry only stops
# the commit, not the file appearing in the working tree, so guard the path
# here — in the library, not the CLI — and on read as well as write.


def _repo_root() -> Optional[Path]:
    # Walk up from this file to the checkout containing it. Returns None if the
    # tool was copied out of the repo, in which case there is no tree to protect.
    for parent in Path(__file__).resolve().parents:
        if (parent / ".git").exists() and (parent / "CLAUDE.md").exists():
            return parent
    return None


def guard_path(path: Path) -> Path:
    """Reject a rules path inside the repo tree. Returns the resolved path."""
    # resolve() first: without it "repo/../repo/rules/x.yaml" or a symlink
    # would walk straight past the check.
    resolved = path.expanduser().resolve()
    root = _repo_root()
    if root is not None and resolved.is_relative_to(root):
        raise SystemExit(
            f"refusing to touch a rules file inside the repo: {resolved}\n"
            f"real rules reveal actual merchants and must live in the external data root "
            f"(see CLAUDE.md); pass --data-dir or set WALLET_WATCH_DATA_DIR")
    return resolved


# --- load / save ---


def _parse_condition(raw: Dict, where: str) -> Condition:
    if not isinstance(raw, dict):
        raise SystemExit(f"{where}: condition must be a mapping, got {type(raw).__name__}")
    missing = [k for k in ("column", "op") if k not in raw]
    if missing:
        raise SystemExit(f"{where}: condition missing {', '.join(missing)}")
    # values are coerced to str: YAML happily parses `value: 0` as an int, and
    # every comparison here is string- or Decimal-based.
    return Condition(column=str(raw["column"]), op=str(raw["op"]),
                     value="" if raw.get("value") is None else str(raw["value"]))


def _parse_rule(raw: Dict, where: str) -> Rule:
    if not isinstance(raw, dict):
        raise SystemExit(f"{where}: rule must be a mapping, got {type(raw).__name__}")
    if "category" not in raw:
        raise SystemExit(f"{where}: rule missing category")
    conditions = raw.get("conditions") or []
    if not isinstance(conditions, list):
        raise SystemExit(f"{where}: conditions must be a list")
    return Rule(
        category=str(raw["category"]),
        match=str(raw.get("match", "all")),
        conditions=[_parse_condition(c, f"{where} condition {i}")
                    for i, c in enumerate(conditions, start=1)],
    )


def load_rules(path: Path) -> List[Rule]:
    """Read keywords.yaml. A missing file is an empty rule list (first run)."""
    path = guard_path(path)
    if not path.is_file():
        return []
    try:
        with path.open() as fh:
            doc = yaml.safe_load(fh)
    except yaml.YAMLError as e:
        raise SystemExit(f"{path}: invalid YAML: {e}")

    # an empty file parses to None — treat as no rules, not as an error
    if doc is None:
        return []
    if not isinstance(doc, dict):
        raise SystemExit(f"{path}: expected a mapping at the top level, "
                         f"got {type(doc).__name__}")

    version = doc.get("version", FORMAT_VERSION)
    if version != FORMAT_VERSION:
        raise SystemExit(f"{path}: unsupported format version {version!r} "
                         f"(this tool speaks {FORMAT_VERSION})")

    raw_rules = doc.get("rules") or []
    if not isinstance(raw_rules, list):
        raise SystemExit(f"{path}: rules must be a list")
    return [_parse_rule(r, f"{path}: rule {i}") for i, r in enumerate(raw_rules, start=1)]


def _to_doc(rules: List[Rule]) -> Dict:
    return {
        "version": FORMAT_VERSION,
        "rules": [
            {
                "category": r.category,
                "match": r.match,
                "conditions": [{"column": c.column, "op": c.op, "value": c.value}
                               for c in r.conditions],
            }
            for r in rules
        ],
    }


def save_rules(path: Path, rules: List[Rule]) -> None:
    """Write keywords.yaml atomically, keeping the previous version as .bak.

    rules/ is gitignored, so this file has NO git history to recover from —
    a half-written save is unrecoverable loss of real human work. Hence the
    temp-file + os.replace dance rather than opening the target for writing.
    """
    path = guard_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.is_file():
        shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))

    body = yaml.safe_dump(_to_doc(rules), sort_keys=False, allow_unicode=True,
                          default_flow_style=False)
    # same dir as the target: os.replace is only atomic within a filesystem
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".keywords-", suffix=".yaml")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(HEADER)
            fh.write(body)
            # the point of the temp file is to never leave a truncated target;
            # that only holds if the bytes are on disk before the rename
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


# --- validation ---


def validate_rule(rule: Rule) -> List[str]:
    """Human-readable problems with a rule; empty list means it is usable.

    Returns rather than raises: the UI validates while a rule is half-typed,
    so "not valid yet" is the normal state, not an error.
    """
    problems: List[str] = []
    if not rule.category.strip():
        problems.append("category is empty")
    if rule.match not in MATCH_MODES:
        problems.append(f"match must be one of {', '.join(MATCH_MODES)}")
    if not rule.conditions:
        problems.append("rule has no conditions")

    for i, cond in enumerate(rule.conditions, start=1):
        if not cond.column.strip():
            problems.append(f"condition {i}: column is empty")
        if cond.op not in OPS:
            problems.append(f"condition {i}: unknown op {cond.op!r}")
            continue
        if not cond.value.strip():
            problems.append(f"condition {i}: value is empty")
            continue
        if cond.op in NUMERIC_OPS:
            try:
                Decimal(cond.value)
            except InvalidOperation:
                problems.append(f"condition {i}: {cond.op} needs a number, "
                                f"got {cond.value!r}")
        elif cond.op == "regex":
            try:
                re.compile(cond.value)
            except re.error as e:
                problems.append(f"condition {i}: invalid regex: {e}")
    return problems


# --- matching ---
#
# Matches raw csv.DictReader string cells rather than typed objects: rules only
# ever need string/Decimal comparisons, and reading rows as plain dicts keeps
# this tool independent of lib/schema.py (and tolerant of a normalized.csv
# written by an older schema).


def match_condition(cond: Condition, row: Dict[str, str]) -> bool:
    # a rule naming a column this CSV doesn't have simply never matches
    cell = row.get(cond.column)
    if cell is None:
        return False

    if cond.op in NUMERIC_OPS:
        try:
            left, right = Decimal(cell), Decimal(cond.value)
        except InvalidOperation:
            # a non-numeric cell ("", "pending") or an unvalidated operand:
            # no match, never an exception — one odd row must not kill a run
            return False
        if cond.op == "gt":
            return left > right
        if cond.op == "gte":
            return left >= right
        if cond.op == "lt":
            return left < right
        return left <= right

    haystack, needle = cell.lower(), cond.value.lower()
    if cond.op == "contains":
        return needle in haystack
    if cond.op == "not_contains":
        return needle not in haystack
    if cond.op == "equals":
        return haystack == needle
    if cond.op == "not_equals":
        return haystack != needle
    if cond.op == "starts_with":
        return haystack.startswith(needle)
    if cond.op == "ends_with":
        return haystack.endswith(needle)
    if cond.op == "regex":
        try:
            return re.search(cond.value, cell, re.IGNORECASE) is not None
        except re.error:
            return False
    raise ValueError(f"unknown op: {cond.op!r}")


def match_rule(rule: Rule, row: Dict[str, str]) -> bool:
    # a rule with no conditions matches nothing; all([]) is True, which would
    # silently swallow every remaining row
    if not rule.conditions:
        return False
    tests = (match_condition(c, row) for c in rule.conditions)
    return any(tests) if rule.match == "any" else all(tests)


def first_match(rules: List[Rule], row: Dict[str, str]) -> Optional[int]:
    """Index of the first rule matching the row, or None.

    Callers that need to show *which* rule won (the editor's preview panel) want
    the index; categorize_row wants the category. Both are the same ordered walk,
    so it lives here once.
    """
    for i, rule in enumerate(rules):
        if match_rule(rule, row):
            return i
    return None


def categorize_row(rules: List[Rule], row: Dict[str, str]) -> Optional[str]:
    """The category of the first matching rule, or None. This is tier 3a."""
    i = first_match(rules, row)
    return rules[i].category if i is not None else None
