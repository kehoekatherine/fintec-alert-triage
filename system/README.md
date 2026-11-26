# How the system decides

`system/run` is a rules engine. It does not call a model unless you pass `--llm` to `python -m triage` yourself. The scored entry point does not.

It reads `POLICY.md`, `cases.jsonl`, `alerts.jsonl`, `customers.jsonl`, `transactions.jsonl`, `evidence.jsonl`, the markdown in `evidence/`, `previous_reviews.jsonl` and `internal_list.jsonl` from the folder you pass. It writes one JSON line per case to the output path. It does not embed development-case ids, names or amounts.

## Order of decision

1. Build suspicion findings first: fraud reports and mule admissions, a device shared by several customers, structuring admissions, list matches where the listed party still controls the counterparty, invoices that do not support the payment, unexplained higher-risk corridors, third-party funding that is paid on, early redemptions with no documented reason, and pass-through between linked companies.
2. If any suspicion finding exists, the case is **ESCALATE**. The reason does not ask the customer a question.
3. Otherwise each alert is explained from the documents. A close requires a document that matches the amount and the party, or a register that shows a list match is a different person. A customer's chat, on its own, is not enough to close.
4. If every alert is explained, the case is **CLOSE**. If any alert is unexplained and nothing is suspicious, the case is **RFI**, and the reason says what to ask for.
5. At the payments firm, a held payment of £10,000 or more to anyone other than the client is not released without a matching invoice or contract.
6. Own-account transfers documented by a statement close below five times declared income or volume, and become an RFI for an updated profile at or above five times.

Reason codes are the ones in `POLICY.md`. Evidence ids are only ids that appear on the case's documents, transactions or the internal list.

## Time and cost for 200 cases

Measured on the 4,000 development cases in this environment: about 0.0003 seconds per case after the folder is loaded, so 200 cases are well under a second of decision time and a few seconds including reading the files. Token cost is zero. The 30-minute and four-dollar test limits are not approached.

An optional OpenRouter pass exists in `triage/llm.py` (`--llm`, temperature 0, cache under `system/.cache`). It is off by default and cannot lower the severity of the rules verdict.
