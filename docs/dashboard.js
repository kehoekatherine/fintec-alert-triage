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
      const privacy = model.id.includes("llama")
        ? cell("caution", "Caution", "Sent to Groq unless you self-host. GPU cost is not in this price.")
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
      label: model.label.split(" ")[0],
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

    const w = 520, h = 248, padL = 12, padR = 8, base = 196, plotH = 148;
    const slot = (w - padL - padR) / rows.length;
    const bw = Math.min(52, slot * 0.62);
    const axis = `<line x1="${padL}" x2="${w - padR}" y1="${base}" y2="${base}" stroke="var(--separator)" stroke-width="1.5"></line>`;

    const maxCost = Math.max(...rows.map(row => row.cost), 1);
    const costBars = rows.map((row, i) => {
      const raw = (row.cost / maxCost) * plotH;
      const bh = row.rules ? 6 : raw;
      const x = padL + i * slot + (slot - bw) / 2;
      const y = base - bh;
      const value = row.rules ? "£0" : money(row.cost, 0);
      return `<rect x="${x}" y="${y}" width="${bw}" height="${bh}" rx="${row.rules ? 2 : 6}" fill="${row.colour}"></rect>
        <text x="${x + bw / 2}" y="${y - 8}" text-anchor="middle" font-size="13" font-weight="650" fill="${row.rules ? "var(--green)" : "var(--text)"}">${value}</text>
        <text x="${x + bw / 2}" y="${base + 20}" text-anchor="middle" font-size="13" fill="${row.rules ? "var(--green)" : "var(--secondary)"}">${row.label}</text>`;
    }).join("");
    $("cost-chart").innerHTML = `<h3 style="margin:0 0 8px">GBP per 1,000 cases</h3>
      <svg viewBox="0 0 ${w} ${h}" width="100%" role="img" aria-label="Cost per one thousand cases. Four models, then the rules engine at zero pounds on the right.">${axis}${costBars}</svg>`;

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
      <p class="footnote">Same order on both charts: the four models, then Rules on the right in green. The rules bar is measured (£0, and ${rulesSeconds.toFixed(2)} seconds for 200 cases). A short green stub keeps that zero-height bar visible. Blue uses a published tokens-per-second figure. Orange is an assumption. Input processing time is not included.</p>`;
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
    const [bench, stats, hands] = await Promise.all([
      fetch("data/llm_benchmarks.json").then(r => r.json()),
      fetch("data/payload_stats.json").then(r => r.json()),
      fetch("data/hand_labels.json").then(r => r.json())
    ]);
    models = bench.models;
    payload = stats;
    labels = hands;
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
