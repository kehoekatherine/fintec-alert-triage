"""Load one institution folder. Reads only that folder."""

from __future__ import annotations

import json
from pathlib import Path

from .parse import parse_policy


def _jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            rows.append(json.loads(text))
    return rows


def load_institution(folder: str | Path) -> dict:
    root = Path(folder).resolve()
    if not (root / "cases.jsonl").exists():
        raise SystemExit(f"No cases.jsonl in {root}")
    policy_text = (root / "POLICY.md").read_text(encoding="utf-8") if (root / "POLICY.md").exists() else ""
    name = root.name.lower().replace("-", "_")
    aliases = {
        "payments": "payments_fx",
        "fx": "payments_fx",
        "e_money": "emoney",
        "emoney": "emoney",
        "bank": "bank",
        "investment": "investment",
        "payments_fx": "payments_fx",
    }
    institution = aliases.get(name, name)
    # Prefer the institution field on the first case when the folder name is unexpected.
    cases = _jsonl(root / "cases.jsonl")
    if cases and cases[0].get("institution"):
        institution = aliases.get(str(cases[0]["institution"]).lower(), str(cases[0]["institution"]).lower())
    evidence_rows = _jsonl(root / "evidence.jsonl")
    for row in evidence_rows:
        file_name = row.get("file") or ""
        body = ""
        if file_name:
            doc_path = root / file_name
            if doc_path.is_file():
                body = doc_path.read_text(encoding="utf-8", errors="replace")
        row["body"] = body
    customers = {row["customer_id"]: row for row in _jsonl(root / "customers.jsonl")}
    alerts_by_case: dict[str, list] = {}
    alerts_by_id = {}
    for alert in _jsonl(root / "alerts.jsonl"):
        alerts_by_case.setdefault(alert.get("case_id"), []).append(alert)
        alerts_by_id[alert["alert_id"]] = alert
    txs_by_customer: dict[str, list] = {}
    txs_by_id = {}
    for tx in _jsonl(root / "transactions.jsonl"):
        txs_by_customer.setdefault(tx.get("customer_id"), []).append(tx)
        txs_by_id[tx["transaction_id"]] = tx
    for rows in txs_by_customer.values():
        rows.sort(key=lambda item: (item.get("booked_at") or "", item.get("transaction_id") or ""))
    docs_by_customer: dict[str, list] = {}
    for doc in evidence_rows:
        docs_by_customer.setdefault(doc.get("customer_id"), []).append(doc)
    reviews_by_customer: dict[str, list] = {}
    for review in _jsonl(root / "previous_reviews.jsonl"):
        reviews_by_customer.setdefault(review.get("customer_id"), []).append(review)
    list_by_id = {row["entry_id"]: row for row in _jsonl(root / "internal_list.jsonl")}
    return {
        "root": str(root),
        "institution": institution,
        "policy_text": policy_text,
        "settings": parse_policy(policy_text, institution),
        "cases": cases,
        "customers": customers,
        "alerts_by_case": alerts_by_case,
        "alerts_by_id": alerts_by_id,
        "txs_by_customer": txs_by_customer,
        "txs_by_id": txs_by_id,
        "docs_by_customer": docs_by_customer,
        "reviews_by_customer": reviews_by_customer,
        "list_by_id": list_by_id,
    }


def known_ids(bundle: dict, customer_id: str) -> set[str]:
    ids = set(bundle["list_by_id"])
    for doc in bundle["docs_by_customer"].get(customer_id, []):
        if doc.get("doc_id"):
            ids.add(doc["doc_id"])
    for tx in bundle["txs_by_customer"].get(customer_id, []):
        if tx.get("transaction_id"):
            ids.add(tx["transaction_id"])
    return ids
