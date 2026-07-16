"""Phase 2 — normalize: raw per-account exports -> unified Transaction rows.

`Normalizer` is the reusable component: feed it one raw export at a time with
`inject()`, then `output()` the merged, date-sorted result. It knows nothing
about argparse, accounts.csv or the batch layout — an orchestrator resolves
those and feeds it. `main()` below is one such orchestrator (the CLI); a UI or
pipeline.py can drive the same class.

See plan.md §4 for the full design.
"""

import argparse
import csv
import os
import sys
from pathlib import Path
from typing import Dict, List

from handlers import get_handler
from schema import FIELDNAMES, Account, Transaction, to_row

ACCOUNTS_FILE = "accounts.csv"

# every checking row carries a trailing empty column; absorb it rather than
# letting DictReader silently stash it under None
RESTKEY = "__extra__"


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

    def inject(self, raw_transaction_path: Path, handler: str, account: Account) -> int:
        """Parse one raw export with the named handler; keep the Transactions.

        Additive: repeated calls accumulate, which is how several accounts
        merge into one output. Returns the number of rows injected.
        """
        try:
            handle = get_handler(handler)
        except KeyError as e:
            # e.args[0], not e: str(KeyError) reprs its arg and adds quotes
            raise NormalizeError(f"{raw_transaction_path.name}: {e.args[0]}")

        rows = []
        with raw_transaction_path.open(newline="") as fh:
            for lineno, row in enumerate(csv.DictReader(fh, restkey=RESTKEY), start=2):
                extra = row.pop(RESTKEY, [])
                if any(v.strip() for v in extra):
                    raise NormalizeError(
                        f"{raw_transaction_path.name}:{lineno}: unexpected trailing data: {extra!r}")
                try:
                    rows.append(handle(row, account))
                except (KeyError, ValueError) as e:
                    raise NormalizeError(f"{raw_transaction_path.name}:{lineno}: {e}")

        # extend only once parsing succeeded, so a failed inject leaves no
        # half-parsed file behind for a caller that catches and continues
        self.transactions.extend(rows)
        return len(rows)

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


def parse_args() -> argparse.Namespace:
    # 1. --batch-dir points at batch/<batch-id>/; raw inputs come from its
    #    raw/ subdir and output goes to <batch-dir>/normalized.csv.
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--batch-dir", required=True, type=Path,
                   help="batch workspace, e.g. $WALLET_WATCH_DATA_DIR/batch/20260101-20260630")
    p.add_argument("--data-dir", type=Path, default=None,
                   help="data root holding accounts.csv; defaults to $WALLET_WATCH_DATA_DIR")
    return p.parse_args()


def resolve_data_dir(arg: Path) -> Path:
    # --data-dir, then $WALLET_WATCH_DATA_DIR, then fail fast — never a
    # silent default into the repo tree (plan.md §2.1).
    data_dir = arg or (Path(os.environ["WALLET_WATCH_DATA_DIR"])
                       if os.environ.get("WALLET_WATCH_DATA_DIR") else None)
    if data_dir is None:
        raise SystemExit("no data root: pass --data-dir or set WALLET_WATCH_DATA_DIR")
    if not data_dir.is_dir():
        raise SystemExit(f"data root does not exist: {data_dir}")
    return data_dir


def list_raw_files(batch_dir: Path) -> List[Path]:
    # 2. Read all files in <batch-dir>/raw/ — one file per account.
    raw_dir = batch_dir / "raw"
    if not raw_dir.is_dir():
        raise SystemExit(f"no raw dir in batch: {raw_dir}")
    files = sorted(raw_dir.glob("*.csv"))
    if not files:
        raise SystemExit(f"no raw exports found in {raw_dir}")
    return files


def load_accounts(data_dir: Path) -> Dict[str, Account]:
    # Load $DATA_DIR/accounts.csv into an id -> Account map
    # (src/schema.py Account: id, name, type, description).
    path = data_dir / ACCOUNTS_FILE
    if not path.is_file():
        raise SystemExit(f"account registry not found: {path}")
    accounts = {}
    with path.open(newline="") as fh:
        for row in csv.DictReader(fh):
            account = Account(
                id=row["id"].strip(),
                name=row["name"].strip(),
                type=row["type"].strip(),
                description=(row.get("description") or "").strip(),
            )
            accounts[account.id] = account
    if not accounts:
        raise SystemExit(f"account registry is empty: {path}")
    return accounts


def account_id_from_filename(raw_file: Path) -> str:
    # 3a. The id is the filename prefix, up to the first underscore:
    #     chaseXXXX_20250101_20260630.csv -> chaseXXXX
    return raw_file.stem.split("_")[0]


def resolve_account(raw_file: Path, accounts: Dict[str, Account]) -> Account:
    # 3b. id -> account. An id with no registry row is a hard error, so
    #     nothing is silently skipped. (type -> handler happens in inject.)
    account_id = account_id_from_filename(raw_file)
    if account_id not in accounts:
        known = ", ".join(sorted(accounts))
        raise SystemExit(
            f"{raw_file.name}: no row in {ACCOUNTS_FILE} for id {account_id!r}; known: {known}")
    return accounts[account_id]


def main() -> None:
    # One orchestrator over Normalizer: scan the batch's raw dir, resolve each
    # file's account, inject, then output. A UI would drive the same class.
    args = parse_args()
    data_dir = resolve_data_dir(args.data_dir)
    accounts = load_accounts(data_dir)
    out_path = args.batch_dir / "normalized.csv"

    normalizer = Normalizer()
    try:
        for raw_file in list_raw_files(args.batch_dir):
            account = resolve_account(raw_file, accounts)
            count = normalizer.inject(raw_file, account.type, account)
            print(f"  {raw_file.name}: {count} rows", file=sys.stderr)
        normalizer.output(out_path)
    except NormalizeError as e:
        raise SystemExit(e)

    print(f"wrote {len(normalizer.transactions)} rows -> {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
