/* Browser port of the rules engine. Bundled sample decisions are produced by
   triage/engine.py and shipped in sample.json. This file triages a folder the
   analyst loads locally, using the same verdict order: escalate, then RFI,
   then close only when every alert is explained. */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.TriageEngine = api;
})(typeof self !== "undefined" ? self : this, function () {
  const PRIORITY = {
    ESC_LISTED_PARTY: 10, ESC_MULE_ACTIVITY: 20, ESC_ACCOUNT_MISUSE: 30, ESC_STRUCTURING: 40,
    ESC_INVOICE_MISMATCH: 50, ESC_HIGH_RISK_CORRIDOR: 60, ESC_THIRD_PARTY_FUNDING: 70,
    ESC_EARLY_REDEMPTION: 80, ESC_PASS_THROUGH: 90, ESC_UNEXPLAINED_FUNDS: 100,
    RFI_COUNTERPARTY_DETAILS: 10, RFI_SOURCE_OF_FUNDS: 20, RFI_PURPOSE_DOCUMENTS: 30,
    RFI_BUSINESS_RECORDS: 40, RFI_UPDATED_PROFILE: 50,
    LIST_MATCH_DIFFERENT_PARTY: 15, INVOICE_MATCHES: 20, REFUND_OF_EARLIER_PAYMENT: 25,
    OWN_ACCOUNT: 30, GROUP_COMPANY: 35, THIRD_PARTY_DOCUMENTED: 40, COLLECTION_DOCUMENTED: 45,
    BUSINESS_RECORDS_RECONCILE: 50, DOCUMENTED_EVENT: 55, RETURNING_CUSTOMER: 60, WITHIN_PROFILE: 70
  };
  const HIGH = ["afghanistan","belarus","democratic republic of the congo","haiti","iran","iraq","lebanon","libya","mali","myanmar","north korea","russia","somalia","south sudan","syria","venezuela","yemen"];

  function gbp(p) {
    const n = Math.round(Number(p) || 0);
    const sign = n < 0 ? "-" : "";
    const v = Math.abs(n) / 100;
    const [a, b] = v.toFixed(2).split(".");
    return sign + "£" + a.replace(/\B(?=(\d{3})+(?!\d))/g, ",") + "." + b;
  }
  function pence(v) {
    if (v == null || v === "") return 0;
    const n = Number(String(v).replace(/£/g, "").replace(/,/g, ""));
    return Number.isFinite(n) ? Math.round(n * 100) : 0;
  }
  function near(a, b) {
    if (a == null || b == null) return false;
    const d = Math.abs(a - b);
    return d <= 100 || d / Math.max(Math.abs(a), Math.abs(b), 1) <= 0.02;
  }
  function tokens(name) {
    return String(name || "").toLowerCase().replace(/&/g, " and ").replace(/[^a-z0-9 ]/g, " ")
      .split(/\s+/).filter(t => t && !["ltd","limited","plc","llp","inc","llc","co","company","the","of","and","uk"].includes(t));
  }
  function namesMatch(a, b) {
    const ta = tokens(a), tb = tokens(b);
    if (!ta.length || !tb.length) return false;
    if (ta.join(" ") === tb.join(" ")) return true;
    const sa = new Set(ta), sb = new Set(tb);
    const [sh, lg] = sa.size <= sb.size ? [sa, sb] : [sb, sa];
    if (sh.size >= 2 && [...sh].every(t => lg.has(t))) return true;
    return false;
  }
  function moneyAmounts(text) {
    const out = [];
    const re = /£\s*([\d,]+(?:\.\d{2})?)/g;
    let m;
    while ((m = re.exec(text || ""))) out.push(pence(m[1]));
    return out;
  }
  function finding(verdict, code, evidence, sentences, alertId, amount) {
    const ev = [];
    (evidence || []).forEach(id => { if (id && !ev.includes(id)) ev.push(id); });
    const text = (sentences || []).filter(Boolean).slice(0, 4).map(s => /[.]$/.test(s.trim()) ? s.trim() : s.trim() + ".");
    return { verdict, reason_code: code, priority: PRIORITY[code], alert_id: alertId || null, evidence: ev, sentences: text, amount: amount || 0 };
  }
  function prepare(raw) {
    const text = raw.body || "";
    const low = text.toLowerCase();
    const flags = new Set();
    if (raw.type === "sending_bank_fraud_report" || low.includes("push payment scam")) flags.add("fraud");
    if (low.includes("send it on") || low.includes("payments assistant") || (low.includes("crypto") && low.includes("account"))) flags.add("mule");
    if (raw.type === "staff_note" && /exempt|threshold|aggregation|aggregated/.test(low)) flags.add("structuring");
    if (raw.type === "staff_note" && /unwilling|unable|evasive|declined to|refused to|no clear explanation/.test(low)) flags.add("contradiction");
    return { id: raw.doc_id, type: raw.type, title: raw.title || "", text, low, amounts: moneyAmounts(text), flags };
  }
  function docsOf(ctx, ...types) {
    const want = new Set(types);
    return ctx.docs.filter(d => want.has(d.type));
  }
  function alertTxs(ctx, alert) {
    const ids = new Set(alert.transaction_ids || []);
    return ctx.txs.filter(t => ids.has(t.transaction_id));
  }
  function baseline(ctx) {
    for (const a of ctx.alerts) {
      if (a.rule_id === "TM01" && a.measured && a.measured.baseline_gbp) return pence(a.measured.baseline_gbp);
    }
    const d = (ctx.customer.declared || {});
    if (d.monthly_income_gbp) return pence(d.monthly_income_gbp);
    if (d.annual_turnover_gbp) return Math.round(pence(d.annual_turnover_gbp) / 12);
    return null;
  }
  function basis(ctx) {
    if (ctx.institution === "payments_fx") return "expected monthly payment volume";
    return ctx.customer.kind === "business" ? "declared monthly turnover" : "declared monthly income";
  }
  function sum(txs) { return txs.reduce((s, t) => s + pence(t.amount_gbp), 0); }
  function largest(txs) { return txs.slice().sort((a, b) => pence(b.amount_gbp) - pence(a.amount_gbp))[0]; }
  function isClient(ctx, tx) { return namesMatch(tx.counterparty_name, ctx.customer.name); }
  function high(country) { return HIGH.includes(String(country || "").toLowerCase()); }
  function mentions(doc, name) {
    if (!name) return false;
    if (namesMatch(name, doc.title)) return true;
    return tokens(name).length >= 2 && (doc.text + " " + doc.title).toLowerCase().includes(String(name).toLowerCase());
  }
  function eventDoc(ctx, amount) {
    const types = ["solicitor_letter","insurer_letter","vehicle_sale_receipt","sale_receipt","loan_agreement","settlement_letter","completion_statement"];
    return ctx.docs.find(d => types.includes(d.type) && d.amounts.some(a => near(a, amount))) || null;
  }
  function refundDoc(ctx, tx) {
    const types = ["credit_note","venue_email","deposit_return_letter","cancellation_confirmation"];
    return ctx.docs.find(d => (types.includes(d.type) || d.low.includes("refund")) && d.amounts.some(a => near(a, pence(tx.amount_gbp))) && (types.includes(d.type) || mentions(d, tx.counterparty_name))) || null;
  }
  function best(list) {
    return list.slice().sort((a, b) => a.priority - b.priority || b.amount - a.amount || String(a.alert_id||"").localeCompare(String(b.alert_id||"")))[0];
  }

  function explainList(alert, ctx) {
    const m = alert.measured || {};
    const entry = (ctx.list || {})[m.list_entry] || null;
    const txs = alertTxs(ctx, alert);
    const tx = largest(txs);
    const amount = tx ? pence(tx.amount_gbp) : 0;
    const person = String(m.matched_party || (entry && entry.name) || "").split(",")[0];
    const on = String(m.matched_on || "").toLowerCase();
    const strong = (on.includes("name") + (on.includes("birth") || on.includes("date of birth")) + on.includes("address") + on.includes("identifier")) >= 2
      || (on.includes("birth") && on.includes("name"));
    const evidence = [m.list_entry, tx && tx.transaction_id];
    if (!entry) {
      return finding("RFI", "RFI_COUNTERPARTY_DETAILS", evidence, [
        `The list alert names ${person || "a counterparty"}, but no list entry is in the file.`,
        `Ask ${ctx.customer.name} who the counterparty is, with a date of birth or identifier.`
      ], alert.alert_id, amount);
    }
    if (strong || on.includes("birth")) {
      return finding("ESCALATE", "ESC_LISTED_PARTY", evidence, [
        `${person} matches internal list entry ${entry.entry_id} on ${m.matched_on}.`,
        `The payment of ${gbp(amount)} was to ${tx ? tx.counterparty_name : "the counterparty"}.`,
        `The listed party is the counterparty or still controls it, so ${ctx.customer.name}'s case is escalated to the MLRO.`
      ], alert.alert_id, amount);
    }
    const register = ctx.docs.find(d => d.type === "company_register_extract" || d.type === "significant_control_register");
    if (register && !register.low.includes(String(entry.name || "").toLowerCase())) {
      const receipt = ctx.docs.find(d => d.type === "receipt" || d.type === "invoice");
      return finding("CLOSE", "LIST_MATCH_DIFFERENT_PARTY", evidence.concat([register.id, receipt && receipt.id]), [
        `The payment of ${gbp(amount)} matched ${entry.name} (${entry.entry_id}) on one detail only: ${m.matched_on}.`,
        `${register.id} shows the payee is a separate party, not ${entry.name}.`,
        receipt ? `Receipt ${receipt.id} matches the payment.` : `A single detail does not prove they are the same party.`
      ], alert.alert_id, amount);
    }
    return finding("RFI", "RFI_COUNTERPARTY_DETAILS", evidence, [
      `${ctx.customer.name}'s payment of ${gbp(amount)} matched list entry ${entry.entry_id} (${entry.name}) on ${m.matched_on || "one detail"} only.`,
      `Nothing on file shows whether this is the listed party.`,
      `Ask ${ctx.customer.name} who the counterparty is and for a date of birth or identifier.`
    ], alert.alert_id, amount);
  }

  function explainGeneric(alert, ctx) {
    const txs = alertTxs(ctx, alert);
    const total = (alert.measured && (alert.measured.received_gbp || alert.measured.total_gbp)) ? pence(alert.measured.received_gbp || alert.measured.total_gbp) : sum(txs);
    const base = baseline(ctx);
    const name = ctx.customer.name;
    const rule = alert.rule_id;
    const evidence = txs.slice(0, 6).map(t => t.transaction_id);
    if (rule === "TM22") return explainList(alert, ctx);
    if (rule === "TM03" || ctx.docs.some(d => d.flags.has("structuring"))) {
      if (ctx.docs.some(d => d.flags.has("structuring"))) {
        const note = ctx.docs.find(d => d.flags.has("structuring"));
        return finding("ESCALATE", "ESC_STRUCTURING", evidence.concat(note.id), [
          `${name} has repeated amounts just under a threshold.`,
          `Staff note ${note.id} records questions about staying under the threshold.`,
          `The case is escalated to the MLRO.`
        ], alert.alert_id, total);
      }
      const records = ctx.docs.find(d => ["takings_book","cash_collection_reports","cash_reconciliation","contract_schedule"].includes(d.type));
      if (records) {
        const code = records.type === "contract_schedule" ? "INVOICE_MATCHES" : "BUSINESS_RECORDS_RECONCILE";
        return finding("CLOSE", code, evidence.concat(records.id), [
          `${name}'s repeated amounts are covered by ${records.title} (${records.id}).`,
          `The pattern is documented, so the alert is closed.`
        ], alert.alert_id, total);
      }
      return finding("ESCALATE", "ESC_STRUCTURING", evidence, [
        `${name} has repeated transactions just under a threshold and no reconciling record.`,
        `The case is escalated to the MLRO.`
      ], alert.alert_id, total);
    }
    if (ctx.docs.some(d => d.flags.has("fraud") || d.flags.has("mule"))) {
      const doc = ctx.docs.find(d => d.flags.has("fraud") || d.flags.has("mule"));
      return finding("ESCALATE", "ESC_MULE_ACTIVITY", evidence.concat(doc.id), [
        `${name}'s account received other people's money and moved it on.`,
        `${doc.title} (${doc.id}) records that.`,
        `The case is escalated to the MLRO.`
      ], alert.alert_id, total);
    }
    if (rule === "TM06") {
      const risky = txs.filter(t => high(t.counterparty_country));
      const doc = ctx.docs.find(d => ["invoice","supplier_invoice","hospital_invoice","supply_contract","venue_email","credit_note"].includes(d.type) && d.amounts.some(a => risky.some(t => near(a, pence(t.amount_gbp)))));
      if (ctx.docs.some(d => d.flags.has("contradiction"))) {
        return finding("ESCALATE", "ESC_INVOICE_MISMATCH", evidence.concat(ctx.docs.find(d => d.flags.has("contradiction")).id), [
          `${name} moved ${gbp(total)} to or from a higher-risk country.`,
          `A staff note says the customer did not explain that routing.`,
          `The case is escalated to the MLRO.`
        ], alert.alert_id, total);
      }
      if (doc) {
        const code = ["venue_email","credit_note"].includes(doc.type) ? "REFUND_OF_EARLIER_PAYMENT" : "INVOICE_MATCHES";
        return finding("CLOSE", code, evidence.concat(doc.id), [
          `${name} moved ${gbp(total)} on a higher-risk corridor.`,
          `${doc.title} (${doc.id}) matches the payment.`,
          `The corridor alert is closed.`
        ], alert.alert_id, total);
      }
      return finding("ESCALATE", "ESC_HIGH_RISK_CORRIDOR", evidence, [
        `${name} moved ${gbp(total)} to or from a higher-risk country that was not declared.`,
        `No invoice or refund on file explains it.`,
        `The case is escalated to the MLRO rather than put to the customer.`
      ], alert.alert_id, total);
    }
    const top = largest(txs.filter(t => t.direction === "in").concat(txs));
    if (top) {
      const ev = eventDoc(ctx, pence(top.amount_gbp)) || eventDoc(ctx, total);
      if (ev) return finding("CLOSE", "DOCUMENTED_EVENT", evidence.concat(ev.id), [
        `${name} received ${gbp(total || pence(top.amount_gbp))} against ${basis(ctx)} of ${gbp(base)}.`,
        `${ev.title} (${ev.id}) documents that amount.`,
        `The alert is closed.`
      ], alert.alert_id, total);
      const refund = refundDoc(ctx, top);
      if (refund) return finding("CLOSE", "REFUND_OF_EARLIER_PAYMENT", evidence.concat(refund.id), [
        `${name} received ${gbp(pence(top.amount_gbp))} from ${top.counterparty_name}.`,
        `${refund.title} (${refund.id}) documents it as a refund.`,
        `The alert is closed.`
      ], alert.alert_id, pence(top.amount_gbp));
    }
    if (rule === "TM10") {
      const letter = ctx.docs.find(d => d.type === "redundancy_letter" || d.type === "cancellation_confirmation");
      if (letter) {
        const code = letter.type === "cancellation_confirmation" ? "REFUND_OF_EARLIER_PAYMENT" : "DOCUMENTED_EVENT";
        return finding("CLOSE", code, evidence.concat(letter.id), [
          `${name} sold a holding within the early-redemption window.`,
          `${letter.title} (${letter.id}) documents why.`,
          `The alert is closed.`
        ], alert.alert_id, total);
      }
      return finding("ESCALATE", "ESC_EARLY_REDEMPTION", evidence, [
        `${name} bought fund units and sold them within 90 days, moving ${gbp(total)}.`,
        `No redundancy letter or cancellation explains it.`,
        `The case is escalated to the MLRO.`
      ], alert.alert_id, total);
    }
    if (rule === "TM07") {
      const rev = ctx.docs.find(d => d.type === "reverification_record");
      if (rev) return finding("CLOSE", "RETURNING_CUSTOMER", evidence.concat(rev.id), [
        `${name}'s dormant account became active again.`,
        `Identity re-verification ${rev.id} confirms the customer.`,
        `The alert is closed.`
      ], alert.alert_id, total);
    }
    if (rule === "TM08") {
      const payer = top && top.counterparty_name;
      const group = ctx.docs.find(d => d.type === "group_structure" && payer && d.low.includes(String(payer).toLowerCase().replace(/ ltd$/, "")));
      if (group) return finding("CLOSE", "GROUP_COMPANY", evidence.concat(group.id), [
        `${payer} paid ${name}.`,
        `Group structure ${group.id} links them.`,
        `The third-party alert is closed.`
      ], alert.alert_id, total);
      const gift = ctx.docs.find(d => d.type === "gift_letter");
      if (gift) return finding("CLOSE", "THIRD_PARTY_DOCUMENTED", evidence.concat(gift.id), [
        `${payer || "A third party"} paid ${gbp(total)} to ${name}.`,
        `Gift letter ${gift.id} documents it.`,
        `The alert is closed.`
      ], alert.alert_id, total);
    }
    if (rule === "TM02" && ctx.docs.some(d => d.type === "booking_confirmation")) {
      const doc = ctx.docs.find(d => d.type === "booking_confirmation");
      return finding("CLOSE", "COLLECTION_DOCUMENTED", evidence.concat(doc.id), [
        `${name} received money from many people and paid it on.`,
        `${doc.title} (${doc.id}) names the travellers.`,
        `The payments are a documented collection.`
      ], alert.alert_id, total);
    }
    if (rule === "TM02" || rule === "TM04") {
      if ((ctx.alerts || []).some(a => a.rule_id === "TM04") && (ctx.alerts || []).some(a => a.rule_id === "TM02") && !ctx.docs.some(d => ["booking_confirmation","payment_file","takings_book"].includes(d.type))) {
        return finding("ESCALATE", "ESC_MULE_ACTIVITY", evidence, [
          `${name} was paid by many people and the money left quickly.`,
          `No collection record explains it.`,
          `The case is escalated to the MLRO.`
        ], alert.alert_id, total);
      }
    }
    if (rule === "TM01" || rule === "TM05" || rule === "TM08") {
      const code = ctx.customer.kind === "business" && rule === "TM05" ? "RFI_BUSINESS_RECORDS" : "RFI_SOURCE_OF_FUNDS";
      return finding("RFI", code, evidence, [
        `${name} has ${gbp(total)} that is not covered by a document on file, against ${basis(ctx)} of ${gbp(base)}.`,
        `Nothing in the case yet shows a scam, a confirmed list match, or that the money was passed on.`,
        `Ask ${name} for proof of where the money came from.`
      ], alert.alert_id, total);
    }
    return finding("RFI", "RFI_PURPOSE_DOCUMENTS", evidence, [
      `Alert ${alert.alert_id} (${rule}) is not explained by the documents on file.`,
      `Ask ${name} for documents showing what the payments were for.`
    ], alert.alert_id, total);
  }

  function triageCase(ctx) {
    const findings = [];
    if (ctx.docs.some(d => d.flags.has("fraud") || d.flags.has("mule"))) {
      const doc = ctx.docs.find(d => d.flags.has("fraud") || d.flags.has("mule"));
      findings.push(finding("ESCALATE", "ESC_MULE_ACTIVITY", [doc.id], [
        `${ctx.customer.name}'s account took in other people's money and moved it on.`,
        `${doc.title} (${doc.id}) is the record relied on.`,
        `The case is escalated to the MLRO.`
      ], null, 0));
    }
    const device = ctx.docs.find(d => d.type === "device_export");
    if (device && (device.text.match(/[A-Z]{2,3}-C\d+/g) || []).filter((v, i, a) => a.indexOf(v) === i).length >= 2) {
      findings.push(finding("ESCALATE", "ESC_ACCOUNT_MISUSE", [device.id], [
        `Device export ${device.id} shows one device logged into more than one customer.`,
        `Somebody other than ${ctx.customer.name} appears to be using the account.`,
        `The case is escalated to the MLRO.`
      ], null, 0));
    }
    for (const alert of ctx.alerts) findings.push(explainGeneric(alert, ctx));
    const escalations = findings.filter(f => f.verdict === "ESCALATE");
    let winner;
    if (escalations.length) winner = best(escalations);
    else {
      const rfis = findings.filter(f => f.verdict === "RFI");
      const ids = ctx.alerts.map(a => a.alert_id);
      const closed = ids.length && ids.every(id => findings.some(f => f.verdict === "CLOSE" && f.alert_id === id));
      if (closed && !rfis.length) {
        const closes = findings.filter(f => f.verdict === "CLOSE");
        winner = closes.find(f => f.reason_code === "LIST_MATCH_DIFFERENT_PARTY") || best(closes);
        winner = Object.assign({}, winner, { evidence: closes.flatMap(f => f.evidence), sentences: closes.flatMap(f => f.sentences).slice(0, 4) });
      } else if (rfis.length) winner = best(rfis);
      else winner = finding("ESCALATE", "ESC_UNEXPLAINED_FUNDS", [], [
        `The alerts on ${ctx.customer.name} are not explained.`,
        `The case is escalated to the MLRO.`
      ]);
    }
    const known = new Set(ctx.docs.map(d => d.id).concat(ctx.txs.map(t => t.transaction_id), Object.keys(ctx.list || {})));
    const evidence = [...new Set(winner.evidence.filter(id => known.has(id)))].sort();
    const decision = {
      case_id: ctx.case.case_id,
      alert_ids: ctx.case.alert_ids || ctx.alerts.map(a => a.alert_id),
      verdict: winner.verdict,
      reason_code: winner.reason_code,
      reason: winner.sentences.join(" "),
      evidence
    };
    decision.action = browserAction(ctx, decision);
    return decision;
  }

  function browserAction(ctx, decision) {
    const held = (ctx.txs || []).find(tx => tx.transaction_id === (ctx.case.held_payment_id));
    const hold = held && ctx.institution === "payments_fx"
      ? `Keep the held payment of ${gbp(Math.round(Number(held.amount_gbp) * 100))} to ${held.counterparty_name || "the beneficiary"} held. `
      : "";
    const release = held && ctx.institution === "payments_fx"
      ? ` and release the held payment of ${gbp(Math.round(Number(held.amount_gbp) * 100))} to ${held.counterparty_name || "the beneficiary"}.`
      : ".";
    if (decision.verdict === "CLOSE") return "No action — close" + (release === "." ? "." : release);
    if (decision.verdict === "ESCALATE") {
      return `${hold}Escalate to the MLRO. Key evidence: ${decision.evidence.slice(0, 6).join(", ") || "the alerted transactions"}. Do not contact the customer about this activity.`;
    }
    return `${hold}Request the missing document from ${ctx.customer.name}. ${decision.reason}`;
  }

  function triageInstitution(payload) {
    const list = {};
    (payload.internal_list || []).forEach(row => { list[row.entry_id] = row; });
    const alertsByCase = {};
    (payload.alerts || []).forEach(a => { (alertsByCase[a.case_id] = alertsByCase[a.case_id] || []).push(a); });
    const txsBy = {};
    (payload.transactions || []).forEach(t => { (txsBy[t.customer_id] = txsBy[t.customer_id] || []).push(t); });
    const docsBy = {};
    (payload.evidence || []).forEach(d => { (docsBy[d.customer_id] = docsBy[d.customer_id] || []).push(prepare(d)); });
    const customers = {};
    (payload.customers || []).forEach(c => { customers[c.customer_id] = c; });
    return (payload.cases || []).map(c => triageCase({
      case: c,
      customer: customers[c.customer_id] || { name: "", kind: "", declared: {} },
      alerts: (c.alert_ids || []).map(id => (payload.alerts || []).find(a => a.alert_id === id)).filter(Boolean).concat(alertsByCase[c.case_id] || []).filter((a, i, arr) => arr.findIndex(x => x.alert_id === a.alert_id) === i),
      txs: txsBy[c.customer_id] || [],
      docs: docsBy[c.customer_id] || [],
      list,
      institution: payload.institution || (c.institution) || ""
    }));
  }

  return { triageCase, triageInstitution, gbp, PRIORITY };
});
