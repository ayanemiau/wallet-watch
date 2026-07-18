"""commit_batch orchestrator / CLI tests (plan.md §6 — step 5). Synthetic data only.

commit appends a batch's APPROVED review rows to categorized/<year>.csv, routed by
year, guarded by an all-approved gate, a date-conflict check (--force to override),
and a .committed marker (blocks re-commit; makes read_history skip the batch).
"""

import csv
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LIB = REPO / "lib"
SCRIPTS = REPO / "scripts"
SCRIPT = SCRIPTS / "commit_batch.py"

sys.path.insert(0, str(LIB))
sys.path.insert(0, str(SCRIPTS))

from resolve_review import BY_HARD, BY_NONE, ReviewRow, write_review  # noqa: E402
from schema import FIELDNAMES, Transaction, to_row  # noqa: E402


def txn(desc, date="2026-03-04", category="Coffee", **kw) -> Transaction:
    base = dict(date=date, amount="-6.00", account="Fake Card",
                original_description=desc, category=category)
    base.update(kw)
    return Transaction(**base)


def write_year_csv(path: Path, txns) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        w.writeheader()
        for t in txns:
            w.writerow(to_row(t))


def make_batch(tmp_path: Path, review_rows, batch="20260301-20260331") -> Path:
    """A data root with one batch holding an approved review_*.csv. Returns data dir."""
    data = tmp_path / "data"
    b = data / "batch" / batch
    b.mkdir(parents=True)
    write_review(b / "review_20260401_000000.csv", review_rows)
    return data


def batch_dir(data: Path, batch="20260301-20260331") -> Path:
    return data / "batch" / batch


def read_year(data: Path, year: str):
    with (data / "categorized" / f"{year}.csv").open(newline="") as fh:
        return list(csv.DictReader(fh))


def run(data: Path, *args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--data-dir", str(data), *args],
        capture_output=True, text=True,
    )


# --- pure helpers ---


def test_route_by_year_splits_boundary():
    from commit_batch import route_by_year
    out = route_by_year([txn("A", date="2025-12-31"), txn("B", date="2026-01-01"),
                         txn("C", date="2026-06-01")])
    assert set(out) == {"2025", "2026"}
    assert [t.original_description for t in out["2026"]] == ["B", "C"]


def test_conflicting_dates():
    from commit_batch import conflicting_dates
    inc = [txn("A", date="2026-03-04"), txn("B", date="2026-03-05")]
    exi = [txn("X", date="2026-03-04"), txn("Y", date="2026-03-09")]
    assert conflicting_dates(inc, exi) == {"2026-03-04"}


# --- happy path ---


def test_commits_approved_rows_to_year_store(tmp_path):
    data = make_batch(tmp_path, [
        ReviewRow(txn("COFFEE HUT", category="Coffee"), BY_HARD, approved=True),
        ReviewRow(txn("TAXI", category="Rideshare"), BY_NONE, approved=True),
    ])
    r = run(data)
    assert r.returncode == 0, r.stderr

    rows = read_year(data, "2026")
    assert [row["original_description"] for row in rows] == ["COFFEE HUT", "TAXI"]
    assert [row["category"] for row in rows] == ["Coffee", "Rideshare"]
    # workflow columns are NOT persisted to the committed store
    assert "resolved_by" not in rows[0] and "approved" not in rows[0]

    assert (batch_dir(data) / ".committed").exists()
    assert "committed 2 rows" in r.stderr


def test_routes_rows_by_year(tmp_path):
    data = make_batch(tmp_path, [
        ReviewRow(txn("OLD YEAR", date="2025-12-31", category="X"), BY_NONE, approved=True),
        ReviewRow(txn("NEW YEAR", date="2026-01-02", category="Y"), BY_NONE, approved=True),
    ])
    r = run(data)
    assert r.returncode == 0, r.stderr
    assert [x["original_description"] for x in read_year(data, "2025")] == ["OLD YEAR"]
    assert [x["original_description"] for x in read_year(data, "2026")] == ["NEW YEAR"]


# --- gates ---


def test_refuses_when_not_all_approved(tmp_path):
    data = make_batch(tmp_path, [
        ReviewRow(txn("A", category="X"), BY_NONE, approved=True),
        ReviewRow(txn("B", category=""), BY_NONE, approved=False),
    ])
    r = run(data)
    assert r.returncode != 0
    assert "not approved" in r.stderr
    assert not (data / "categorized").exists()            # nothing written
    assert not (batch_dir(data) / ".committed").exists()


def test_date_conflict_refused_then_forced(tmp_path):
    data = make_batch(tmp_path, [
        ReviewRow(txn("NEW", date="2026-03-04", category="Coffee"), BY_NONE, approved=True),
    ])
    write_year_csv(data / "categorized" / "2026.csv",
                   [txn("OLD", date="2026-03-04", category="Old")])   # same date already there

    r = run(data)
    assert r.returncode != 0
    assert "2026-03-04 already in categorized/2026.csv" in r.stderr
    assert len(read_year(data, "2026")) == 1                          # nothing appended

    r2 = run(data, "--force")
    assert r2.returncode == 0, r2.stderr
    assert [x["original_description"] for x in read_year(data, "2026")] == ["OLD", "NEW"]


def test_refuses_recommit_unless_forced(tmp_path):
    data = make_batch(tmp_path, [
        ReviewRow(txn("A", category="X"), BY_NONE, approved=True),
    ])
    assert run(data).returncode == 0

    again = run(data)                                     # marker blocks a second commit
    assert again.returncode != 0
    assert "already committed" in again.stderr

    forced = run(data, "--force")                         # --force overrides marker + conflict
    assert forced.returncode == 0, forced.stderr
    assert len(read_year(data, "2026")) == 2              # appended a second time


def test_data_dir_required(tmp_path, monkeypatch):
    monkeypatch.delenv("WALLET_WATCH_DATA_DIR", raising=False)
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--batch-dir", str(tmp_path)],
        capture_output=True, text=True,
    )
    assert r.returncode != 0
    assert "no data root" in r.stderr
