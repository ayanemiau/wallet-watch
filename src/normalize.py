"""Phase 2 — normalize: raw per-account exports -> unified Transaction rows.

Reads every raw export in a batch's `raw/` dir, dispatches each file to the
handler registered for its account's type, and writes the merged, date-sorted
result to `<batch-dir>/normalized.csv`.

See plan.md §4 for the full design.
"""

import argparse


def parse_args() -> argparse.Namespace:
    # 1. The script takes a --batch-dir flag pointing at batch/<batch-id>/.
    #    Raw inputs are read from <batch-dir>/raw/, output goes to
    #    <batch-dir>/normalized.csv.
    #    Also needs the data root (--data-dir, else $WALLET_WATCH_DATA_DIR,
    #    else fail fast) to locate account.csv.
    ...


def list_raw_files(batch_dir):
    # 2. Read all files in <batch-dir>/raw/ — one file per account.
    ...


def load_accounts(data_dir):
    # Load $DATA_DIR/account.csv into an id -> Account map
    # (src/schema.py Account: id, name, type, description).
    ...


def load_handler(raw_file, accounts):
    # 3. Extract the account id from the filename prefix (raw/<id>.csv),
    #    look that id up in the account map to get its `type`, and use the
    #    type as the selector into the handler registry.
    #    Unknown id, or a type with no registered handler -> hard error,
    #    so nothing is silently skipped.
    ...


def handle(raw_row, account):
    # 4. Handler interface: (raw row, account) -> Transaction.
    #    Stamps Transaction.account = account.name.
    #    Registry lives in src/handlers/, keyed by account type.
    #    Handlers do not set is_reference — ledger references are labelled
    #    later, in cross-account resolution (plan.md §4.3).
    ...


def write_normalized(transactions, out_path):
    # 5. Sort all Transactions by date and write them to
    #    <batch-dir>/normalized.csv.
    ...


def main() -> None:
    # Wire up 1-5.
    ...


if __name__ == "__main__":
    main()
