"""Batch orchestrator over the Normalizer library (plan.md §4).

Scans a batch's raw/ dir, resolves each file's account against accounts.csv,
runs the matching handler through Normalizer, and writes normalized.csv. This
is one orchestrator over the library; a UI (drag files in, one-click run) would
be another. The "scan dir -> load files -> look up type -> run" flow lives here,
not in the library, precisely so it can be replaced.

    python3 scripts/normalize_batch.py --batch-dir <batch> --data-dir <root>
"""

import sys
from pathlib import Path

# make the src/ library importable without an install (see plan.md Decisions)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import argparse  # noqa: E402
import csv  # noqa: E402
import os  # noqa: E402
from typing import Dict, List  # noqa: E402

from normalizer import NormalizeError, Normalizer  # noqa: E402
from schema import Account  # noqa: E402

ACCOUNTS_FILE = "accounts.csv"


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
    # Scan the batch's raw dir, resolve each file's account, inject, then output.
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
