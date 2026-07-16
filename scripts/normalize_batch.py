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

# make the lib/ library importable without an install (see plan.md Decisions)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

import argparse  # noqa: E402
import csv  # noqa: E402
import os  # noqa: E402
from datetime import datetime  # noqa: E402
from typing import Dict, List, Optional, Tuple  # noqa: E402

from handlers import has_handler  # noqa: E402
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
    # (lib/schema.py Account: id, name, type, description).
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


def date_range_from_filename(raw_file: Path) -> Tuple[str, str]:
    # 3b. The two fields after the id are the export's window, YYYYMMDD each:
    #     chaseXXXX_20250101_20260630.csv -> ("2025-01-01", "2026-06-30")
    #     inject() then filters transactions to this inclusive range.
    parts = raw_file.stem.split("_")
    if len(parts) != 3:
        raise SystemExit(f"{raw_file.name}: expected <id>_<startYYYYMMDD>_<endYYYYMMDD>.csv")
    try:
        start = datetime.strptime(parts[1], "%Y%m%d").strftime("%Y-%m-%d")
        end = datetime.strptime(parts[2], "%Y%m%d").strftime("%Y-%m-%d")
    except ValueError as e:
        raise SystemExit(f"{raw_file.name}: bad date range in filename: {e}")
    return start, end


def resolve_account(raw_file: Path, accounts: Dict[str, Account]) -> Optional[Account]:
    # 3b. id -> account, or None if the id has no registry row. main() skips
    #     such files with a warning rather than failing the whole batch.
    return accounts.get(account_id_from_filename(raw_file))


def main() -> None:
    # Scan the batch's raw dir, resolve each file's account, inject, then output.
    args = parse_args()
    data_dir = resolve_data_dir(args.data_dir)
    accounts = load_accounts(data_dir)
    out_path = args.batch_dir / "normalized.csv"

    normalizer = Normalizer()
    try:
        for raw_file in list_raw_files(args.batch_dir):
            # skip (don't fail) files the pipeline can't process yet: an
            # unknown id, or a known account whose type has no handler.
            account = resolve_account(raw_file, accounts)
            if account is None:
                print(f"  {raw_file.name}: no account in {ACCOUNTS_FILE} for id "
                      f"{account_id_from_filename(raw_file)!r}, skipping", file=sys.stderr)
                continue
            if not has_handler(account.type):
                print(f"  {raw_file.name}: no handler for account type {account.type!r}, "
                      f"skipping", file=sys.stderr)
                continue
            start, end = date_range_from_filename(raw_file)
            result = normalizer.inject(raw_file, account.type, account, start, end)
            msg = f"  {raw_file.name}: {result.kept} rows in {start}..{end}"
            if result.dropped:
                msg += f" ({result.dropped} outside range dropped)"
            print(msg, file=sys.stderr)
        normalizer.output(out_path)
    except NormalizeError as e:
        raise SystemExit(e)

    print(f"wrote {len(normalizer.transactions)} rows -> {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
