"""Build the static site data from the institution folders.

Writes payload measurements, a hand-labelled stratified sample, and a
browser bundle of real cases with this engine's decisions.
"""

from __future__ import annotations

import collections
import json
import statistics
import time
from pathlib import Path

from triage.cli import dumps_line
from triage.engine import triage_bundle
from triage.loader import load_institution
from triage.score import score_cases

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "docs" / "data"
BOILER = {"due_diligence_summary", "proof_of_address", "contact_note"}

# Analyst labels made by reading the documents against POLICY.md.
# The predicates select the case; the verdict is the label, not the engine output.
PATTERNS = [
    ("bank", lambda r, t: r == {"TM03"} and "cash_collection_reports" in t, "CLOSE", "BUSINESS_RECORDS_RECONCILE", "Parking cash deposits match the collection reports."),
    ("bank", lambda r, t: r == {"TM01", "TM03"} and "staff_note" in t, "ESCALATE", "ESC_STRUCTURING", "Staff note records questions about staying under the cash threshold."),
    ("bank", lambda r, t: r == {"TM22"} and "receipt" in t and "significant_control_register" not in t, "CLOSE", "LIST_MATCH_DIFFERENT_PARTY", "Address-only list match; register shows a different company and officer."),
    ("bank", lambda r, t: r == {"TM22"} and not t, "RFI", "RFI_COUNTERPARTY_DETAILS", "Name-only list match and no document settles identity."),
    ("bank", lambda r, t: r == {"TM22"} and "significant_control_register" in t, "ESCALATE", "ESC_LISTED_PARTY", "Listed officer still controlled the company on the payment date."),
    ("bank", lambda r, t: r == {"TM06"} and "hospital_invoice" in t, "CLOSE", "INVOICE_MATCHES", "Hospital invoice matches the higher-risk payment."),
    ("bank", lambda r, t: r == {"TM01"} and "solicitor_letter" in t, "CLOSE", "DOCUMENTED_EVENT", "Solicitor letter documents an inheritance matching the inflow."),
    ("bank", lambda r, t: r == {"TM01", "TM05"} and "vehicle_sale_receipt" in t, "CLOSE", "DOCUMENTED_EVENT", "Vehicle sale receipt matches the cash."),
    ("bank", lambda r, t: r == {"TM02", "TM04"} and "booking_confirmation" in t, "CLOSE", "COLLECTION_DOCUMENTED", "Booking names the people who paid."),
    ("bank", lambda r, t: "sending_bank_fraud_report" in t, "ESCALATE", "ESC_MULE_ACTIVITY", "Sending bank reports an authorised push-payment scam."),
    ("bank", lambda r, t: r == {"TM02", "TM04"} and not t, "ESCALATE", "ESC_MULE_ACTIVITY", "Many payers and rapid payout with no collection record."),
    ("bank", lambda r, t: r == {"TM01", "TM06"} and t == {"support_chat"}, "ESCALATE", "ESC_HIGH_RISK_CORRIDOR", "Higher-risk payments with only an unsupported chat explanation."),
    ("bank", lambda r, t: r == {"TM01"} and not t, "RFI", "RFI_SOURCE_OF_FUNDS", "Profile breach with no source document and no suspicion flag."),
    ("bank", lambda r, t: r == {"TM01"} and t == {"bank_statement"}, "RFI", "RFI_UPDATED_PROFILE", "Own-account transfer at or above five times declared income."),
    ("bank", lambda r, t: r == {"TM06"} and t == {"venue_email"}, "CLOSE", "REFUND_OF_EARLIER_PAYMENT", "Venue email is a refund matching the higher-risk receipt."),
    ("emoney", lambda r, t: r == {"TM03"} and "takings_book" in t, "CLOSE", "BUSINESS_RECORDS_RECONCILE", "Takings book reconciles to the cash top-ups."),
    ("emoney", lambda r, t: r == {"TM01", "TM02"} and "device_export" in t and "sending_bank_fraud_report" not in t, "ESCALATE", "ESC_ACCOUNT_MISUSE", "One device logged into several customer accounts."),
    ("emoney", lambda r, t: "deposit_return_letter" in t and "holding_deposit_receipt" in t, "CLOSE", "REFUND_OF_EARLIER_PAYMENT", "Deposit return and new holding deposit are both documented."),
    ("payments_fx", lambda r, t: r == {"TM03"} and "contract_schedule" in t, "CLOSE", "INVOICE_MATCHES", "Each sub-threshold payment matches a contract stage."),
    ("payments_fx", lambda r, t: r == {"TM03"} and "staff_note" in t, "ESCALATE", "ESC_STRUCTURING", "Customer asked about splitting payments to avoid the invoice threshold."),
    ("payments_fx", lambda r, t: r == {"TM06"} and "supply_contract" in t, "CLOSE", "INVOICE_MATCHES", "Supply contract and invoice match the higher-risk payment."),
    ("payments_fx", lambda r, t: r == {"TM08"} and "group_structure" in t and "email" not in t, "CLOSE", "GROUP_COMPANY", "Group chart shows the funder owns the client."),
    ("payments_fx", lambda r, t: r == {"TM08"} and "factoring_agreement" in t and "TM02" not in r, "CLOSE", "THIRD_PARTY_DOCUMENTED", "Factoring agreement and remittance document the funder."),
    ("payments_fx", lambda r, t: r == {"TM06"} and "staff_note" in t and "supplier_invoices" in t, "ESCALATE", "ESC_INVOICE_MISMATCH", "Invoices do not support the explanation; staff note says the customer would not explain the corridor."),
    ("investment", lambda r, t: r == {"TM10"} and "redundancy_letter" in t, "CLOSE", "DOCUMENTED_EVENT", "Redundancy letter explains the early sale."),
    ("investment", lambda r, t: r == {"TM10"} and not t, "ESCALATE", "ESC_EARLY_REDEMPTION", "Units sold within 90 days with no documented reason."),
    ("investment", lambda r, t: "completion_statement" in t, "CLOSE", "DOCUMENTED_EVENT", "Completion statement matches the sale proceeds paid in."),
    ("investment", lambda r, t: "cancellation_confirmation" in t and "TM10" not in r, "CLOSE", "REFUND_OF_EARLIER_PAYMENT", "ISA cancellation returns the subscription."),
]


def _types(bundle, case):
    return {
        doc["type"]
        for doc in bundle["docs_by_customer"].get(case["customer_id"], [])
        if doc["type"] not in BOILER
    }


def _rules(bundle, case):
    return {alert["rule_id"] for alert in bundle["alerts_by_case"].get(case["case_id"], [])}


def hand_labels(bundles, decisions):
    rows = []
    for inst, pred, verdict, code, note in PATTERNS:
        bundle = bundles[inst]
        found = None
        for case in bundle["cases"]:
            if pred(_rules(bundle, case), _types(bundle, case)):
                found = case
                break
        if not found:
            raise SystemExit(f"no case for {inst} {code}")
        got = decisions[inst][found["case_id"]]
        rows.append({
            "institution": inst,
            "case_id": found["case_id"],
            "verdict": verdict,
            "reason_code": code,
            "note": note,
            "engine_verdict": got["verdict"],
            "engine_reason_code": got["reason_code"],
            "engine_agrees": got["verdict"] == verdict and got["reason_code"] == code,
        })
    return rows


def payload_row(bundle, decisions):
    chars = []
    reasons = []
    for case, dec in zip(bundle["cases"], decisions):
        cid = case["customer_id"]
        parts = [json.dumps(bundle["customers"].get(cid), default=str)]
        for aid in case.get("alert_ids") or []:
            parts.append(json.dumps(bundle["alerts_by_id"].get(aid), default=str))
        start = case["review_window"]["from"]
        end = case["review_window"]["to"]
        txs = [
            tx for tx in bundle["txs_by_customer"].get(cid, [])
            if start <= (tx.get("booked_at") or "")[:10] <= end
        ]
        parts.append(json.dumps(txs, default=str))
        for doc in bundle["docs_by_customer"].get(cid, []):
            parts.append(doc.get("body") or "")
        chars.append(sum(len(part) for part in parts))
        reasons.append(len(dec["reason"]))
    chars.sort()
    def pct(p):
        return chars[min(len(chars) - 1, int(round((len(chars) - 1) * p)))]
    return {
        "cases": len(chars),
        "chars_mean": round(sum(chars) / len(chars)),
        "chars_p50": pct(0.5),
        "chars_p90": pct(0.9),
        "reason_chars_mean": round(sum(reasons) / len(reasons)),
        "input_tokens_mean_est": round((sum(chars) / len(chars)) / 4),
        "input_tokens_p90_est": round(pct(0.9) / 4),
        "output_tokens_rules_est": round((sum(reasons) / len(reasons)) / 4),
    }


def bundle_case(bundle, case, decision):
    cid = case["customer_id"]
    start = case["review_window"]["from"]
    end = case["review_window"]["to"]
    txs = [
        tx for tx in bundle["txs_by_customer"].get(cid, [])
        if start <= (tx.get("booked_at") or "")[:10] <= end
    ][:80]
    docs = []
    for doc in bundle["docs_by_customer"].get(cid, []):
        docs.append({
            "doc_id": doc.get("doc_id"),
            "type": doc.get("type"),
            "title": doc.get("title"),
            "dated": doc.get("dated"),
            "body": doc.get("body") or "",
        })
    alerts = [bundle["alerts_by_id"][aid] for aid in case.get("alert_ids") or [] if aid in bundle["alerts_by_id"]]
    return {
        "case": case,
        "customer": bundle["customers"].get(cid),
        "alerts": alerts,
        "transactions": txs,
        "documents": docs,
        "decision": decision,
    }


def main():
    DATA.mkdir(parents=True, exist_ok=True)
    bundles = {}
    decisions = {}
    timings = {}
    for inst in ("bank", "emoney", "investment", "payments_fx"):
        started = time.perf_counter()
        bundles[inst] = load_institution(ROOT / "institutions" / inst)
        load_s = time.perf_counter() - started
        started = time.perf_counter()
        rows = triage_bundle(bundles[inst])
        triage_s = time.perf_counter() - started
        decisions[inst] = {row["case_id"]: row for row in rows}
        timings[inst] = {"load_seconds": round(load_s, 3), "triage_seconds": round(triage_s, 3), "cases": len(rows)}
    labels = hand_labels(bundles, decisions)
    scored = score_cases(labels, [decisions[row["institution"]][row["case_id"]] for row in labels])
    # Stratified even/odd split by label order (already one per pattern).
    even = [row for i, row in enumerate(labels) if i % 2 == 0]
    odd = [row for i, row in enumerate(labels) if i % 2 == 1]
    def part(rows):
        return score_cases(rows, [decisions[row["institution"]][row["case_id"]] for row in rows])
    even_s, odd_s = part(even), part(odd)
    held = 0
    held_closed = 0
    for case in bundles["payments_fx"]["cases"]:
        if case.get("held_payment_id"):
            held += 1
            if decisions["payments_fx"][case["case_id"]]["verdict"] == "CLOSE":
                held_closed += 1
    stats = {
        "method": "Characters in the customer, alerts, review-window transactions and evidence markdown, divided by 4. This is an estimate of tokens, not a tokenizer count.",
        "institutions": {inst: payload_row(bundles[inst], [decisions[inst][c["case_id"]] for c in bundles[inst]["cases"]]) | timings[inst] for inst in bundles},
        "held_payments": held,
        "held_payments_closed": held_closed,
    }
    all_chars = []
    for inst in bundles:
        all_chars.append(stats["institutions"][inst]["input_tokens_mean_est"])
    stats["input_tokens_mean_est"] = round(statistics.mean(all_chars))
    stats["output_tokens_rules_est"] = round(statistics.mean(row["output_tokens_rules_est"] for row in stats["institutions"].values()))
    stats["triage_seconds_per_case"] = round(sum(timings[i]["triage_seconds"] for i in timings) / 4000, 6)
    (DATA / "payload_stats.json").write_text(json.dumps(stats, indent=2), encoding="utf-8")
    (DATA / "hand_labels.json").write_text(json.dumps({
        "warning": "These are hand labels on one stratified case per pattern, made by reading the documents against POLICY.md. They are not the hackathon answer key. Dev cases have no official labels. Do not treat the score below as the official unseen score.",
        "labels": labels,
        "score": {k: scored[k] for k in ("cases", "cost", "escalate_all_cost", "verdict_points", "wrongful_closes")},
        "verdict_accuracy": round(sum(1 for row in labels if decisions[row["institution"]][row["case_id"]]["verdict"] == row["verdict"]) / len(labels), 4),
        "reason_accuracy": round(sum(1 for row in labels if decisions[row["institution"]][row["case_id"]]["reason_code"] == row["reason_code"]) / len(labels), 4),
        "split": {
            "method": "Even and odd positions in this stratified list. The engine was not fitted to these labels.",
            "even_points": even_s["verdict_points"],
            "odd_points": odd_s["verdict_points"],
            "gap": round(abs(even_s["verdict_points"] - odd_s["verdict_points"]), 2),
        },
    }, indent=2), encoding="utf-8")
    # One case from each scenario for the browser, capped.
    sample = {"institutions": {}, "note": "Real development cases. Decisions are from the rules engine in triage/engine.py."}
    for inst, bundle in bundles.items():
        picked = []
        seen = set()
        for case in bundle["cases"]:
            key = (tuple(sorted(_rules(bundle, case))), tuple(sorted(_types(bundle, case))))
            if key in seen:
                continue
            seen.add(key)
            picked.append(bundle_case(bundle, case, decisions[inst][case["case_id"]]))
            if len(picked) >= 36:
                break
        sample["institutions"][inst] = picked
    (DATA / "sample.json").write_text(json.dumps(sample), encoding="utf-8")
    print("labels", len(labels), "agree", sum(1 for row in labels if row["engine_agrees"]))
    print("score", scored["verdict_points"], "wrongful", scored["wrongful_closes"])
    print("sample cases", sum(len(v) for v in sample["institutions"].values()))
    print("tokens mean", stats["input_tokens_mean_est"], "per case sec", stats["triage_seconds_per_case"])


if __name__ == "__main__":
    main()
