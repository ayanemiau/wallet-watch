"""Path setup for the rule_editor test suite.

The tier-3a rule engine now lives in lib/rules.py (the pipeline's canonical
copy), so `from rules import ...` must resolve there. Put both lib/ (for rules
+ schema) and the tool dir (for preview) on sys.path before any test module is
imported. This mirrors editor.py's runtime path setup.
"""

import sys
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent
LIB = TOOL.parent.parent / "lib"

for path in (str(LIB), str(TOOL)):
    if path not in sys.path:
        sys.path.insert(0, path)
