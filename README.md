# Wallet Watch

少女祈祷中

Now Loading...

Usage:

```bash
# launch the interactive category rule editor.
python3 tools/rule_editor/editor.py --data-dir ../wallet-watch-data

# manually run phase 2 - normalization
# --batch-dir is optional. if not specified, the script will run on the batch with latest start date.
python3 scripts/normalize_batch.py \
  --batch-dir ../wallet-watch-data/batch/20250101-20260630 \
  --data-dir ../wallet-watch-data
```