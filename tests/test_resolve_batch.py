"""resolve_batch orchestrator / CLI tests (plan.md §6). Synthetic data only.

resolve_batch writes ONE review_<stamp>.csv holding every row: hard-filter hits
pre-approved (resolved_by=hard), and the unmatched rows resolved via the maps at
approved=0. That one file feeds both approver tabs.
"""

import csv
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LIB = REPO / "lib"
SCRIPTS = REPO / "scripts"
SCRIPT = SCRIPTS / "resolve_batch.py"

sys.path.insert(0, str(LIB))
sys.path.insert(0, str(SCRIPTS))

from resolve_lookup import Lookup  # noqa: E402
from rules import Condition, Rule, save_rules  # noqa: E402
from schema import CategorySource, FIELDNAMES, Transaction, to_row  # noqa: E402

OUTPUT_RE = re.compile(r"review_\d{8}_\d{6}\.csv")


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
        txn("STARBUCKS #1", category="Coffee",
            category_source=CategorySource.FILTER_RULES),              # hard-matched
        txn("SQ *BLUE BOTTLE #99"),                                    # -> desc-map, method 1
        txn("ZELLE TO SAM 0001"),                                      # -> cat-map, method 2
        txn("MYSTERY LLC"),                                            # -> none
    ]


def read_review_rows(batch_dir: Path):
    outs = sorted(batch_dir.glob("review_*.csv"))
    assert len(outs) == 1 and OUTPUT_RE.fullmatch(outs[0].name)
    with outs[0].open(newline="") as fh:
        return list(csv.DictReader(fh))


def run(data: Path, *args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--data-dir", str(data), *args],
        capture_output=True, text=True,
    )


def test_end_to_end_all_rows_in_one_review_file(tmp_path):
    data = build_data_dir(tmp_path, sample_rows())
    r = run(data)
    assert r.returncode == 0, r.stderr

    rows = read_review_rows(data / "batch" / "20260101-20260131")
    # every row is present, in order — the hard row is NOT dropped
    assert [row["original_description"] for row in rows] == \
        ["STARBUCKS #1", "SQ *BLUE BOTTLE #99", "ZELLE TO SAM 0001", "MYSTERY LLC"]
    assert [row["resolved_by"] for row in rows] == ["hard", "desc-map", "cat-map", "none"]
    assert [row["approved"] for row in rows] == ["1", "0", "0", "0"]
    # the resolver never writes the human column
    assert all(row["category_override"] == "" for row in rows)

    hard, desc, cat, none = rows
    assert hard["category"] == "Coffee" and hard["category_source"] == "filter-rules"
    assert desc["corrected_description"] == "Blue Bottle Coffee"
    assert desc["category"] == "Coffee" and desc["category_source"] == "filter-rules"
    assert cat["category"] == "Transfers" and cat["category_source"] == "dict-match"
    assert none["category"] == "" and none["category_source"] == ""


def test_summary_reports_counts(tmp_path):
    data = build_data_dir(tmp_path, sample_rows())
    r = run(data)
    assert "4 rows by source" in r.stderr
    assert "3 awaiting approval" in r.stderr
    assert "wrote" in r.stderr and "review_" in r.stderr


def test_no_maps_first_run_unmatched_are_none(tmp_path):
    # first run: no lookup/ dir yet -> every non-hard row is resolved_by none
    data = build_data_dir(tmp_path, sample_rows(), with_maps=False)
    r = run(data)
    assert r.returncode == 0, r.stderr
    rows = read_review_rows(data / "batch" / "20260101-20260131")
    non_hard = [row for row in rows if row["resolved_by"] != "hard"]
    assert all(row["resolved_by"] == "none" for row in non_hard)
    assert all(row["category"] == "" for row in non_hard)


def test_no_rules_file_is_not_fatal(tmp_path):
    # a missing rule table means path-1 re-match never fires (falls to maps/manual)
    data = build_data_dir(tmp_path, sample_rows(), with_rules=False)
    r = run(data)
    assert r.returncode == 0, r.stderr
    rows = read_review_rows(data / "batch" / "20260101-20260131")
    desc = next(row for row in rows if row["original_description"] == "SQ *BLUE BOTTLE #99")
    # description_map still applied its fix, but no rule to categorize it -> none
    assert desc["corrected_description"] == "Blue Bottle Coffee"
    assert desc["resolved_by"] == "none"
    assert desc["category"] == ""


def test_all_hard_batch_all_pre_approved(tmp_path):
    data = build_data_dir(tmp_path, [txn("STARBUCKS", category="Coffee")])
    r = run(data)
    assert r.returncode == 0, r.stderr
    rows = read_review_rows(data / "batch" / "20260101-20260131")
    assert len(rows) == 1
    assert rows[0]["resolved_by"] == "hard" and rows[0]["approved"] == "1"


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
