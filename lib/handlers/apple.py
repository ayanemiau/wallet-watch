"""Apple Card handler — manual CSV export (Wallet → Card Balance → Export).

  apple-credit: Transaction Date,Clearing Date,Description,Merchant,Category,
                Type,Amount (USD),Purchased By

Apple signs amounts the OPPOSITE of our convention (like Discover): a purchase —
and a Daily Cash "Debit" clawback — is positive, while a payment / return /
credit-adjustment is negative. We flip the sign so spending is negative and
money received is positive, uniformly across every Type (so the Type column is
never inspected). Transaction Date is when it happened; Clearing Date is
settlement and is ignored.

Apple's export uniquely ships a cleaned `Merchant` and its own `Category`
column. We fold both into `original_description` as text —
`"<Category> <Merchant> <Description>"` — to give the Phase 3 rule engine and
Phase 4 dict-match extra signal. Category always leads. Merchant is always
included: in the real export only ~70% of descriptions already begin with the
merchant, so (per "if all are prefixed, drop it; otherwise prefix, duplication
ok") we prepend it unconditionally, tolerating a duplicate prefix on the rows
that already matched. This is text only — `Transaction.category` stays empty;
categorization remains Phase 3's job.
"""

from datetime import datetime
from typing import Dict

from schema import Account, Transaction

from . import handler

APPLE_DATE = "%m/%d/%Y"


def _date(raw: str) -> str:
    """MM/DD/YYYY -> YYYY-MM-DD. Raises on anything else."""
    return datetime.strptime(raw.strip(), APPLE_DATE).strftime("%Y-%m-%d")


def _flip(raw: str) -> str:
    """Flip Apple's sign to ours: purchase (+) -> spent (-), payment (-) -> (+)."""
    raw = raw.strip()
    if raw.startswith("-"):
        return raw[1:]
    if raw.startswith("+"):
        return "-" + raw[1:]
    return "-" + raw


@handler("apple-credit")
def handle_credit(row: Dict[str, str], account: Account) -> Transaction:
    # fold Apple's Category + cleaned Merchant + raw Description into one
    # original_description (Category, then Merchant, then Description)
    parts = [row["Category"].strip(), row["Merchant"].strip(), row["Description"].strip()]
    return Transaction(
        date=_date(row["Transaction Date"]),
        amount=_flip(row["Amount (USD)"]),
        account=account.name,
        original_description=" ".join(p for p in parts if p),
    )
