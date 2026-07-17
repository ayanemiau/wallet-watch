"""Canonical schema for the pipeline.

These dataclasses are the source of truth for the data model. Field names map
one-to-one onto the CSV column headers of the layered stores (normalized/,
categorized/) and the account registry.

NEVER put a real account number in this file — use placeholder labels
(see CLAUDE.md).
"""

from dataclasses import dataclass, field, fields
from enum import Enum
from typing import Dict, List


class CategorySource(str, Enum):
    """Where a transaction's `category` came from (plan.md §6.4).

    kebab-case string values, consistent with the codebase's other tokens
    (account types like `chase-checking`, and the sibling `resolved_by` values
    `desc-map`/`cat-map`). Member names stay UPPER_SNAKE; only the on-disk value
    is kebab. An empty `category` is `NONE`.
    """

    NONE = ""                    # no category yet (uncategorized) — the default
    FILTER_RULES = "filter-rules"  # hard-filter rule match (original OR updated description)
    DICT_MATCH = "dict-match"    # learned from prior categories, via dict/lookup match
    LLM_LABEL = "llm-label"      # learned from prior categories, via the LLM agent
    HUMAN_REVIEW = "human-review"  # entered by a human in Phase 4 review (≠ category_override)


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

    # the category of the transaction — the LEARNABLE value. Machine-set first
    # (Phase 3 hard filter, then Phase 4 dict/LLM), then correctable by a human in
    # Phase 4 review. This is the column dict-match and the LLM reuse to label
    # future transactions, so a human correction here is training signal (learned
    # via committed history, not a writeback — see plan.md §6.3).
    category: str = ""

    # a human's ONE-OFF category override (Phase 4 review). Empty = no override.
    # A separate layer from `category` for a transaction that must be treated
    # specially: it wins downstream (`effective_category = category_override or
    # category`) but is NEVER learned — it must not seed the maps/LLM. Editing it
    # (like `category`) never touches a raw field, so raw data stays immutable.
    category_override: str = ""

    # where `category` came from — see CategorySource. NONE ("") when there is no
    # category yet; FILTER_RULES / DICT_MATCH / LLM_LABEL for the machine paths;
    # HUMAN_REVIEW when a human set it in review (distinct from category_override).
    category_source: CategorySource = CategorySource.NONE

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


def encode_source(source: CategorySource) -> str:
    # explicit .value: `str(CategorySource.X)` is the member repr on some Python
    # versions, not the kebab value we persist.
    return source.value


def decode_source(raw: str) -> CategorySource:
    # "" (or a missing column) is NONE; an unknown token is a hard error rather
    # than a silent NONE, so a typo in a hand-edited CSV surfaces.
    try:
        return CategorySource(raw)
    except ValueError:
        raise ValueError(f"unknown category_source: {raw!r}")


def to_row(txn: Transaction) -> Dict[str, str]:
    return {
        "date": txn.date,
        "amount": txn.amount,
        "account": txn.account,
        "is_reference": encode_bool(txn.is_reference),
        "original_description": txn.original_description,
        "corrected_description": txn.corrected_description,
        "category": txn.category,
        "category_override": txn.category_override,
        "category_source": encode_source(txn.category_source),
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
        category_override=row.get("category_override", ""),
        category_source=decode_source(row.get("category_source", "")),
        tags=decode_tags(row.get("tags", "")),
    )


def effective_category(txn: Transaction) -> str:
    """The category to use downstream: a human override wins over the machine's.

    Charts/commit read this, not `category` directly, so a `category_override`
    transparently supersedes whatever the pipeline categorized (including a
    hard-filter match) without mutating machine output.
    """
    return txn.category_override or txn.category
