"""Path setup for the review_approver test suite.

Put both lib/ (for resolve_review + schema) and the tool dir (for review_model)
on sys.path before any test module is imported. This mirrors approver.py's
runtime path setup, so tests import the no-UI modules without a lib install and
without ever importing dearpygui.
"""

import sys
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent
LIB = TOOL.parent.parent / "lib"

for path in (str(LIB), str(TOOL)):
    if path not in sys.path:
        sys.path.insert(0, path)
