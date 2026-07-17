"""resolve_batch orchestrator / CLI tests (plan.md §6). Synthetic data only."""

import csv
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
LIB = REPO / "lib"
SCRIPTS = REPO / "scripts"
SCRIPT = SCRIPTS / "resolve_batch.py"

sys.path.insert(0, str(LIB))
sys.path.insert(0, str(SCRIPTS))

from resolve_lookup import Lookup  # noqa: E402
from rules import Condition, Rule, save_rules  # noqa: E402
from schema import FIELDNAMES, Transaction, to_row  # noqa: E402

OUTPUT_RE = re.compile(r"review_inbox_\d{8}_\d{6}\.csv")


def build_data_dir(tmp_path: Path, rows, *, with_rules=True, with_maps=True) -> Path:
    """A minimal data root: one batch with a categorized CSV, plus rules/maps."""
    data = tmp_path / "data"
    batch = data / "batch" / "20260101-20260131"
    batch.mkdir(parents=True)

    cat = batch / "categorized_20260101_000000.csv"
    with cat.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        w.writeheader()
        for txn in rows:
            w.writerow(to_row(txn))

    if with_rules:
        save_rules(data / "rules" / "keywords.yaml", [
            Rule(category="Coffee", match="all",
                 conditions=[Condition("original_description", "contains", "blue bottle")]),
        ])
    if with_maps:
        dm = Lookup()
        dm.put("SQ *BLUE BOTTLE #1", "Blue Bottle Coffee")
        dm.save(data / "lookup" / "description_map.csv")
        cm = Lookup()
        cm.put("ZELLE TO SAM 8842", "Transfers")
        cm.save(data / "lookup" / "category_map.csv")
    return data


def txn(desc, category="", **kw) -> Transaction:
    base = dict(date="2026-01-05", amount="-6.00", account="Fake Card",
                original_description=desc, category=category)
    base.update(kw)
    return Transaction(**base)


def sample_rows():
    return [
        txn("STARBUCKS #1", category="Coffee", categorize_method=0),   # already matched
        txn("SQ *BLUE BOTTLE #99"),                                    # -> desc-map, method 1
        txn("ZELLE TO SAM 0001"),                                      # -> cat-map, method 2
        txn("MYSTERY LLC"),                                            # -> none
    ]


def read_inbox_rows(batch_dir: Path):
    outs = sorted(batch_dir.glob("review_inbox_*.csv"))
    assert len(outs) == 1 and OUTPUT_RE.fullmatch(outs[0].name)
    with outs[0].open(newline="") as fh:
        return list(csv.DictReader(fh))


def run(data: Path, *args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--data-dir", str(data), *args],
        capture_output=True, text=True,
    )


def test_end_to_end_unmatched_rows_go_to_inbox(tmp_path):
    data = build_data_dir(tmp_path, sample_rows())
    r = run(data)
    assert r.returncode == 0, r.stderr

    rows = read_inbox_rows(data / "batch" / "20260101-20260131")
    # only the 3 unmatched rows; the already-categorized STARBUCKS row is excluded
    assert [row["original_description"] for row in rows] == \
        ["SQ *BLUE BOTTLE #99", "ZELLE TO SAM 0001", "MYSTERY LLC"]

    # every row awaits approval
    assert all(row["approved"] == "0" for row in rows)
    assert [row["resolved_by"] for row in rows] == ["desc-map", "cat-map", "none"]

    desc, cat, none = rows
    # path 1: corrected description re-matched to a category, method 1
    assert desc["corrected_description"] == "Blue Bottle Coffee"
    assert desc["category"] == "Coffee"
    assert desc["categorize_method"] == "1"
    # path 2: direct category, method 2
    assert cat["category"] == "Transfers"
    assert cat["categorize_method"] == "2"
    # unmatched: nothing filled, method stays 0
    assert none["category"] == ""
    assert none["categorize_method"] == "0"


def test_summary_reports_counts(tmp_path):
    data = build_data_dir(tmp_path, sample_rows())
    r = run(data)
    assert "1 already categorized (Phase 3), 3 unmatched" in r.stderr
    assert "review_inbox_" in r.stderr


def test_no_maps_first_run_all_unmatched(tmp_path):
    # first run: no lookup/ dir yet -> every unmatched row is resolved_by none
    data = build_data_dir(tmp_path, sample_rows(), with_maps=False)
    r = run(data)
    assert r.returncode == 0, r.stderr
    rows = read_inbox_rows(data / "batch" / "20260101-20260131")
    assert all(row["resolved_by"] == "none" for row in rows)
    assert all(row["category"] == "" for row in rows)


def test_no_rules_file_is_not_fatal(tmp_path):
    # a missing rule table means path-1 re-match never fires (falls to maps/manual)
    data = build_data_dir(tmp_path, sample_rows(), with_rules=False)
    r = run(data)
    assert r.returncode == 0, r.stderr
    rows = read_inbox_rows(data / "batch" / "20260101-20260131")
    desc = next(row for row in rows if row["original_description"] == "SQ *BLUE BOTTLE #99")
    # description_map still applied its fix, but no rule to categorize it -> none
    assert desc["corrected_description"] == "Blue Bottle Coffee"
    assert desc["resolved_by"] == "none"
    assert desc["category"] == ""


def test_all_categorized_batch_writes_empty_inbox(tmp_path):
    data = build_data_dir(tmp_path, [txn("STARBUCKS", category="Coffee")])
    r = run(data)
    assert r.returncode == 0, r.stderr
    rows = read_inbox_rows(data / "batch" / "20260101-20260131")
    assert rows == []


def test_uses_latest_batch_by_default(tmp_path):
    data = build_data_dir(tmp_path, sample_rows())
    r = run(data)
    assert "using latest batch: 20260101-20260131" in r.stderr


def test_no_categorized_file_is_error(tmp_path):
    data = tmp_path / "data"
    (data / "batch" / "20260101-20260131").mkdir(parents=True)
    r = run(data)
    assert r.returncode != 0
    assert "no categorized_*.csv" in r.stderr


def test_data_dir_required(tmp_path, monkeypatch):
    monkeypatch.delenv("WALLET_WATCH_DATA_DIR", raising=False)
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--batch-dir", str(tmp_path)],
        capture_output=True, text=True,
    )
    assert r.returncode != 0
    assert "no data root" in r.stderr
