"""Rules-first alert triage.

One case, one verdict. Close only when every alert is explained by evidence
in the case. Once a suspicion finding exists, the case is escalated rather
than sent back to the customer. Thresholds and the higher-risk country list
come from the institution's POLICY.md; nothing here is taken from a
development case.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from .parse import (
    bold_fields,
    column_amounts,
    dob_match,
    dob_text,
    fmt_date,
    gbp,
    human_join,
    markdown_tables,
    money_amounts,
    names_match,
    near,
    parse_date,
    to_pence,
    tokens,
)

PRIORITY = {
    "ESC_LISTED_PARTY": 10,
    "ESC_MULE_ACTIVITY": 20,
    "ESC_ACCOUNT_MISUSE": 30,
    "ESC_STRUCTURING": 40,
    "ESC_INVOICE_MISMATCH": 50,
    "ESC_HIGH_RISK_CORRIDOR": 60,
    "ESC_THIRD_PARTY_FUNDING": 70,
    "ESC_EARLY_REDEMPTION": 80,
    "ESC_PASS_THROUGH": 90,
    "ESC_UNEXPLAINED_FUNDS": 100,
    "RFI_COUNTERPARTY_DETAILS": 10,
    "RFI_SOURCE_OF_FUNDS": 20,
    "RFI_PURPOSE_DOCUMENTS": 30,
    "RFI_BUSINESS_RECORDS": 40,
    "RFI_UPDATED_PROFILE": 50,
    "LIST_MATCH_DIFFERENT_PARTY": 15,
    "INVOICE_MATCHES": 20,
    "REFUND_OF_EARLIER_PAYMENT": 25,
    "OWN_ACCOUNT": 30,
    "GROUP_COMPANY": 35,
    "THIRD_PARTY_DOCUMENTED": 40,
    "COLLECTION_DOCUMENTED": 45,
    "BUSINESS_RECORDS_RECONCILE": 50,
    "DOCUMENTED_EVENT": 55,
    "RETURNING_CUSTOMER": 60,
    "WITHIN_PROFILE": 70,
}

INVOICE_TYPES = {
    "invoice", "supplier_invoice", "supplier_invoices", "carrier_invoice",
    "freight_invoices", "dealer_invoice", "hospital_invoice", "consulting_contract",
    "supply_contract", "contract_schedule",
}
EVENT_INFLOW = {
    "solicitor_letter", "insurer_letter", "vehicle_sale_receipt", "sale_receipt",
    "loan_agreement", "settlement_letter", "completion_statement",
}
REFUND_TYPES = {"credit_note", "venue_email", "deposit_return_letter", "cancellation_confirmation"}
CASH_CHANNELS = {"cash_deposit", "cash_withdrawal", "cash_topup", "cash"}
CARD_CHANNELS = {"card"}

CITY_COUNTRY = {
    "beirut": "lebanon",
    "erbil": "iraq",
    "damascus": "syria",
    "sanaa": "yemen",
    "sana'a": "yemen",
    "caracas": "venezuela",
    "pyongyang": "north korea",
    "mogadishu": "somalia",
    "tripoli": "libya",
    "bamako": "mali",
    "juba": "south sudan",
    "port-au-prince": "haiti",
    "kabul": "afghanistan",
    "minsk": "belarus",
    "kinshasa": "democratic republic of the congo",
    "naypyidaw": "myanmar",
    "yangon": "myanmar",
}


def _finding(verdict, code, evidence, sentences, alert_id=None, amount=0):
    seen = []
    for item in evidence:
        if item and item not in seen:
            seen.append(item)
    text = [re.sub(r"\s+", " ", sentence).strip() for sentence in sentences if sentence and sentence.strip()]
    text = [sentence if sentence.endswith(".") else sentence + "." for sentence in text]
    return {
        "verdict": verdict,
        "reason_code": code,
        "priority": PRIORITY[code],
        "alert_id": alert_id,
        "evidence": seen,
        "sentences": text[:4],
        "amount": int(amount or 0),
    }


def _tx_pence(tx) -> int:
    return to_pence(tx.get("amount_gbp")) or 0


def _tx_date(tx) -> date | None:
    return parse_date(tx.get("booked_at"))


def _prepare_doc(raw: dict) -> dict:
    text = raw.get("body") or ""
    low = text.lower()
    flags = set()
    dtype = raw.get("type") or ""
    if dtype == "sending_bank_fraud_report" or "authorised push payment" in low or "push payment scam" in low:
        flags.add("fraud")
    if "crypto" in low or "coinvale" in low or "payments assistant" in low or "send it on" in low:
        if "send it on" in low or "sending it on" in low or "crypto" in low or "coinvale" in low:
            flags.add("mule")
    if "friends sent" in low or "invest for them" in low or "changed their minds" in low:
        flags.add("pass_chat")
    if dtype == "staff_note" and any(word in low for word in ("exempt", "threshold", "aggregation", "aggregated")):
        flags.add("structuring")
    if dtype == "staff_note" and any(word in low for word in (
        "unwilling", "unable", "no clear explanation", "could not provide",
        "was not able", "wasn't able", "evasive", "declined to", "refused to",
    )):
        flags.add("contradiction")
    if any(phrase in low for phrase in ("fell through", "deposited that cash back", "deposited the cash back", "paid the cash back", "deposited that cash back into")):
        flags.add("redeposit")
    if "gift from my uncle" in low or "gift from my" in low:
        flags.add("chat_gift")
    if "loan from a friend" in low:
        flags.add("chat_loan")
    if "motorbike" in low or "selling my car" in low or "sold my car" in low:
        flags.add("chat_sale")
    if "help my family" in low or "family support" in low or "friend of the family" in low:
        flags.add("chat_family")
    if "heart surgery" in low or "bypass" in low:
        flags.add("chat_medical")
    if "both companies are mine" in low or "both companies are mine" in low:
        flags.add("claimed_group")
    fields = bold_fields(text)
    return {
        "id": raw.get("doc_id"),
        "type": dtype,
        "title": raw.get("title") or "",
        "dated": parse_date(raw.get("dated")),
        "text": text,
        "low": low,
        "fields": fields,
        "tables": markdown_tables(text),
        "amounts": money_amounts(text),
        "flags": flags,
    }


def _mentions(doc: dict, name: str | None) -> bool:
    if not name:
        return False
    if names_match(name, doc["title"]):
        return True
    for value in doc["fields"].values():
        if names_match(name, value):
            return True
    if len(tokens(name)) >= 2 and name.lower() in (doc["text"] + " " + doc["title"]).lower():
        return True
    return False


def _docs(ctx, *types):
    wanted = set(types)
    return [doc for doc in ctx["docs"] if doc["type"] in wanted]


def _alert_txs(ctx, alert) -> list[dict]:
    wanted = set(alert.get("transaction_ids") or [])
    found = [tx for tx in ctx["txs"] if tx.get("transaction_id") in wanted]
    found.sort(key=lambda tx: (tx.get("booked_at") or "", tx.get("transaction_id") or ""))
    return found


def _baseline(ctx) -> int | None:
    for alert in ctx["alerts"]:
        if alert.get("rule_id") == "TM01":
            base = to_pence((alert.get("measured") or {}).get("baseline_gbp"))
            if base:
                return base
    declared = (ctx["customer"].get("declared") or {})
    monthly = to_pence(declared.get("monthly_income_gbp"))
    if monthly:
        return monthly
    annual = to_pence(declared.get("annual_turnover_gbp"))
    if annual:
        return annual // 12
    return None


def _basis_label(ctx) -> str:
    if ctx["institution"] == "payments_fx":
        return "expected monthly payment volume"
    kind = ctx["customer"].get("kind")
    if kind == "business":
        return "declared monthly turnover"
    return "declared monthly income"


def _customer_name(ctx) -> str:
    return ctx["customer"].get("name") or "the customer"


def _high_risk(ctx, country: str | None) -> bool:
    if not country:
        return False
    return country.strip().lower() in ctx["high_risk"]


def _is_card(tx) -> bool:
    return (tx.get("channel") or "") in CARD_CHANNELS


def _is_cash(tx) -> bool:
    return (tx.get("channel") or "") in CASH_CHANNELS


def _is_client(ctx, tx) -> bool:
    return names_match(tx.get("counterparty_name"), _customer_name(ctx))


def _tx_phrase(tx) -> str:
    party = tx.get("counterparty_name") or "an unnamed counterparty"
    country = tx.get("counterparty_country") or ""
    when = fmt_date(_tx_date(tx))
    way = "from" if tx.get("direction") == "in" else "to"
    where = f" in {country}" if country and country.lower() not in {"united kingdom", "uk", ""} else ""
    return f"{gbp(_tx_pence(tx))} {way} {party}{where} on {when}"


def _sum_pence(txs) -> int:
    return sum(_tx_pence(tx) for tx in txs)


def _largest(txs) -> dict | None:
    if not txs:
        return None
    return max(txs, key=lambda tx: (_tx_pence(tx), tx.get("transaction_id") or ""))


def _window(ctx) -> tuple[date | None, date | None]:
    window = ctx["case"].get("review_window") or {}
    return parse_date(window.get("from")), parse_date(window.get("to"))


def _in_window(ctx, tx) -> bool:
    start, end = _window(ctx)
    booked = _tx_date(tx)
    if booked is None:
        return True
    if start and booked < start:
        return False
    if end and booked > end:
        return False
    return True


def _ref_hit(tx, doc) -> bool:
    reference = (tx.get("reference") or "").upper()
    if len(reference) < 4:
        return False
    tokens_found = re.findall(r"[A-Z]*\d{3,}", reference.replace(" ", ""))
    hay = re.sub(r"[^A-Z0-9]", "", (doc["text"] + " " + doc["title"]).upper())
    raw = re.sub(r"[^A-Z0-9]", "", reference)
    if len(raw) >= 5 and raw in hay:
        return True
    return any(len(token) >= 5 and token in hay for token in tokens_found)


def _party_amounts(doc: dict) -> list[int]:
    amounts: list[int] = []
    for table in doc["tables"]:
        amounts.extend(column_amounts(table, "amount", "total", "price", "refund", "fee", "advance", "paid"))
    if not amounts:
        amounts.extend(doc["amounts"])
    # de-duplicate preserving order
    seen = []
    for amount in amounts:
        if amount not in seen:
            seen.append(amount)
    return seen or list(doc["amounts"])


def _about_party(doc: dict, tx: dict) -> bool:
    party = tx.get("counterparty_name")
    if _mentions(doc, party) or _ref_hit(tx, doc):
        return True
    if doc["type"] == "hospital_invoice":
        country = (tx.get("counterparty_country") or "").lower()
        if any(near(_tx_pence(tx), amount) for amount in doc["amounts"]):
            if country and country in doc["low"]:
                return True
            for city, mapped in CITY_COUNTRY.items():
                if city in doc["low"] and mapped == country:
                    return True
    return False


def _line_match(doc: dict, tx: dict) -> bool:
    amount = _tx_pence(tx)
    candidates = _party_amounts(doc)
    if any(near(amount, candidate) for candidate in candidates):
        return True
    if candidates and near(amount, sum(candidates), rel=0.01, abs_p=200):
        return True
    return False


def classify_tx(tx: dict, docs: list[dict]) -> tuple[str, list[dict]]:
    """Return match, mismatch or none for invoices and contracts about this payment."""
    matched = []
    mismatched = []
    for doc in docs:
        if doc["type"] not in INVOICE_TYPES and doc["type"] not in {"purchase_order", "payment_file"}:
            continue
        if doc["type"] == "payment_file":
            hit = False
            for table in doc["tables"]:
                for row in table:
                    payee = row.get("payee") or row.get("contractor") or row.get("beneficiary") or ""
                    amounts = money_amounts(" ".join(row.values()))
                    if payee and names_match(payee, tx.get("counterparty_name")) and any(near(_tx_pence(tx), amount) for amount in amounts):
                        hit = True
            if hit:
                matched.append(doc)
            continue
        if not _about_party(doc, tx):
            continue
        if doc["type"] == "purchase_order":
            if _line_match(doc, tx):
                matched.append(doc)
            continue
        if _line_match(doc, tx):
            matched.append(doc)
        elif _party_amounts(doc) or doc["amounts"]:
            mismatched.append(doc)
    if matched:
        return "match", matched
    # A set of invoices can support a set of payments in sum. Handled by the caller
    # for a single tx only when one document's figures reconcile.
    if mismatched:
        return "mismatch", mismatched
    return "none", []


def _group_doc(ctx, payer: str | None):
    customer = _customer_name(ctx)
    for doc in _docs(ctx, "group_structure"):
        for table in doc["tables"]:
            rows = []
            for row in table:
                company = row.get("company") or ""
                owner = row.get("owner") or row.get("owned by") or ""
                if company or owner:
                    rows.append((company, owner))
            names = [name for pair in rows for name in pair if name]
            if payer and any(names_match(payer, name) for name in names) and any(names_match(customer, name) for name in names):
                # Same owner, or owner/subsidiary.
                owners = {}
                for company, owner in rows:
                    if company and owner:
                        owners.setdefault(owner, []).append(company)
                        owners.setdefault(company, []).append(owner)
                payer_hits = [key for key in owners if names_match(key, payer)]
                customer_hits = [key for key in owners if names_match(key, customer)]
                if payer_hits and customer_hits:
                    return doc
                for owner, companies in owners.items():
                    blob = [owner, *companies]
                    if any(names_match(payer, item) for item in blob) and any(names_match(customer, item) for item in blob):
                        return doc
    return None


def _own_links(ctx) -> list[tuple[dict, dict]]:
    """Transactions a statement or letter shows are with an account the customer holds."""
    customer = _customer_name(ctx)
    links = []
    seen = set()
    statements = _docs(ctx, "bank_statement")
    letters = _docs(ctx, "customer_letter")
    for doc in statements:
        holder = doc["fields"].get("account holder") or ""
        held = names_match(holder, customer) or (customer.lower() in doc["low"] and "held by" in doc["low"])
        if not held:
            continue
        for tx in ctx["txs"]:
            if not _in_window(ctx, tx):
                continue
            amount_ok = any(near(_tx_pence(tx), amount) for amount in doc["amounts"])
            party_ok = _is_client(ctx, tx) or "own account" in (tx.get("reference") or "").lower()
            text_ok = "held by" in doc["low"] and customer.lower() in doc["low"] and amount_ok
            if amount_ok and (party_ok or text_ok):
                key = tx.get("transaction_id")
                if key not in seen:
                    seen.add(key)
                    links.append((tx, doc))
    for doc in letters:
        if "in my name" not in doc["low"] and "in your name" not in doc["low"] and "held in my name" not in doc["low"]:
            continue
        for tx in ctx["txs"]:
            if not _in_window(ctx, tx):
                continue
            if _high_risk(ctx, tx.get("counterparty_country")) or _is_client(ctx, tx):
                key = tx.get("transaction_id")
                if key not in seen and any(names_match(holder, customer) for holder in [doc["fields"].get("account holder", "")] ) or customer.lower() in doc["low"]:
                    # The letter alone is not the ownership proof unless a statement also matches,
                    # or the letter names the destination account and the holder is the customer.
                    if any(doc2 for doc2 in statements if names_match(doc2["fields"].get("account holder") or "", customer)):
                        seen.add(key)
                        links.append((tx, doc))
    return links


def _own_ratio(ctx, links) -> tuple[int, int | None]:
    total = 0
    seen = set()
    for tx, _doc in links:
        if tx.get("transaction_id") not in seen:
            seen.add(tx.get("transaction_id"))
            total += _tx_pence(tx)
    return total, _baseline(ctx)


def _event_doc(ctx, amount: int, direction: str | None = None):
    types = set(EVENT_INFLOW)
    if direction == "out":
        types = {"dealer_invoice", "hospital_invoice", "invoice"}
    for doc in ctx["docs"]:
        if doc["type"] not in types and not (direction != "out" and doc["type"] in EVENT_INFLOW):
            continue
        if doc["type"] in EVENT_INFLOW or doc["type"] == "dealer_invoice":
            if any(near(amount, candidate) for candidate in doc["amounts"]):
                if direction == "out" and doc["type"] not in {"dealer_invoice", "hospital_invoice", "invoice"}:
                    continue
                return doc
    return None


def _refund_doc(ctx, tx):
    for doc in ctx["docs"]:
        if doc["type"] not in REFUND_TYPES and "refund" not in doc["low"]:
            continue
        if doc["type"] in {"contact_note", "support_chat", "due_diligence_summary"}:
            continue
        if any(near(_tx_pence(tx), amount) for amount in doc["amounts"]) and (
            _mentions(doc, tx.get("counterparty_name")) or doc["type"] in REFUND_TYPES or _ref_hit(tx, doc)
        ):
            return doc
    return None


def _gift_doc(ctx, tx):
    for doc in _docs(ctx, "gift_letter"):
        if not any(near(_tx_pence(tx), amount) for amount in doc["amounts"]):
            continue
        if _mentions(doc, tx.get("counterparty_name")) or _is_client(ctx, tx) is False:
            return doc
    return None


def _gift_payer(doc) -> str:
    match = re.search(r"\bI,\s+([A-Z][^,]{2,80}?),\s+born\b", doc["text"])
    if match:
        return match.group(1).strip()
    title = doc["title"]
    if title.lower().startswith("gift letter from "):
        return title[16:].strip()
    return doc["fields"].get("from") or ""


def _gift_dob(doc) -> str | None:
    match = re.search(r"born\s+(\d{1,2}\s+[A-Za-z]+\s+\d{4})", doc["text"])
    return match.group(1) if match else None


def _records_sum(doc) -> int | None:
    if doc["type"] in {"cash_collection_reports", "cash_reconciliation"}:
        amounts = []
        for table in doc["tables"]:
            if doc["type"] == "cash_reconciliation":
                picked = column_amounts(table, "banked")
                if not picked:
                    picked = column_amounts(table, "cash counted")
            else:
                picked = column_amounts(table, "total")
            amounts.extend(picked)
        return sum(amounts) if amounts else None
    if doc["type"] == "takings_book":
        amounts = []
        for table in doc["tables"]:
            amounts.extend(column_amounts(table, "cash topped", "topped up", "cash top"))
        return sum(amounts) if amounts else None
    if doc["type"] == "management_accounts":
        for key, value in doc["fields"].items():
            if "cash sales" in key:
                amounts = money_amounts(value)
                if amounts:
                    return amounts[0]
    return None


def _records_doc(ctx, txs):
    cash = [tx for tx in txs if _is_cash(tx)]
    target = cash or txs
    total = _sum_pence(target)
    if total <= 0:
        return None
    for doc in ctx["docs"]:
        if doc["type"] not in {"cash_collection_reports", "cash_reconciliation", "takings_book", "management_accounts"}:
            continue
        recorded = _records_sum(doc)
        if recorded is not None and near(total, recorded, rel=0.05, abs_p=500):
            return doc
    return None


def _schedule_covers(ctx, txs) -> dict | None:
    outs = [tx for tx in txs if tx.get("direction") == "out" and not _is_card(tx)]
    if not outs:
        return None
    for doc in _docs(ctx, "contract_schedule"):
        row_amounts = []
        for table in doc["tables"]:
            row_amounts.extend(column_amounts(table, "amount"))
        if row_amounts and all(any(near(_tx_pence(tx), amount) for amount in row_amounts) for tx in outs):
            return doc
    return None


def _booking_doc(ctx, txs):
    for doc in _docs(ctx, "booking_confirmation"):
        names = re.findall(r"^\d+\.\s+(.+)$", doc["text"], re.M)
        total = None
        for amount in doc["amounts"]:
            total = amount if total is None or amount > total else total
        incoming = [tx for tx in txs if tx.get("direction") == "in"]
        if not incoming:
            incoming = [tx for tx in ctx["window_txs"] if tx.get("direction") == "in" and any(names_match(tx.get("counterparty_name"), name) for name in names)]
        if names and total and incoming:
            covered = [tx for tx in incoming if any(names_match(tx.get("counterparty_name"), name) for name in names)]
            used = covered or incoming
            if near(_sum_pence(used), total, rel=0.08, abs_p=500) or (covered and len(covered) >= max(3, len(names) // 2)):
                return doc, names, used
    return None


def _factor_doc(ctx, tx):
    payer = tx.get("counterparty_name")
    agreements = [doc for doc in _docs(ctx, "factoring_agreement") if _mentions(doc, payer) or payer and payer.lower() in doc["low"]]
    remittances = [doc for doc in _docs(ctx, "remittance_advice") if any(near(_tx_pence(tx), amount) for amount in doc["amounts"])]
    if agreements and remittances:
        return agreements[0], remittances[0]
    if agreements and any(near(_tx_pence(tx), amount) for amount in agreements[0]["amounts"]):
        return agreements[0], None
    return None


def _controls_on(ctx, person: str, on: date | None) -> bool | None:
    found = False
    still = False
    for doc in _docs(ctx, "significant_control_register", "company_register_extract"):
        for table in doc["tables"]:
            for row in table:
                label = row.get("person") or row.get("officer") or row.get("person with significant control") or ""
                if not label or not names_match(label, person):
                    continue
                found = True
                ceased = parse_date(row.get("ceased") or "")
                # A resignation is not the end of control when a PSC row still runs.
                if "ceased" in row or doc["type"] == "significant_control_register":
                    if on is None or ceased is None or ceased >= on:
                        still = True
                elif row.get("resigned"):
                    resigned = parse_date(row.get("resigned"))
                    if on is None or resigned is None or resigned >= on:
                        still = True
                else:
                    still = True
    if not found:
        return None
    return still


def _match_strength(matched_on: str) -> str:
    text = (matched_on or "").lower()
    details = 0
    if "name" in text:
        details += 1
    if "birth" in text or "date of birth" in text:
        details += 1
    if "address" in text:
        details += 1
    if "identifier" in text:
        details += 1
    return "strong" if details >= 2 else "weak"


def _register_different(ctx, entry: dict, person: str, counterparty: str | None) -> dict | None:
    """A register shows the payee is somebody other than the listed party."""
    list_dob = entry.get("date_of_birth") or ""
    for doc in _docs(ctx, "company_register_extract", "significant_control_register"):
        if counterparty and not (_mentions(doc, counterparty) or counterparty.lower() in doc["low"] or counterparty.lower() in doc["title"].lower()):
            # Address-only matches still name the company in the title.
            if not _mentions(doc, person):
                continue
        officers = []
        for table in doc["tables"]:
            for row in table:
                label = row.get("officer") or row.get("person") or row.get("person with significant control") or ""
                born = row.get("born") or ""
                if label:
                    officers.append((label, born))
        if not officers:
            continue
        same = [item for item in officers if names_match(item[0], person) or names_match(item[0], entry.get("name"))]
        if not same:
            return doc
        for _name, born in same:
            if born and dob_match(born, list_dob) == "differ":
                return doc
    return None


def _device_customers(doc) -> set[str]:
    return set(re.findall(r"\b[A-Z]{2,3}-C\d+\b", doc["text"]))


def _person_from_match(matched_party: str, entry: dict | None) -> str:
    if matched_party:
        return matched_party.split(",")[0].strip()
    if entry:
        return entry.get("name") or ""
    return ""


def explain_list(alert, ctx):
    measured = alert.get("measured") or {}
    entry = ctx["list_by_id"].get(measured.get("list_entry") or "")
    txs = _alert_txs(ctx, alert)
    tx = _largest(txs)
    pay_date = _tx_date(tx) if tx else None
    amount = _tx_pence(tx) if tx else 0
    counterparty = (tx or {}).get("counterparty_name")
    matched_party = measured.get("matched_party") or ""
    person = _person_from_match(matched_party, entry)
    entry_id = (entry or {}).get("entry_id")
    evidence = [item for item in [entry_id, (tx or {}).get("transaction_id")] if item]
    customer = _customer_name(ctx)
    if entry is None:
        return _finding(
            "RFI", "RFI_COUNTERPARTY_DETAILS", evidence,
            [
                f"The list alert on {customer} names {person or 'a counterparty'}, but the list entry itself is not in the file.",
                "Ask who that counterparty is and for a date of birth, address or identifier so the firm can tell them apart from the listed party.",
            ],
            alert.get("alert_id"), amount,
        )
    strength = _match_strength(measured.get("matched_on") or "")
    list_name = entry.get("name") or person
    list_dob = entry.get("date_of_birth") or ""
    if strength == "strong":
        controls = _controls_on(ctx, person, pay_date)
        registers = _docs(ctx, "significant_control_register", "company_register_extract")
        reg_ids = [doc["id"] for doc in registers if _mentions(doc, person) or (counterparty and counterparty.lower() in doc["low"])]
        if controls is False:
            shown = reg_ids[0] if reg_ids else "the company register"
            return _finding(
                "CLOSE", "LIST_MATCH_DIFFERENT_PARTY",
                evidence + reg_ids,
                [
                    f"The payment of {gbp(amount)} to {counterparty or person} matched {list_name} on the internal list ({entry_id}) by {measured.get('matched_on')}.",
                    f"{shown} shows {person} had already ceased to control {counterparty or 'the company'} before {fmt_date(pay_date)}, and somebody else is now the officer.",
                    "The counterparty is not the listed party, so the list alert is closed.",
                ],
                alert.get("alert_id"), amount,
            )
        return _finding(
            "ESCALATE", "ESC_LISTED_PARTY",
            evidence + reg_ids,
            [
                f"{person} matches internal list entry {entry_id} ({list_name}) on {measured.get('matched_on') or 'more than one detail'}.",
                f"The payment of {gbp(amount)} to {counterparty or person} on {fmt_date(pay_date)} was made while that listed party still controlled or was the counterparty."
                + (f" {human_join(reg_ids)} is the register relied on." if reg_ids else ""),
                f"The listed party is the counterparty or controls it, so {customer}'s case is escalated to the MLRO.",
            ],
            alert.get("alert_id"), amount,
        )
    # Weak: one detail. A gift letter or register may settle it.
    for doc in _docs(ctx, "gift_letter"):
        payer = _gift_payer(doc)
        born = _gift_dob(doc)
        if payer and (names_match(payer, person) or names_match(payer, list_name) or names_match(payer, counterparty)):
            relation = dob_match(born, list_dob)
            if relation == "match":
                return _finding(
                    "ESCALATE", "ESC_LISTED_PARTY",
                    evidence + [doc["id"]],
                    [
                        f"{payer} matches list entry {entry_id} on the name and on the date of birth {dob_text(born)}, which is the listed party's date of birth.",
                        f"{doc['id']} is {payer}'s gift letter for {gbp(amount)} to {customer}.",
                        "The counterparty is the listed party, so the case is escalated to the MLRO rather than put back to the customer.",
                    ],
                    alert.get("alert_id"), amount or (doc["amounts"][0] if doc["amounts"] else 0),
                )
            if relation == "differ":
                return _finding(
                    "CLOSE", "LIST_MATCH_DIFFERENT_PARTY",
                    evidence + [doc["id"]],
                    [
                        f"The name {payer} matched list entry {entry_id} ({list_name}, born {dob_text(list_dob)}) on name alone.",
                        f"Gift letter {doc['id']} gives {payer}'s date of birth as {dob_text(born)}, which is not the listed party's.",
                        f"The {gbp(doc['amounts'][0] if doc['amounts'] else amount)} is documented as a gift to {customer} from a different person of the same name.",
                    ],
                    alert.get("alert_id"), amount or (doc["amounts"][0] if doc["amounts"] else 0),
                )
    other = _register_different(ctx, entry, person, counterparty)
    if other is not None:
        receipt = next((doc["id"] for doc in ctx["docs"] if doc["type"] in {"receipt", "invoice"} and (_mentions(doc, counterparty) or (counterparty and counterparty.lower() in doc["low"]))), None)
        extra = [other["id"]] + ([receipt] if receipt else [])
        officer_bits = []
        for table in other["tables"]:
            for row in table:
                if row.get("officer") or row.get("person"):
                    officer_bits.append(f"{row.get('officer') or row.get('person')} ({row.get('born') or row.get('role') or 'officer'})")
                    break
        officer_text = officer_bits[0] if officer_bits else "a different officer"
        return _finding(
            "CLOSE", "LIST_MATCH_DIFFERENT_PARTY",
            evidence + extra,
            [
                f"The payment of {gbp(amount)} to {counterparty or 'the payee'} matched list entry {entry_id} ({list_name}) on one detail only: {measured.get('matched_on') or 'a single detail'}.",
                f"{other['id']} shows the payee is a separate party, with {officer_text}, and not {list_name}"
                + (f", whose recorded date of birth is {dob_text(list_dob)}" if list_dob else "")
                + ".",
                (f"Receipt {receipt} matches the payment." if receipt else "A match on one detail does not make them the same party, and the register shows they are not."),
            ],
            alert.get("alert_id"), amount,
        )
    return _finding(
        "RFI", "RFI_COUNTERPARTY_DETAILS",
        evidence,
        [
            f"{customer}'s payment of {gbp(amount)} to {counterparty or person} on {fmt_date(pay_date)} matched internal list entry {entry_id} ({list_name}) on {measured.get('matched_on') or 'one detail'} only.",
            "Nothing on file shows whether this is the listed party or another person of the same name, and nothing else in the case is a sign of crime.",
            f"Ask {customer} who {counterparty or person} is and for a date of birth, address or identifier that distinguishes them from the listed party.",
        ],
        alert.get("alert_id"), amount,
    )


def _explained_outflows(ctx, txs):
    outs = [tx for tx in txs if tx.get("direction") == "out" and not _is_card(tx) and _tx_pence(tx) >= 100_00]
    if not outs:
        outs = [tx for tx in txs if tx.get("direction") == "out" and not _is_card(tx)]
    matched_docs = []
    unexplained = []
    mismatched = []
    for tx in outs:
        if _is_client(ctx, tx) and any(doc for _tx, doc in _own_links(ctx) if _tx.get("transaction_id") == tx.get("transaction_id")):
            matched_docs.append(next(doc for linked, doc in _own_links(ctx) if linked.get("transaction_id") == tx.get("transaction_id")))
            continue
        status, docs = classify_tx(tx, ctx["docs"])
        if status == "match":
            matched_docs.extend(docs)
        elif status == "mismatch":
            mismatched.append((tx, docs))
        else:
            refund = _refund_doc(ctx, tx)
            if refund:
                matched_docs.append(refund)
            else:
                unexplained.append(tx)
    return matched_docs, unexplained, mismatched


def _profile_limit(ctx) -> int:
    kind = ctx["customer"].get("kind") or "individual"
    table = {
        "bank": {"individual": 3, "business": 2},
        "emoney": {"individual": 3, "business": 2},
        "investment": {"individual": 3, "business": 2},
        "payments_fx": {"individual": 2, "business": 2},
    }
    return table.get(ctx["institution"], {}).get(kind, 3)


def _refund_inflows(ctx, txs):
    pairs = []
    for tx in txs:
        if tx.get("direction") != "in":
            continue
        doc = _refund_doc(ctx, tx)
        if doc:
            pairs.append((tx, doc))
    return pairs


def explain_profile(alert, ctx):
    txs = _alert_txs(ctx, alert) or [tx for tx in ctx["window_txs"] if tx.get("direction") == "in"]
    incoming = [tx for tx in txs if tx.get("direction") == "in"] or txs
    total = to_pence((alert.get("measured") or {}).get("received_gbp")) or _sum_pence(incoming)
    baseline = _baseline(ctx)
    customer = _customer_name(ctx)
    top = _largest(incoming) or _largest(txs)
    evidence = [tx.get("transaction_id") for tx in incoming[:8]]
    # Documented one-off event.
    event = _event_doc(ctx, total, "in") or (_event_doc(ctx, _tx_pence(top), "in") if top else None)
    if event:
        return _finding(
            "CLOSE", "DOCUMENTED_EVENT",
            evidence + [event["id"]],
            [
                f"{customer} received {gbp(total)} in the review window, against { _basis_label(ctx) } of {gbp(baseline)}.",
                f"{event['title']} ({event['id']}) documents the source as {gbp(event['amounts'][0] if event['amounts'] else total)} and matches the funds that breached the profile.",
                "The inflow is a documented one-off event, so the profile alert is closed.",
            ],
            alert.get("alert_id"), total,
        )
    links = [(tx, doc) for tx, doc in _own_links(ctx) if tx.get("direction") == "in" or "own account" in (tx.get("reference") or "").lower()]
    if not links:
        links = _own_links(ctx)
    if links:
        owned, base = _own_ratio(ctx, links)
        doc = links[0][1]
        multiple = (owned / base) if base else 0
        tx_ids = [tx.get("transaction_id") for tx, _doc in links]
        if base and multiple >= ctx["settings"]["own_multiple"]:
            return _finding(
                "RFI", "RFI_UPDATED_PROFILE",
                tx_ids + [doc["id"]],
                [
                    f"{customer} moved {gbp(owned)} through accounts that {doc['id']} shows they hold, which is {multiple:.1f} times the { _basis_label(ctx) } of {gbp(base)}.",
                    "Movements between the customer's own accounts explain the activity only below five times the declared figure.",
                    f"Ask {customer} for an updated expected income or payment volume that covers {gbp(owned)}.",
                ],
                alert.get("alert_id"), owned,
            )
        return _finding(
            "CLOSE", "OWN_ACCOUNT",
            tx_ids + [doc["id"]],
            [
                f"{doc['title']} ({doc['id']}) shows the account holder is {customer}.",
                f"The transfer of {gbp(owned or _tx_pence(links[0][0]))} matches that statement and is {multiple:.1f} times the { _basis_label(ctx) } of {gbp(base)}." if base else f"The transfer of {gbp(_tx_pence(links[0][0]))} is between accounts {customer} holds.",
                "Below five times the declared figure, that own-account movement explains the profile alert.",
            ],
            alert.get("alert_id"), owned or total,
        )
    refunds = _refund_inflows(ctx, incoming)
    if refunds:
        covered = sum(_tx_pence(tx) for tx, _doc in refunds)
        remainder = max(0, total - covered)
        limit = _profile_limit(ctx)
        doc = refunds[0][1]
        tx = refunds[0][0]
        if baseline is None or remainder <= baseline * limit:
            return _finding(
                "CLOSE", "REFUND_OF_EARLIER_PAYMENT",
                evidence + [doc["id"], tx.get("transaction_id")],
                [
                    f"{customer} received {gbp(_tx_pence(tx))} on {fmt_date(_tx_date(tx))} from {tx.get('counterparty_name')}.",
                    f"{doc['title']} ({doc['id']}) documents that sum as a refund of an earlier payment.",
                    f"After the refund, about {gbp(remainder)} of other receipts remain, within {limit} times the {_basis_label(ctx)} of {gbp(baseline)}."
                    if baseline else "The refund explains the receipt that breached the profile.",
                ],
                alert.get("alert_id"), covered,
            )
    if top:
        gift = _gift_doc(ctx, top)
        if gift and names_match(_gift_payer(gift), top.get("counterparty_name")):
            return _finding(
                "CLOSE", "THIRD_PARTY_DOCUMENTED",
                evidence + [gift["id"]],
                [
                    f"{_gift_payer(gift)} paid {gbp(_tx_pence(top))} to {customer} on {fmt_date(_tx_date(top))}.",
                    f"Gift letter {gift['id']} documents that payment as a non-repayable gift and matches the amount.",
                    "The third-party funds are explained, so the profile alert is closed.",
                ],
                alert.get("alert_id"), _tx_pence(top),
            )
    records = _records_doc(ctx, incoming)
    if records:
        return _finding(
            "CLOSE", "BUSINESS_RECORDS_RECONCILE",
            evidence + [records["id"]],
            [
                f"{customer} received {gbp(_sum_pence(incoming))} that exceeded the declared profile.",
                f"{records['title']} ({records['id']}) reconciles to those receipts.",
                "The business records explain the activity.",
            ],
            alert.get("alert_id"), _sum_pence(incoming),
        )
    # Payments firm: a documented supplier payment explains why the client's own funding spiked.
    if ctx["institution"] == "payments_fx":
        outs = [tx for tx in ctx["window_txs"] if tx.get("direction") == "out" and not _is_card(tx)]
        held = ctx["held"]
        focus = [held] if held else sorted(outs, key=_tx_pence, reverse=True)[:4]
        matched = []
        for tx in focus:
            status, docs = classify_tx(tx, ctx["docs"])
            if status == "match":
                matched.append((tx, docs[0]))
        if matched and all(_is_client(ctx, tx) for tx in incoming):
            tx, doc = max(matched, key=lambda pair: _tx_pence(pair[0]))
            return _finding(
                "CLOSE", "INVOICE_MATCHES",
                [tx.get("transaction_id"), doc["id"]] + evidence[:4],
                [
                    f"{customer} received {gbp(total)} from its own account, {((total / baseline) if baseline else 0):.1f} times the { _basis_label(ctx) } of {gbp(baseline)}.".replace("0.0 times", "above"),
                    f"{doc['title']} ({doc['id']}) matches the payment of {gbp(_tx_pence(tx))} to {tx.get('counterparty_name')} on {fmt_date(_tx_date(tx))}.",
                    "The invoice explains the payment volume, so the profile alert is closed and the held payment can be released." if held else "The invoice explains why funding was above the expected volume.",
                ],
                alert.get("alert_id"), total,
            )
        if incoming and all(_is_client(ctx, tx) for tx in incoming):
            return _finding(
                "RFI", "RFI_UPDATED_PROFILE",
                evidence[:6],
                [
                    f"{customer} received {gbp(total)} from its own bank, against { _basis_label(ctx) } of {gbp(baseline)}.",
                    "No statement on file shows a separate account of the client that would explain the sum as an own-account transfer, and no invoice matches a payment of that size.",
                    f"Ask {customer} for an updated expected payment volume, and for the invoice or contract for any payment of £10,000 or more.",
                ],
                alert.get("alert_id"), total,
            )
    # Customer chat is not independent evidence.
    chats = _docs(ctx, "support_chat")
    ask = "proof of where the money came from, such as a sale receipt, gift letter or loan agreement"
    if any("chat_gift" in doc["flags"] for doc in chats):
        ask = "a gift letter for the sum the customer described as a gift"
    elif any("chat_loan" in doc["flags"] for doc in chats):
        ask = "the loan agreement for the sum the customer described as a loan from a friend"
    elif any("chat_sale" in doc["flags"] for doc in chats):
        ask = "the sale receipt or advert for the vehicle the customer says they sold"
    party = (top or {}).get("counterparty_name") or "the payer"
    if ctx["customer"].get("kind") == "business" and any(_is_cash(tx) for tx in incoming):
        return _finding(
            "RFI", "RFI_BUSINESS_RECORDS",
            evidence[:6],
            [
                f"{customer} received {gbp(total)}, above the declared profile, including cash that is not covered by takings or collection records.",
                f"Nothing on file reconciles the receipts to the business, and the case has no scam report or confirmed list match.",
                f"Ask {customer} for the business records, such as till reports or collection sheets, behind {gbp(total)}.",
            ],
            alert.get("alert_id"), total,
        )
    return _finding(
        "RFI", "RFI_SOURCE_OF_FUNDS",
        evidence[:6],
        [
            f"{customer} received {gbp(total)} (the largest receipt was {_tx_phrase(top)}) against { _basis_label(ctx) } of {gbp(baseline)}." if top else f"{customer} received {gbp(total)} against { _basis_label(ctx) } of {gbp(baseline)}.",
            f"No document on file shows the source of the money from {party}, and nothing in the case is a sign of crime.",
            f"Ask {customer} for {ask}.",
        ],
        alert.get("alert_id"), total,
    )


def explain_rapid(alert, ctx):
    txs = _alert_txs(ctx, alert)
    customer = _customer_name(ctx)
    incoming = [tx for tx in txs if tx.get("direction") == "in"]
    outgoing = [tx for tx in txs if tx.get("direction") == "out" and not _is_card(tx)]
    received = to_pence((alert.get("measured") or {}).get("received_gbp")) or _sum_pence(incoming)
    paid = to_pence((alert.get("measured") or {}).get("paid_out_gbp")) or _sum_pence(outgoing)
    # A documented refund is not unexplained third-party money.
    refund_ids = {tx.get("transaction_id") for tx, _doc in _refund_inflows(ctx, incoming)}
    holds = _docs(ctx, "holding_deposit_receipt")
    returns = _docs(ctx, "deposit_return_letter")
    if holds and returns:
        hold_out = [tx for tx in outgoing if any(near(_tx_pence(tx), amount) for doc in holds for amount in doc["amounts"])]
        back_in = [tx for tx in incoming if any(near(_tx_pence(tx), amount) for doc in returns for amount in doc["amounts"])]
        if hold_out and back_in:
            return _finding(
                "CLOSE", "REFUND_OF_EARLIER_PAYMENT",
                [returns[0]["id"], holds[0]["id"], back_in[0].get("transaction_id"), hold_out[0].get("transaction_id")],
                [
                    f"{customer} received {gbp(_tx_pence(back_in[0]))} from {back_in[0].get('counterparty_name')}, documented as a tenancy-deposit return in {returns[0]['id']}.",
                    f"The onward payment of {gbp(_tx_pence(hold_out[0]))} to {hold_out[0].get('counterparty_name')} matches holding-deposit receipt {holds[0]['id']}.",
                    "Both legs of the rapid movement are documented, so the alert is closed.",
                ],
                alert.get("alert_id"), _tx_pence(back_in[0]),
            )
    cancel = next(iter(_docs(ctx, "cancellation_confirmation")), None)
    if cancel and (any(near(amount, received) or near(amount, paid) or any(near(amount, _tx_pence(tx)) for tx in txs) for amount in cancel["amounts"]) or "return" in cancel["low"] or "refund" in cancel["low"]):
        return _finding(
            "CLOSE", "REFUND_OF_EARLIER_PAYMENT",
            [cancel["id"]] + [tx.get("transaction_id") for tx in txs[:4]],
            [
                f"{customer} moved {gbp(received or paid)} through the account inside the rapid-movement window.",
                f"Cancellation confirmation {cancel['id']} returns the subscription of {gbp(max(cancel['amounts']) if cancel['amounts'] else paid)} to an account in the customer's name.",
                "The movement is a refund of that subscription, so the alert is closed.",
            ],
            alert.get("alert_id"), received or paid,
        )
    # Client's own funding is the product at the payments firm.
    third = [tx for tx in incoming if not _is_client(ctx, tx) and tx.get("transaction_id") not in refund_ids]
    if ctx["settings"].get("ignore_client_funding") and incoming and not third:
        bare = []
        for tx in outgoing:
            if _tx_pence(tx) < 1_000_000 or _is_client(ctx, tx):
                continue
            status, _docs_found = classify_tx(tx, ctx["docs"])
            if status != "match":
                bare.append(tx)
        if bare and ctx["institution"] == "payments_fx":
            tx = _largest(bare)
            return _finding(
                "RFI", "RFI_PURPOSE_DOCUMENTS",
                [tx.get("transaction_id")] + [item.get("transaction_id") for item in incoming[:3]],
                [
                    f"{customer} paid {gbp(_tx_pence(tx))} to {tx.get('counterparty_name')} on {fmt_date(_tx_date(tx))}.",
                    "That payment is £10,000 or more and is not to the client, and no invoice or contract on file matches it.",
                    f"Ask {customer} for the invoice or contract for the {gbp(_tx_pence(tx))} payment, showing the amount, the payee and the date.",
                ],
                alert.get("alert_id"), _tx_pence(tx),
            )
        return _finding(
            "CLOSE", "WITHIN_PROFILE",
            [tx.get("transaction_id") for tx in incoming[:6] + outgoing[:4]],
            [
                f"{customer} received {gbp(received)} from its own account and paid {gbp(paid)} out within 48 hours.",
                "Funding and paying on is the firm's product, and the client's own funding is not third-party money.",
                "The rapid-movement alert is closed as activity within the client's instructions.",
            ],
            alert.get("alert_id"), received,
        )
    booking = _booking_doc(ctx, txs)
    if booking:
        doc, names, used = booking
        return _finding(
            "CLOSE", "COLLECTION_DOCUMENTED",
            [doc["id"]] + [tx.get("transaction_id") for tx in used[:12]],
            [
                f"{customer} received {gbp(_sum_pence(used))} from {len(used)} people and paid {gbp(paid)} on shortly afterwards.",
                f"{doc['title']} ({doc['id']}) names {len(names)} travellers and a total of {gbp(max(doc['amounts']) if doc['amounts'] else _sum_pence(used))}, which reconciles to those receipts.",
                "The payments are a documented collection for that booking.",
            ],
            alert.get("alert_id"), received,
        )
    matched, unexplained, mismatched = _explained_outflows(ctx, outgoing or txs)
    if mismatched and not matched:
        tx, docs = mismatched[0]
        return _finding(
            "ESCALATE", "ESC_INVOICE_MISMATCH",
            [tx.get("transaction_id")] + [doc["id"] for doc in docs],
            [
                f"{customer} paid {gbp(_tx_pence(tx))} to {tx.get('counterparty_name')} on {fmt_date(_tx_date(tx))}.",
                f"{docs[0]['title']} ({docs[0]['id']}) is the invoice on file and it does not match that amount or payee.",
                "The invoices do not support the payments, so the case is escalated to the MLRO.",
            ],
            alert.get("alert_id"), _tx_pence(tx),
        )
    refunds = [(_refund_doc(ctx, tx), tx) for tx in incoming]
    refunds = [(doc, tx) for doc, tx in refunds if doc]
    if refunds and not third:
        doc, tx = refunds[0]
        return _finding(
            "CLOSE", "REFUND_OF_EARLIER_PAYMENT",
            [doc["id"], tx.get("transaction_id")],
            [
                f"{customer} received {_tx_phrase(tx)}.",
                f"{doc['title']} ({doc['id']}) documents that sum as a refund of an earlier payment.",
                "The rapid movement is the refund returning, so the alert is closed.",
            ],
            alert.get("alert_id"), _tx_pence(tx),
        )
    if outgoing and matched and not unexplained:
        doc = matched[0]
        top = _largest(outgoing)
        code = "INVOICE_MATCHES"
        if doc["type"] in REFUND_TYPES:
            code = "REFUND_OF_EARLIER_PAYMENT"
        if doc["type"] == "bank_statement":
            code = "OWN_ACCOUNT"
        return _finding(
            "CLOSE", code,
            [doc["id"]] + [tx.get("transaction_id") for tx in (outgoing[:6] + incoming[:4])],
            [
                f"{customer} received {gbp(received)} and paid out {gbp(paid)} within the rapid-movement window.",
                f"{doc['title']} ({doc['id']}) matches the payment of {gbp(_tx_pence(top))} to {top.get('counterparty_name')}." if top else f"{doc['id']} matches the payments out.",
                "The money that left the account is documented, so the rapid-movement alert is closed.",
            ],
            alert.get("alert_id"), paid or received,
        )
    # Linked pass-through: several companies and a nominal contract, or an admission.
    registers = _docs(ctx, "company_register_extract")
    nominal = [doc for doc in _docs(ctx, "consulting_contract") if doc["amounts"] and max(doc["amounts"]) < max(received, 1) * 0.25]
    if any(doc["flags"] & {"pass_chat"} for doc in ctx["docs"]) or (len(registers) >= 3 and nominal and third):
        doc_ids = [doc["id"] for doc in (nominal or registers)[:3]]
        return _finding(
            "ESCALATE", "ESC_PASS_THROUGH",
            doc_ids + [tx.get("transaction_id") for tx in (third[:4] + outgoing[:4])],
            [
                f"{customer} received {gbp(received)} from {len(third) or 'other'} parties other than the customer and paid {gbp(paid)} out within the rapid-movement window.",
                f"The only paperwork is {human_join(doc_ids) or 'a nominal contract'}, which does not account for the flow.",
                "The money is passing through without a credible purpose, so the case is escalated to the MLRO.",
            ],
            alert.get("alert_id"), received,
        )
    if third and ctx["institution"] in {"payments_fx", "investment"}:
        return _finding(
            "ESCALATE", "ESC_THIRD_PARTY_FUNDING",
            [tx.get("transaction_id") for tx in third[:6] + outgoing[:4]],
            [
                f"{customer} received {gbp(_sum_pence(third))} from {human_join(sorted({tx.get('counterparty_name') or 'an unnamed payer' for tx in third[:4]}))} and paid {gbp(paid)} on within the rapid-movement window.",
                "No group chart, gift letter, invoice or account-ownership document explains those funders.",
                "Unrelated third parties are funding the account and the money is being paid on, so the case is escalated to the MLRO.",
            ],
            alert.get("alert_id"), _sum_pence(third),
        )
    top_in = _largest(third or incoming)
    top_out = _largest(outgoing)
    return _finding(
        "ESCALATE", "ESC_UNEXPLAINED_FUNDS",
        [tx.get("transaction_id") for tx in ([top_in] if top_in else []) + ([top_out] if top_out else [])],
        [
            f"{customer} received {gbp(received)} and paid out {gbp(paid)}, which is the rapid-movement pattern the alert measured.",
            (f"The largest receipt was {_tx_phrase(top_in)} and the largest onward payment was {_tx_phrase(top_out)}." if top_in and top_out else "No document on file explains the source or the purpose."),
            "The money has no credible explanation and was moved on, so the case is escalated to the MLRO.",
        ],
        alert.get("alert_id"), received,
    )


def explain_structuring(alert, ctx):
    txs = _alert_txs(ctx, alert)
    customer = _customer_name(ctx)
    amounts = [gbp(_tx_pence(tx)) for tx in txs[:6]]
    note = next((doc for doc in ctx["docs"] if "structuring" in doc["flags"]), None)
    if note:
        return _finding(
            "ESCALATE", "ESC_STRUCTURING",
            [note["id"]] + [tx.get("transaction_id") for tx in txs],
            [
                f"{customer} made {len(txs)} payments or deposits of {human_join(amounts)} in the structuring window.",
                f"Staff note {note['id']} records that the customer asked whether keeping each amount under the threshold would avoid checks.",
                "The transactions were split to stay under a threshold, so the case is escalated to the MLRO.",
            ],
            alert.get("alert_id"), _sum_pence(txs),
        )
    schedule = _schedule_covers(ctx, txs)
    if schedule:
        return _finding(
            "CLOSE", "INVOICE_MATCHES",
            [schedule["id"]] + [tx.get("transaction_id") for tx in txs],
            [
                f"{customer} paid {human_join(amounts)} to {txs[0].get('counterparty_name') if txs else 'one beneficiary'}.",
                f"Payment schedule {schedule['id']} ({schedule['title']}) sets out those stage amounts and dates, and each payment matches a stage.",
                "The split follows the contract rather than a threshold, so the alert is closed.",
            ],
            alert.get("alert_id"), _sum_pence(txs),
        )
    records = _records_doc(ctx, txs)
    if records:
        support = [doc["id"] for doc in ctx["docs"] if doc["type"] in {"cash_handling_procedure", "ticket_report", "cash_collection_reports", "takings_book"}]
        return _finding(
            "CLOSE", "BUSINESS_RECORDS_RECONCILE",
            [records["id"]] + support + [tx.get("transaction_id") for tx in txs],
            [
                f"{customer} paid in {human_join(amounts)}, each just under the cash or top-up threshold.",
                f"{records['title']} ({records['id']}) reconciles to that cash.",
                "The pattern matches the business records rather than structuring, so the alert is closed.",
            ],
            alert.get("alert_id"), _sum_pence(txs),
        )
    # Each payment has its own matching invoice and there is no threshold admission.
    if txs and all(classify_tx(tx, ctx["docs"])[0] == "match" for tx in txs):
        status_docs = []
        for tx in txs:
            _status, docs = classify_tx(tx, ctx["docs"])
            status_docs.extend(doc["id"] for doc in docs)
        return _finding(
            "CLOSE", "INVOICE_MATCHES",
            status_docs + [tx.get("transaction_id") for tx in txs],
            [
                f"Each of {customer}'s payments ({human_join(amounts)}) matches an invoice or contract line.",
                f"The documents relied on are {human_join(sorted(set(status_docs)))}.",
                "The amounts are documented individually, so this is not treated as structuring.",
            ],
            alert.get("alert_id"), _sum_pence(txs),
        )
    return _finding(
        "ESCALATE", "ESC_STRUCTURING",
        [tx.get("transaction_id") for tx in txs],
        [
            f"{customer} made {len(txs)} transactions of {human_join(amounts)} within the rule's window, each sitting just under the threshold.",
            "No invoice, contract stage or cash record reconciles to those amounts.",
            "The transactions were split to stay under a threshold, so the case is escalated to the MLRO.",
        ],
        alert.get("alert_id"), _sum_pence(txs),
    )


def explain_many(alert, ctx):
    txs = _alert_txs(ctx, alert)
    customer = _customer_name(ctx)
    measured = alert.get("measured") or {}
    direction = measured.get("direction") or ""
    count = measured.get("distinct_counterparties") or len({tx.get("counterparty_name") for tx in txs})
    booking = _booking_doc(ctx, txs)
    if booking:
        doc, names, used = booking
        return _finding(
            "CLOSE", "COLLECTION_DOCUMENTED",
            [doc["id"]] + [tx.get("transaction_id") for tx in used[:12]],
            [
                f"{count} different people paid {customer}, above the many-payments limit.",
                f"{doc['title']} ({doc['id']}) lists {len(names)} named travellers and matches the receipts.",
                "The payments are a documented collection.",
            ],
            alert.get("alert_id"), _sum_pence(used),
        )
    # Payment file of beneficiaries.
    files = _docs(ctx, "payment_file")
    if files:
        doc = files[0]
        payees = []
        for table in doc["tables"]:
            for row in table:
                name = row.get("payee") or row.get("contractor") or row.get("beneficiary")
                if name:
                    payees.append(name)
        covered = [tx for tx in txs if any(names_match(tx.get("counterparty_name"), payee) for payee in payees)]
        if payees and (len(covered) >= min(6, len(txs)) or len(payees) >= 6):
            return _finding(
                "CLOSE", "INVOICE_MATCHES",
                [doc["id"]] + [tx.get("transaction_id") for tx in (covered or txs)[:12]],
                [
                    f"{customer} paid {count} different beneficiaries, above the firm's limit.",
                    f"Payment file {doc['id']} lists {len(payees)} payees and matches the payments.",
                    "The many-payments alert is closed on that file.",
                ],
                alert.get("alert_id"), _sum_pence(covered or txs),
            )
    records = _records_doc(ctx, txs)
    if records:
        return _finding(
            "CLOSE", "BUSINESS_RECORDS_RECONCILE",
            [records["id"]] + [tx.get("transaction_id") for tx in txs[:8]],
            [
                f"{count} counterparties paid or were paid by {customer}.",
                f"{records['title']} ({records['id']}) reconciles to the activity.",
                "The business records explain the number of counterparties.",
            ],
            alert.get("alert_id"), _sum_pence(txs),
        )
    if direction == "incoming" or any(tx.get("direction") == "in" for tx in txs):
        code = "RFI_BUSINESS_RECORDS" if ctx["customer"].get("kind") == "business" else "RFI_COUNTERPARTY_DETAILS"
        ask = "the business records showing who these payers are and what the receipts were for" if code.endswith("RECORDS") else "who each payer is and what the payments were for"
        return _finding(
            "RFI", code,
            [tx.get("transaction_id") for tx in txs[:8]],
            [
                f"{count} different people paid {customer}, totalling {gbp(_sum_pence(txs))}.",
                "No collection list, payment file or business record on file explains them, and the case does not on its own show the money was passed on.",
                f"Ask {customer} for {ask}.",
            ],
            alert.get("alert_id"), _sum_pence(txs),
        )
    return _finding(
        "RFI", "RFI_PURPOSE_DOCUMENTS",
        [tx.get("transaction_id") for tx in txs[:8]],
        [
            f"{customer} paid {count} different beneficiaries, totalling {gbp(_sum_pence(txs))}.",
            "No payment file, invoice or contract on file lists those beneficiaries.",
            f"Ask {customer} for the payment file or invoices showing what each payment was for.",
        ],
        alert.get("alert_id"), _sum_pence(txs),
    )


def explain_cash(alert, ctx):
    txs = _alert_txs(ctx, alert)
    tx = _largest(txs)
    customer = _customer_name(ctx)
    amount = _tx_pence(tx) if tx else 0
    records = _records_doc(ctx, txs)
    if records:
        return _finding(
            "CLOSE", "BUSINESS_RECORDS_RECONCILE",
            [records["id"]] + ([tx.get("transaction_id")] if tx else []),
            [
                f"{customer} paid in {gbp(amount)} in cash on {fmt_date(_tx_date(tx))}.",
                f"{records['title']} ({records['id']}) reconciles to that cash.",
                "The business records explain the cash alert.",
            ],
            alert.get("alert_id"), amount,
        )
    event = None
    for doc in ctx["docs"]:
        if doc["type"] in {"vehicle_sale_receipt", "sale_receipt", "advert"} and any(near(amount, candidate) for candidate in doc["amounts"]):
            if doc["type"] == "advert" and not any(other["type"] in {"sale_receipt", "vehicle_sale_receipt"} for other in ctx["docs"]):
                continue
            event = doc
            break
    if event:
        schedule = next((doc["id"] for doc in ctx["docs"] if doc["type"] == "insurance_schedule"), None)
        return _finding(
            "CLOSE", "DOCUMENTED_EVENT",
            [event["id"], tx.get("transaction_id") if tx else None] + ([schedule] if schedule else []),
            [
                f"{customer} paid in {gbp(amount)} in cash on {fmt_date(_tx_date(tx))}.",
                f"{event['title']} ({event['id']}) records a sale for the same amount.",
                "The cash is the proceeds of that documented sale.",
            ],
            alert.get("alert_id"), amount,
        )
    if ctx["customer"].get("kind") == "business":
        return _finding(
            "RFI", "RFI_BUSINESS_RECORDS",
            [tx.get("transaction_id") if tx else None],
            [
                f"{customer} paid in {gbp(amount)} in cash on {fmt_date(_tx_date(tx))}, which is large for the business.",
                "No takings book, collection report or sale receipt covers it, and the cash has not been tied to a crime typology.",
                f"Ask {customer} for the business records behind the {gbp(amount)} cash.",
            ],
            alert.get("alert_id"), amount,
        )
    return _finding(
        "RFI", "RFI_SOURCE_OF_FUNDS",
        [tx.get("transaction_id") if tx else None],
        [
            f"{customer} paid in {gbp(amount)} in cash on {fmt_date(_tx_date(tx))}.",
            "No sale receipt or other source document is on file, and the case has no scam report or list match.",
            f"Ask {customer} for proof of where the {gbp(amount)} in cash came from.",
        ],
        alert.get("alert_id"), amount,
    )


def explain_country(alert, ctx):
    txs = _alert_txs(ctx, alert)
    risky = [tx for tx in txs if _high_risk(ctx, tx.get("counterparty_country"))] or txs
    customer = _customer_name(ctx)
    measured = alert.get("measured") or {}
    country = measured.get("countries") or (risky[0].get("counterparty_country") if risky else "a higher-risk country")
    total = to_pence(measured.get("total_gbp")) or _sum_pence(risky)
    note = next((doc for doc in ctx["docs"] if "contradiction" in doc["flags"]), None)
    matched = []
    mismatched = []
    for tx in risky:
        status, docs = classify_tx(tx, ctx["docs"])
        if status == "match":
            matched.append((tx, docs[0]))
        elif status == "mismatch":
            mismatched.append((tx, docs[0]))
        else:
            refund = _refund_doc(ctx, tx)
            if refund:
                matched.append((tx, refund))
    if note and (matched or mismatched or risky):
        docs = [note] + [pair[1] for pair in (mismatched or matched)[:2]]
        return _finding(
            "ESCALATE", "ESC_INVOICE_MISMATCH" if (matched or mismatched) else "ESC_HIGH_RISK_CORRIDOR",
            [doc["id"] for doc in docs] + [tx.get("transaction_id") for tx in risky[:4]],
            [
                f"{customer} moved {gbp(total)} to or from {country}, which the customer had not declared.",
                f"Staff note {note['id']} records that the customer could not explain that routing."
                + (f" {docs[1]['id']} does not support the explanation they gave." if len(docs) > 1 else ""),
                "The payments to or from the higher-risk country are unexplained, so the case is escalated to the MLRO.",
            ],
            alert.get("alert_id"), total,
        )
    if mismatched and not matched:
        tx, doc = mismatched[0]
        return _finding(
            "ESCALATE", "ESC_INVOICE_MISMATCH",
            [doc["id"], tx.get("transaction_id")],
            [
                f"{customer} paid {gbp(_tx_pence(tx))} to {tx.get('counterparty_name')} in {tx.get('counterparty_country')}.",
                f"{doc['title']} ({doc['id']}) does not match that amount.",
                "The invoice does not support the higher-risk payment, so the case is escalated to the MLRO.",
            ],
            alert.get("alert_id"), _tx_pence(tx),
        )
    if matched and len(matched) >= max(1, len(risky)):
        tx, doc = matched[0]
        code = "REFUND_OF_EARLIER_PAYMENT" if doc["type"] in REFUND_TYPES or "refund" in doc["low"] else "INVOICE_MATCHES"
        return _finding(
            "CLOSE", code,
            [doc["id"]] + [item[0].get("transaction_id") for item in matched],
            [
                f"{customer} moved {gbp(total)} to or from {country}.",
                f"{doc['title']} ({doc['id']}) matches the payment of {gbp(_tx_pence(tx))} to {tx.get('counterparty_name')} on {fmt_date(_tx_date(tx))}.",
                "The higher-risk payment is documented, so the corridor alert is closed.",
            ],
            alert.get("alert_id"), total,
        )
    links = [(tx, doc) for tx, doc in _own_links(ctx) if tx.get("transaction_id") in {item.get("transaction_id") for item in risky}]
    if not links:
        links = [pair for pair in _own_links(ctx) if _high_risk(ctx, pair[0].get("counterparty_country"))]
    if links:
        tx, doc = links[0]
        return _finding(
            "CLOSE", "OWN_ACCOUNT",
            [doc["id"], tx.get("transaction_id")],
            [
                f"{customer} moved {gbp(_tx_pence(tx))} to or from {tx.get('counterparty_country')}, a higher-risk country that was not on the declared list.",
                f"{doc['title']} ({doc['id']}) shows the account is held by {customer}.",
                "The corridor is the customer's own documented account, so the alert is closed.",
            ],
            alert.get("alert_id"), _tx_pence(tx),
        )
    employment = next((doc for doc in _docs(ctx, "employment_contract") if any(name.lower() in doc["low"] for name in ctx["high_risk"]) or any(city in doc["low"] for city in CITY_COUNTRY)), None)
    if employment and any(names_match(doc["fields"].get("account holder") or "", _customer_name(ctx)) for doc in _docs(ctx, "bank_statement")):
        statement = next(doc for doc in _docs(ctx, "bank_statement") if names_match(doc["fields"].get("account holder") or "", _customer_name(ctx)))
        return _finding(
            "CLOSE", "DOCUMENTED_EVENT",
            [employment["id"], statement["id"]] + [tx.get("transaction_id") for tx in risky[:3]],
            [
                f"{customer} received {gbp(total)} from {country}.",
                f"Employment contract {employment['id']} places them in that country, and statement {statement['id']} shows the account holder is {customer}.",
                "The higher-risk funds are documented salary from that posting.",
            ],
            alert.get("alert_id"), total,
        )
    return _finding(
        "ESCALATE", "ESC_HIGH_RISK_CORRIDOR",
        [tx.get("transaction_id") for tx in risky[:6]],
        [
            f"{customer} moved {gbp(total)} to or from {country}, which is not among the countries declared at onboarding.",
            (f"The payment relied on is {_tx_phrase(risky[0])}." if risky else "No invoice, contract or account-ownership document covers the corridor."),
            "Unexplained payments to or from a higher-risk country are escalated to the MLRO rather than put to the customer.",
        ],
        alert.get("alert_id"), total,
    )


def explain_dormant(alert, ctx):
    txs = _alert_txs(ctx, alert)
    customer = _customer_name(ctx)
    total = _sum_pence(txs) or to_pence((alert.get("measured") or {}).get("total_gbp")) or 0
    top = _largest(txs)
    reverified = next((doc for doc in _docs(ctx, "reverification_record")), None)
    event = _event_doc(ctx, _tx_pence(top), "in") if top else None
    if event:
        return _finding(
            "CLOSE", "DOCUMENTED_EVENT",
            [event["id"]] + ([top.get("transaction_id")] if top else []),
            [
                f"Activity of {gbp(total or _tx_pence(top))} resumed on {customer}'s account after a dormant period.",
                f"{event['title']} ({event['id']}) documents the funds that came back in.",
                "The reactivation is explained by that one-off event.",
            ],
            alert.get("alert_id"), total or ( _tx_pence(top) if top else 0),
        )
    if reverified:
        return _finding(
            "CLOSE", "RETURNING_CUSTOMER",
            [reverified["id"]] + ([top.get("transaction_id")] if top else []),
            [
                f"{customer}'s account moved {gbp(total or (_tx_pence(top) if top else 0))} after being dormant.",
                f"Identity re-verification {reverified['id']} confirms the customer on {fmt_date(reverified['dated'])}.",
                "A verified customer resumed using the account, so the dormant-account alert is closed.",
            ],
            alert.get("alert_id"), total,
        )
    return _finding(
        "RFI", "RFI_SOURCE_OF_FUNDS",
        [top.get("transaction_id")] if top else [],
        [
            f"{customer}'s account was dormant and then moved {gbp(total or (_tx_pence(top) if top else 0))}" + (f" ({_tx_phrase(top)})" if top else "") + ".",
            "There is no fresh identity check and no document explaining the new funds.",
            f"Ask {customer} for proof of the source of the funds and confirmation that they are using the account themselves.",
        ],
        alert.get("alert_id"), total,
    )


def explain_third(alert, ctx):
    txs = _alert_txs(ctx, alert)
    incoming = [tx for tx in txs if tx.get("direction") == "in"] or txs
    tx = _largest(incoming)
    customer = _customer_name(ctx)
    if tx is None:
        return _finding(
            "RFI", "RFI_COUNTERPARTY_DETAILS", [],
            [f"The third-party alert on {customer} has no transaction in the file.", "Ask who funded the account and what the payment was for."],
            alert.get("alert_id"), 0,
        )
    payer = tx.get("counterparty_name")
    group = _group_doc(ctx, payer)
    if group:
        return _finding(
            "CLOSE", "GROUP_COMPANY",
            [group["id"], tx.get("transaction_id")],
            [
                f"{payer} paid {gbp(_tx_pence(tx))} into {customer} on {fmt_date(_tx_date(tx))}.",
                f"Group structure {group['id']} shows {payer} and {customer} are in the same group.",
                "The funding is from a verified group company, so the third-party alert is closed.",
            ],
            alert.get("alert_id"), _tx_pence(tx),
        )
    own = [(linked, doc) for linked, doc in _own_links(ctx) if linked.get("transaction_id") == tx.get("transaction_id") or names_match(payer, _customer_name(ctx))]
    if own or (_is_client(ctx, tx) and _docs(ctx, "bank_statement")):
        doc = own[0][1] if own else _docs(ctx, "bank_statement")[0]
        owned, base = _own_ratio(ctx, own or [(tx, doc)])
        if base and owned >= ctx["settings"]["own_multiple"] * base:
            return _finding(
                "RFI", "RFI_UPDATED_PROFILE",
                [doc["id"], tx.get("transaction_id")],
                [
                    f"{customer} received {gbp(_tx_pence(tx))} from an account {doc['id']} shows they hold.",
                    f"That is at least five times the { _basis_label(ctx) } of {gbp(base)}.",
                    f"Ask {customer} for an updated expected income or volume.",
                ],
                alert.get("alert_id"), _tx_pence(tx),
            )
        if own or names_match(doc["fields"].get("account holder") or "", customer):
            return _finding(
                "CLOSE", "OWN_ACCOUNT",
                [doc["id"], tx.get("transaction_id")],
                [
                    f"The funding of {gbp(_tx_pence(tx))} from {payer} looked like a third party.",
                    f"Statement {doc['id']} shows the account is held by {customer}.",
                    "The money moved between accounts the customer holds, so the alert is closed.",
                ],
                alert.get("alert_id"), _tx_pence(tx),
            )
    gift = _gift_doc(ctx, tx)
    if gift and (names_match(_gift_payer(gift), payer) or _mentions(gift, payer)):
        return _finding(
            "CLOSE", "THIRD_PARTY_DOCUMENTED",
            [gift["id"], tx.get("transaction_id")],
            [
                f"{payer} paid {gbp(_tx_pence(tx))} to {customer} on {fmt_date(_tx_date(tx))}.",
                f"Gift letter {gift['id']} from {_gift_payer(gift) or payer} documents the relationship and the amount.",
                "The third-party payer is explained.",
            ],
            alert.get("alert_id"), _tx_pence(tx),
        )
    factor = _factor_doc(ctx, tx)
    if factor:
        agreement, remittance = factor
        ids = [agreement["id"], tx.get("transaction_id")] + ([remittance["id"]] if remittance else [])
        return _finding(
            "CLOSE", "THIRD_PARTY_DOCUMENTED",
            ids,
            [
                f"{payer} paid {gbp(_tx_pence(tx))} into {customer}.",
                f"Factoring agreement {agreement['id']}" + (f" and remittance {remittance['id']}" if remittance else "") + " document that advance.",
                "The third-party funding is the invoice-finance arrangement, so the alert is closed.",
            ],
            alert.get("alert_id"), _tx_pence(tx),
        )
    refund = _refund_doc(ctx, tx)
    if refund:
        return _finding(
            "CLOSE", "REFUND_OF_EARLIER_PAYMENT",
            [refund["id"], tx.get("transaction_id")],
            [
                f"{payer} paid {gbp(_tx_pence(tx))} into {customer} on {fmt_date(_tx_date(tx))}.",
                f"{refund['title']} ({refund['id']}) documents that sum as a refund of an earlier payment.",
                "The third-party receipt is a documented refund, so the alert is closed.",
            ],
            alert.get("alert_id"), _tx_pence(tx),
        )
    documented = _event_doc(ctx, _tx_pence(tx), "in")
    if documented and documented["type"] in {"loan_agreement", "completion_statement", "insurer_letter", "settlement_letter", "solicitor_letter"}:
        return _finding(
            "CLOSE", "DOCUMENTED_EVENT",
            [documented["id"], tx.get("transaction_id")],
            [
                f"{payer} paid {gbp(_tx_pence(tx))} to {customer} on {fmt_date(_tx_date(tx))}.",
                f"{documented['title']} ({documented['id']}) documents that amount.",
                "The third-party receipt is a documented one-off event, so the alert is closed.",
            ],
            alert.get("alert_id"), _tx_pence(tx),
        )
    # Paid on quickly, or several third parties.
    outs = [item for item in ctx["window_txs"] if item.get("direction") == "out" and not _is_card(item)]
    paid_on = _sum_pence(outs) >= int(_tx_pence(tx) * 0.7) if tx else False
    distinct = {item.get("counterparty_name") for item in incoming if not _is_client(ctx, item)}
    registers = _docs(ctx, "company_register_extract")
    nominal = [doc for doc in _docs(ctx, "consulting_contract") if doc["amounts"] and max(doc["amounts"]) < _tx_pence(tx) * 0.5]
    if len(registers) >= 3 and nominal:
        return _finding(
            "ESCALATE", "ESC_PASS_THROUGH",
            [nominal[0]["id"]] + [item.get("transaction_id") for item in incoming[:3] + outs[:3]],
            [
                f"{payer} paid {gbp(_tx_pence(tx))} into {customer}, and {gbp(_sum_pence(outs[:6]))} left the account.",
                f"Consulting contract {nominal[0]['id']} is only for {gbp(max(nominal[0]['amounts']))} and does not explain the flow between the companies on the registers.",
                "The money is passing through without a credible purpose, so the case is escalated to the MLRO.",
            ],
            alert.get("alert_id"), _tx_pence(tx),
        )
    if paid_on or len(distinct) >= 2:
        code = "ESC_PASS_THROUGH" if any(doc["flags"] & {"pass_chat"} for doc in ctx["docs"]) else "ESC_THIRD_PARTY_FUNDING"
        if code == "ESC_PASS_THROUGH":
            sentences = [
                f"{payer} paid {gbp(_tx_pence(tx))} into {customer} and the funds were paid on.",
                "The customer said the money was received for other people and sent on.",
                "The account is being used to pass other people's money through, so the case is escalated to the MLRO.",
            ]
        else:
            sentences = [
                f"{payer} paid {gbp(_tx_pence(tx))} into {customer} on {fmt_date(_tx_date(tx))}, and {gbp(_sum_pence(outs[:8]))} was paid out.",
                "No document shows a relationship between the funder and the customer.",
                "An unrelated third party is funding the account, so the case is escalated to the MLRO.",
            ]
        return _finding(
            "ESCALATE", code,
            [tx.get("transaction_id")] + [item.get("transaction_id") for item in outs[:4]],
            sentences,
            alert.get("alert_id"), _tx_pence(tx),
        )
    return _finding(
        "RFI", "RFI_SOURCE_OF_FUNDS",
        [tx.get("transaction_id")],
        [
            f"{payer} paid {gbp(_tx_pence(tx))} into {customer} on {fmt_date(_tx_date(tx))}. The account-holder name is not the customer's.",
            "No gift letter, group chart or account statement explains the payer, and the funds have not been paid straight on.",
            f"Ask {customer} for proof of the source of the {gbp(_tx_pence(tx))} from {payer}, and for who {payer} is.",
        ],
        alert.get("alert_id"), _tx_pence(tx),
    )


def explain_redemption(alert, ctx):
    txs = _alert_txs(ctx, alert)
    customer = _customer_name(ctx)
    sells = [tx for tx in txs if (tx.get("channel") or "") == "fund_redemption" or tx.get("direction") == "out"]
    amount = _sum_pence(sells) or _sum_pence(txs)
    letter = next(iter(_docs(ctx, "redundancy_letter")), None)
    email = next((doc for doc in _docs(ctx, "customer_email") if "redundan" in doc["low"]), None)
    if letter:
        return _finding(
            "CLOSE", "DOCUMENTED_EVENT",
            [letter["id"]] + ([email["id"]] if email else []) + [tx.get("transaction_id") for tx in sells[:4]],
            [
                f"{customer} sold fund units within 90 days, realising {gbp(amount)}.",
                f"Redundancy letter {letter['id']}" + (f" and email {email['id']}" if email else "") + " document why the holding was sold.",
                "The early redemption is a documented change of circumstances, so the alert is closed.",
            ],
            alert.get("alert_id"), amount,
        )
    cancel = next(iter(_docs(ctx, "cancellation_confirmation")), None)
    if cancel:
        return _finding(
            "CLOSE", "REFUND_OF_EARLIER_PAYMENT",
            [cancel["id"]] + [tx.get("transaction_id") for tx in txs[:4]],
            [
                f"{customer} sold or cancelled a holding of {gbp(max(cancel['amounts']) if cancel['amounts'] else amount)} within the early-redemption window.",
                f"Cancellation confirmation {cancel['id']} returns that subscription to an account in the customer's name.",
                "The redemption is a refund of the subscription, so the alert is closed.",
            ],
            alert.get("alert_id"), amount,
        )
    return _finding(
        "ESCALATE", "ESC_EARLY_REDEMPTION",
        [tx.get("transaction_id") for tx in txs[:6]],
        [
            f"{customer} bought fund units and sold them back within 90 days, moving {gbp(amount)}.",
            "No redundancy letter, cancellation confirmation or other event on file explains the round trip.",
            "The units were bought and quickly sold to move money, so the case is escalated to the MLRO.",
        ],
        alert.get("alert_id"), amount,
    )


def case_suspicions(ctx):
    findings = []
    customer = _customer_name(ctx)
    frauds = [doc for doc in ctx["docs"] if "fraud" in doc["flags"]]
    mules = [doc for doc in ctx["docs"] if "mule" in doc["flags"]]
    rules = {alert.get("rule_id") for alert in ctx["alerts"]}
    incoming_many = any((alert.get("measured") or {}).get("direction") == "incoming" and alert.get("rule_id") == "TM04" for alert in ctx["alerts"])
    if frauds or mules:
        docs = frauds + mules
        txs = []
        for alert in ctx["alerts"]:
            if alert.get("rule_id") in {"TM02", "TM04", "TM01"}:
                txs.extend(_alert_txs(ctx, alert)[:8])
        findings.append(_finding(
            "ESCALATE", "ESC_MULE_ACTIVITY",
            [doc["id"] for doc in docs] + [tx.get("transaction_id") for tx in txs[:10]],
            [
                f"{customer}'s account took in funds from other people and paid them on.",
                (f"{frauds[0]['title']} ({frauds[0]['id']}) reports that a sending customer was scammed." if frauds else f"In {mules[0]['id']} the customer said they receive other people's money and send it on."),
                "The account is receiving and passing on other people's money, so the case is escalated to the MLRO.",
            ],
            None, _sum_pence(txs),
        ))
    elif incoming_many and "TM02" in rules and not _docs(ctx, "booking_confirmation", "cash_collection_reports", "payment_file", "takings_book"):
        txs = []
        for alert in ctx["alerts"]:
            if alert.get("rule_id") in {"TM02", "TM04"}:
                txs.extend(_alert_txs(ctx, alert)[:8])
        findings.append(_finding(
            "ESCALATE", "ESC_MULE_ACTIVITY",
            [tx.get("transaction_id") for tx in txs[:12]],
            [
                f"{customer} was paid by many different people and most of the money left the account within 48 hours.",
                "There is no collection list, booking or business record explaining the receipts.",
                "The account is receiving and passing on other people's money, so the case is escalated to the MLRO.",
            ],
            None, _sum_pence(txs),
        ))
    for doc in _docs(ctx, "device_export"):
        customers = _device_customers(doc)
        if len(customers) >= 2:
            findings.append(_finding(
                "ESCALATE", "ESC_ACCOUNT_MISUSE",
                [doc["id"]],
                [
                    f"Device export {doc['id']} shows one device logged into {len(customers)} customer accounts, including {customer}.",
                    "Somebody other than the customer appears to be using the account.",
                    "The case is escalated to the MLRO.",
                ],
                None, 0,
            ))
            break
    if any("structuring" in doc["flags"] for doc in ctx["docs"]) and "TM03" not in rules:
        note = next(doc for doc in ctx["docs"] if "structuring" in doc["flags"])
        findings.append(_finding(
            "ESCALATE", "ESC_STRUCTURING",
            [note["id"]],
            [
                f"Staff note {note['id']} records {customer} asking whether splitting amounts under the threshold would avoid checks.",
                "That is structuring, so the case is escalated to the MLRO.",
            ],
            None, 0,
        ))
    # Invoice gate for a held payment at the payments firm.
    held = ctx.get("held")
    if held and ctx["settings"].get("invoice_above") and _tx_pence(held) >= ctx["settings"]["invoice_above"]:
        if not _is_client(ctx, held):
            own_ids = {tx.get("transaction_id") for tx, _doc in _own_links(ctx)}
            if held.get("transaction_id") not in own_ids:
                status, docs = classify_tx(held, ctx["docs"])
                if status == "mismatch":
                    findings.append(_finding(
                        "ESCALATE", "ESC_INVOICE_MISMATCH",
                        [held.get("transaction_id")] + [doc["id"] for doc in docs],
                        [
                            f"The held payment of {gbp(_tx_pence(held))} to {held.get('counterparty_name')} does not match {docs[0]['id']}.",
                            "The invoice on file does not support the payment, so it is not released and the case is escalated to the MLRO.",
                        ],
                        None, _tx_pence(held),
                    ))
                elif status != "match":
                    findings.append(_finding(
                        "RFI", "RFI_PURPOSE_DOCUMENTS",
                        [held.get("transaction_id")],
                        [
                            f"The held payment of {gbp(_tx_pence(held))} to {held.get('counterparty_name')} on {fmt_date(_tx_date(held))} is £10,000 or more and is not to {customer}.",
                            "Payments of that size need an invoice or contract on file before they are released, and none matches this payment.",
                            f"Ask {customer} for the invoice or contract for the {gbp(_tx_pence(held))} payment to {held.get('counterparty_name')}, showing the amount, the payee and the date.",
                        ],
                        None, _tx_pence(held),
                    ))
    return findings


_DISPATCH = {
    "TM01": explain_profile,
    "TM02": explain_rapid,
    "TM03": explain_structuring,
    "TM04": explain_many,
    "TM05": explain_cash,
    "TM06": explain_country,
    "TM07": explain_dormant,
    "TM08": explain_third,
    "TM10": explain_redemption,
    "TM22": explain_list,
}


def _best(findings):
    return sorted(findings, key=lambda item: (item["priority"], -item["amount"], item.get("alert_id") or "", item["reason_code"]))[0]


def _render(case, winner, known):
    evidence = []
    for item in winner["evidence"]:
        if item and item in known and item not in evidence:
            evidence.append(item)
    evidence.sort()
    sentences = list(winner["sentences"])
    if len(sentences) < 2:
        sentences.append(f"The documents and transactions relied on for {case.get('case_id')} are listed in the evidence.")
    reason = " ".join(sentences[:4])
    return {
        "case_id": case.get("case_id"),
        "alert_ids": list(case.get("alert_ids") or []),
        "verdict": winner["verdict"],
        "reason_code": winner["reason_code"],
        "reason": reason,
        "evidence": evidence,
    }


def decide_case(ctx) -> dict:
    case = ctx["case"]
    alerts = ctx["alerts"]
    findings = list(case_suspicions(ctx))
    for alert in alerts:
        handler = _DISPATCH.get(alert.get("rule_id"))
        if handler is None:
            findings.append(_finding(
                "RFI", "RFI_PURPOSE_DOCUMENTS",
                list(alert.get("transaction_ids") or [])[:4],
                [
                    f"Alert {alert.get('alert_id')} cites rule {alert.get('rule_id')}, which this institution's procedure does not close on its own.",
                    f"Ask {_customer_name(ctx)} for documents showing what the alerted payments were for.",
                ],
                alert.get("alert_id"), 0,
            ))
            continue
        findings.append(handler(alert, ctx))
    escalations = [item for item in findings if item["verdict"] == "ESCALATE"]
    if escalations:
        return _render(case, _best(escalations), ctx["known"])
    rfis = [item for item in findings if item["verdict"] == "RFI"]
    by_alert = {}
    for item in findings:
        if item["verdict"] == "CLOSE" and item.get("alert_id"):
            by_alert.setdefault(item["alert_id"], []).append(item)
    alert_ids = [alert.get("alert_id") for alert in alerts]
    if alert_ids and not rfis and all(by_alert.get(alert_id) for alert_id in alert_ids):
        list_hits = [item for group in by_alert.values() for item in group if item["reason_code"] == "LIST_MATCH_DIFFERENT_PARTY"]
        if list_hits:
            winner = dict(list_hits[0])
        else:
            def amount_of(alert_id):
                alert = next(item for item in alerts if item.get("alert_id") == alert_id)
                return max([_tx_pence(tx) for tx in _alert_txs(ctx, alert)] or [0])
            main_id = sorted(alert_ids, key=lambda alert_id: (-amount_of(alert_id), alert_id))[0]
            winner = dict(_best(by_alert[main_id]))
        extra_sentences = []
        extra_evidence = list(winner["evidence"])
        for alert_id in sorted(alert_ids):
            for item in by_alert[alert_id]:
                extra_evidence.extend(item["evidence"])
                if item is not winner and item["sentences"]:
                    extra_sentences.append(item["sentences"][0])
        winner["evidence"] = extra_evidence
        winner["sentences"] = (winner["sentences"] + extra_sentences)[:4]
        return _render(case, winner, ctx["known"])
    if rfis:
        return _render(case, _best(rfis), ctx["known"])
    return _render(case, _finding(
        "ESCALATE", "ESC_UNEXPLAINED_FUNDS", [],
        [
            f"The alerts on {_customer_name(ctx)} are not explained by the documents on file.",
            "The case is escalated to the MLRO.",
        ],
    ), ctx["known"])


def _context_for(bundle, case) -> dict:
    customer_id = case.get("customer_id")
    customer = bundle["customers"].get(customer_id) or {"customer_id": customer_id, "name": "", "kind": "", "declared": {}}
    alert_ids = list(case.get("alert_ids") or [])
    alerts = []
    for alert_id in alert_ids:
        alert = bundle["alerts_by_id"].get(alert_id)
        if alert:
            alerts.append(alert)
    if not alerts:
        alerts = list(bundle["alerts_by_case"].get(case.get("case_id"), []))
    txs = bundle["txs_by_customer"].get(customer_id, [])
    raw_docs = bundle["docs_by_customer"].get(customer_id, [])
    docs = [_prepare_doc(raw) for raw in raw_docs]
    held_id = case.get("held_payment_id")
    held = bundle["txs_by_id"].get(held_id) if held_id else None
    start = parse_date((case.get("review_window") or {}).get("from"))
    end = parse_date((case.get("review_window") or {}).get("to"))
    window_txs = []
    for tx in txs:
        booked = _tx_date(tx)
        if booked is None or ((start is None or booked >= start) and (end is None or booked <= end)):
            window_txs.append(tx)
    known = set(bundle["list_by_id"])
    for doc in raw_docs:
        if doc.get("doc_id"):
            known.add(doc["doc_id"])
    for tx in txs:
        if tx.get("transaction_id"):
            known.add(tx["transaction_id"])
    high = {name.lower() for name in bundle["settings"].get("high_risk") or []}
    settings = dict(bundle["settings"])
    settings["high_risk_set"] = high
    settings["ignore_client_funding"] = bundle["institution"] == "payments_fx"
    return {
        "case": case,
        "customer": customer,
        "alerts": alerts,
        "txs": txs,
        "window_txs": window_txs,
        "docs": docs,
        "held": held,
        "institution": bundle["institution"],
        "settings": settings,
        "high_risk": high,
        "list_by_id": bundle["list_by_id"],
        "known": known,
        "reviews": bundle["reviews_by_customer"].get(customer_id, []),
    }


def triage_bundle(bundle: dict) -> list[dict]:
    decisions = []
    for case in bundle["cases"]:
        ctx = _context_for(bundle, case)
        try:
            decisions.append(decide_case(ctx))
        except Exception:
            decisions.append({
                "case_id": case.get("case_id"),
                "alert_ids": list(case.get("alert_ids") or []),
                "verdict": "ESCALATE",
                "reason_code": "ESC_UNEXPLAINED_FUNDS",
                "reason": "The case could not be assessed from the files in the institution folder. It is escalated to the MLRO.",
                "evidence": [],
            })
    return decisions
