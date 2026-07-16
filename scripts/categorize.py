"""Batch orchestrator over the Categorizer library (plan.md §5, tier 3a).

Reads a batch's approved normalized CSV, applies the tier-3a rule table
(rules/keywords.yaml) via Categorizer, and writes a run-timestamped
categorized_<YYYYMMDD>_<HHMMSS>.csv beside it. Like normalize_batch.py this is
one orchestrator over the library; a UI would be another. The "resolve root ->
find batch -> pick input -> run" flow lives here, not in the library.

    python3 scripts/categorize.py [--batch-dir <batch>] [--rules <yaml>] \
            [--input <normalized.csv>] --data-dir <root>

With no --batch-dir it uses the latest-start batch under <root>/batch/; with no
--input it categorizes the newest normalized_*.csv in that batch; with no
--rules it reads <root>/rules/keywords.yaml.
"""

import sys
from pathlib import Path

# make the lib/ library importable without an install (see plan.md Decisions)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

import argparse  # noqa: E402
import csv  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
from datetime import datetime  # noqa: E402
from typing import List  # noqa: E402

from categorizer import Categorizer  # noqa: E402
from schema import FIELDNAMES, Transaction, from_row, to_row  # noqa: E402

# a batch dir is named by its date range, YYYYMMDD-YYYYMMDD
BATCH_ID_RE = re.compile(r"\d{8}-\d{8}")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--batch-dir", type=Path, default=None,
                   help="batch workspace, e.g. $WALLET_WATCH_DATA_DIR/batch/20260101-20260630; "
                        "defaults to the latest-start batch under <data-root>/batch/")
    p.add_argument("--data-dir", type=Path, default=None,
                   help="data root holding rules/keywords.yaml; defaults to $WALLET_WATCH_DATA_DIR")
    p.add_argument("--rules", type=Path, default=None,
                   help="rule table to apply; defaults to <data-root>/rules/keywords.yaml")
    p.add_argument("--input", type=Path, default=None,
                   help="normalized CSV to categorize; defaults to the newest "
                        "normalized_*.csv in the batch")
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


def latest_batch_dir(data_dir: Path) -> Path:
    # Default when --batch-dir is omitted: the batch with the latest start date.
    # Batch dirs are named YYYYMMDD-YYYYMMDD, so a name sort orders by start date
    # (then end date), and the max is the latest start.
    batch_root = data_dir / "batch"
    if not batch_root.is_dir():
        raise SystemExit(f"no batch dir under data root: {batch_root}")
    batches = sorted(d for d in batch_root.iterdir()
                     if d.is_dir() and BATCH_ID_RE.fullmatch(d.name))
    if not batches:
        raise SystemExit(f"no batches found in {batch_root} (expected YYYYMMDD-YYYYMMDD dirs)")
    return batches[-1]


def latest_normalized(batch_dir: Path) -> Path:
    # normalize writes normalized_<YYYYMMDD>_<HHMMSS>.csv per run; a name sort
    # orders by run timestamp, so the last is the newest run. GATE 1 approves a
    # specific run; the operator points --input at another to override.
    found = sorted(batch_dir.glob("normalized_*.csv"))
    if not found:
        raise SystemExit(f"no normalized_*.csv in batch: {batch_dir} (run normalize first)")
    return found[-1]


def resolve_rules_path(arg: Path, data_dir: Path) -> Path:
    path = arg or (data_dir / "rules" / "keywords.yaml")
    if not path.is_file():
        raise SystemExit(f"rule table not found: {path}")
    return path


def read_transactions(path: Path) -> List[Transaction]:
    with path.open(newline="") as fh:
        return [from_row(row) for row in csv.DictReader(fh)]


def write_transactions(path: Path, transactions: List[Transaction]) -> None:
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        w.writeheader()
        for txn in transactions:
            w.writerow(to_row(txn))


def main() -> None:
    args = parse_args()
    data_dir = resolve_data_dir(args.data_dir)

    if args.batch_dir is not None:
        batch_dir = args.batch_dir
    else:
        batch_dir = latest_batch_dir(data_dir)
        print(f"using latest batch: {batch_dir.name}", file=sys.stderr)

    input_path = args.input or latest_normalized(batch_dir)
    rules_path = resolve_rules_path(args.rules, data_dir)

    transactions = read_transactions(input_path)
    categorized = Categorizer().apply_rules(transactions, str(rules_path))

    # timestamp the output so a rerun versions rather than overwrites (run time,
    # local): categorized_<YYYYMMDD>_<HHMMSS>.csv
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = batch_dir / f"categorized_{stamp}.csv"
    write_transactions(out_path, categorized)

    total = len(categorized)
    matched = sum(1 for t in categorized if t.category)
    print(f"categorized {matched}/{total} rows ({total - matched} uncategorized) "
          f"using {rules_path.name}", file=sys.stderr)
    print(f"read {input_path.name} -> wrote {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
