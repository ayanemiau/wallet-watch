"""Phase 3 categorize — the reusable library (tier 3a).

`Categorizer.apply_rules` takes normalized `Transaction` rows and a path to the
tier-3a rule table (`rules/keywords.yaml`), and returns the same rows with only
their `category` filled from the first matching rule. Every other column is left
untouched.

Behavior consistency with the interactive editor is structural, not aspirational:
this applies the *same* engine the editor's preview uses — `lib/rules.py` — over
the *same* row shape (`schema.to_row`, whose keys are the CSV column contract).
So a rule that categorizes a transaction one way in the editor categorizes it the
same way here.

Scope: the Phase 3 hard filter (the human-maintained rule table) only. A row no
rule matches keeps its existing (empty) category and is left for Phase 4
("Process Unmatched Transactions", plan.md §6) to resolve and route through
review — this library never flags or guesses.

The orchestrator that reads/writes batch files lives in `scripts/categorize.py`;
this library knows nothing about the data root or batch layout.
"""

from dataclasses import replace
from pathlib import Path
from typing import List

from rules import categorize_row, load_rules
from schema import Transaction, to_row

# The categorizer carries a version (plan.md §5): output-schema changes must stay
# backward compatible so committed batches can be re-run with a newer version.
# Bumped only when the categorization output changes in a way history must track.
CATEGORIZER_VERSION = 1


class Categorizer:
    """Applies the tier-3a rule table to transactions."""

    def apply_rules(self, transactions: List[Transaction],
                    rule_config: str) -> List[Transaction]:
        """Return copies of `transactions` with `category` filled by tier 3a.

        `rule_config` is the path to a `keywords.yaml` rule table. Rules are
        ordered and the first match wins (see lib/rules.py). A row no rule
        matches keeps its existing category (empty on a normalized row) — it is
        not an error. Inputs are not mutated; new `Transaction` objects are
        returned in the same order.
        """
        rules = load_rules(Path(rule_config))
        result: List[Transaction] = []
        for txn in transactions:
            # to_row yields the exact Dict[str, str] shape the engine (and the
            # editor's preview) matches against — same columns, same encoding.
            category = categorize_row(rules, to_row(txn))
            # a hit is the hard filter: stamp categorize_method=0 (plan.md §6.4).
            # a miss leaves the row untouched (empty category) — Phase 4's input.
            result.append(txn if category is None
                          else replace(txn, category=category, categorize_method=0))
        return result
