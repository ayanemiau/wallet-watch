"""Batch orchestrator over the Resolver library (plan.md §6 — Phase 4).

Reads a batch's categorized CSV and writes a run-timestamped
review_<YYYYMMDD>_<HHMMSS>.csv beside it holding EVERY row: hard-filter hits pass
through pre-approved (resolved_by=hard), and the rows Phase 3 left unmatched are
resolved via the persisted lookup maps (re-running the rule table for path-1
fixes) at approved=0 for manual approval. One review file feeds both approver
tabs. Like categorize.py this is one orchestrator over the library; the
review_approver UI is the other.

    python3 scripts/resolve_batch.py [--batch-dir <batch>] [--rules <yaml>] \
            [--input <categorized.csv>] --data-dir <root>

With no --batch-dir it uses the latest-start batch under <root>/batch/; with no
--input it resolves the newest categorized_*.csv in that batch; with no --rules
it reads <root>/rules/keywords.yaml. The maps live in <root>/lookup/.
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

from resolve_lookup import build_lookup  # noqa: E402
from resolve_review import ReviewRow, read_review, write_review  # noqa: E402
from resolver import Resolver  # noqa: E402
from rules import load_rules  # noqa: E402
from schema import Transaction, from_row  # noqa: E402

# a batch dir is named by its date range, YYYYMMDD-YYYYMMDD
BATCH_ID_RE = re.compile(r"\d{8}-\d{8}")

DESCRIPTION_MAP = "description_map.csv"
CATEGORY_MAP = "category_map.csv"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--batch-dir", type=Path, default=None,
                   help="batch workspace, e.g. $WALLET_WATCH_DATA_DIR/batch/20260101-20260630; "
                        "defaults to the latest-start batch under <data-root>/batch/")
    p.add_argument("--data-dir", type=Path, default=None,
                   help="data root holding rules/ and lookup/; defaults to $WALLET_WATCH_DATA_DIR")
    p.add_argument("--rules", type=Path, default=None,
                   help="rule table for path-1 re-match; defaults to <data-root>/rules/keywords.yaml")
    p.add_argument("--input", type=Path, default=None,
                   help="categorized CSV to resolve; defaults to the newest "
                        "categorized_*.csv in the batch")
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


def latest_categorized(batch_dir: Path) -> Path:
    # categorize writes categorized_<YYYYMMDD>_<HHMMSS>.csv per run; a name sort
    # orders by run timestamp, so the last is the newest run.
    found = sorted(batch_dir.glob("categorized_*.csv"))
    if not found:
        raise SystemExit(f"no categorized_*.csv in batch: {batch_dir} (run categorize first)")
    return found[-1]


def resolve_rules_path(arg: Path, data_dir: Path) -> Path:
    # unlike categorize, a missing rule table is not fatal here: with no rules,
    # path-1 re-match simply never fires and rows fall to path-2/manual.
    return arg or (data_dir / "rules" / "keywords.yaml")


def read_transactions(path: Path) -> List[Transaction]:
    with path.open(newline="") as fh:
        return [from_row(row) for row in csv.DictReader(fh)]


def read_history(data_dir: Path) -> List[Transaction]:
    """Categorized history the maps learn from, oldest -> newest.

    1. the committed year store `categorized/<year>.csv` (year-ordered);
    2. then the approved rows of the latest `review_*.csv` per batch, in
       chronological (stamp) order — the most recent categorizations, so a fresh
       edit wins. Until a commit step exists this is how approvals feed the maps.
    """
    history: List[Transaction] = []

    year_store = data_dir / "categorized"
    if year_store.is_dir():
        for path in sorted(year_store.glob("*.csv")):
            history.extend(read_transactions(path))

    batch_root = data_dir / "batch"
    if batch_root.is_dir():
        latest_per_batch = []
        for batch in batch_root.iterdir():
            if not (batch.is_dir() and BATCH_ID_RE.fullmatch(batch.name)):
                continue
            reviews = sorted(batch.glob("review_*.csv"))
            if reviews:
                latest_per_batch.append(reviews[-1])          # newest run in that batch
        for review in sorted(latest_per_batch, key=lambda p: p.name):  # stamp ascending
            history.extend(row.txn for row in read_review(review) if row.approved)

    return history


def main() -> None:
    args = parse_args()
    data_dir = resolve_data_dir(args.data_dir)

    if args.batch_dir is not None:
        batch_dir = args.batch_dir
    else:
        batch_dir = latest_batch_dir(data_dir)
        print(f"using latest batch: {batch_dir.name}", file=sys.stderr)

    input_path = args.input or latest_categorized(batch_dir)
    rules_path = resolve_rules_path(args.rules, data_dir)
    lookup_dir = data_dir / "lookup"

    rules = load_rules(rules_path)   # missing file -> [] (first run)
    # learn the maps from history (committed store + approved review rows), then
    # persist them under lookup/ for inspection (they are outputs, not inputs).
    history = read_history(data_dir)
    description_map, category_map = build_lookup(history)
    description_map.save(lookup_dir / DESCRIPTION_MAP)
    category_map.save(lookup_dir / CATEGORY_MAP)
    print(f"learned from {len(history)} history rows -> {len(category_map)} category "
          f"+ {len(description_map)} description entries", file=sys.stderr)

    transactions = read_transactions(input_path)
    rows: List[ReviewRow] = Resolver(rules, description_map,
                                     category_map).resolve_all(transactions)

    # timestamp the output so a rerun versions rather than overwrites (run time,
    # local): review_<YYYYMMDD>_<HHMMSS>.csv
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = batch_dir / f"review_{stamp}.csv"
    write_review(out_path, rows)

    # summarize by source: hard rows are pre-approved; everything else awaits
    # approval in the review_approver UI.
    from collections import Counter
    by = Counter(r.resolved_by for r in rows)
    pending = sum(1 for r in rows if not r.approved)
    print(f"{len(rows)} rows by source: {dict(by)} ({pending} awaiting approval)",
          file=sys.stderr)
    print(f"read {input_path.name} -> wrote {out_path} (approve every non-hard row before commit)",
          file=sys.stderr)


if __name__ == "__main__":
    main()
