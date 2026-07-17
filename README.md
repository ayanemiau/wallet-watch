# Wallet Watch

少女祈祷中

Now Loading...

Usage:

Launch the interactive category rule editor.
```bash
python3 tools/rule_editor/editor.py --data-dir ../wallet-watch-data
```

Manually run phase 2 - normalization
```bash
# --batch-dir is optional. if not specified, the script will run on the batch with latest start date.
python3 scripts/normalize_batch.py \
  --batch-dir ../wallet-watch-data/batch/20250101-20260630 \
  --data-dir ../wallet-watch-data
```

Manually run phase 3 - resolve unmatched categories
```bash
python3 scripts/resolve_batch.py --data-dir ../wallet-watch-data
```

Launch the review UI
```bash
pip3 install -r tools/review_approver/requirements.txt
python3 tools/review_approver/approver.py --data-dir ../wallet-watch-data
```