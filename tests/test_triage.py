"""Tests for the rules engine. Hand labels are analyst judgments, not an answer key."""

from __future__ import annotations

import json
from pathlib import Path

from triage.engine import triage_bundle
from triage.loader import load_institution
from triage.score import COST, score_cases

ROOT = Path(__file__).resolve().parents[1]


def test_cost_matrix_matches_the_brief():
    assert COST["CLOSE"]["CLOSE"] == 0
    assert COST["CLOSE"]["RFI"] == 3
    assert COST["CLOSE"]["ESCALATE"] == 3
    assert COST["RFI"]["CLOSE"] == 10
    assert COST["RFI"]["RFI"] == 0
    assert COST["RFI"]["ESCALATE"] == 2
    assert COST["ESCALATE"]["CLOSE"] == 10
    assert COST["ESCALATE"]["RFI"] == 3
    assert COST["ESCALATE"]["ESCALATE"] == 0


def test_missing_case_counts_as_escalation():
    result = score_cases(
        [{"case_id": "X", "verdict": "CLOSE", "reason_code": "WITHIN_PROFILE"}],
        [],
    )
    assert result["details"][0]["predicted"] == "ESCALATE"
    assert result["details"][0]["cost"] == 3


def test_reason_code_belongs_to_verdict_and_evidence_exists():
    codes = {
        "CLOSE": {
            "COLLECTION_DOCUMENTED", "BUSINESS_RECORDS_RECONCILE", "DOCUMENTED_EVENT",
            "INVOICE_MATCHES", "OWN_ACCOUNT", "GROUP_COMPANY", "THIRD_PARTY_DOCUMENTED",
            "REFUND_OF_EARLIER_PAYMENT", "RETURNING_CUSTOMER", "LIST_MATCH_DIFFERENT_PARTY",
            "WITHIN_PROFILE",
        },
        "RFI": {
            "RFI_PURPOSE_DOCUMENTS", "RFI_BUSINESS_RECORDS", "RFI_SOURCE_OF_FUNDS",
            "RFI_COUNTERPARTY_DETAILS", "RFI_UPDATED_PROFILE",
        },
        "ESCALATE": {
            "ESC_MULE_ACTIVITY", "ESC_STRUCTURING", "ESC_UNEXPLAINED_FUNDS", "ESC_PASS_THROUGH",
            "ESC_INVOICE_MISMATCH", "ESC_HIGH_RISK_CORRIDOR", "ESC_ACCOUNT_MISUSE",
            "ESC_THIRD_PARTY_FUNDING", "ESC_EARLY_REDEMPTION", "ESC_LISTED_PARTY",
        },
    }
    for institution in ("bank", "emoney", "investment", "payments_fx"):
        bundle = load_institution(ROOT / "institutions" / institution)
        decisions = triage_bundle(bundle)
        assert len(decisions) == len(bundle["cases"])
        known_customers = {}
        for case in bundle["cases"]:
            ids = {tx["transaction_id"] for tx in bundle["txs_by_customer"].get(case["customer_id"], [])}
            ids |= {doc["doc_id"] for doc in bundle["docs_by_customer"].get(case["customer_id"], [])}
            ids |= set(bundle["list_by_id"])
            known_customers[case["case_id"]] = ids
        for decision in decisions:
            assert decision["verdict"] in codes
            assert decision["reason_code"] in codes[decision["verdict"]]
            assert decision["alert_ids"]
            assert 80 <= len(decision["reason"]) <= 1200
            for evidence_id in decision["evidence"]:
                assert evidence_id in known_customers[decision["case_id"]]


def test_five_runs_are_identical_on_bank():
    bundle = load_institution(ROOT / "institutions" / "bank")
    first = triage_bundle(bundle)
    for _ in range(4):
        assert triage_bundle(bundle) == first


def test_hand_labels_agree_with_the_engine():
    payload = json.loads((ROOT / "docs" / "data" / "hand_labels.json").read_text())
    assert payload["labels"]
    assert all(row["engine_agrees"] for row in payload["labels"])
    assert payload["score"]["wrongful_closes"] == 0


def test_engine_source_has_no_development_case_ids():
    text = "\n".join(path.read_text() for path in (ROOT / "triage").rglob("*.py"))
    assert "BK-CS" not in text
    assert "PX-CS" not in text
    assert "EM-CS" not in text
    assert "IV-CS" not in text
    assert "Fernbrook" not in text


def test_output_files_cover_every_case():
    for institution in ("bank", "emoney", "investment", "payments_fx"):
        cases = (ROOT / "institutions" / institution / "cases.jsonl").read_text().splitlines()
        output = (ROOT / "output" / f"{institution}.jsonl").read_text().splitlines()
        assert len(cases) == len(output) == 1000
        case_ids = [json.loads(line)["case_id"] for line in cases if line.strip()]
        out_ids = [json.loads(line)["case_id"] for line in output if line.strip()]
        assert case_ids == out_ids
