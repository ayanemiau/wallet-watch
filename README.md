# Wallet Watch

少女祈祷中

Now Loading...

### Usage

Process a batch end to end: normalize → rule editor → categorize. Runs against the
batch with the latest start date, and pins every step to the same normalized CSV.
Close the editor window to continue on to categorize.

```bash
pip3 install -r tools/rule_editor/requirements.txt
python3 scripts/process_batch.py --data-dir ../wallet-watch-data

# same chain without the editor, for a rerun once the rules are settled
python3 scripts/process_batch.py --data-dir ../wallet-watch-data --skip-editor
```

The individual phase commands below stay the way to run one step on its own.

Launch the interactive category rule editor.

```bash
pip3 install -r tools/rule_editor/requirements.txt
python3 tools/rule_editor/editor.py --data-dir ../wallet-watch-data
```

Manually run phase 2 - normalization

```bash
# --batch-dir is optional. if not specified, the script will run on the batch with latest start date.
python3 scripts/normalize_batch.py --data-dir ../wallet-watch-data
```

Manually run phase 3 - categorize based on filter rules
```bash
scripts/categorize.py --data-dir ../wallet-watch-data
```

Manually run phase 4 - resolve unmatched categories
```bash
python3 scripts/resolve_batch.py --data-dir ../wallet-watch-data
```

Launch the review UI for phase 4
```bash
pip3 install -r tools/review_approver/requirements.txt
python3 tools/review_approver/approver.py --data-dir ../wallet-watch-data
```

