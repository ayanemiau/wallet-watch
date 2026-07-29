"""Splitwise handler — per-group CSV export (Group → Export as spreadsheet).

  splitwise: Date,Description,Category,Cost,Currency,<person>,<person>,...

Splitwise is a shared-ledger export, not a bank statement, so it differs from
every other handler in three ways:

1. One account, many files. A person can export several groups
   (splitwise_bond_..., splitwise_yoruhato_...); all resolve to the single
   `splitwise` account. We treat each file independently.

2. Per-person NET columns. Every participant gets a column; its value is that
   person's net for the row = (what they paid out of pocket) - (their share).
   We only read the operator's column, named by `account.handler_user` (the
   Splitwise display name that is "me"). Splitwise already signs it our way:
   negative = you owe = spent, positive = you're owed.

3. One row can be two economic events. Splitwise's normal shape is a single
   payer who fronted the whole `Cost`; on that assumption we can split the
   operator's net back into its parts, and emit up to two Transactions:

     col = operator's column, cost = row Cost, share S = amount they consumed
       col == 0  -> not involved / nets out         -> emit nothing
       col <  0  -> owed, paid nothing out of pocket -> ONE expense, amount=col
                                                        (S = -col)
       col >  0  -> fronted the bill (paid = cost)   -> TWO rows:
                      expense       amount = col - cost   (= -S; skip if 0)
                      reimbursement amount = cost         (their out-of-pocket)

   The two amounts sum to `col`, so nothing is invented. The reimbursement is
   the money the operator fronted; a later phase reconciles it against the
   matching bank/card charge of -cost. Settle-up ("Payment") rows need no
   special case: they land in the col>0 branch with cost==col, so the expense
   is 0 (dropped) and only the reimbursement survives.

   The single-payer assumption is what lets us recover S and out-of-pocket from
   the export; a rare multi-payer row (several people fronted one expense) would
   mis-split, since the export doesn't say who paid how much.

The `Total balance` trailing row is a summary, not a transaction, and is
dropped. Category is folded into original_description (like the Apple handler)
to give the Phase 3 rules signal; the reimbursement row carries a trailing
`splitwise-repaid` token so a rule can classify it as a transfer/reference.
"""

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Dict, List

from schema import Account, Transaction

from . import handler

# Splitwise's export already uses ISO dates
SPLITWISE_DATE = "%Y-%m-%d"

# a summary row, not a transaction (blank Cost); Splitwise always emits it last
TOTAL_ROW = "Total balance"

# tacked onto the reimbursement's description so a Phase 3 rule can spot it
REPAID_TOKEN = "splitwise-repaid"

CENTS = Decimal("0.01")


def _date(raw: str) -> str:
    """YYYY-MM-DD -> YYYY-MM-DD, validated. Raises on anything else."""
    return datetime.strptime(raw.strip(), SPLITWISE_DATE).strftime("%Y-%m-%d")


def _money(value: Decimal) -> str:
    """Decimal -> our 2dp string ("-8.04", "32.13"); no leading + on positives."""
    return str(value.quantize(CENTS))


def _decimal(raw: str, field: str) -> Decimal:
    try:
        return Decimal(raw.strip())
    except InvalidOperation:
        raise ValueError(f"{field} is not a number: {raw!r}")


def _description(row: Dict[str, str]) -> str:
    # fold "<Category> <Description>" like the Apple handler; Category leads
    parts = [row["Category"].strip(), row["Description"].strip()]
    return " ".join(p for p in parts if p)


@handler("splitwise")
def handle(row: Dict[str, str], account: Account) -> List[Transaction]:
    user = account.handler_user
    if not user:
        raise ValueError("splitwise account has no handler_user (which column is 'me')")
    if user not in row:
        raise ValueError(f"no column {user!r} in this export; columns: "
                         f"{', '.join(k for k in row if k)}")

    if row["Description"].strip() == TOTAL_ROW:
        return []                                  # summary row, not a transaction

    date = _date(row["Date"])
    desc = _description(row)
    col = _decimal(row[user], user)

    if col == 0:
        return []                                  # operator not involved / nets out

    if col < 0:
        # owed, nothing out of pocket: the whole net is the operator's share
        return [Transaction(date=date, amount=_money(col), account=account.name,
                            original_description=desc)]

    # col > 0: operator fronted the bill (paid the full Cost). Split the net
    # into their share (an expense) and what they paid out of pocket (a
    # reimbursement that reconciles against the bank/card charge later).
    cost = _decimal(row["Cost"], "Cost")
    produced = []
    share = col - cost                             # = -S, <= 0
    if share != 0:
        produced.append(Transaction(date=date, amount=_money(share), account=account.name,
                                    original_description=desc))
    reimbursement = Transaction(
        date=date, amount=_money(cost), account=account.name,
        original_description=f"{desc} {REPAID_TOKEN}".strip())
    produced.append(reimbursement)
    return produced
