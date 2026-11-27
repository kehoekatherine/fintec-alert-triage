(function () {
  const state = { items: [], filterInst: "all", filterVerdict: "all", query: "", index: 0, view: "board", pendingCaseId: "" };
  const queue = document.getElementById("queue");
  const caseView = document.getElementById("case-view");
  const boardView = document.getElementById("board-view");

  function visible() {
    const q = state.query.trim().toLowerCase();
    return state.items.filter(item => {
      if (state.filterInst !== "all" && item.institution !== state.filterInst) return false;
      if (state.filterVerdict !== "all" && item.decision.verdict !== state.filterVerdict) return false;
      if (!q) return true;
      const blob = `${item.case.case_id} ${item.customer.name} ${item.decision.reason_code}`.toLowerCase();
      return blob.includes(q);
    });
  }
  function pill(verdict) { return `<span class="pill ${verdict}">${verdict}</span>`; }
  function renderQueue() {
    const rows = visible();
    if (state.index >= rows.length) state.index = Math.max(0, rows.length - 1);
    queue.innerHTML = rows.map((item, i) => `
      <button class="qrow" role="option" data-i="${i}" aria-selected="${i === state.index ? "true" : "false"}" aria-current="${i === state.index ? "true" : "false"}">
        <strong>${escapeHtml(item.customer.name || item.case.case_id)}</strong>
        ${pill(item.decision.verdict)}
        <small>${escapeHtml(item.case.case_id)} · ${escapeHtml(item.institution)}</small>
        <small class="code">${escapeHtml(item.decision.reason_code)}</small>
      </button>`).join("") || `<p class="muted" style="padding:12px">No cases in this filter.</p>`;
    queue.querySelectorAll(".qrow").forEach(btn => btn.addEventListener("click", () => {
      state.index = Number(btn.dataset.i);
      state.pendingCaseId = rows[state.index]?.case?.case_id || "";
      state.view = "case";
      render();
      syncUrl(true);
    }));
  }
  function revealSelection() {
    const row = queue.querySelector('[aria-selected="true"]');
    if (!row) return;
    const pane = queue.getBoundingClientRect();
    const box = row.getBoundingClientRect();
    if (box.top < pane.top) queue.scrollTop -= pane.top - box.top;
    else if (box.bottom > pane.bottom) queue.scrollTop += box.bottom - pane.bottom;
  }
  function applyPendingCase() {
    if (!state.pendingCaseId) return;
    const rows = visible();
    const index = rows.findIndex(item => item.case.case_id === state.pendingCaseId);
    if (index >= 0) state.index = index;
  }
  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
  }
  function current() { return visible()[state.index]; }
  function overviewBlock() {
    return `<section class="about overview">
      <p class="brand-role">ClearPath AML</p>
      <h2>Alert Triage Assistant</h2>
      <p>AML means Anti-Money Laundering. ClearPath reads a transaction-monitoring alert against that firm's own policy and decides, the same way each time, whether to close it, ask for information, or escalate it to the MLRO. The reason cites the document, transaction or list entry it used. It is for AML analysts and compliance teams at banks, payment and FX firms, e-money providers and investment platforms.</p>
      <h3>How to use it</h3>
      <ol>
        <li>Open Case Analysis. The sample cases load on their own. To use another firm, choose Open folder and pick a folder that contains cases.jsonl.</li>
        <li>Click a case in the list, or in the cases at the top of About ClearPath AML. The row stays highlighted, including when you come from About or Model cost, and when you use the address bar or the back button.</li>
        <li>Read the statement, the reason and the next action before you do anything else.</li>
        <li>About ClearPath AML shows the counts for the list you are looking at, then this guide. Model cost is where the prices and the model comparison live.</li>
      </ol>
      <h3>How to read a case</h3>
      <ul>
        <li><strong>Statement.</strong> Rows run from oldest to newest, like a bank statement. Money in, in green, is a credit, deposit, top-up or funding receipt. Money out, in red, is a debit, withdrawal, card spend or payment sent. There is no running balance, because the file has no opening balance. A marker on a row means it is on an alert, or the decision cites it.</li>
        <li><strong>CLOSE.</strong> The activity is explained. There is nothing further to do. At the payments firm, the next action also says whether a held payment can be released.</li>
        <li><strong>RFI.</strong> Request for information. Ask for the document or detail named in the next action. Do this only when nothing in the case already looks like a crime.</li>
        <li><strong>ESCALATE.</strong> Send the case to the money-laundering reporting officer. Do not contact the customer about the activity. Once there is a sign of suspicion, the decision is escalate, not an RFI. Asking the customer would tip them off.</li>
        <li><strong>Reason code.</strong> The short policy label for why, such as a documented event or a list match that is a different person.</li>
        <li><strong>Evidence IDs.</strong> The document, transaction or list entry the decision relied on. In the case they link to that document.</li>
      </ul>
      <h3>The counts above</h3>
      <p>Those figures are the close, RFI and escalate cases in the list on screen, after your search and filters. They are not an official score, and they are not the hand-labelled sample. Click a name in that list to open the case.</p>
      <h3>Model cost</h3>
      <ul>
        <li><strong>Fitness</strong> is how much of the cost of escalating every case the system avoids. A wrongful close costs 10, so fitness matters more than a raw accuracy percentage.</li>
        <li><strong>Unsafe clearance</strong> is closing a case that should have been an RFI or an escalation.</li>
        <li><strong>Language-model prices</strong> are published token rates. Llama 3.3 70B is open weight, priced here as Groq's hosted service. DeepSeek V4.1 Flash is DeepSeek's hosted API price, read 27 Nov 2026, not a self-hosted GPU bill. No language model was run on these cases, so those accuracy cells stay empty.</li>
        <li><strong>Classical models</strong> are logistic regression, a decision tree, naive Bayes, nearest neighbours and a small neural network. They were trained to copy the rules engine, because the case files have no official labels. The rules engine is the one to use: on the 28 hand labels it scores fitness 100 with no unsafe close, it cites the evidence, and it does not vary. The nearest-neighbour model is 24 fitness points lower and has one unsafe close. Copying the engine is not an independent test of the policy.</li>
      </ul>
    </section>`;
  }
  function renderCase() {
    const item = current();
    if (!item) {
      caseView.innerHTML = `${overviewBlock()}<p class="muted">Choose Sample cases, or open an institution folder that contains cases.jsonl.</p>`;
      return;
    }
    const ev = new Set(item.decision.evidence || []);
    const cust = item.customer;
    const declared = cust.declared || {};
    const docs = (item.documents || []).map(doc => `<details class="doc ${ev.has(doc.doc_id) ? "hit" : ""}" id="doc-${escapeHtml(doc.doc_id)}">
          <summary><strong>${escapeHtml(doc.doc_id)}</strong> ${escapeHtml(doc.title || doc.type)}</summary>
          <pre class="muted" style="white-space:pre-wrap;padding:8px 4px;font-size:15px">${escapeHtml(doc.body || "")}</pre>
        </details>`).join("");
    caseView.innerHTML = `
      <div class="case-grid">
        <div class="span-2 hero">
          <div>
            <h2>${escapeHtml(cust.name || "Customer")}</h2>
            <p class="muted">${escapeHtml(item.case.case_id)} · ${escapeHtml(item.institution)} · ${escapeHtml(cust.kind || "")} · <span class="code">${escapeHtml(item.decision.reason_code)}</span></p>
          </div>
          <div>${pill(item.decision.verdict).replace('class="pill', 'class="pill hero-pill')}</div>
        </div>
        <div class="decision-row">
          <article class="why">
            <h3>Why</h3>
            <p class="code">${escapeHtml(item.decision.reason_code)}</p>
            <p>${escapeHtml(item.decision.reason)}</p>
            <p class="footnote">Evidence ${(item.decision.evidence || []).map(id => `<a href="#doc-${escapeHtml(id)}">${escapeHtml(id)}</a>`).join(", ") || "none cited"}</p>
          </article>
          <article class="action ${escapeHtml(item.decision.verdict)}">
            <h3>Next action</h3>
            <p>${escapeHtml(actionLine(item))}</p>
          </article>
        </div>
        <section class="card">
          <h3>Alerts</h3>
          ${(item.alerts || []).map(a => `<div class="row"><span>${escapeHtml(a.rule_id)}</span><div><strong>${escapeHtml(a.rule_name || "")}</strong><br><span class="muted">${escapeHtml(a.message || "")}</span></div></div>`).join("")}
        </section>
        <section class="card">
          <h3>Profile</h3>
          ${row("Kind", cust.kind)}
          ${row("Declared", declared.monthly_income_gbp ? "£" + Number(declared.monthly_income_gbp).toLocaleString("en-GB") + " / month" : (declared.annual_turnover_gbp ? "£" + Number(declared.annual_turnover_gbp).toLocaleString("en-GB") + " / year" : "—"))}
          ${row("Purpose", declared.account_purpose)}
          ${row("Countries", (declared.expected_countries || []).join(", "))}
          ${row("Window", (item.case.review_window || {}).from + " to " + (item.case.review_window || {}).to)}
        </section>
        <section class="card">
          <h3>Documents</h3>
          ${docs}
        </section>
        <section class="card span-2">
          <h3>Statement</h3>
          ${statementTable(item, ev)}
          <p class="footnote" style="padding:8px 16px 12px">Oldest first. In is a credit, deposit, top-up or funding receipt. Out is a debit, withdrawal, card spend or payment sent. No running balance: the file has no opening balance. A marker means the row is on an alert or cited as evidence.</p>
        </section>
      </div>`;
  }
  function row(k, v) { return `<div class="row"><span>${escapeHtml(k)}</span><div>${escapeHtml(v || "—")}</div></div>`; }
  const CHANNEL = {
    card: "card payment", bacs: "Bacs", faster_payment: "Faster Payment", swift: "SWIFT",
    cash_deposit: "cash deposit", cash_withdrawal: "cash withdrawal", cash_topup: "cash top-up",
    wallet_transfer: "wallet transfer", standing_order: "standing order", direct_debit: "direct debit",
    fund_subscription: "fund purchase", fund_redemption: "fund sale"
  };
  function moneyIn(tx) {
    const direction = String(tx.direction || "").toLowerCase();
    return direction === "in" || direction === "credit" || direction === "inbound";
  }
  function gbp(amount) {
    const n = Number(amount);
    if (!Number.isFinite(n)) return "";
    const sign = n < 0 ? "-" : "";
    const [whole, frac] = Math.abs(n).toFixed(2).split(".");
    return sign + "£" + whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",") + "." + frac;
  }
  function statementTable(item, evidence) {
    const alertByTx = {};
    (item.alerts || []).forEach(alert => {
      (alert.transaction_ids || []).forEach(id => {
        (alertByTx[id] = alertByTx[id] || []).push(alert.rule_id);
      });
    });
    const rows = (item.transactions || []).slice().sort((a, b) => String(a.booked_at).localeCompare(String(b.booked_at)) || String(a.transaction_id).localeCompare(String(b.transaction_id)));
    let totalIn = 0, totalOut = 0;
    const body = rows.map(tx => {
      const amount = Number(tx.amount_gbp) || 0;
      const inbound = moneyIn(tx);
      if (inbound) totalIn += amount; else totalOut += amount;
      const rules = [...new Set(alertByTx[tx.transaction_id] || [])];
      const cited = evidence.has(tx.transaction_id);
      const flagged = rules.length || cited;
      const channel = CHANNEL[tx.channel] || String(tx.channel || "").replace(/_/g, " ");
      const bits = [tx.counterparty_name, tx.counterparty_country, channel, tx.reference].filter(Boolean);
      const ruleLinks = rules.map(rule => `<span class="flag-link">${escapeHtml(rule)}</span>`).join(" ");
      const evidenceLink = cited ? ` <a class="flag-link" href="#doc-${escapeHtml((item.decision.evidence || []).find(id => String(id).match(/^[A-Z]+-/)) || "")}">Evidence ${escapeHtml(tx.transaction_id)}</a>` : "";
      return `<tr id="tx-${escapeHtml(tx.transaction_id)}" class="${cited ? "cited" : ""} ${rules.length ? "alerted" : ""}">
        <td>${escapeHtml(String(tx.booked_at || "").slice(0, 10))}</td>
        <td class="desc">${flagged ? `<span class="flag" title="Alerted or cited"></span>` : ""}${escapeHtml(bits.join(" · "))}${ruleLinks || evidenceLink ? `<small>${ruleLinks}${evidenceLink}</small>` : ""}</td>
        <td class="num in money-in">${inbound ? "+" + gbp(amount) : ""}</td>
        <td class="num out money-out">${inbound ? "" : "−" + gbp(amount)}</td>
      </tr>`;
    }).join("");
    const net = totalIn - totalOut;
    return `<div class="statement-wrap"><table class="statement">
      <thead><tr><th>Date</th><th>Description</th><th class="num">Money in</th><th class="num">Money out</th></tr></thead>
      <tbody>${body}</tbody>
      <tfoot>
        <tr><td colspan="2">Total</td><td class="num money-in">+${gbp(totalIn)}</td><td class="num money-out">−${gbp(totalOut)}</td></tr>
        <tr><td colspan="2">Net</td><td class="num" colspan="2">${net < 0 ? "−" : "+"}${gbp(Math.abs(net))}</td></tr>
      </tfoot>
    </table></div>`;
  }
  function actionLine(item) {
    if (item.decision.action) return item.decision.action;
    if (item.decision.verdict === "ESCALATE") return "Escalate to the MLRO. Do not contact the customer about this activity.";
    if (item.decision.verdict === "RFI") return item.decision.reason;
    return "No action — close.";
  }
  function renderBoard() {
    const rows = visible();
    const counts = { CLOSE: 0, RFI: 0, ESCALATE: 0 };
    rows.forEach(item => { counts[item.decision.verdict] = (counts[item.decision.verdict] || 0) + 1; });
    const n = rows.length || 1;
    const picks = rows.map((item, i) => `
      <li><button type="button" class="case-pick" data-i="${i}">
        <strong>${escapeHtml(item.customer.name || item.case.case_id)}</strong>
        <span class="code">${escapeHtml(item.case.case_id)}</span>
        ${pill(item.decision.verdict)}
      </button></li>`).join("");
    boardView.innerHTML = `
      <h2>Cases on screen</h2>
      <p class="muted">Verdict mix for the cases in the list, after search and filters. This is not an official score.</p>
      <div class="stats">
        <div class="stat"><b>${rows.length}</b><span class="muted">Cases</span></div>
        <div class="stat"><b style="color:var(--green)">${counts.CLOSE || 0}</b><span class="muted">Close</span></div>
        <div class="stat"><b style="color:var(--orange)">${counts.RFI || 0}</b><span class="muted">RFI</span></div>
        <div class="stat"><b style="color:var(--red)">${counts.ESCALATE || 0}</b><span class="muted">Escalate</span></div>
      </div>
      <div class="bar" aria-hidden="true">
        <i class="CLOSE" style="width:${(counts.CLOSE || 0) / n * 100}%"></i>
        <i class="RFI" style="width:${(counts.RFI || 0) / n * 100}%"></i>
        <i class="ESCALATE" style="width:${(counts.ESCALATE || 0) / n * 100}%"></i>
      </div>
      <ul class="case-picks" aria-label="Cases on screen">${picks || `<li class="muted">No cases in this filter.</li>`}</ul>
      ${overviewBlock()}`;
    boardView.querySelectorAll(".case-pick").forEach(btn => btn.addEventListener("click", () => {
      const index = Number(btn.dataset.i);
      state.index = index;
      state.pendingCaseId = rows[index]?.case?.case_id || "";
      state.view = "case";
      render();
      syncUrl(true);
    }));
  }
  function showView() {
    caseView.hidden = state.view !== "case";
    boardView.hidden = state.view !== "board";
    const cost = document.getElementById("cost-view");
    if (cost) cost.hidden = state.view !== "cost";
    document.getElementById("tab-case").setAttribute("aria-pressed", state.view === "case" ? "true" : "false");
    document.getElementById("tab-board").setAttribute("aria-pressed", state.view === "board" ? "true" : "false");
    document.getElementById("tab-cost").setAttribute("aria-pressed", state.view === "cost" ? "true" : "false");
  }
  function render() {
    applyPendingCase();
    renderQueue();
    showView();
    if (state.view === "board") renderBoard();
    else if (state.view === "case") renderCase();
    revealSelection();
  }
  function viewParam() {
    if (state.view === "board") return "about";
    if (state.view === "cost") return "cost";
    return "analysis";
  }
  function syncUrl(push) {
    const params = new URLSearchParams(location.search);
    params.delete("shot");
    params.set("view", viewParam());
    const id = current()?.case?.case_id || "";
    if (id) params.set("case", id);
    else params.delete("case");
    const next = `${location.pathname}?${params.toString()}${location.hash}`;
    const data = { view: state.view, caseId: id };
    if (push) history.pushState(data, "", next);
    else history.replaceState(data, "", next);
  }
  function applyLocation() {
    const params = new URLSearchParams(location.search);
    const view = params.get("view");
    if (view === "about" || view === "summary") state.view = "board";
    else if (view === "cost") state.view = "cost";
    else if (view === "analysis" || view === "case" || view === "queue") state.view = "case";
    state.pendingCaseId = params.get("case") || state.pendingCaseId || "";
  }
  function addItems(list) { state.items = list; state.index = 0; render(); }
  async function loadSample() {
    const response = await fetch("data/sample.json");
    const data = await response.json();
    const items = [];
    Object.entries(data.institutions).forEach(([inst, rows]) => {
      rows.forEach(row => items.push({ institution: inst, source: "python", ...row }));
    });
    addItems(items);
  }
  function parseJsonl(text) {
    return text.split(/\n/).map(line => line.trim()).filter(Boolean).map(line => JSON.parse(line));
  }
  async function loadFolder(fileList) {
    const files = [...fileList];
    const textOf = async file => file.text();
    const byName = new Map(files.map(file => [file.webkitRelativePath || file.name, file]));
    const groups = new Map();
    for (const file of files) {
      const rel = file.webkitRelativePath || file.name;
      const parts = rel.split("/");
      const idx = parts.lastIndexOf("cases.jsonl");
      if (idx === -1) continue;
      const dir = parts.slice(0, idx).join("/");
      groups.set(dir, file);
    }
    if (!groups.size) { alert("No cases.jsonl in that folder."); return; }
    const items = [];
    for (const [dir, caseFile] of groups) {
      const read = async name => {
        const file = byName.get(dir ? `${dir}/${name}` : name);
        return file ? parseJsonl(await file.text()) : [];
      };
      const cases = parseJsonl(await caseFile.text());
      const institution = (cases[0] && cases[0].institution) || dir.split("/").pop();
      const payload = {
        institution,
        cases,
        alerts: await read("alerts.jsonl"),
        customers: await read("customers.jsonl"),
        transactions: await read("transactions.jsonl"),
        evidence: await read("evidence.jsonl"),
        internal_list: await read("internal_list.jsonl")
      };
      const evidenceFiles = new Map();
      for (const [path, file] of byName) {
        if (path.startsWith(dir) && path.endsWith(".md")) evidenceFiles.set(path.slice(dir.length + 1), file);
      }
      for (const doc of payload.evidence) {
        const file = evidenceFiles.get(doc.file);
        doc.body = file ? await textOf(file) : "";
      }
      const decisions = TriageEngine.triageInstitution(payload);
      const byId = Object.fromEntries(decisions.map(d => [d.case_id, d]));
      const customers = Object.fromEntries(payload.customers.map(c => [c.customer_id, c]));
      const alerts = Object.fromEntries(payload.alerts.map(a => [a.alert_id, a]));
      const txs = {};
      payload.transactions.forEach(t => { (txs[t.customer_id] = txs[t.customer_id] || []).push(t); });
      const docs = {};
      payload.evidence.forEach(d => { (docs[d.customer_id] = docs[d.customer_id] || []).push(d); });
      cases.forEach(c => {
        const start = c.review_window.from, end = c.review_window.to;
        items.push({
          institution,
          case: c,
          customer: customers[c.customer_id] || { name: c.customer_id },
          alerts: (c.alert_ids || []).map(id => alerts[id]).filter(Boolean),
          transactions: (txs[c.customer_id] || []).filter(t => t.booked_at.slice(0, 10) >= start && t.booked_at.slice(0, 10) <= end).slice(0, 80),
          documents: docs[c.customer_id] || [],
          decision: byId[c.case_id],
          source: "browser"
        });
      });
    }
    addItems(items);
  }
  function download(name, text, type) {
    const blob = new Blob([text], { type });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = name; a.click();
    URL.revokeObjectURL(url);
  }
  function exportJsonl() {
    const lines = state.items.map(item => JSON.stringify(item.decision));
    download("triage.jsonl", lines.join("\n") + "\n", "application/jsonl");
  }
  function exportCsv() {
    const header = ["case_id", "alert_ids", "verdict", "reason_code", "reason", "evidence"];
    const lines = [header.join(",")].concat(state.items.map(item => {
      const d = item.decision;
      const cells = [d.case_id, (d.alert_ids || []).join("|"), d.verdict, d.reason_code, d.reason, (d.evidence || []).join("|")];
      return cells.map(value => `"${String(value).replace(/"/g, '""')}"`).join(",");
    }));
    download("triage.csv", lines.join("\n"), "text/csv");
  }

  document.getElementById("load-sample").addEventListener("click", loadSample);
  document.getElementById("folder").addEventListener("change", event => loadFolder(event.target.files));
  document.getElementById("export-jsonl").addEventListener("click", exportJsonl);
  document.getElementById("export-csv").addEventListener("click", exportCsv);
  function keepSelection() {
    const index = visible().findIndex(item => item.case.case_id === state.pendingCaseId);
    state.index = index >= 0 ? index : 0;
  }
  document.getElementById("search").addEventListener("input", event => {
    state.pendingCaseId = current()?.case?.case_id || state.pendingCaseId;
    state.query = event.target.value;
    keepSelection();
    render();
    syncUrl(false);
  });
  document.querySelectorAll("[data-inst]").forEach(btn => btn.addEventListener("click", () => {
    state.pendingCaseId = current()?.case?.case_id || state.pendingCaseId;
    state.filterInst = btn.dataset.inst;
    document.querySelectorAll("[data-inst]").forEach(el => el.setAttribute("aria-pressed", el === btn ? "true" : "false"));
    keepSelection();
    render();
    syncUrl(true);
  }));
  document.querySelectorAll("[data-verdict]").forEach(btn => btn.addEventListener("click", () => {
    state.pendingCaseId = current()?.case?.case_id || state.pendingCaseId;
    state.filterVerdict = btn.dataset.verdict;
    document.querySelectorAll("[data-verdict]").forEach(el => el.setAttribute("aria-pressed", el === btn ? "true" : "false"));
    keepSelection();
    render();
    syncUrl(true);
  }));
  document.getElementById("tab-case").addEventListener("click", () => { state.view = "case"; render(); syncUrl(true); });
  document.getElementById("tab-board").addEventListener("click", () => { state.view = "board"; render(); syncUrl(true); });
  document.getElementById("tab-cost").addEventListener("click", () => { state.view = "cost"; render(); syncUrl(true); });
  document.querySelectorAll("[data-theme]").forEach(btn => btn.addEventListener("click", () => {
    const theme = btn.dataset.theme;
    if (theme === "system") document.documentElement.removeAttribute("data-theme");
    else document.documentElement.setAttribute("data-theme", theme);
    document.querySelectorAll("[data-theme]").forEach(el => el.setAttribute("aria-pressed", el === btn ? "true" : "false"));
  }));
  queue.addEventListener("keydown", event => {
    const rows = visible();
    if (event.key === "ArrowDown") { state.index = Math.min(rows.length - 1, state.index + 1); state.pendingCaseId = rows[state.index]?.case?.case_id || ""; state.view = "case"; render(); syncUrl(true); event.preventDefault(); }
    if (event.key === "ArrowUp") { state.index = Math.max(0, state.index - 1); state.pendingCaseId = rows[state.index]?.case?.case_id || ""; state.view = "case"; render(); syncUrl(true); event.preventDefault(); }
  });
  const params = new URLSearchParams(location.search);
  if (params.get("theme") === "dark" || params.get("theme") === "light") {
    document.documentElement.setAttribute("data-theme", params.get("theme"));
  }
  if (params.get("verdict")) state.filterVerdict = params.get("verdict");
  if (params.get("shot") === "tradeoffs" || params.get("shot") === "charts") state.view = "cost";
  applyLocation();
  window.addEventListener("popstate", () => { applyLocation(); render(); });
  loadSample().then(() => {
    if (params.get("verdict")) {
      document.querySelectorAll("[data-verdict]").forEach(el => el.setAttribute("aria-pressed", el.dataset.verdict === params.get("verdict") ? "true" : "false"));
    }
    render();
    syncUrl(false);
    const shot = params.get("shot");
    const shotNode = shot === "tradeoffs" ? document.getElementById("tradeoffs")
      : shot === "charts" ? document.getElementById("cost-chart")
      : shot === "overview" ? document.querySelector(".overview") : null;
    if (shotNode) shotNode.scrollIntoView({ block: "start" });
  }).catch(err => { caseView.innerHTML = `<p>Could not load the sample cases (${escapeHtml(err.message)}).</p>`; });
})();
