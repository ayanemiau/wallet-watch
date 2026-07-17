"""Phase 4 resolve — the persisted norm_key maps (the reusable library).

A `Lookup` is a two-column CSV (`key,value`) whose key is `norm_key(description)`.
Phase 4 keeps two of them in the data root's `lookup/` dir:

  - description_map.csv  norm_key(original) -> corrected_description  (path 1)
  - category_map.csv     norm_key(original) -> category               (path 2)

`norm_key` collapses the volatile parts of a bank description — order ids, store
numbers, dates, punctuation, casing — so every appearance of one merchant shares
a single key. That is what lets a decision made once ("SQ *BLUE BOTTLE #4471" ->
"Blue Bottle Coffee") auto-apply to "SQ *BLUE BOTTLE #0093" next run.

The maps are rebuilt/extended from *approved* history, so the manual work of
resolving a merchant is done once. This module is pure I/O + lookup; the
resolving logic lives in `lib/resolver.py`. See plan.md §6.2.
"""

import csv
import re
from pathlib import Path
from typing import Dict, Iterator, Optional, Tuple

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
