"""Front-to-back driver for one batch: normalize -> edit rules -> categorize.

Runs the three phase commands in order against a single batch, pinning each to
the same normalized CSV so the run the rule editor previews is exactly the run
categorize reads:

    python3 scripts/process_batch.py [--batch-dir <batch>] [--skip-editor] \
            --data-dir <root>

With no --batch-dir it drives the latest-start batch under <root>/batch/. The
individual scripts stay the supported way to run one phase on its own; this is
the "do the usual thing" wrapper over them. Phases 4-5 (resolve_batch.py,
commit_batch.py) are deliberately not included — they have their own human gate.
"""

import sys
from pathlib import Path

# the phase scripts live beside this one; import their resolvers rather than
# growing a sixth copy (see plan.md Decisions)
sys.path.insert(0, str(Path(__file__).resolve().parent))

import argparse  # noqa: E402
import subprocess  # noqa: E402
from typing import Optional  # noqa: E402

from categorize import latest_normalized  # noqa: E402
from normalize_batch import latest_batch_dir, resolve_data_dir  # noqa: E402

SCRIPTS = Path(__file__).resolve().parent
REPO = SCRIPTS.parent
NORMALIZE = SCRIPTS / "normalize_batch.py"
CATEGORIZE = SCRIPTS / "categorize.py"
EDITOR = REPO / "tools" / "rule_editor" / "editor.py"

# what to do after the rule editor exits non-zero
RELAUNCH, CONTINUE, ABORT = "relaunch", "continue", "abort"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--batch-dir", type=Path, default=None,
                   help="batch workspace named <startYYYYMMDD>-<endYYYYMMDD>, e.g. "
                        "$WALLET_WATCH_DATA_DIR/batch/20260101-20260630; defaults to the "
                        "latest-start batch under <data-root>/batch/")
    p.add_argument("--data-dir", type=Path, default=None,
                   help="data root holding accounts.csv and rules/keywords.yaml; "
                        "defaults to $WALLET_WATCH_DATA_DIR")
    p.add_argument("--skip-editor", action="store_true",
                   help="run normalize -> categorize without opening the rule editor "
                        "(headless rerun once the rules are settled)")
    return p.parse_args()


def run_step(label: str, cmd: list) -> int:
    # Children inherit stdout/stderr rather than being captured: their progress
    # lines are the operator's feedback, and the editor is interactive.
    print(f"\n=== {label} ===", file=sys.stderr)
    return subprocess.run([str(c) for c in cmd]).returncode


def newest_normalized(batch_dir: Path) -> Optional[Path]:
    # latest_normalized() is a hard error when the batch has no output yet; here
    # "none yet" is a normal pre-normalize state, so soften it to None.
    try:
        return latest_normalized(batch_dir)
    except SystemExit:
        return None


def prompt_after_editor_failure(code: int, is_tty: bool) -> str:
    # The editor exiting non-zero is ambiguous — a force-quit leaves the saved
    # rules perfectly usable, a crash mid-edit may not — so ask rather than
    # guess. Pure (code, is_tty) -> action so the branches stay testable.
    if not is_tty:
        return ABORT
    print(f"\nrule editor exited with code {code}.", file=sys.stderr)
    print("  [r] relaunch the editor   [c] categorize anyway   [q] quit", file=sys.stderr)
    print("  (if it failed to start: pip3 install -r tools/rule_editor/requirements.txt)",
          file=sys.stderr)
    while True:
        try:
            answer = input("choice [r/c/q]: ").strip().lower()
        except EOFError:
            return ABORT
        if answer in ("r", "relaunch"):
            return RELAUNCH
        if answer in ("c", "continue", "categorize"):
            return CONTINUE
        if answer in ("q", "quit", ""):
            return ABORT


def run_editor(data_dir: Path, preview_csv: Path) -> str:
    # Blocking: the editor's render loop returns when the operator closes the
    # window. --preview-csv is the whole point of driving it from here — its own
    # default is the newest normalized across ALL batches, which is usually but
    # not always the run we just produced.
    while True:
        code = run_step("rule editor (close the window to continue)",
                        [sys.executable, EDITOR,
                         "--data-dir", data_dir, "--preview-csv", preview_csv])
        if code == 0:
            return CONTINUE
        action = prompt_after_editor_failure(code, sys.stdin.isatty())
        if action != RELAUNCH:
            return action


def main() -> None:
    args = parse_args()
    data_dir = resolve_data_dir(args.data_dir)

    if args.batch_dir is not None:
        batch_dir = args.batch_dir
    else:
        batch_dir = latest_batch_dir(data_dir)
    print(f"processing batch: {batch_dir.name}", file=sys.stderr)

    before = newest_normalized(batch_dir)

    code = run_step("normalize", [sys.executable, NORMALIZE,
                                  "--data-dir", data_dir, "--batch-dir", batch_dir])
    if code != 0:
        raise SystemExit(code)

    normalized = newest_normalized(batch_dir)
    if normalized is None or normalized == before:
        # normalize reported success but left no new output — don't hand the
        # editor and categorize a stale run from an earlier session.
        raise SystemExit(f"normalize wrote no new output in {batch_dir}")

    if args.skip_editor:
        print("\nskipping the rule editor (--skip-editor); rules read from disk as-is",
              file=sys.stderr)
    elif run_editor(data_dir, normalized) == ABORT:
        raise SystemExit(
            f"stopped after the rule editor; normalized output is kept at {normalized}\n"
            f"resume with: python3 scripts/categorize.py --data-dir {data_dir} "
            f"--batch-dir {batch_dir} --input {normalized}")

    rules_path = data_dir / "rules" / "keywords.yaml"
    if not rules_path.is_file():
        raise SystemExit(f"no rule table at {rules_path} — the editor writes it on Save; "
                         f"normalized output is kept at {normalized}")

    code = run_step("categorize", [sys.executable, CATEGORIZE,
                                   "--data-dir", data_dir, "--batch-dir", batch_dir,
                                   "--input", normalized])
    if code != 0:
        raise SystemExit(code)

    categorized = sorted(batch_dir.glob("categorized_*.csv"))
    print(f"\nbatch {batch_dir.name}: {normalized.name} -> "
          f"{categorized[-1].name if categorized else '(no output)'}", file=sys.stderr)


if __name__ == "__main__":
    main()
