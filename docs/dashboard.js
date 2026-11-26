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
  }

  function renderCharts(list) {
    const costs = list.map(model => modelCost(model).per1000);
    const max = Math.max(...costs, 1);
    const w = 460, h = 220, pad = 28;
    const bw = (w - pad * 2) / list.length * 0.6;
    const bars = list.map((model, i) => {
      const bh = (costs[i] / max) * (h - 60);
      const x = pad + i * ((w - pad * 2) / list.length) + 8;
      const y = h - 36 - bh;
      return `<rect x="${x}" y="${y}" width="${bw}" height="${bh}" rx="6" fill="var(--blue)"></rect>
        <text x="${x + bw / 2}" y="${h - 16}" text-anchor="middle" font-size="11" fill="var(--secondary)">${model.label.split(" ")[0]}</text>
        <text x="${x + bw / 2}" y="${y - 6}" text-anchor="middle" font-size="11" fill="var(--text)">${money(costs[i], 0)}</text>`;
    }).join("");
    $("cost-chart").innerHTML = `<h3 style="margin:0 0 8px">GBP per 1,000 cases</h3><svg viewBox="0 0 ${w} ${h}" width="100%" role="img" aria-label="Cost per one thousand cases">${bars}</svg>`;

    const times = [{ label: "Rules", seconds: payload.triage_seconds_per_case * 200, published: true }]
      .concat(list.map(model => ({ label: model.label.split(" ")[0], seconds: modelCost(model).seconds * 200, published: !!model.published_output_tokens_per_sec })));
    const tmax = Math.max(...times.map(t => t.seconds), 1800);
    const tbw = (w - pad * 2) / times.length * 0.55;
    const tbars = times.map((row, i) => {
      const bh = (row.seconds / tmax) * (h - 70);
      const x = pad + i * ((w - pad * 2) / times.length) + 10;
      const y = h - 36 - bh;
      const colour = row.label === "Rules" ? "var(--green)" : (row.published ? "var(--blue)" : "var(--orange)");
      return `<rect x="${x}" y="${y}" width="${tbw}" height="${bh}" rx="6" fill="${colour}"></rect>
        <text x="${x + tbw / 2}" y="${h - 16}" text-anchor="middle" font-size="11" fill="var(--secondary)">${row.label}</text>`;
    }).join("");
    const limitY = h - 36 - (1800 / tmax) * (h - 70);
    $("time-chart").innerHTML = `<h3 style="margin:0 0 8px">Minutes for 200 cases</h3>
      <svg viewBox="0 0 ${w} ${h}" width="100%" role="img" aria-label="Time for two hundred cases against the thirty minute limit">
        <line x1="${pad}" x2="${w - 8}" y1="${limitY}" y2="${limitY}" stroke="var(--red)" stroke-dasharray="4 4"></line>
        <text x="${w - 8}" y="${limitY - 4}" text-anchor="end" font-size="11" fill="var(--red)">30 min</text>
        ${tbars}
      </svg>
      <p class="footnote">Green is measured. Blue uses a published tokens-per-second figure. Orange is an assumption you can edit in the JSON file. Input processing time is not included.</p>`;
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
