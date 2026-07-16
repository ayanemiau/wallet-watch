"""Wealthfront handler — manual CSV export (Cash Account).

  wealthfront-saving: Transaction date,Description,Type,Amount

Amount is already signed in our convention (deposits/interest positive,
withdrawals negative), so it passes through untouched. Type is informational
only. The current export carries only inflows; if a withdrawal ever appears
with the opposite sign, revisit this.
"""

from datetime import datetime
from typing import Dict

from schema import Account, Transaction

from . import handler

WEALTHFRONT_DATE = "%m/%d/%Y"  # unpadded, e.g. 7/1/2026


def _date(raw: str) -> str:
    """M/D/YYYY (unpadded ok) -> YYYY-MM-DD. Raises on anything else."""
    return datetime.strptime(raw.strip(), WEALTHFRONT_DATE).strftime("%Y-%m-%d")


@handler("wealthfront-saving")
def handle_saving(row: Dict[str, str], account: Account) -> Transaction:
    # header is "Transaction date" (lowercase d); amount is already signed
    return Transaction(
        date=_date(row["Transaction date"]),
        amount=row["Amount"].strip(),
        account=account.name,
        original_description=row["Description"].strip(),
    )
