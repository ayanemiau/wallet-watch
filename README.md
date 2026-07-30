# Wallet Watch

少女祈祷中

Now Loading...

### Usage

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

