"""resolve_batch orchestrator / CLI tests (plan.md §6). Synthetic data only.

resolve_batch now LEARNS the maps from history (committed categorized/<year>.csv
plus approved review_*.csv rows) via build_lookup, then resolves the batch. It
writes ONE review_<stamp>.csv holding every row: hard-filter hits pre-approved,
unmatched rows resolved via the learned maps at approved=0.
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

from resolve_review import BY_NONE, ReviewRow, write_review  # noqa: E402
from rules import Condition, Rule, save_rules  # noqa: E402
from schema import CategorySource, FIELDNAMES, Transaction, to_row  # noqa: E402

OUTPUT_RE = re.compile(r"review_\d{8}_\d{6}\.csv")


def txn(desc, category="", **kw) -> Transaction:
    base = dict(date="2026-01-05", amount="-6.00", account="Fake Card",
                original_description=desc, category=category)
    base.update(kw)
    return Transaction(**base)


def write_csv(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        w.writeheader()
        for t in rows:
            w.writerow(to_row(t))


def build_data_dir(tmp_path: Path, batch_rows, *, history=None, with_rules=True) -> Path:
    """A data root: one batch's categorized input, optional committed history + rules."""
    data = tmp_path / "data"
    batch = data / "batch" / "20260101-20260131"
    write_csv(batch / "categorized_20260101_000000.csv", batch_rows)
    if history:
        write_csv(data / "categorized" / "2026.csv", history)   # what the maps learn from
    if with_rules:
        save_rules(data / "rules" / "keywords.yaml", [
            Rule(category="Coffee", match="all",
                 conditions=[Condition("original_description", "contains", "blue bottle")]),
        ])
    return data


def history_rows():
    # seeds both maps: description_map (corrected_description) + category_map (category)
    return [
        txn("SQ *BLUE BOTTLE #1", category="Coffee",
            corrected_description="Blue Bottle Coffee"),   # -> desc-map + rule -> Coffee
        txn("ZELLE TO SAM 8842", category="Transfers"),    # -> cat-map wildcard
    ]


def sample_rows():
    return [
        txn("STARBUCKS #1", category="Coffee",
            category_source=CategorySource.FILTER_RULES),   # hard-matched
        txn("SQ *BLUE BOTTLE #99"),                         # -> desc-map (path 1)
        txn("ZELLE TO SAM 0001"),                           # -> cat-map (path 2)
        txn("MYSTERY LLC"),                                 # -> none
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


def test_end_to_end_learns_maps_from_history(tmp_path):
    data = build_data_dir(tmp_path, sample_rows(), history=history_rows())
    r = run(data)
    assert r.returncode == 0, r.stderr

    rows = read_review_rows(data / "batch" / "20260101-20260131")
    assert [row["original_description"] for row in rows] == \
        ["STARBUCKS #1", "SQ *BLUE BOTTLE #99", "ZELLE TO SAM 0001", "MYSTERY LLC"]
    assert [row["resolved_by"] for row in rows] == ["hard", "desc-map", "cat-map", "none"]
    assert [row["approved"] for row in rows] == ["1", "0", "0", "0"]
    assert all(row["category_override"] == "" for row in rows)

    hard, desc, cat, none = rows
    assert hard["category"] == "Coffee" and hard["category_source"] == "filter-rules"
    assert desc["corrected_description"] == "Blue Bottle Coffee"   # from the learned desc-map
    assert desc["category"] == "Coffee" and desc["category_source"] == "filter-rules"
    assert cat["category"] == "Transfers" and cat["category_source"] == "dict-match"
    assert none["category"] == "" and none["category_source"] == ""

    # the learned maps are persisted under lookup/ for inspection
    assert (data / "lookup" / "category_map.csv").is_file()
    assert (data / "lookup" / "description_map.csv").is_file()


def test_amount_specific_category_wins_end_to_end(tmp_path):
    history = [
        txn("SQ TAXI 1", amount="-8.00", category="Rideshare"),
        txn("SQ TAXI 2", amount="-8.00", category="Rideshare"),
        txn("SQ TAXI 3", amount="-40.00", category="Airport"),
    ]
    batch = [txn("SQ TAXI 7", amount="-8.00"), txn("SQ TAXI 8", amount="-40.00")]
    data = build_data_dir(tmp_path, batch, history=history)
    r = run(data)
    assert r.returncode == 0, r.stderr
    rows = read_review_rows(data / "batch" / "20260101-20260131")
    by_amount = {row["amount"]: row["category"] for row in rows}
    assert by_amount["-8.00"] == "Rideshare"     # exact-amount entry
    assert by_amount["-40.00"] == "Airport"      # exact-amount entry
    assert all(row["resolved_by"] == "cat-map" for row in rows)


def test_no_history_leaves_unmatched_as_none(tmp_path):
    data = build_data_dir(tmp_path, sample_rows(), history=None)
    r = run(data)
    assert r.returncode == 0, r.stderr
    rows = read_review_rows(data / "batch" / "20260101-20260131")
    non_hard = [row for row in rows if row["resolved_by"] != "hard"]
    assert all(row["resolved_by"] == "none" for row in non_hard)
    assert all(row["category"] == "" for row in non_hard)


def test_no_rules_file_is_not_fatal(tmp_path):
    # a missing rule table means path-1 re-match never fires (falls to maps/manual)
    data = build_data_dir(tmp_path, sample_rows(), history=history_rows(), with_rules=False)
    r = run(data)
    assert r.returncode == 0, r.stderr
    rows = read_review_rows(data / "batch" / "20260101-20260131")
    desc = next(row for row in rows if row["original_description"] == "SQ *BLUE BOTTLE #99")
    # desc-map still applied its fix, but with no rule the re-match can't categorize.
    # the cat-map wildcard (learned from the same history row) still catches it, though.
    assert desc["corrected_description"] == "Blue Bottle Coffee"
    assert desc["category"] == "Coffee" and desc["resolved_by"] == "cat-map"


def test_summary_reports_learning_and_counts(tmp_path):
    data = build_data_dir(tmp_path, sample_rows(), history=history_rows())
    r = run(data)
    assert "learned from 2 history rows" in r.stderr
    assert "4 rows by source" in r.stderr
    assert "3 awaiting approval" in r.stderr


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


# --- read_history (committed store + approved review union, recency order) ---


def test_read_history_unions_store_and_approved_reviews(tmp_path):
    from resolve_batch import read_history

    data = tmp_path / "data"
    write_csv(data / "categorized" / "2026.csv", [txn("SHOP A", category="Groceries")])
    batch = data / "batch" / "20260201-20260228"
    write_review(batch / "review_20260301_000000.csv", [
        ReviewRow(txn("SHOP B", category="Dining"), BY_NONE, approved=True),
        ReviewRow(txn("SHOP C", category="Nope"), BY_NONE, approved=False),  # dropped
    ])

    history = read_history(data)
    # committed store first (older), then approved review rows (newer); unapproved dropped
    assert [t.original_description for t in history] == ["SHOP A", "SHOP B"]


def test_read_history_empty_when_nothing_committed(tmp_path):
    data = tmp_path / "data"
    (data / "batch").mkdir(parents=True)
    from resolve_batch import read_history
    assert read_history(data) == []
