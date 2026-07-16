"""Phase 2 handler registry, keyed by account type.

A handler converts one raw row from a source export into a Transaction:

    (raw_row: dict, account: Account) -> Transaction

Multiple account ids may share a type (e.g. two chase credit cards), so the
registry is keyed by Account.type, never by Account.id — the repo therefore
never needs to know a real account id.
"""

from typing import Callable, Dict

from schema import Account, Transaction

Handler = Callable[[Dict[str, str], Account], Transaction]

HANDLERS: Dict[str, Handler] = {}


def handler(type_: str):
    """Register a handler for an account type."""

    def register(fn: Handler) -> Handler:
        if type_ in HANDLERS:
            raise ValueError(f"handler already registered for type {type_!r}")
        HANDLERS[type_] = fn
        return fn

    return register


def get_handler(type_: str) -> Handler:
    # a type with no handler is a hard error, so nothing is silently skipped
    if type_ not in HANDLERS:
        known = ", ".join(sorted(HANDLERS)) or "(none)"
        raise KeyError(f"no handler registered for account type {type_!r}; known: {known}")
    return HANDLERS[type_]


def has_handler(type_: str) -> bool:
    """Whether a handler is registered for this type — lets a caller skip
    files it can't process yet instead of provoking a hard error."""
    return type_ in HANDLERS


# import for side effect: each module registers its types on import
from . import capital, chase, discover, wealthfront  # noqa: E402,F401
