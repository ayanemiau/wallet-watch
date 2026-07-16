"""Phase 2 — normalize: raw per-account exports -> unified Transaction rows.

Reads every raw export in a batch's `raw/` dir, dispatches each file to the
handler registered for its account's type, and writes the merged, date-sorted
result to `<batch-dir>/normalized.csv`.

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


def load_handler(raw_file: Path, accounts: Dict[str, Account]):
    # 3b. id -> account -> type -> handler. An id with no registry row, or a
    #     type with no handler, is a hard error so nothing is silently skipped.
    account_id = account_id_from_filename(raw_file)
    if account_id not in accounts:
        known = ", ".join(sorted(accounts))
        raise SystemExit(
            f"{raw_file.name}: no row in {ACCOUNTS_FILE} for id {account_id!r}; known: {known}")
    account = accounts[account_id]
    try:
        return account, get_handler(account.type)
    except KeyError as e:
        raise SystemExit(f"{raw_file.name}: {e}")


def normalize_file(raw_file: Path, accounts: Dict[str, Account]) -> List[Transaction]:
    # 4. Apply the handler to every raw row: (row, account) -> Transaction.
    account, handle = load_handler(raw_file, accounts)
    out = []
    with raw_file.open(newline="") as fh:
        for lineno, row in enumerate(csv.DictReader(fh, restkey=RESTKEY), start=2):
            extra = row.pop(RESTKEY, [])
            if any(v.strip() for v in extra):
                raise SystemExit(f"{raw_file.name}:{lineno}: unexpected trailing data: {extra!r}")
            try:
                out.append(handle(row, account))
            except (KeyError, ValueError) as e:
                raise SystemExit(f"{raw_file.name}:{lineno}: {e}")
    return out


def write_normalized(transactions: List[Transaction], out_path: Path) -> None:
    # 5. Sort by date and write <batch-dir>/normalized.csv. Dates are
    #    YYYY-MM-DD, so a lexicographic sort is chronological; it is stable,
    #    so rows keep their source order within a date.
    transactions.sort(key=lambda t: t.date)
    with out_path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        w.writeheader()
        for txn in transactions:
            w.writerow(to_row(txn))


def main() -> None:
    args = parse_args()
    data_dir = resolve_data_dir(args.data_dir)
    accounts = load_accounts(data_dir)

    transactions: List[Transaction] = []
    for raw_file in list_raw_files(args.batch_dir):
        rows = normalize_file(raw_file, accounts)
        print(f"  {raw_file.name}: {len(rows)} rows", file=sys.stderr)
        transactions.extend(rows)

    out_path = args.batch_dir / "normalized.csv"
    write_normalized(transactions, out_path)
    print(f"wrote {len(transactions)} rows -> {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
