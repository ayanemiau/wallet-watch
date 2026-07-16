"""Phase 2 normalize tests. Fixtures are synthetic — never real exports."""

import csv
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"
DATA = REPO / "tests" / "fixtures" / "data"
BATCH = DATA / "batch" / "20260101-20260131"

sys.path.insert(0, str(SRC))

from handlers import get_handler  # noqa: E402
from normalize import account_id_from_filename, load_accounts, normalize_file  # noqa: E402
from schema import Account  # noqa: E402


def test_account_id_is_filename_prefix():
    assert account_id_from_filename(Path("chaseXXXX_20250101_20260630.csv")) == "chaseXXXX"


def test_handler_selected_by_type_not_id():
    accounts = load_accounts(DATA)
    # two distinct ids share one type -> same handler
    assert accounts["chaseYYYY"].type == accounts["chaseZZZZ"].type == "chase-credit"
    assert get_handler("chase-credit") is get_handler(accounts["chaseZZZZ"].type)


def test_checking_parses_and_absorbs_trailing_column():
    accounts = load_accounts(DATA)
    rows = normalize_file(BATCH / "raw" / "chaseXXXX_20260101_20260131.csv", accounts)
    assert len(rows) == 3
    credit = rows[0]
    assert credit.date == "2026-01-05"          # MM/DD/YYYY -> ISO
    assert credit.amount == "1000.00"           # positive = received
    assert credit.account == "Fake Checking"    # Account.name, not id
    assert credit.is_reference is False         # stamped later, in reconcile
    assert credit.category == ""                # Phase 3's job
    assert rows[1].amount == "-50.00"           # negative = spent


def test_credit_uses_transaction_date_not_post_date():
    accounts = load_accounts(DATA)
    rows = normalize_file(BATCH / "raw" / "chaseYYYY_20260101_20260131.csv", accounts)
    # row 2 transacted 12/31/2025 but posted 01/02/2026
    assert rows[1].date == "2025-12-31"
    assert rows[0].account == "Fake Card"


def test_unknown_id_is_hard_error(tmp_path):
    raw = tmp_path / "nosuchacct_20260101_20260131.csv"
    raw.write_text("Transaction Date,Post Date,Description,Category,Type,Amount,Memo\n")
    with pytest.raises(SystemExit, match="no row in accounts.csv"):
        normalize_file(raw, load_accounts(DATA))


def test_unregistered_type_is_hard_error(tmp_path):
    raw = tmp_path / "x_1_2.csv"
    raw.write_text("a\n")
    with pytest.raises(SystemExit, match="no handler registered"):
        normalize_file(raw, {"x": Account(id="x", name="X", type="venmo")})


def test_end_to_end_output_is_date_sorted(tmp_path):
    out_batch = tmp_path / "20260101-20260131"
    (out_batch / "raw").mkdir(parents=True)
    for f in (BATCH / "raw").glob("*.csv"):
        (out_batch / "raw" / f.name).write_bytes(f.read_bytes())

    subprocess.run(
        [sys.executable, str(SRC / "normalize.py"),
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


def test_data_dir_required(tmp_path, monkeypatch):
    monkeypatch.delenv("WALLET_WATCH_DATA_DIR", raising=False)
    r = subprocess.run(
        [sys.executable, str(SRC / "normalize.py"), "--batch-dir", str(tmp_path)],
        capture_output=True, text=True,
    )
    assert r.returncode != 0
    assert "no data root" in r.stderr           # fails fast, no repo-tree default
