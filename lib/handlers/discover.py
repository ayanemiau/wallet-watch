"""Discover handler — manual CSV export from Discover's web UI.

  discover-credit: Trans. Date,Post Date,Description,Amount,Category

Discover signs amounts the OPPOSITE of our convention: a purchase is positive
and a payment/credit is negative. We flip the sign so spending is negative and
money received is positive, matching every other handler. Post Date and
Category are ignored.
"""

from datetime import datetime
from typing import Dict

from schema import Account, Transaction

from . import handler

DISCOVER_DATE = "%m/%d/%Y"


def _date(raw: str) -> str:
    """MM/DD/YYYY -> YYYY-MM-DD. Raises on anything else."""
    return datetime.strptime(raw.strip(), DISCOVER_DATE).strftime("%Y-%m-%d")


def _flip(raw: str) -> str:
    """Flip Discover's sign to ours: purchase (+) -> spent (-), payment (-) -> (+)."""
    raw = raw.strip()
    if raw.startswith("-"):
        return raw[1:]
    if raw.startswith("+"):
        return "-" + raw[1:]
    return "-" + raw


@handler("discover-credit")
def handle_credit(row: Dict[str, str], account: Account) -> Transaction:
    # Trans. Date is when it happened; Post Date is when Discover settled it
    return Transaction(
        date=_date(row["Trans. Date"]),
        amount=_flip(row["Amount"]),
        account=account.name,
        original_description=row["Description"].strip(),
    )
