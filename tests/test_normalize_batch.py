"""normalize_batch orchestrator / CLI tests. Fixtures are synthetic."""

import csv
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"
DATA = REPO / "tests" / "fixtures" / "data"
BATCH = DATA / "batch" / "20260101-20260131"
SCRIPT = SCRIPTS / "normalize_batch.py"

sys.path.insert(0, str(SCRIPTS))

from normalize_batch import account_id_from_filename  # noqa: E402


def test_account_id_is_filename_prefix():
    assert account_id_from_filename(Path("chaseXXXX_20250101_20260630.csv")) == "chaseXXXX"


def test_end_to_end_output_is_date_sorted(tmp_path):
    out_batch = tmp_path / "20260101-20260131"
    (out_batch / "raw").mkdir(parents=True)
    for f in (BATCH / "raw").glob("*.csv"):
        (out_batch / "raw" / f.name).write_bytes(f.read_bytes())

    subprocess.run(
        [sys.executable, str(SCRIPT),
         "--batch-dir", str(out_batch), "--data-dir", str(DATA)],
        check=True, capture_output=True,
    )

    with (out_batch / "normalized.csv").open(newline="") as fh:
        rows = list(csv.DictReader(fh))

    assert len(rows) == 7                                   # 3 checking + 4 credit
    dates = [r["date"] for r in rows]
    assert dates == sorted(dates)
    assert dates[0] == "2025-12-31"                         # merged across accounts
    assert {r["account"] for r in rows} == {"Fake Checking", "Fake Card"}
    assert all(r["is_reference"] == "0" for r in rows)      # 1/0, never true/false


def test_unknown_id_is_hard_error_at_cli(tmp_path):
    # id -> account is the orchestrator's job now, so drive it through the CLI
    batch = tmp_path / "20260101-20260131"
    (batch / "raw").mkdir(parents=True)
    (batch / "raw" / "nosuchacct_20260101_20260131.csv").write_text("a\n")
    r = subprocess.run(
        [sys.executable, str(SCRIPT),
         "--batch-dir", str(batch), "--data-dir", str(DATA)],
        capture_output=True, text=True,
    )
    assert r.returncode != 0
    assert "no row in accounts.csv" in r.stderr


def test_data_dir_required(tmp_path, monkeypatch):
    monkeypatch.delenv("WALLET_WATCH_DATA_DIR", raising=False)
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--batch-dir", str(tmp_path)],
        capture_output=True, text=True,
    )
    assert r.returncode != 0
    assert "no data root" in r.stderr           # fails fast, no repo-tree default
