"""Phase 4 resolve — the persisted norm_key maps (the reusable library).

Two maps live in the data root's `lookup/` dir, both keyed by `norm_key`:

  - description_map.csv  `Lookup` (2-col)          norm_key(original) -> corrected_description  (path 1)
  - category_map.csv     `CategoryLookup` (3-col)  norm_key(original)[, amount] -> category      (path 2)

`norm_key` collapses the volatile parts of a bank description — order ids, store
numbers, dates, punctuation, casing — so every appearance of one merchant shares
a single key. That is what lets a decision made once ("SQ *BLUE BOTTLE #4471" ->
"Blue Bottle Coffee") auto-apply to "SQ *BLUE BOTTLE #0093" next run. The category
map is additionally **amount-aware**: a merchant whose category depends on the
amount pins per-`(key, amount)` entries, otherwise a single wildcard matches any
amount (see `build_category_map`).

The maps are **projected from history** by `build_lookup` each run (not
hand-maintained), so resolving a merchant once — then rerunning — auto-applies it.
This module is pure I/O + lookup + the build; the resolving logic lives in
`lib/resolver.py`. See plan.md §6.2.
"""

import csv
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

from schema import Transaction

# 1. casefold + drop punctuation (unicode-aware, so non-ASCII merchant names
#    survive), 2. drop digit runs (store #, order id, dates), 3. collapse space.
_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_DIGITS = re.compile(r"\d+")
_SPACE = re.compile(r"\s+")


def norm_key(description: str) -> str:
    """Merchant-stable key for a description (plan.md §6.2)."""
    s = description.casefold()
    s = _PUNCT.sub(" ", s)
    s = _DIGITS.sub(" ", s)
    return _SPACE.sub(" ", s).strip()


def canon_amount(amount: str) -> Optional[str]:
    """A canonical string for an amount so value-equal amounts share a key.

    "-9.90", "-9.9" and "-9.900" all collapse to "-9.9"; "100.00" -> "100".
    Returns None for a non-numeric/empty amount (can't be amount-conditioned).
    """
    try:
        d = Decimal(amount).normalize()
    except (InvalidOperation, TypeError, ValueError):
        return None
    if d == 0:                      # normalize() can yield "0E+1" / "-0"
        d = Decimal(0)
    return f"{d:f}"                 # fixed-point, never scientific notation


class Lookup:
    """A `norm_key -> value` map, persisted as a `key,value` CSV.

    Keys are stored already-normalized, so distinct raw descriptions for the same
    merchant collapse to one entry. Queries normalize on the way in, so callers
    pass the raw `original_description` and never touch `norm_key` themselves.
    """

    KEY, VALUE = "key", "value"
    FIELDNAMES = [KEY, VALUE]

    def __init__(self, entries: Optional[Dict[str, str]] = None):
        self._entries: Dict[str, str] = dict(entries or {})

    def get(self, description: str) -> Optional[str]:
        """The stored value for this description's merchant, or None."""
        return self._entries.get(norm_key(description))

    def __contains__(self, description: str) -> bool:
        return norm_key(description) in self._entries

    def __len__(self) -> int:
        return len(self._entries)

    def put(self, description: str, value: str) -> None:
        """Record (or overwrite) the decision for this description's merchant."""
        self._entries[norm_key(description)] = value

    def items(self) -> Iterator[Tuple[str, str]]:
        return iter(self._entries.items())

    @classmethod
    def load(cls, path: Path) -> "Lookup":
        """Read a map CSV. A missing file is an empty map (first run)."""
        if not path.is_file():
            return cls()
        with path.open(newline="") as fh:
            entries = {row[cls.KEY]: row.get(cls.VALUE, "")
                       for row in csv.DictReader(fh) if row.get(cls.KEY)}
        return cls(entries)

    def save(self, path: Path) -> None:
        """Write the map CSV atomically-ish (key-sorted for a stable diff)."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=self.FIELDNAMES)
            w.writeheader()
            for key in sorted(self._entries):
                w.writerow({self.KEY: key, self.VALUE: self._entries[key]})


class CategoryLookup:
    """An amount-aware `(norm_key[, amount]) -> category` map (dict match, path 2).

    Two tiers: a per-merchant wildcard (`norm_key -> category`, matches any amount)
    and per-`(norm_key, canon_amount)` overrides for merchants where the amount
    decides the category. A query prefers an exact-amount override, then the
    wildcard. Persisted as a 3-column CSV `key,amount,category` (empty amount = the
    wildcard row). Built from history by `build_category_map`.
    """

    KEY, AMOUNT, VALUE = "key", "amount", "value"
    FIELDNAMES = [KEY, AMOUNT, VALUE]

    def __init__(self, wildcard: Optional[Dict[str, str]] = None,
                 by_amount: Optional[Dict[Tuple[str, str], str]] = None):
        self._wild: Dict[str, str] = dict(wildcard or {})
        self._by_amount: Dict[Tuple[str, str], str] = dict(by_amount or {})

    def get(self, description: str, amount: str) -> Optional[str]:
        """The learned category for this description (+ amount), or None.

        An exact-amount override wins; otherwise the merchant's wildcard; a
        merchant never seen returns None.
        """
        key = norm_key(description)
        a = canon_amount(amount)
        if a is not None:
            hit = self._by_amount.get((key, a))
            if hit is not None:
                return hit
        return self._wild.get(key)

    def __len__(self) -> int:
        return len(self._wild) + len(self._by_amount)

    @classmethod
    def load(cls, path: Path) -> "CategoryLookup":
        """Read a 3-column map CSV. A missing file is an empty map (first run)."""
        if not path.is_file():
            return cls()
        wild: Dict[str, str] = {}
        by_amount: Dict[Tuple[str, str], str] = {}
        with path.open(newline="") as fh:
            for row in csv.DictReader(fh):
                key = row.get(cls.KEY)
                if not key:
                    continue
                cat = row.get(cls.VALUE, "")
                amt = row.get(cls.AMOUNT, "")
                if amt:
                    by_amount[(key, amt)] = cat
                else:
                    wild[key] = cat
        return cls(wild, by_amount)

    def save(self, path: Path) -> None:
        """Write the 3-column map CSV, sorted for a stable diff."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=self.FIELDNAMES)
            w.writeheader()
            for key in sorted(self._wild):
                w.writerow({self.KEY: key, self.AMOUNT: "", self.VALUE: self._wild[key]})
            for key, amt in sorted(self._by_amount):
                w.writerow({self.KEY: key, self.AMOUNT: amt,
                            self.VALUE: self._by_amount[(key, amt)]})


# --- learning: project the maps from history (plan.md §6.2/§6.3) ---
#
# History is fed oldest -> newest, so a plain dict assignment (last wins) yields
# "most-recent categorization wins" — editing a few transactions and rerunning
# picks up the recent edit. Only the learnable `category` seeds the maps;
# `category_override` (the one-off layer) is intentionally never learned.


def build_category_map(history: List[Transaction]) -> CategoryLookup:
    """Amount-aware category map from history (see module note for ordering)."""
    wild: Dict[str, str] = {}
    amt: Dict[Tuple[str, str], str] = {}
    for txn in history:
        if not txn.category:                 # skip uncategorized / override-only rows
            continue
        key = norm_key(txn.original_description)
        wild[key] = txn.category             # last wins -> most recent for the merchant
        a = canon_amount(txn.amount)
        if a is not None:
            amt[(key, a)] = txn.category      # most recent at this exact amount
    # keep only the amount rows that actually override the wildcard
    by_amount = {ka: c for ka, c in amt.items() if c != wild.get(ka[0])}
    return CategoryLookup(wild, by_amount)


def build_description_map(history: List[Transaction]) -> Lookup:
    """`norm_key(original) -> corrected_description` from history (last wins)."""
    lk = Lookup()
    for txn in history:
        if txn.corrected_description:
            lk.put(txn.original_description, txn.corrected_description)
    return lk


def build_lookup(history: List[Transaction]) -> Tuple[Lookup, CategoryLookup]:
    """Both learned maps from history: (description_map, category_map)."""
    return build_description_map(history), build_category_map(history)
