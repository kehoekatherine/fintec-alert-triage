(function () {
  const $ = id => document.getElementById(id);
  let models = [];
  let payload = null;
  let labels = null;

  const inputs = [
    ["usdPerGbp", "GBP per 1 USD (assumption)", 0.79, 0.5, 1.2, 0.01],
    ["outputTokens", "Output tokens per case (assumption)", 400, 50, 2000, 10],
    ["monthly", "Alerts per month", 4000, 200, 50000, 100],
    ["minutes", "Analyst minutes per case a person still reviews", 12, 1, 60, 1],
    ["rate", "Analyst cost, GBP per hour (assumption)", 48, 15, 150, 1],
    ["closeShare", "Share of alerts the rules engine closes", 0.45, 0, 1, 0.01],
    ["heldValue", "Assumed GBP value of releasing one held payment", 0, 0, 500, 1]
  ];

  function field(spec) {
    const [key, label, value, min, max, step] = spec;
    return `<label class="field">${label}<input id="${key}" type="number" min="${min}" max="${max}" step="${step}" value="${value}"></label>`;
  }

  function num(id) { return Number($(id).value); }
  function money(n, digits = 2) {
    const sign = n < 0 ? "-" : "";
    return sign + "£" + Math.abs(n).toLocaleString("en-GB", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  }
  function usd(n) {
    return "$" + n.toLocaleString("en-GB", { minimumFractionDigits: n < 1 ? 4 : 2, maximumFractionDigits: n < 1 ? 4 : 2 });
  }

  function renderBaseline() {
    $("label-warning").textContent = labels.warning;
    const score = labels.score;
    const unsafe = labels.labels.filter(row => row.verdict !== "CLOSE" && row.engine_verdict === "CLOSE").length;
    $("baseline-stats").innerHTML = [
      ["Fitness", score.verdict_points, "out of 100 on " + labels.labels.length + " hand labels"],
      ["Verdict accuracy", Math.round(labels.verdict_accuracy * 1000) / 10 + "%", "reason code " + Math.round(labels.reason_accuracy * 1000) / 10 + "%"],
      ["Unsafe clearance", unsafe + " / " + labels.labels.length, "wrongful CLOSE on this sample"],
      ["Overfit gap", labels.split.gap, "even " + labels.split.even_points + " vs odd " + labels.split.odd_points]
    ].map(([title, value, note]) => `<div class="stat"><b>${value}</b><span class="muted">${title}</span><div class="footnote">${note}</div></div>`).join("");
  }

  function modelCost(model) {
    const fx = num("usdPerGbp");
    const inTok = payload.input_tokens_mean_est;
    const outTok = num("outputTokens");
    const usdCase = (inTok / 1e6) * model.input_usd_per_mtok + (outTok / 1e6) * model.output_usd_per_mtok;
    const gbpCase = usdCase * fx;
    const monthly = num("monthly");
    const tps = model.assumed_output_tokens_per_sec || 40;
    const seconds = outTok / tps;
    return { gbpCase, usdCase, per1000: gbpCase * 1000, per800: gbpCase * 800, perMonth: gbpCase * monthly, seconds, tps };
  }

  function renderModels() {
    const body = document.querySelector("#model-table tbody");
    const rulesSeconds = payload.triage_seconds_per_case;
    const rows = [{
      label: "Rules engine",
      provider: "local",
      input_usd_per_mtok: 0,
      output_usd_per_mtok: 0,
      assumed_output_tokens_per_sec: null,
      measured: { note: "Hand-labelled sample only" },
      isRules: true
    }].concat(models);
    body.innerHTML = rows.map(model => {
      if (model.isRules) {
        const twoHundred = rulesSeconds * 200;
        return `<tr>
          <td><strong>Rules engine</strong><div class="footnote">No tokens</div></td>
          <td>0</td><td>0</td><td>${money(0, 4)}</td><td>${money(0)}</td><td>${money(0)}</td><td>${money(0)}</td>
          <td>${rulesSeconds.toFixed(4)} s measured</td>
          <td>${twoHundred.toFixed(2)} s · inside 30 min</td>
          <td>See the sample above. Not an official key.</td>
        </tr>`;
      }
      const cost = modelCost(model);
      const twoHundred = cost.seconds * 200;
      const limit = twoHundred <= 1800 ? "inside 30 min on this arithmetic" : "over 30 min on this arithmetic";
      const measured = model.measured && model.measured.verdict_accuracy != null
        ? `${Math.round(model.measured.verdict_accuracy * 1000) / 10}% verdict`
        : "Not measured";
      return `<tr>
        <td><strong>${model.label}</strong><div class="footnote">${model.provider}</div></td>
        <td>${usd(model.input_usd_per_mtok)}</td>
        <td>${usd(model.output_usd_per_mtok)}</td>
        <td>${money(cost.gbpCase, 4)}</td>
        <td>${money(cost.per1000)}</td>
        <td>${money(cost.per800)}</td>
        <td>${money(cost.perMonth)}</td>
        <td>${cost.seconds.toFixed(1)} s ${model.published_output_tokens_per_sec ? "from published speed" : "assumption"}</td>
        <td>${(twoHundred / 60).toFixed(1)} min · ${limit}</td>
        <td>${measured}</td>
      </tr>`;
    }).join("");
    renderCharts(rows.filter(model => !model.isRules));
    renderTradeoffs(rows.filter(model => !model.isRules));
  }

  function chip(kind, label) {
    return `<span class="chip ${kind}">${label}</span>`;
  }
  function cell(kind, label, line) {
    return `<td>${chip(kind, label)}<div>${line}</div></td>`;
  }
  function renderTradeoffs(list) {
    const rulesTime = (payload.triage_seconds_per_case * 200).toFixed(2) + " s measured";
    const head = `<tr><th>Option</th><th>Accuracy on these cases</th><th>Determinism</th><th>Data privacy</th><th>Rate limits</th><th>Wrongful-close safety</th><th>Cost per 800 cases</th><th>Time for 200 cases</th></tr>`;
    const rules = `<tr>
      <th>Rules baseline</th>
      ${cell("good", "Sample only", "Matches 28 hand labels. Not an official key.")}
      ${cell("good", "Good", "Five bank runs were identical.")}
      ${cell("good", "Good", "The case folder stays on this machine.")}
      ${cell("good", "Good", "Local CPU only.")}
      ${cell("good", "Good", "Does not close once suspicion exists.")}
      <td>${chip("good", "£0")}<div>No token charge.</div></td>
      <td>${chip("good", "Under 30 min")}<div>${rulesTime}.</div></td>
    </tr>`;
    const body = list.map(model => {
      const cost = modelCost(model);
      const minutes = cost.seconds * 200 / 60;
      const timeKind = minutes <= 30 ? "caution" : "risk";
      const timeLabel = minutes <= 30 ? "Estimate" : "Over 30 min";
      const privacy = model.hosting === "groq"
        ? cell("caution", "Caution", "Open weights. This price is Groq's hosted API. A self-hosted GPU is not included. Case text is sent to Groq unless you self-host.")
        : model.hosting === "deepseek-api"
          ? cell("caution", "Caution", "Hosted API price, not a GPU quote. Case text is sent to DeepSeek.")
          : cell("risk", "Risk", "Case text is sent to " + model.provider + ".");
      const speedNote = model.published_output_tokens_per_sec ? "From the published tokens/sec figure." : "Speed is an assumption, not a measurement.";
      return `<tr>
        <th>${model.label}</th>
        ${cell("unknown", "Unknown", "Not measured.")}
        ${cell("caution", "Caution", "Temperature 0 and a cache. Not guaranteed.")}
        ${privacy}
        ${cell("caution", "Caution", "Provider quotas can stall a run.")}
        ${cell("caution", "Caution", "Can close a case the rules would escalate.")}
        <td>${chip("caution", "Priced")}<div>${money(cost.per800)} at the sliders above.</div></td>
        <td>${chip(timeKind, timeLabel)}<div>${minutes.toFixed(1)} min. ${speedNote}</div></td>
      </tr>`;
    }).join("");
    $("tradeoffs-table").innerHTML = `<table class="tradeoffs"><thead>${head}</thead><tbody>${rules}${body}</tbody></table>`;
  }

  function renderCharts(list) {
    const rulesSeconds = payload.triage_seconds_per_case * 200;
    const rows = list.map(model => ({
      label: model.chart_label || model.label.split(" ")[0],
      cost: modelCost(model).per1000,
      seconds: modelCost(model).seconds * 200,
      colour: "var(--blue)",
      timeColour: model.published_output_tokens_per_sec ? "var(--blue)" : "var(--orange)",
      rules: false
    })).concat([{
      label: "Rules",
      cost: 0,
      seconds: rulesSeconds,
      colour: "var(--green)",
      rules: true
    }]);

    const w = 680, h = 248, padL = 12, padR = 8, base = 196, plotH = 148;
    const slot = (w - padL - padR) / rows.length;
    const bw = Math.min(52, slot * 0.62);
    const axis = `<line x1="${padL}" x2="${w - padR}" y1="${base}" y2="${base}" stroke="var(--separator)" stroke-width="1.5"></line>`;

    const maxCost = Math.max(...rows.map(row => row.cost), 1);
    const costBars = rows.map((row, i) => {
      const raw = (row.cost / maxCost) * plotH;
      const bh = row.rules ? 6 : raw;
      const x = padL + i * slot + (slot - bw) / 2;
      const y = base - bh;
      const value = row.rules ? "£0" : money(row.cost, row.cost < 10 ? 1 : 0);
      return `<rect x="${x}" y="${y}" width="${bw}" height="${bh}" rx="${row.rules ? 2 : 6}" fill="${row.colour}"></rect>
        <text x="${x + bw / 2}" y="${y - 8}" text-anchor="middle" font-size="13" font-weight="650" fill="${row.rules ? "var(--green)" : "var(--text)"}">${value}</text>
        <text x="${x + bw / 2}" y="${base + 20}" text-anchor="middle" font-size="13" fill="${row.rules ? "var(--green)" : "var(--secondary)"}">${row.label}</text>`;
    }).join("");
    $("cost-chart").innerHTML = `<h3 style="margin:0 0 8px">GBP per 1,000 cases</h3>
      <svg viewBox="0 0 ${w} ${h}" width="100%" role="img" aria-label="Cost per one thousand cases. The language models, then the rules engine at zero pounds on the right.">${axis}${costBars}</svg>`;

    const tmax = Math.max(...rows.map(row => row.seconds), 1800);
    const limitY = base - (1800 / tmax) * plotH;
    const timeBars = rows.map((row, i) => {
      const raw = (row.seconds / tmax) * plotH;
      const bh = row.rules ? Math.max(raw, 6) : raw;
      const x = padL + i * slot + (slot - bw) / 2;
      const y = base - bh;
      const minutes = row.seconds / 60;
      const value = row.rules
        ? (minutes < 0.1 ? row.seconds.toFixed(2) + " s" : minutes.toFixed(1) + " min")
        : minutes.toFixed(1) + " min";
      const fill = row.rules ? "var(--green)" : row.timeColour;
      return `<rect x="${x}" y="${y}" width="${bw}" height="${bh}" rx="${row.rules ? 2 : 6}" fill="${fill}"></rect>
        <text x="${x + bw / 2}" y="${y - 8}" text-anchor="middle" font-size="13" font-weight="650" fill="${row.rules ? "var(--green)" : "var(--text)"}">${value}</text>
        <text x="${x + bw / 2}" y="${base + 20}" text-anchor="middle" font-size="13" fill="${row.rules ? "var(--green)" : "var(--secondary)"}">${row.label}</text>`;
    }).join("");
    $("time-chart").innerHTML = `<h3 style="margin:0 0 8px">Minutes for 200 cases</h3>
      <svg viewBox="0 0 ${w} ${h}" width="100%" role="img" aria-label="Time for two hundred cases. Models first, rules engine last, against the thirty minute limit.">
        <line x1="${padL}" x2="${w - padR}" y1="${limitY}" y2="${limitY}" stroke="var(--red)" stroke-dasharray="4 4"></line>
        <text x="${w - padR}" y="${Math.max(14, limitY - 6)}" text-anchor="end" font-size="12" fill="var(--red)">30 min</text>
        ${axis}${timeBars}
      </svg>
      <p class="footnote">Same order on both charts: the language models, then Rules on the right in green. Llama is open weight, priced as Groq's hosted API. DeepSeek is the hosted API list price, not a GPU quote. The rules bar is measured (£0, and ${rulesSeconds.toFixed(2)} seconds for 200 cases). A short green stub keeps that zero-height bar visible. Blue uses a published tokens-per-second figure. Orange is an assumption. Input processing time is not included.</p>`;
  }

  function esc(value) {
    return String(value ?? "").replace(/[&<>"']/g, ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
  }
  function pct(value) {
    return value == null ? "—" : (Number(value) * 100).toFixed(1) + "%";
  }
  function latencyText(seconds) {
    if (seconds == null) return "—";
    if (seconds < 0.001) return (seconds * 1e6).toFixed(1) + " µs";
    if (seconds < 1) return (seconds * 1000).toFixed(2) + " ms";
    return Number(seconds).toFixed(3) + " s";
  }
  function plusMinus(stat, digits = 2) {
    if (!stat) return "—";
    return Number(stat.mean).toFixed(digits) + " ± " + Number(stat.fold_std).toFixed(digits);
  }
  function confusionTable(score) {
    const matrix = score.confusion_matrix;
    const head = matrix.columns.map(label => `<th>${esc(label)}</th>`).join("");
    const body = matrix.rows.map((label, i) => `<tr><th>${esc(label)}</th>${matrix.counts[i].map(n => `<td>${n}</td>`).join("")}</tr>`).join("");
    return `<table><thead><tr><th>Right \\ sent</th>${head}</tr></thead><tbody>${body}</tbody></table>`;
  }
  function metricBars(title, aria, rows) {
    const w = 680, h = 230, padL = 16, padR = 8, base = 186, plotH = 140;
    const max = Math.max(...rows.map(row => row.value), 1);
    const slot = (w - padL - padR) / rows.length;
    const bw = Math.min(36, slot * 0.7);
    const bars = rows.map((row, i) => {
      const raw = (row.value / max) * plotH;
      const bh = row.value === 0 ? 6 : raw;
      const x = padL + i * slot + (slot - bw) / 2;
      const y = base - bh;
      return `<rect x="${x}" y="${y}" width="${bw}" height="${bh}" rx="${row.value === 0 ? 2 : 5}" fill="${row.colour}"></rect>
        <text x="${x + bw / 2}" y="${y - 6}" text-anchor="middle" font-size="11" fill="var(--text)">${esc(row.display)}</text>
        <text x="${x + bw / 2}" y="${base + 16}" text-anchor="middle" font-size="11" fill="var(--secondary)">${esc(row.label)}</text>`;
    }).join("");
    return `<div class="chart"><h3 style="margin:0 0 8px">${esc(title)}</h3>
      <svg viewBox="0 0 ${w} ${h}" width="100%" role="img" aria-label="${esc(aria)}">
        <line x1="${padL}" x2="${w - padR}" y1="${base}" y2="${base}" stroke="var(--separator)" stroke-width="1.5"></line>
        ${bars}
      </svg></div>`;
  }
  function renderML(report) {
    const node = $("ml-compare");
    if (!node || !report) return;
    const colours = {
      rules: "var(--green)", logreg: "var(--blue)", tree: "var(--orange)",
      nb: "var(--fill-strong)", knn: "#5856d6", mlp: "var(--red)"
    };
    const rows = [report.rules_engine].concat(report.models);
    const shortName = id => ({ rules: "Rules", logreg: "Logistic", tree: "Tree", nb: "Bayes", knn: "KNN", mlp: "MLP" }[id] || id);
    const byId = Object.fromEntries(report.margins.map(row => [row.id, row]));
    const table = rows.map(item => {
      const cv = item.cv;
      return `<tr>
        <td><strong>${esc(item.label)}</strong></td>
        <td>${pct(item.hand.accuracy)}</td>
        <td>${pct(item.hand.macro_f1)}</td>
        <td>${item.hand.fitness}</td>
        <td>${item.hand.unsafe_clearance_count} <span class="footnote">(${pct(item.hand.unsafe_clearance_rate)})</span></td>
        <td>${item.hand.over_caution_count}</td>
        <td>${item.hand.tipping_off_count}</td>
        <td>${cv ? plusMinus(cv.fitness) : "Not cross-validated"}</td>
        <td>${cv ? plusMinus(cv.macro_f1, 3) : "—"}</td>
        <td>${latencyText(cv ? cv.predict_seconds_per_case.mean : item.predict_seconds_per_case_measured)}</td>
      </tr>`;
    }).join("");
    const handBars = rows.map(item => ({
      label: shortName(item.id),
      value: item.hand.fitness,
      display: String(item.hand.fitness),
      colour: colours[item.id] || "var(--blue)"
    }));
    const unsafeBars = rows.map(item => ({
      label: shortName(item.id),
      value: item.hand.unsafe_clearance_count,
      display: String(item.hand.unsafe_clearance_count),
      colour: item.hand.unsafe_clearance_count ? "var(--red)" : "var(--green)"
    }));
    const marginBars = rows.map(item => ({
      label: shortName(item.id),
      value: byId[item.id].hand_fitness_behind_winner,
      display: String(byId[item.id].hand_fitness_behind_winner),
      colour: byId[item.id].hand_fitness_behind_winner === 0 ? "var(--green)" : "var(--blue)"
    }));
    const classRows = rows.map(item => {
      const cells = ["CLOSE", "RFI", "ESCALATE"].map(label => {
        const row = item.hand.per_class[label];
        return `<td>${pct(row.precision)} / ${pct(row.recall)}</td>`;
      }).join("");
      return `<tr><th>${esc(item.label)}</th>${cells}</tr>`;
    }).join("");
    const matrices = rows.map(item => `<div class="chart"><h3 style="margin:0 0 8px">${esc(item.label)}</h3>${confusionTable(item.hand)}<p class="footnote">Hand labels, ${item.hand.n} cases. Rows are the right verdict.</p></div>`).join("");
    node.innerHTML = `
      <p>${esc(report.winner.reason)}</p>
      <p class="footnote">${esc(report.winner.tie_break)} Seed ${report.seed}, ${report.folds} stratified folds. ${report.n_cases} development cases, ${report.n_features} features. Training labels are the rules engine's own verdicts.</p>
      <div class="table-wrap">
        <table>
          <thead><tr>
            <th>Model</th><th>Hand accuracy</th><th>Hand macro F1</th><th>Hand fitness</th>
            <th>Unsafe closes</th><th>Over-caution</th><th>Tipping-off</th>
            <th>CV fitness</th><th>CV macro F1</th><th>Predict time</th>
          </tr></thead>
          <tbody>${table}</tbody>
        </table>
      </div>
      <p class="footnote">Hand columns use the 28 hand labels. CV columns train and test on the rules engine's verdicts, so they measure how well the model copies the policy. The rules engine is not cross-validated against its own labels. CV cells are the five-fold mean ± sample standard deviation. Unsafe closes are true RFI or ESCALATE cases sent as CLOSE. Over-caution is a true CLOSE sent as RFI or ESCALATE. Tipping-off is a true ESCALATE sent as RFI. Predict time for the rules engine is the measured figure in the payload file. Classical times are this machine's batch predict, per case.</p>
      <div class="grid" style="margin-top:16px">
        ${metricBars("Hand-label fitness", "Cost-matrix fitness on the 28 hand labels. Higher is better. The rules engine is 100.", handBars)}
        ${metricBars("Unsafe closes on the 28 hand labels", "Count of wrongful closes on the hand labels. Lower is better. A zero bar is a short green stub.", unsafeBars)}
      </div>
      <div class="grid" style="margin-top:16px">
        ${metricBars("Fitness points behind the leader", "How many hand-label fitness points each system sits behind the leader. The leader is a short green stub at zero.", marginBars)}
        <div class="chart">
          <h3 style="margin:0 0 8px">Policy-reproduction gap</h3>
          <p>Cross-validated fitness is how much of the escalate-everything cost the model still avoids when the label is the rules engine. 100 would mean it copied the policy. The gap below is 100 minus the five-fold mean.</p>
          <ul>${report.models.map(item => `<li><strong>${esc(item.label)}</strong>: ${item.cv.fitness.mean} fitness, gap ${byId[item.id].policy_reproduction_gap}. Fold interval ${item.cv.fitness.interval_95[0]} to ${item.cv.fitness.interval_95[1]}.</li>`).join("")}</ul>
          <p class="footnote">${esc(report.models[0].cv.fitness.interval_note)}</p>
        </div>
      </div>
      <h3>Precision / recall on the hand labels</h3>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Model</th><th>CLOSE</th><th>RFI</th><th>ESCALATE</th></tr></thead>
          <tbody>${classRows}</tbody>
        </table>
      </div>
      <p class="footnote">Each cell is precision / recall. Support is the hand-label count for that class, which is small.</p>
      <h3>Confusion matrices, hand labels</h3>
      <div class="grid">${matrices}</div>
      <h3>What a regulator can audit</h3>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Model</th><th>Cites evidence</th><th>Deterministic</th><th>Explanation</th><th>Overfit gap, fitness</th></tr></thead>
          <tbody>${rows.map(item => `<tr>
            <th>${esc(item.label)}</th>
            <td>${item.cites_evidence ? "Yes" : "No"}</td>
            <td>${item.deterministic ? "Yes" : "No. Fixed seed 7 on this scikit-learn version."}</td>
            <td>${esc(item.explainability)}</td>
            <td>${item.cv ? plusMinus(item.cv.overfit_fitness_gap) : "None. It was not fitted."}</td>
          </tr>`).join("")}</tbody>
        </table>
      </div>
      <ul class="footnote">${report.caveats.map(line => `<li>${esc(line)}</li>`).join("")}</ul>`;
  }

  function renderSources() {
    $("sources").innerHTML = models.map(model => `<li><a href="${model.source}">${model.label}</a> — ${model.source_note} ${model.speed_note || ""}</li>`).join("")
      + `<li>Input tokens: ${payload.method} Mean estimate across institutions: ${payload.input_tokens_mean_est} tokens per case. Rules reasons average about ${payload.output_tokens_rules_est} tokens; the output slider is a separate assumption.</li>`
      + `<li>Rules timing: ${payload.triage_seconds_per_case} seconds per case, measured on the 4,000 development cases in this environment.</li>`
      + `<li>Workload uses the close share slider. On the development run the rules engine closed about 45% of cases; that share is not a promise about unseen cases. Held payments closed at the payments firm: ${payload.held_payments_closed} of ${payload.held_payments}.</li>`;
  }

  function renderValue() {
    let box = $("value-box");
    if (!box) {
      box = document.createElement("section");
      box.id = "value-box";
      box.className = "stats";
      $("controls").after(box);
    }
    const monthly = num("monthly");
    const closed = monthly * num("closeShare");
    const hours = closed * num("minutes") / 60;
    const saved = hours * num("rate");
    const held = payload.held_payments ? (payload.held_payments_closed / payload.held_payments) * monthly * 0.25 * num("heldValue") : 0;
    box.innerHTML = `
      <div class="stat"><b>${money(saved, 0)}</b><span class="muted">Analyst cost avoided / month</span><div class="footnote">${closed.toFixed(0)} closes × ${num("minutes")} min × ${money(num("rate"), 0)}/hour</div></div>
      <div class="stat"><b>${hours.toFixed(0)} h</b><span class="muted">Review time not spent on closes</span><div class="footnote">RFIs and escalations are still assumed to take a person.</div></div>
      <div class="stat"><b>${closed.toFixed(0)}</b><span class="muted">Cases not sent back as an RFI</span><div class="footnote">This is the close count, not a count of RFIs proven unnecessary.</div></div>
      <div class="stat"><b>${money(held, 0)}</b><span class="muted">Held-payment value / month</span><div class="footnote">Uses your value per release. Zero until you set one. Not from the dataset.</div></div>`;
  }

  function redraw() { renderModels(); renderValue(); }

  async function init() {
    const [bench, stats, hands, ml] = await Promise.all([
      fetch("data/llm_benchmarks.json").then(r => r.json()),
      fetch("data/payload_stats.json").then(r => r.json()),
      fetch("data/hand_labels.json").then(r => r.json()),
      fetch("data/ml_comparison.json").then(r => r.json())
    ]);
    models = bench.models;
    payload = stats;
    labels = hands;
    renderML(ml);
    if (bench.output_tokens_default) inputs.find(row => row[0] === "outputTokens")[2] = bench.output_tokens_default;
    $("controls").innerHTML = inputs.map(field).join("");
    $("controls").querySelectorAll("input").forEach(input => input.addEventListener("input", redraw));
    const themeButtons = document.querySelectorAll("[data-theme]");
    themeButtons.forEach(btn => btn.addEventListener("click", () => {
      const theme = btn.dataset.theme;
      if (theme === "system") document.documentElement.removeAttribute("data-theme");
      else document.documentElement.setAttribute("data-theme", theme);
      themeButtons.forEach(el => el.setAttribute("aria-pressed", String(el === btn)));
    }));
    const params = new URLSearchParams(location.search);
    if (params.get("theme")) document.documentElement.setAttribute("data-theme", params.get("theme"));
    renderBaseline();
    renderSources();
    redraw();
  }
  init().catch(err => { document.getElementById("main").insertAdjacentHTML("beforeend", `<p>${err.message}</p>`); });
})();
