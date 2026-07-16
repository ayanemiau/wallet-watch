# Wallet Watch — Repository Rules

## 🔒 Data protection (non-negotiable)

**This repository stores infra code ONLY. Real financial data must NEVER enter it.**

"Real data" means anything that reflects the operator's actual financial activity or identity, including but not limited to:

- Bank / card / Apple / Amazon / e-commerce exports (CSV, PDF, JSON, XLSX, screenshots)
- Transaction records, order histories, invoices, receipts, statements
- Account numbers, card numbers, balances, real merchant names tied to the operator's spending
- Splitwise / Venmo / Zelle transfer histories, memos, contact names
- Any `batch/<id>/` workspace (raw exports, `normalized.csv`, `categorized.csv`) and the committed `normalized/`, `categorized/`, `lookup/`, `index/`, `cache/` stores
- A real `rules/keywords.yaml` (it reveals the operator's actual merchants)
- Any real `WALLET_WATCH_DATA_DIR` contents

### Rules for every session in this directory

1. **Never create, copy, move, write, or commit real data into this repo tree.** All data lives in the external data root (`--data-dir` / `WALLET_WATCH_DATA_DIR`), which is outside this repository.
1a. **Never leak real account numbers or identifiers.** Do not write a real bank/card/account number anywhere in this repo — not in code, comments, docs, examples, `src/schema.py`, fixtures, or filenames. Account labels must be placeholders: `chaseXXXX`, `chaseYYYY`, `applecard`, `0000` stubs, etc. This applies to any digits that identify a real account (full or partial/last-4). If you see a real account number in a file or diff, treat it as a leak per rule 5.
2. **Fixtures and examples must be synthetic.** Any sample data committed under `tests/fixtures/` must be obviously fake (fake merchants, round fake amounts, `0000` account stubs). Never derive a fixture from a real export.
3. **Do not weaken the guards.** Never remove or narrow the data entries in `.gitignore`, and never `git add -f` a path they block.
4. **When you need real data to test, use the external data root** — read from `$WALLET_WATCH_DATA_DIR`, write outputs back there, and keep it untracked.
5. **If you spot real data staged, committed, or sitting inside the repo tree, STOP and flag it** to the user before doing anything else. Do not push.
6. **Before any commit**, verify the diff contains no real financial data. If unsure whether something is real, treat it as real and keep it out.

The pipeline resolves its data root from `--data-dir`, then `WALLET_WATCH_DATA_DIR`, then fails fast. There is intentionally no in-repo `data/` default.
