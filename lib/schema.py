"""Canonical schema for the pipeline.

These dataclasses are the source of truth for the data model. Field names map
one-to-one onto the CSV column headers of the layered stores (normalized/,
categorized/) and the account registry.

NEVER put a real account number in this file — use placeholder labels
(see CLAUDE.md).
"""

from dataclasses import dataclass, field, fields
from typing import Dict, List


@dataclass
class Transaction:
    """One transaction, in any layer of the pipeline."""

    # the date when the transaction happened, in format YYYY-MM-DD
    date: str

    # the amount of the transaction, positive means received money,
    # negative means spent money
    amount: str

    # the account where the transaction happened; set from Account.name
    # available values: "chaseXXXX", "chaseYYYY", "amazon", "apple", "applecard"...
    account: str

    # whether the transaction is a reference transaction (ledger reference:
    # reconciled against order history, not counted as a transaction).
    # Handlers leave this alone — it is stamped later, in cross-account
    # resolution (plan.md §4.3).
    is_reference: bool = False

    # the original description of the transaction
    original_description: str = ""

    # the manually corrected description of the transaction
    corrected_description: str = ""

    # --- For labelled transactions only ---

    # the category of the transaction
    category: str = ""

    # how the category was assigned (plan.md §6.4):
    #   0 = Phase 3 hard filter (rule table)
    #   1 = Phase 4 updated description (corrected_description re-matched by rules)
    #   2 = Phase 4 manually entered / mapped category
    # Defaults to 0: a fresh normalized row (no category yet) carries 0, and so
    # does a Phase 3 hard-filter hit.
    categorize_method: int = 0

    # tags of the transaction
    tags: List[str] = field(default_factory=list)


@dataclass
class Account:
    """A source account the pipeline ingests from.

    Listed in the data repo's account.csv (one row per account). Phase 2
    (normalize) resolves the account by `id` (extracted from the raw filename
    prefix) and dispatches to a handler by `type`.
    """

    # unique account key; MUST equal the raw export filename prefix in a batch:
    #   batch/<batch-id>/raw/<id>.csv   (e.g. "chaseXXXX")
    # Phase 2 extracts this id from the filename to resolve the account.
    id: str

    # the account name written to the `account` field of every Transaction
    # produced from this account (e.g. "chase-checking").
    name: str

    # selects the Phase 2 handler that parses this account's raw export.
    # multiple accounts may share a type (e.g. two chase cards).
    # available values: "chase-checking", "chase-credit", "discover-credit",
    # "capital-saving", "wealthfront-saving"... (more as handlers are added)
    type: str

    # optional human-readable note (institution, card purpose, etc.)
    description: str = ""


# --- CSV encoding ---
#
# On disk a Transaction is one CSV row; these column names are the contract for
# every layered store (normalized/, categorized/).

FIELDNAMES = [f.name for f in fields(Transaction)]

TAG_PREFIX = "#"


def encode_tags(tags: List[str]) -> str:
    """["food", "trip"] -> "#food #trip" """
    for tag in tags:
        # a tag with whitespace would silently split into two on read
        if tag != "".join(tag.split()):
            raise ValueError(f"tag must not contain whitespace: {tag!r}")
    return " ".join(TAG_PREFIX + tag for tag in tags)


def decode_tags(raw: str) -> List[str]:
    """"#food #trip" -> ["food", "trip"] """
    return [tag.lstrip(TAG_PREFIX) for tag in raw.split()]


def encode_bool(value: bool) -> str:
    return "1" if value else "0"


def decode_bool(raw: str) -> bool:
    # never fall back to bool(raw): bool("0") is True
    if raw in ("1", "0"):
        return raw == "1"
    if raw == "":
        return False
    raise ValueError(f"expected 1 or 0, got {raw!r}")


def encode_method(value: int) -> str:
    return str(value)


def decode_method(raw: str) -> int:
    # an older CSV without the column reads as 0 (the default / hard-filter code)
    if raw == "":
        return 0
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"expected an integer categorize_method, got {raw!r}")


def to_row(txn: Transaction) -> Dict[str, str]:
    return {
        "date": txn.date,
        "amount": txn.amount,
        "account": txn.account,
        "is_reference": encode_bool(txn.is_reference),
        "original_description": txn.original_description,
        "corrected_description": txn.corrected_description,
        "category": txn.category,
        "categorize_method": encode_method(txn.categorize_method),
        "tags": encode_tags(txn.tags),
    }


def from_row(row: Dict[str, str]) -> Transaction:
    # missing optional columns fall back to defaults, so a file written by an
    # older schema still reads (plan.md §0: append a column, tolerate missing).
    return Transaction(
        date=row["date"],
        amount=row["amount"],
        account=row["account"],
        is_reference=decode_bool(row.get("is_reference", "")),
        original_description=row.get("original_description", ""),
        corrected_description=row.get("corrected_description", ""),
        category=row.get("category", ""),
        categorize_method=decode_method(row.get("categorize_method", "")),
        tags=decode_tags(row.get("tags", "")),
    )
