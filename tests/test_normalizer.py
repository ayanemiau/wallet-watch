"""Normalizer library tests. Fixtures are synthetic — never real exports."""

import csv
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
LIB = REPO / "lib"
SCRIPTS = REPO / "scripts"
DATA = REPO / "tests" / "fixtures" / "data"
BATCH = DATA / "batch" / "20260101-20260131"
BATCH2 = DATA / "batch" / "20260201-20260228"

sys.path.insert(0, str(LIB))
sys.path.insert(0, str(SCRIPTS))

from handlers import get_handler  # noqa: E402
from normalize_batch import account_id_from_filename, load_accounts  # noqa: E402
from normalizer import NormalizeError, Normalizer  # noqa: E402
from schema import Account  # noqa: E402


def inject_fixture(name: str, batch: Path = BATCH) -> Normalizer:
    """Inject one fixture export, resolving its account the way main() does."""
    accounts = load_accounts(DATA)
    raw = batch / "raw" / name
    account = accounts[account_id_from_filename(raw)]
    n = Normalizer()
    n.inject(raw, account.type, account)
    return n


def test_handler_selected_by_type_not_id():
    accounts = load_accounts(DATA)
    # two distinct ids share one type -> same handler
    assert accounts["chaseYYYY"].type == accounts["chaseZZZZ"].type == "chase-credit"
    assert get_handler("chase-credit") is get_handler(accounts["chaseZZZZ"].type)


def test_checking_parses_and_absorbs_trailing_column():
    rows = inject_fixture("chaseXXXX_20260101_20260131.csv").transactions
    assert len(rows) == 3
    credit = rows[0]
    assert credit.date == "2026-01-05"          # MM/DD/YYYY -> ISO
    assert credit.amount == "1000.00"           # positive = received
    assert credit.account == "Fake Checking"    # Account.name, not id
    assert credit.is_reference is False         # stamped later, in reconcile
    assert credit.category == ""                # Phase 3's job
    assert rows[1].amount == "-50.00"           # negative = spent


def test_credit_uses_transaction_date_not_post_date():
    rows = inject_fixture("chaseYYYY_20260101_20260131.csv").transactions
    # row 2 transacted 12/31/2025 but posted 01/02/2026
    assert rows[1].date == "2025-12-31"
    assert rows[0].account == "Fake Card"


def test_inject_accumulates_and_output_merges(tmp_path):
    accounts = load_accounts(DATA)
    n = Normalizer()
    for name in ("chaseXXXX_20260101_20260131.csv", "chaseYYYY_20260101_20260131.csv"):
        raw = BATCH / "raw" / name
        account = accounts[account_id_from_filename(raw)]
        assert n.inject(raw, account.type, account).kept > 0   # no range -> all kept
    assert len(n.transactions) == 7                            # 3 checking + 4 credit

    out = tmp_path / "normalized.csv"
    n.output(out)
    with out.open(newline="") as fh:
        rows = list(csv.DictReader(fh))
    dates = [r["date"] for r in rows]
    assert dates == sorted(dates)
    assert {r["account"] for r in rows} == {"Fake Checking", "Fake Card"}


def test_inject_error_does_not_kill_the_process(tmp_path):
    # the point of NormalizeError: a caller can catch it and carry on
    accounts = load_accounts(DATA)
    bad = tmp_path / "chaseYYYY_bad.csv"
    bad.write_text("Transaction Date,Post Date,Description,Category,Type,Amount,Memo\n"
                   "not-a-date,01/02/2026,FAKE SHOP,Shopping,Sale,-1.00,\n")
    n = Normalizer()
    with pytest.raises(NormalizeError, match="chaseYYYY_bad.csv:2"):
        n.inject(bad, "chase-credit", accounts["chaseYYYY"])
    assert n.transactions == []                           # failed file left nothing behind

    # the same instance stays usable afterwards
    good = BATCH / "raw" / "chaseYYYY_20260101_20260131.csv"
    assert n.inject(good, "chase-credit", accounts["chaseYYYY"]).kept == 4


def test_unregistered_type_is_hard_error(tmp_path):
    raw = tmp_path / "x_1_2.csv"
    raw.write_text("a\n")
    with pytest.raises(NormalizeError, match="no handler registered"):
        Normalizer().inject(raw, "venmo", Account(id="x", name="X", type="venmo"))


def test_inject_filters_to_date_range():
    # the credit fixture has a 2025-12-31 row (transacted Dec, posted Jan);
    # a January window drops it on its transaction date.
    accounts = load_accounts(DATA)
    raw = BATCH / "raw" / "chaseYYYY_20260101_20260131.csv"
    account = accounts[account_id_from_filename(raw)]

    n = Normalizer()
    result = n.inject(raw, account.type, account, "2026-01-01", "2026-01-31")
    assert result == (3, 1)                                    # kept 3, dropped the Dec row
    assert len(n.transactions) == 3
    assert all("2026-01-01" <= t.date <= "2026-01-31" for t in n.transactions)


def test_inject_range_bounds_are_inclusive():
    # window edges land exactly on real transaction dates (01/07 and 01/20),
    # which must be kept — the range is inclusive on both ends.
    accounts = load_accounts(DATA)
    raw = BATCH / "raw" / "chaseYYYY_20260101_20260131.csv"
    account = accounts[account_id_from_filename(raw)]

    n = Normalizer()
    result = n.inject(raw, account.type, account, "2026-01-07", "2026-01-20")
    assert result.kept == 3                                    # 01/07, 01/09, 01/20
    assert result.dropped == 1                                 # 2025-12-31
    kept_dates = {t.date for t in n.transactions}
    assert {"2026-01-07", "2026-01-20"} <= kept_dates          # both boundaries survive


def test_inject_bad_range_bound_is_error():
    accounts = load_accounts(DATA)
    raw = BATCH / "raw" / "chaseYYYY_20260101_20260131.csv"
    account = accounts[account_id_from_filename(raw)]
    with pytest.raises(NormalizeError, match="start_date not YYYY-MM-DD"):
        Normalizer().inject(raw, account.type, account, "2026-1-1", "2026-01-31")


def test_discover_flips_sign_and_uses_trans_date():
    # Discover signs the opposite way: a purchase is +, a payment is -.
    rows = inject_fixture("discoverXXXX_20260201_20260228.csv", BATCH2).transactions
    assert len(rows) == 2
    assert rows[0].date == "2026-02-03"          # Trans. Date, not Post Date
    assert rows[0].amount == "-25.00"            # purchase (raw +25) -> spent
    assert rows[0].account == "Fake Discover"    # Account.name, not id
    assert rows[1].amount == "25.00"             # payment (raw -25) -> received


def test_capital_direction_from_type():
    # amount is an unsigned magnitude; Credit/Debit gives the sign.
    rows = inject_fixture("capitalXXXX_20260201_20260228.csv", BATCH2).transactions
    assert len(rows) == 2
    assert rows[0].date == "2026-02-28"          # MM/DD/YY -> ISO
    assert rows[0].amount == "10.00"             # Credit -> received
    assert rows[1].amount == "-500.00"           # Debit -> spent
    assert rows[0].account == "Fake Capital"


def test_capital_unknown_type_is_error(tmp_path):
    accounts = load_accounts(DATA)
    bad = tmp_path / "capitalXXXX_20260201_20260228.csv"
    bad.write_text("Account Number,Transaction Description,Transaction Date,"
                   "Transaction Type,Transaction Amount,Balance\n"
                   "0000,Mystery,02/10/26,Reversal,1.00,1.00\n")
    n = Normalizer()
    with pytest.raises(NormalizeError, match="unknown Capital transaction type"):
        n.inject(bad, "capital-saving", accounts["capitalXXXX"])


def test_wealthfront_passes_signed_amount():
    # amount is already in our convention; date is unpadded M/D/YYYY.
    rows = inject_fixture("wealthfrontXXXX_20260201_20260228.csv", BATCH2).transactions
    assert len(rows) == 2
    assert rows[0].date == "2026-02-01"          # 2/1/2026 -> ISO
    assert rows[0].amount == "12.34"             # interest, passed through
    assert rows[1].amount == "1000.00"           # deposit, passed through
    assert rows[0].account == "Fake Wealthfront"
