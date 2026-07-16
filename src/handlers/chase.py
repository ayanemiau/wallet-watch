"""Chase handlers — manual CSV exports from the Chase web UI.

Two export shapes, one per account type:

  chase-checking: Details,Posting Date,Description,Amount,Type,Balance,Check or Slip #
  chase-credit:   Transaction Date,Post Date,Description,Category,Type,Amount,Memo

Both already use our amount convention (negative = spent, positive = received),
so amounts pass through untouched.
"""

from datetime import datetime
from typing import Dict

from schema import Account, Transaction

from . import handler

CHASE_DATE = "%m/%d/%Y"


def _date(raw: str) -> str:
    """MM/DD/YYYY -> YYYY-MM-DD. Raises on anything else."""
    return datetime.strptime(raw.strip(), CHASE_DATE).strftime("%Y-%m-%d")


@handler("chase-checking")
def handle_checking(row: Dict[str, str], account: Account) -> Transaction:
    # only Posting Date is available; there is no separate transaction date
    return Transaction(
        date=_date(row["Posting Date"]),
        amount=row["Amount"].strip(),
        account=account.name,
        original_description=row["Description"].strip(),
    )


@handler("chase-credit")
def handle_credit(row: Dict[str, str], account: Account) -> Transaction:
    # Transaction Date is when it happened; Post Date is when Chase settled it
    return Transaction(
        date=_date(row["Transaction Date"]),
        amount=row["Amount"].strip(),
        account=account.name,
        original_description=row["Description"].strip(),
    )
