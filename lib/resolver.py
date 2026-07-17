"""Phase 4 resolve — categorize the transactions Phase 3 left unmatched.

`Resolver.resolve` takes the *unmatched* rows from a categorized batch (empty
`category`) and tries, per row, two deterministic paths before giving up to a
human (plan.md §6.1–6.2):

  path 1 (categorize_method=1): the description_map holds a clean rewrite for
      this merchant. Apply it to `corrected_description`, then re-run the Phase 3
      rule engine — if a rule now hits, the row is categorized. Preferred: one
      description fix lets a keyword rule generalize to every sibling merchant.

  path 2 (categorize_method=2): the category_map holds a category for this
      merchant outright. Assign it. Used when there is no merchant name to
      recover (a raw memo, a one-off).

description_map wins if both hit; a path-1 fix that still matches no rule keeps
the corrected description and falls through to path 2, then to manual.

Nothing here is trusted blindly: EVERY input row comes back as an `InboxRow`
(approved defaults to False), including map hits, so the operator approves all of
them (plan.md §6.3). `resolved_by` records which path produced the suggestion so
the reviewer can triage. The LLM agent (path between the maps and manual) is
deferred — `agent=` is the seam for it.

Re-match detail: Phase 3 rules are authored against `original_description` (the
column a normalized row carries), so to re-match a *corrected* description this
substitutes the corrected text into `original_description` for the match only —
the stored Transaction keeps its true `original_description` and records the fix
in `corrected_description`.

The orchestrator that reads the categorized CSV and writes the inbox lives in
`scripts/resolve_batch.py`; this library knows nothing about the data root.
"""

from dataclasses import replace
from typing import Callable, List, Optional

from resolve_lookup import Lookup
from resolve_review import (BY_AGENT, BY_CAT_MAP, BY_DESC_MAP, BY_NONE, InboxRow)
from rules import Rule, categorize_row
from schema import Transaction, to_row

# An agent proposes a (corrected_description, category) for a still-unmatched row.
# Either element may be None. Deferred (plan.md §6.2 / M7) — the type is the seam.
Agent = Callable[[Transaction], "AgentProposal"]


def unmatched(transactions: List[Transaction]) -> List[Transaction]:
    """The rows Phase 3 could not place — Phase 4's input."""
    return [t for t in transactions if not t.category]


class Resolver:
    """Resolves unmatched transactions via the persisted maps (+ agent, later)."""

    def __init__(self, rules: List[Rule], description_map: Lookup,
                 category_map: Lookup, agent: Optional[Agent] = None):
        self._rules = rules
        self._description_map = description_map
        self._category_map = category_map
        self._agent = agent

    def resolve(self, transactions: List[Transaction]) -> List[InboxRow]:
        """One `InboxRow` (approved=False) per input row, in order."""
        return [self._resolve_one(txn) for txn in transactions]

    def _resolve_one(self, txn: Transaction) -> InboxRow:
        # path 1 — description map -> corrected description -> re-run rules
        corrected = self._description_map.get(txn.original_description)
        if corrected is not None:
            category = self._rematch(txn, corrected)
            if category is not None:
                return InboxRow(
                    replace(txn, corrected_description=corrected,
                            category=category, categorize_method=1),
                    resolved_by=BY_DESC_MAP)
            # keep the fix, but no rule caught it yet — fall through
            txn = replace(txn, corrected_description=corrected)

        # path 2 — category map -> direct category
        category = self._category_map.get(txn.original_description)
        if category is not None:
            return InboxRow(
                replace(txn, category=category, categorize_method=2),
                resolved_by=BY_CAT_MAP)

        # agent (deferred): propose, never adopt — the operator confirms in review
        if self._agent is not None:
            proposed = self._apply_agent(txn)
            if proposed is not None:
                return proposed

        # nothing matched: the operator fills in category / corrected_description
        return InboxRow(txn, resolved_by=BY_NONE)

    def _rematch(self, txn: Transaction, corrected: str) -> Optional[str]:
        # rules target original_description; feed the corrected text there so an
        # existing keyword rule catches it, without mutating the stored row.
        row = to_row(replace(txn, original_description=corrected))
        return categorize_row(self._rules, row)

    def _apply_agent(self, txn: Transaction) -> Optional[InboxRow]:
        proposal = self._agent(txn)
        if proposal is None:
            return None
        # path-1-style proposal: a corrected description, re-matched by rules
        if proposal.corrected_description:
            category = self._rematch(txn, proposal.corrected_description)
            if category is not None:
                return InboxRow(
                    replace(txn, corrected_description=proposal.corrected_description,
                            category=category, categorize_method=1),
                    resolved_by=BY_AGENT)
        # path-2-style proposal: a category outright
        if proposal.category:
            return InboxRow(
                replace(txn, category=proposal.category, categorize_method=2),
                resolved_by=BY_AGENT)
        return None


class AgentProposal:
    """What an agent suggests for one row (deferred). Either field may be empty."""

    def __init__(self, corrected_description: str = "", category: str = ""):
        self.corrected_description = corrected_description
        self.category = category
