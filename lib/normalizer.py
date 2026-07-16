"""Phase 2 normalize — the reusable library.

`Normalizer` turns raw per-account exports into unified `Transaction` rows:
feed it one export at a time with `inject()`, then `output()` the merged,
date-sorted result. It knows nothing about argparse, accounts.csv or the batch
layout — a caller (an orchestrator script, or a UI) resolves which handler and
account each file needs and feeds it. See `scripts/normalize_batch.py` for the
CLI orchestrator, and plan.md §4 for the full design.
"""

import csv
from datetime import datetime
from pathlib import Path
from typing import List, NamedTuple, Optional

from handlers import get_handler
from schema import FIELDNAMES, Account, Transaction, to_row

# every checking row carries a trailing empty column; absorb it rather than
# letting DictReader silently stash it under None
RESTKEY = "__extra__"


class InjectResult(NamedTuple):
    """Outcome of one inject(): rows kept vs. rows dropped as out-of-range.

    dropped is reported (not silently swallowed) because a dropped row is a
    real transaction leaving the output — a caller should be able to surface it.
    """

    kept: int
    dropped: int


class NormalizeError(Exception):
    """A raw export could not be normalized.

    Raised instead of exiting so callers decide how to surface it: the CLI
    converts it to SystemExit, a UI can catch it and keep running.
    """


class Normalizer:
    """Accumulates Transactions from raw exports, then emits them as CSV.

    Deliberately knows nothing about accounts.csv or the batch layout — the
    caller resolves which handler and which account each file needs.
    """

    def __init__(self) -> None:
        self.transactions: List[Transaction] = []

    def inject(self, raw_transaction_path: Path, handler: str, account: Account,
               start_date: Optional[str] = None, end_date: Optional[str] = None) -> InjectResult:
        """Parse one raw export with the named handler; keep the Transactions.

        Additive: repeated calls accumulate, which is how several accounts
        merge into one output. `start_date`/`end_date` (both "YYYY-MM-DD",
        both inclusive; None means that side is unbounded) filter the rows to a
        window — a transaction whose date falls outside is dropped rather than
        kept. Returns how many rows were kept vs. dropped.
        """
        self._check_bound(raw_transaction_path.name, "start_date", start_date)
        self._check_bound(raw_transaction_path.name, "end_date", end_date)

        try:
            handle = get_handler(handler)
        except KeyError as e:
            # e.args[0], not e: str(KeyError) reprs its arg and adds quotes
            raise NormalizeError(f"{raw_transaction_path.name}: {e.args[0]}")

        kept = []
        dropped = 0
        with raw_transaction_path.open(newline="") as fh:
            for lineno, row in enumerate(csv.DictReader(fh, restkey=RESTKEY), start=2):
                extra = row.pop(RESTKEY, [])
                if any(v.strip() for v in extra):
                    raise NormalizeError(
                        f"{raw_transaction_path.name}:{lineno}: unexpected trailing data: {extra!r}")
                try:
                    txn = handle(row, account)
                except (KeyError, ValueError) as e:
                    raise NormalizeError(f"{raw_transaction_path.name}:{lineno}: {e}")
                # dates are YYYY-MM-DD, so a lexicographic compare is chronological
                if (start_date is not None and txn.date < start_date) or \
                        (end_date is not None and txn.date > end_date):
                    dropped += 1
                    continue
                kept.append(txn)

        # extend only once parsing succeeded, so a failed inject leaves no
        # half-parsed file behind for a caller that catches and continues
        self.transactions.extend(kept)
        return InjectResult(kept=len(kept), dropped=dropped)

    @staticmethod
    def _check_bound(name: str, label: str, bound: Optional[str]) -> None:
        # a malformed bound would mis-compare lexicographically and silently
        # keep/drop the wrong rows — reject it up front, like schema's codecs do.
        if bound is None:
            return
        try:
            parsed = datetime.strptime(bound, "%Y-%m-%d")
        except ValueError:
            raise NormalizeError(f"{name}: {label} not YYYY-MM-DD: {bound!r}")
        # strptime accepts unpadded "2026-1-1", which sorts wrong against a padded
        # date; require the exact canonical form so the string compare is safe.
        if parsed.strftime("%Y-%m-%d") != bound:
            raise NormalizeError(f"{name}: {label} not YYYY-MM-DD: {bound!r}")

    def output(self, output_path: Path) -> None:
        """Sort accumulated Transactions by date asc and write CSV.

        Dates are YYYY-MM-DD, so a lexicographic sort is chronological; it is
        stable, so rows keep their source order within a date.
        """
        self.transactions.sort(key=lambda t: t.date)
        with output_path.open("w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=FIELDNAMES)
            w.writeheader()
            for txn in self.transactions:
                w.writerow(to_row(txn))
