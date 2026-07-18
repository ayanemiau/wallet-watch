"""Commit a batch's approved review to the year store (plan.md §6 — step 5).

Reads a batch's approved review_<stamp>.csv, and once every row is approved
appends the reviewed transactions to the committed store categorized/<year>.csv,
routed by each transaction's year. This is the durable source of truth: once a
batch is committed its dir can be archived, and build_lookup (via read_history)
learns from the year store rather than the per-batch review file.

    python3 scripts/commit_batch.py [--batch-dir <batch>] [--input <review.csv>] \
            [--force] --data-dir <root>

Safety:
  - refuses unless EVERY row is approved (GATE 2);
  - a date-conflict guard refuses if the target year file already holds rows for a
    date in the batch (guards against committing the same batch twice) — pass
    --force to append anyway;
  - a <batch>/.committed marker records the commit and blocks re-committing (and
    tells read_history to stop reading that batch's review file). --force overrides.
"""

import sys
from pathlib import Path

# make the lib/ library importable without an install (see plan.md Decisions)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

import argparse  # noqa: E402
import csv  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
from collections import defaultdict  # noqa: E402
from datetime import datetime  # noqa: E402
from typing import Dict, List, Set  # noqa: E402

from resolve_review import all_approved, read_review  # noqa: E402
from schema import FIELDNAMES, Transaction, from_row, to_row  # noqa: E402

# a batch dir is named by its date range, YYYYMMDD-YYYYMMDD
BATCH_ID_RE = re.compile(r"\d{8}-\d{8}")

COMMITTED_MARKER = ".committed"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--batch-dir", type=Path, default=None,
                   help="batch workspace to commit; defaults to the latest-start "
                        "batch under <data-root>/batch/")
    p.add_argument("--data-dir", type=Path, default=None,
                   help="data root holding categorized/; defaults to $WALLET_WATCH_DATA_DIR")
    p.add_argument("--input", type=Path, default=None,
                   help="approved review CSV to commit; defaults to the newest "
                        "review_*.csv in the batch")
    p.add_argument("--force", action="store_true",
                   help="commit despite a date conflict or an existing .committed marker")
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
    batch_root = data_dir / "batch"
    if not batch_root.is_dir():
        raise SystemExit(f"no batch dir under data root: {batch_root}")
    batches = sorted(d for d in batch_root.iterdir()
                     if d.is_dir() and BATCH_ID_RE.fullmatch(d.name))
    if not batches:
        raise SystemExit(f"no batches found in {batch_root} (expected YYYYMMDD-YYYYMMDD dirs)")
    return batches[-1]


def latest_review(batch_dir: Path) -> Path:
    found = sorted(batch_dir.glob("review_*.csv"))
    if not found:
        raise SystemExit(f"no review_*.csv in batch: {batch_dir} (run resolve_batch first)")
    return found[-1]


def read_transactions(path: Path) -> List[Transaction]:
    with path.open(newline="") as fh:
        return [from_row(row) for row in csv.DictReader(fh)]


# --- pure helpers (year routing + conflict detection) ---


def year_of(txn: Transaction) -> str:
    """The year a transaction belongs to (its date is YYYY-MM-DD)."""
    return txn.date[:4]


def route_by_year(txns: List[Transaction]) -> Dict[str, List[Transaction]]:
    """Group transactions by year, so a boundary-spanning batch splits by file."""
    by_year: Dict[str, List[Transaction]] = defaultdict(list)
    for txn in txns:
        by_year[year_of(txn)].append(txn)
    return dict(by_year)


def conflicting_dates(incoming: List[Transaction], existing: List[Transaction]) -> Set[str]:
    """Dates present in BOTH the batch and the year file — the double-commit signal."""
    return {t.date for t in incoming} & {t.date for t in existing}


# --- committed store I/O ---


def append_year(path: Path, txns: List[Transaction]) -> None:
    """Append rows to categorized/<year>.csv, writing the header only when new."""
    path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not path.is_file()
    with path.open("a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        if new_file:
            w.writeheader()
        for txn in txns:
            w.writerow(to_row(txn))


def main() -> None:
    args = parse_args()
    data_dir = resolve_data_dir(args.data_dir)

    if args.batch_dir is not None:
        batch_dir = args.batch_dir
    else:
        batch_dir = latest_batch_dir(data_dir)
        print(f"using latest batch: {batch_dir.name}", file=sys.stderr)

    marker = batch_dir / COMMITTED_MARKER
    if marker.exists() and not args.force:
        raise SystemExit(f"batch already committed: {marker}\n"
                         f"re-run with --force to commit again")

    input_path = args.input or latest_review(batch_dir)
    rows = read_review(input_path)

    # GATE 2: every row must be approved before anything is written.
    if not all_approved(rows):
        pending = sum(1 for r in rows if not r.approved)
        raise SystemExit(f"{pending} of {len(rows)} rows not approved in {input_path.name}; "
                         f"approve every row (GATE 2) before committing")

    by_year = route_by_year([r.txn for r in rows])
    store = data_dir / "categorized"

    # date-conflict guard: refuse (unless --force) if a year file already holds a
    # date from this batch — the safety net against committing the same batch twice.
    conflicts: Dict[str, Set[str]] = {}
    for year, txns in by_year.items():
        year_path = store / f"{year}.csv"
        existing = read_transactions(year_path) if year_path.is_file() else []
        clash = conflicting_dates(txns, existing)
        if clash:
            conflicts[year] = clash
    if conflicts and not args.force:
        for year in sorted(conflicts):
            dates = ", ".join(sorted(conflicts[year]))
            print(f"conflict: {dates} already in categorized/{year}.csv", file=sys.stderr)
        raise SystemExit("refusing; re-run with --force to append anyway")

    for year in sorted(by_year):
        append_year(store / f"{year}.csv", by_year[year])

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    years = ", ".join(sorted(by_year))
    marker.write_text(f"committed {stamp}\nrows {len(rows)}\nyears {years}\n"
                      f"from {input_path.name}\n")

    total = sum(len(v) for v in by_year.values())
    print(f"committed {total} rows -> categorized/{{{years}}}.csv "
          f"(marker {marker.name})", file=sys.stderr)


if __name__ == "__main__":
    main()
