"""process_batch driver tests. Fixtures are synthetic.

The rule editor step is a GUI, so the chain is covered with --skip-editor and
the post-editor decision is covered by unit-testing prompt_after_editor_failure.
"""

import csv
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"
DATA = REPO / "tests" / "fixtures" / "data"
SCRIPT = SCRIPTS / "process_batch.py"

sys.path.insert(0, str(SCRIPTS))

from process_batch import (  # noqa: E402
    ABORT,
    CONTINUE,
    RELAUNCH,
    prompt_after_editor_failure,
)

# a rule against a fixture merchant; every name here is made up
RULES_YAML = """version: 1
rules:
  - category: Coffee
    match: all
    conditions:
      - column: original_description
        op: contains
        value: FAKE COFFEE CO
"""


def make_data_root(tmp_path: Path, batches=("20260101-20260131",), rules=RULES_YAML) -> Path:
    """A temp data root: accounts.csv, copies of fixture batches, and a rule table."""
    data = tmp_path / "data"
    (data / "batch").mkdir(parents=True)
    (data / "accounts.csv").write_bytes((DATA / "accounts.csv").read_bytes())
    for batch in batches:
        raw = data / "batch" / batch / "raw"
        raw.mkdir(parents=True)
        for f in (DATA / "batch" / batch / "raw").glob("*.csv"):
            (raw / f.name).write_bytes(f.read_bytes())
    if rules is not None:
        # outside the repo tree, or rules.py's in-repo guard rejects the path
        (data / "rules").mkdir()
        (data / "rules" / "keywords.yaml").write_text(rules)
    return data


def run_driver(data: Path, *extra, expect_ok=True) -> subprocess.CompletedProcess:
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--data-dir", str(data), "--skip-editor", *extra],
        capture_output=True, text=True,
    )
    if expect_ok:
        assert r.returncode == 0, r.stderr
    return r


def read_rows(path: Path):
    with path.open(newline="") as fh:
        return list(csv.DictReader(fh))


def only(batch_dir: Path, pattern: str) -> Path:
    found = sorted(batch_dir.glob(pattern))
    assert len(found) == 1, f"expected one {pattern}, got {[f.name for f in found]}"
    return found[0]


def test_chain_normalizes_then_categorizes(tmp_path):
    data = make_data_root(tmp_path)
    batch = data / "batch" / "20260101-20260131"

    r = run_driver(data)

    normalized = only(batch, "normalized_*.csv")
    categorized = only(batch, "categorized_*.csv")
    assert len(read_rows(normalized)) == len(read_rows(categorized))   # same run, all rows

    coffee = [row for row in read_rows(categorized) if row["category"] == "Coffee"]
    assert len(coffee) == 1                                            # the rule landed
    assert "FAKE COFFEE CO" in coffee[0]["original_description"]
    # categorize read the run normalize just wrote, not some earlier one
    assert f"read {normalized.name} ->" in r.stderr


def test_defaults_to_latest_batch(tmp_path):
    data = make_data_root(tmp_path, batches=("20260101-20260131", "20260201-20260228"))

    r = run_driver(data)

    assert "processing batch: 20260201-20260228" in r.stderr
    assert list((data / "batch" / "20260201-20260228").glob("categorized_*.csv"))
    assert not list((data / "batch" / "20260101-20260131").glob("normalized_*.csv"))


def test_batch_dir_overrides_the_default(tmp_path):
    data = make_data_root(tmp_path, batches=("20260101-20260131", "20260201-20260228"))
    older = data / "batch" / "20260101-20260131"

    r = run_driver(data, "--batch-dir", str(older))

    assert "processing batch: 20260101-20260131" in r.stderr
    assert only(older, "categorized_*.csv")


def test_missing_rules_keeps_the_normalized_output(tmp_path):
    # the rule table is what the editor writes on Save; without one categorize
    # can't run, but normalize's work must not be thrown away.
    data = make_data_root(tmp_path, rules=None)
    batch = data / "batch" / "20260101-20260131"

    r = run_driver(data, expect_ok=False)

    assert r.returncode != 0
    assert "no rule table at" in r.stderr
    assert only(batch, "normalized_*.csv")                             # kept
    assert not list(batch.glob("categorized_*.csv"))


def test_normalize_failure_stops_before_categorize(tmp_path):
    # a batch dir that isn't named for a date range is a hard error in
    # normalize_batch; the driver must not go on to categorize.
    data = make_data_root(tmp_path)
    junk = data / "batch" / "junk"
    (junk / "raw").mkdir(parents=True)
    (junk / "raw" / "chaseXXXX.csv").write_text("a\n")

    r = run_driver(data, "--batch-dir", str(junk), expect_ok=False)

    assert r.returncode != 0
    assert "must be named <startYYYYMMDD>-<endYYYYMMDD>" in r.stderr
    assert not list(junk.glob("categorized_*.csv"))


def test_no_data_root_fails_fast(tmp_path, monkeypatch):
    monkeypatch.delenv("WALLET_WATCH_DATA_DIR", raising=False)
    r = subprocess.run([sys.executable, str(SCRIPT), "--skip-editor"],
                       capture_output=True, text=True)
    assert r.returncode != 0
    assert "no data root" in r.stderr           # fails fast, no repo-tree default


@pytest.mark.parametrize("answer, expected", [
    ("r", RELAUNCH),
    ("relaunch", RELAUNCH),
    ("c", CONTINUE),
    ("q", ABORT),
    ("", ABORT),                                # bare enter is the safe choice
])
def test_prompt_after_editor_failure(monkeypatch, answer, expected):
    monkeypatch.setattr("builtins.input", lambda *a: answer)
    assert prompt_after_editor_failure(1, is_tty=True) == expected


def test_prompt_reasks_until_valid(monkeypatch):
    answers = iter(["what", "?", "c"])
    monkeypatch.setattr("builtins.input", lambda *a: next(answers))
    assert prompt_after_editor_failure(1, is_tty=True) == CONTINUE


def test_prompt_without_a_tty_aborts_without_asking(monkeypatch):
    def boom(*a):
        raise AssertionError("must not prompt when stdin isn't a tty")
    monkeypatch.setattr("builtins.input", boom)
    assert prompt_after_editor_failure(1, is_tty=False) == ABORT
