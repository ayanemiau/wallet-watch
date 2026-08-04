# Wallet Watch

少女祈祷中

Now Loading...

---

### The magical one-liner

One-time installing dependencies.

```bash
pip3 install -r tools/rule_editor/requirements.txt
```

Run the magic end-to-end: normalized raw csv transactions --> launch rule editor --> categorize.

```bash
python3 scripts/process_batch.py --data-dir ../wallet-watch-data
```

### Individual phases

1 - Normalization: process raw csv transactions downloaded from banks and combine into unified transaction list.

```bash
# --batch-dir is optional. if not specified, the script will run on the batch with latest start date.
python3 scripts/normalize_batch.py --data-dir ../wallet-watch-data
```

2 - Launch the interactive category rule editor UI.

```bash
pip3 install -r tools/rule_editor/requirements.txt
python3 tools/rule_editor/editor.py --data-dir ../wallet-watch-data
```

3 - Categorization: apply categories based on hard rules.

```bash
scripts/categorize.py --data-dir ../wallet-watch-data
```

4 - Resolution: resolve unmatched transactions base on previous categories.

```bash
python3 scripts/resolve_batch.py --data-dir ../wallet-watch-data
```

5 - Launch the interactive resolution review UI.

```bash
pip3 install -r tools/review_approver/requirements.txt
python3 tools/review_approver/approver.py --data-dir ../wallet-watch-data
```

