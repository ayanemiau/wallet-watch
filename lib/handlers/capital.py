"""Capital One handler — manual CSV export (360 savings).

  capital-saving: Account Number,Transaction Description,Transaction Date,
                  Transaction Type,Transaction Amount,Balance

Transaction Amount is an unsigned magnitude; direction lives in Transaction
Type (Credit = money in, Debit = money out), so we sign it ourselves. Account
Number and Balance are ignored — no real account number is read into a
Transaction.
"""

from datetime import datetime
from typing import Dict

from schema import Account, Transaction

from . import handler

CAPITAL_DATE = "%m/%d/%y"  # 2-digit year, e.g. 06/30/26


def _date(raw: str) -> str:
    """MM/DD/YY -> YYYY-MM-DD. Raises on anything else."""
    return datetime.strptime(raw.strip(), CAPITAL_DATE).strftime("%Y-%m-%d")


@handler("capital-saving")
def handle_saving(row: Dict[str, str], account: Account) -> Transaction:
    # amount is a positive magnitude; the Type column carries the direction
    amount = row["Transaction Amount"].strip()
    txn_type = row["Transaction Type"].strip()
    if txn_type == "Debit":
        amount = "-" + amount   # money out -> negative
    elif txn_type != "Credit":
        raise ValueError(f"unknown Capital transaction type: {txn_type!r}")
    return Transaction(
        date=_date(row["Transaction Date"]),
        amount=amount,
        account=account.name,
        original_description=row["Transaction Description"].strip(),
    )
