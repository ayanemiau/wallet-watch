"""Batch orchestrator over the Resolver library (plan.md §6 — Phase 4).

Reads a batch's categorized CSV, takes the rows Phase 3 left unmatched (empty
category), resolves each via the persisted lookup maps (re-running the rule table
for path-1 fixes), and writes a run-timestamped review_inbox_<YYYYMMDD>_<HHMMSS>.csv
beside it. Every unmatched row lands in the inbox with approved=0 for manual
approval — nothing Phase 4 produces is trusted blindly. Like categorize.py this
is one orchestrator over the library; the review_approver UI would be another.

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

from resolve_lookup import Lookup  # noqa: E402
from resolve_review import InboxRow, write_inbox  # noqa: E402
from resolver import Resolver, unmatched  # noqa: E402
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
    description_map = Lookup.load(lookup_dir / DESCRIPTION_MAP)
    category_map = Lookup.load(lookup_dir / CATEGORY_MAP)

    transactions = read_transactions(input_path)
    todo = unmatched(transactions)
    rows: List[InboxRow] = Resolver(rules, description_map, category_map).resolve(todo)

    # timestamp the output so a rerun versions rather than overwrites (run time,
    # local): review_inbox_<YYYYMMDD>_<HHMMSS>.csv
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = batch_dir / f"review_inbox_{stamp}.csv"
    write_inbox(out_path, rows)

    # summarize by source so the operator knows how much is auto-suggested vs
    # needs a fresh decision (all still require approval).
    from collections import Counter
    by = Counter(r.resolved_by for r in rows)
    matched = len(transactions) - len(todo)
    print(f"{matched} already categorized (Phase 3), {len(rows)} unmatched -> inbox: "
          f"{dict(by)}", file=sys.stderr)
    print(f"read {input_path.name} -> wrote {out_path} (approve every row before commit)",
          file=sys.stderr)


if __name__ == "__main__":
    main()
