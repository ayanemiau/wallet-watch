"""normalize_batch orchestrator / CLI tests. Fixtures are synthetic."""

import csv
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"
DATA = REPO / "tests" / "fixtures" / "data"
BATCH = DATA / "batch" / "20260101-20260131"
SCRIPT = SCRIPTS / "normalize_batch.py"

OUTPUT_RE = re.compile(r"normalized_\d{8}_\d{6}\.csv")

sys.path.insert(0, str(SCRIPTS))

from normalize_batch import (  # noqa: E402
    account_id_from_filename,
    date_range_from_filename,
    latest_batch_dir,
)


def read_output(batch_dir: Path):
    """Locate the single run-timestamped output and return its rows."""
    outs = sorted(batch_dir.glob("normalized_*.csv"))
    assert len(outs) == 1, f"expected one normalized_*.csv, got {[o.name for o in outs]}"
    assert OUTPUT_RE.fullmatch(outs[0].name)               # versioned, run-timestamped
    assert not (batch_dir / "normalized.csv").exists()     # never the plain name
    with outs[0].open(newline="") as fh:
        return list(csv.DictReader(fh))


def test_account_id_is_filename_prefix():
    assert account_id_from_filename(Path("chaseXXXX_20250101_20260630.csv")) == "chaseXXXX"


def test_date_range_from_filename():
    assert date_range_from_filename(Path("chaseXXXX_20250101_20260630.csv")) == \
        ("2025-01-01", "2026-06-30")


def test_malformed_filename_is_hard_error(tmp_path):
    # a name without the <id>_<start>_<end> shape exits non-zero
    batch = tmp_path / "20260101-20260131"
    (batch / "raw").mkdir(parents=True)
    (batch / "raw" / "chaseXXXX.csv").write_text("a\n")
    r = subprocess.run(
        [sys.executable, str(SCRIPT),
         "--batch-dir", str(batch), "--data-dir", str(DATA)],
        capture_output=True, text=True,
    )
    assert r.returncode != 0
    assert "expected <id>_<startYYYYMMDD>_<endYYYYMMDD>" in r.stderr


def test_end_to_end_output_is_date_sorted(tmp_path):
    out_batch = tmp_path / "20260101-20260131"
    (out_batch / "raw").mkdir(parents=True)
    for f in (BATCH / "raw").glob("*.csv"):
        (out_batch / "raw" / f.name).write_bytes(f.read_bytes())

    r = subprocess.run(
        [sys.executable, str(SCRIPT),
         "--batch-dir", str(out_batch), "--data-dir", str(DATA)],
        check=True, capture_output=True, text=True,
    )

    rows = read_output(out_batch)

    # the credit file's 2025-12-31 row falls outside its 20260101_20260131
    # window and is dropped; 3 checking + 3 remaining credit = 6.
    assert len(rows) == 6
    dates = [r["date"] for r in rows]
    assert dates == sorted(dates)
    assert "2025-12-31" not in dates                        # dropped by the range filter
    assert dates[0] == "2026-01-05"                         # earliest in-window, across accounts
    assert {r["account"] for r in rows} == {"Fake Checking", "Fake Card"}
    assert all(r["is_reference"] == "0" for r in rows)      # 1/0, never true/false
    assert "1 outside range dropped" in r.stderr            # the drop is reported


def test_unknown_id_is_skipped_with_warning(tmp_path):
    # an id with no accounts.csv row is skipped (warned), not fatal
    batch = tmp_path / "20260101-20260131"
    (batch / "raw").mkdir(parents=True)
    (batch / "raw" / "nosuchacct_20260101_20260131.csv").write_text("a\n")
    r = subprocess.run(
        [sys.executable, str(SCRIPT),
         "--batch-dir", str(batch), "--data-dir", str(DATA)],
        capture_output=True, text=True,
    )
    assert r.returncode == 0                                # skip, don't fail
    assert "no account in accounts.csv" in r.stderr
    assert "skipping" in r.stderr


def test_unhandled_type_is_skipped_with_warning(tmp_path):
    # the 20260201 batch has a venmo file whose type has no handler; it is
    # skipped while the discover/capital/wealthfront files still normalize.
    src = DATA / "batch" / "20260201-20260228"
    out_batch = tmp_path / "20260201-20260228"
    (out_batch / "raw").mkdir(parents=True)
    for f in (src / "raw").glob("*.csv"):
        (out_batch / "raw" / f.name).write_bytes(f.read_bytes())

    r = subprocess.run(
        [sys.executable, str(SCRIPT),
         "--batch-dir", str(out_batch), "--data-dir", str(DATA)],
        check=True, capture_output=True, text=True,
    )
    assert "no handler for account type 'venmo'" in r.stderr
    assert "skipping" in r.stderr

    rows = read_output(out_batch)

    assert len(rows) == 6                                   # 2 discover + 2 capital + 2 wealthfront
    assert {r["account"] for r in rows} == {"Fake Discover", "Fake Capital", "Fake Wealthfront"}
    assert "-25.00" in {r["amount"] for r in rows}          # discover purchase, sign flipped


def test_latest_batch_dir_selected(tmp_path):
    batch_root = tmp_path / "batch"
    (batch_root / "20250101-20250131").mkdir(parents=True)
    (batch_root / "20260201-20260228").mkdir()
    (batch_root / "notes").mkdir()                          # non-matching, ignored
    assert latest_batch_dir(tmp_path).name == "20260201-20260228"


def test_latest_batch_dir_none_is_error(tmp_path):
    (tmp_path / "batch" / "notes").mkdir(parents=True)      # nothing matches the id shape
    with pytest.raises(SystemExit, match="no batches found"):
        latest_batch_dir(tmp_path)


def test_defaults_to_latest_batch(tmp_path):
    # with no --batch-dir, the latest-start batch is chosen and written to.
    data = tmp_path / "data"
    (data / "batch").mkdir(parents=True)
    (data / "accounts.csv").write_bytes((DATA / "accounts.csv").read_bytes())

    older = data / "batch" / "20250101-20250131"
    newer = data / "batch" / "20260201-20260228"
    for b in (older, newer):
        (b / "raw").mkdir(parents=True)
    for f in (DATA / "batch" / "20260201-20260228" / "raw").glob("*.csv"):
        (newer / "raw" / f.name).write_bytes(f.read_bytes())

    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--data-dir", str(data)],
        check=True, capture_output=True, text=True,
    )
    assert "using latest batch: 20260201-20260228" in r.stderr
    assert OUTPUT_RE.fullmatch(sorted(newer.glob("normalized_*.csv"))[0].name)
    assert not list(older.glob("normalized_*.csv"))         # older batch untouched


def test_data_dir_required(tmp_path, monkeypatch):
    monkeypatch.delenv("WALLET_WATCH_DATA_DIR", raising=False)
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--batch-dir", str(tmp_path)],
        capture_output=True, text=True,
    )
    assert r.returncode != 0
    assert "no data root" in r.stderr           # fails fast, no repo-tree default
