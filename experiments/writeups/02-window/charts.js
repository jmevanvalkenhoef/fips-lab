// Charts for write-up 02, drawn from posterior.json (produced by
// experiments/bayes/fit.py). Numbers quoted in the text are filled in from the
// same file, so prose and charts cannot disagree.

const fmt = (x, digits = 1) => (x == null ? "–" : x.toFixed(digits));
const pct = (x) => `${Math.round(100 * x)}%`;
const SOURCE_W = 300; // VERIFIED_TTL_MS in FIPS v0.5.3, for comparison only

// ── Tooltip ────────────────────────────────────────────────────────────

function tooltip(container) {
  const tip = d3.select(container).append("div").attr("class", "tooltip").style("display", "none");
  return {
    show(event, lines) {
      // Labels come from data: build with textContent, never innerHTML.
      tip.selectAll("*").remove();
      lines.forEach(([text, cls], i) => {
        if (i) tip.append("br");
        tip.append(i === 0 ? "strong" : "span").attr("class", cls || null).text(text);
      });
      const box = container.getBoundingClientRect();
      const x = event.clientX - box.left;
      const y = event.clientY - box.top;
      tip.style("display", "block");
      const w = tip.node().offsetWidth;
      tip.style("left", `${Math.min(Math.max(0, x - w / 2), box.width - w)}px`).style("top", `${y - 64}px`);
    },
    hide() {
      tip.style("display", "none");
    },
  };
}

function hdi(xs, mass = 0.94) {
  const s = [...xs].sort((a, b) => a - b);
  const k = Math.floor(mass * s.length);
  let best = [s[0], s[s.length - 1]];
  for (let i = 0; i + k < s.length; i++) {
    if (s[i + k] - s[i] < best[1] - best[0]) best = [s[i], s[i + k]];
  }
  return best;
}

// ── Chart 1: the window, as each kind of evidence narrows it ────────────

const EVIDENCE = [
  { key: "where", label: "which cuts got stuck" },
  { key: "how-long", label: "+ how long they lasted at least" },
  { key: "when-back", label: "+ when the watched ones came back" },
];

function windowChart(el, fits) {
  const W = 700, rowH = 92;
  const m = { top: 8, right: 20, bottom: 46, left: 20 };
  const H = m.top + rowH * EVIDENCE.length + m.bottom;
  const x = d3.scaleLinear().domain([0, 600]).range([m.left, W - m.right]);
  const bins = d3.bin().domain(x.domain()).thresholds(d3.range(0, 601, 5));

  const svg = d3.select(el).append("svg").attr("viewBox", `0 0 ${W} ${H}`)
    .attr("role", "img").attr("aria-label", "Posterior of the window length for three amounts of evidence");
  const tip = tooltip(el);

  svg.append("g").attr("class", "grid").selectAll("line").data(x.ticks(12)).join("line")
    .attr("x1", x).attr("x2", x).attr("y1", m.top).attr("y2", H - m.bottom);
  svg.append("g").attr("class", "axis").attr("transform", `translate(0,${H - m.bottom})`)
    .call(d3.axisBottom(x).ticks(12).tickSizeOuter(0).tickFormat((d) => `${d} s`));
  svg.append("text").attr("class", "axis-label").attr("x", (m.left + W - m.right) / 2).attr("y", H - 8)
    .attr("text-anchor", "middle").text("W: when the window closes, seconds after start-up");

  const rows = [];
  EVIDENCE.forEach((ev, i) => {
    const draws = fits[ev.key].W;
    const hist = bins(draws).map((b) => ({ x0: b.x0, x1: b.x1, p: b.length / draws.length }));
    const [lo, hi] = hdi(draws);
    rows.push({ ...ev, lo, hi, median: d3.median(draws) });
    const base = m.top + rowH * (i + 1) - 8;
    const peak = d3.max(hist, (d) => d.p);
    // Each row scaled to its own peak: the shape is what matters, and a sharp
    // posterior would otherwise flatten the others to nothing.
    const y = d3.scaleLinear().domain([0, peak]).range([base, base - rowH + 34]);
    const g = svg.append("g");
    g.append("text").attr("class", "ref-label").attr("x", m.left).attr("y", base - rowH + 22).text(ev.label);
    g.append("text").attr("class", "row-label").attr("x", W - m.right).attr("y", base - rowH + 22)
      .attr("text-anchor", "end").text(`94% between ${fmt(lo, 0)} and ${fmt(hi, 0)} s`);
    g.append("path").attr("class", "density")
      .attr("d", d3.area().curve(d3.curveStepAfter).x((d) => x(d.x0)).y0(base).y1((d) => y(d.p))(
        [...hist, { x0: 600, p: 0 }]));
    g.append("line").attr("class", "axis-base").attr("x1", m.left).attr("x2", W - m.right).attr("y1", base).attr("y2", base);
    g.selectAll("rect").data(hist).join("rect").attr("class", "hit")
      .attr("x", (d) => x(d.x0)).attr("width", (d) => x(d.x1) - x(d.x0))
      .attr("y", base - rowH + 30).attr("height", rowH - 30)
      .on("pointermove", (event, d) => tip.show(event, [
        [`${pct(d.p)} of the posterior`],
        [`W between ${d.x0} and ${d.x1} s`],
        [ev.label, "muted"],
      ]))
      .on("pointerleave", tip.hide);
  });

  svg.append("line").attr("class", "ref source").attr("x1", x(SOURCE_W)).attr("x2", x(SOURCE_W))
    .attr("y1", m.top).attr("y2", H - m.bottom);

  table(el.closest("figure"), ["evidence", "median W (s)", "94% interval (s)"],
    rows.map((r) => [r.label, fmt(r.median, 0), `${fmt(r.lo, 0)}–${fmt(r.hi, 0)}`]));
  return rows;
}

// Shade the stretch of cut times with no measurements, so the curve there
// reads as the model's extrapolation.
function noData(svg, x, top, bottom, fresh) {
  const ts = fresh.map((r) => r.t).sort((a, b) => a - b);
  let gap = [0, 0];
  for (let i = 1; i < ts.length; i++) if (ts[i] - ts[i - 1] > gap[1] - gap[0]) gap = [ts[i - 1], ts[i]];
  const g = svg.append("g");
  g.append("rect").attr("class", "nodata").attr("x", x(gap[0]) + 6).attr("width", x(gap[1]) - x(gap[0]) - 12)
    .attr("y", top).attr("height", bottom - top);
  g.append("text").attr("class", "row-label").attr("x", (x(gap[0]) + x(gap[1])) / 2).attr("y", bottom - 8)
    .attr("text-anchor", "middle").text("no cuts measured here");
  return gap;
}

// ── Chart 2: chance that a cut gets stuck, by when it happens ───────────

function observed(fresh) {
  const groups = d3.groups(fresh, (r) => Math.round(r.t / 5) * 5);
  return groups.map(([t, rs]) => {
    // Stuck: the wait ran out, or a came back only as the window closed.
    const stuck = rs.filter((r) => r.cens || r.long_wait && r.t + r.y > 285).length;
    return { t: d3.mean(rs, (r) => r.t), k: stuck, n: rs.length };
  }).sort((a, b) => a.t - b.t);
}

function stuckChart(el, fit, fresh) {
  const c = fit.stuck_curve;
  const pts = c.t.map((t, i) => ({ t, q5: c.q5[i], q25: c.q25[i], q50: c.q50[i], q75: c.q75[i], q95: c.q95[i] }));
  const obs = observed(fresh);
  const W = 700, H = 320;
  const m = { top: 16, right: 24, bottom: 46, left: 52 };
  const x = d3.scaleLinear().domain([0, 400]).range([m.left, W - m.right]);
  const y = d3.scaleLinear().domain([0, 1]).range([H - m.bottom, m.top]);

  const svg = d3.select(el).append("svg").attr("viewBox", `0 0 ${W} ${H}`)
    .attr("role", "img").attr("aria-label", "Chance that a cut gets stuck against its time after start-up");
  const tip = tooltip(el);

  svg.append("g").attr("class", "grid").selectAll("line").data(y.ticks(5)).join("line")
    .attr("x1", m.left).attr("x2", W - m.right).attr("y1", y).attr("y2", y);
  svg.append("g").attr("class", "axis").attr("transform", `translate(0,${H - m.bottom})`)
    .call(d3.axisBottom(x).ticks(8).tickSizeOuter(0).tickFormat((d) => `${d} s`));
  svg.append("g").attr("class", "axis").attr("transform", `translate(${m.left},0)`)
    .call(d3.axisLeft(y).ticks(5).tickSize(0).tickPadding(8).tickFormat(d3.format(".0%")))
    .call((g) => g.select(".domain").remove());
  svg.append("text").attr("class", "axis-label").attr("x", (m.left + W - m.right) / 2).attr("y", H - 8)
    .attr("text-anchor", "middle").text("cut this long after start-up");

  noData(svg, x, m.top, H - m.bottom, fresh);
  const band = (lo, hi) => d3.area().x((d) => x(d.t)).y0((d) => y(d[lo])).y1((d) => y(d[hi]));
  svg.append("path").attr("class", "band outer").attr("d", band("q5", "q95")(pts));
  svg.append("path").attr("class", "band inner").attr("d", band("q25", "q75")(pts));
  svg.append("path").attr("class", "line").attr("d", d3.line().x((d) => x(d.t)).y((d) => y(d.q50))(pts));

  const g = svg.append("g").selectAll("g").data(obs).join("g")
    .attr("transform", (d) => `translate(${x(d.t)},${y(d.k / d.n)})`);
  g.append("circle").attr("class", "hit").attr("r", 14);
  g.append("circle").attr("class", "obs").attr("r", 5);
  g.append("text").attr("class", "value-label").attr("x", 9).attr("y", -8).text((d) => `${d.k}/${d.n}`);
  g.on("pointermove", (event, d) => {
    const i = d3.bisector((p) => p.t).center(pts, d.t);
    tip.show(event, [
      [`${d.k} of ${d.n} stuck`],
      [`cut ${fmt(d.t, 0)} s after start-up`],
      [`model: ${pct(pts[i].q50)} (90%: ${pct(pts[i].q5)}–${pct(pts[i].q95)})`, "muted"],
    ]);
  }).on("pointerleave", tip.hide);

  // Hover on the curve anywhere.
  svg.append("rect").attr("class", "hit").attr("x", m.left).attr("y", m.top)
    .attr("width", W - m.left - m.right).attr("height", H - m.top - m.bottom).lower()
    .on("pointermove", (event) => {
      const t = x.invert(d3.pointer(event)[0]);
      const p = pts[d3.bisector((d) => d.t).center(pts, t)];
      tip.show(event, [[`${pct(p.q50)} chance stuck`], [`cut ${p.t} s after start-up`], [`90%: ${pct(p.q5)}–${pct(p.q95)}`, "muted"]]);
    })
    .on("pointerleave", tip.hide);

  table(el.closest("figure"), ["cut after start-up", "observed stuck", "model median", "model 90%"],
    obs.map((d) => {
      const p = pts[d3.bisector((q) => q.t).center(pts, d.t)];
      return [`${fmt(d.t, 0)} s`, `${d.k}/${d.n}`, pct(p.q50), `${pct(p.q5)}–${pct(p.q95)}`];
    }));
  return pts;
}

// ── Chart 3: how long a is cut off, by when the cut happens ─────────────

function outageChart(el, fit, fresh) {
  const o = fit.outage;
  const pts = o.t.map((t, i) => ({ t, q50: o.q50[i], q90: o.q90[i], q95: o.q95[i], q99: o.q99[i], mean: o.mean[i] }));
  const W = 700, H = 340;
  const m = { top: 16, right: 24, bottom: 46, left: 56 };
  const x = d3.scaleLinear().domain([0, 400]).range([m.left, W - m.right]);
  const y = d3.scaleLog().domain([5, 400]).range([H - m.bottom, m.top]).clamp(true);

  const svg = d3.select(el).append("svg").attr("viewBox", `0 0 ${W} ${H}`)
    .attr("role", "img").attr("aria-label", "Predicted time without a route after a cut, by time after start-up");
  const tip = tooltip(el);
  const yTicks = [5, 10, 20, 50, 100, 200, 400];

  svg.append("g").attr("class", "grid").selectAll("line").data(yTicks).join("line")
    .attr("x1", m.left).attr("x2", W - m.right).attr("y1", y).attr("y2", y);
  svg.append("g").attr("class", "axis").attr("transform", `translate(0,${H - m.bottom})`)
    .call(d3.axisBottom(x).ticks(8).tickSizeOuter(0).tickFormat((d) => `${d} s`));
  svg.append("g").attr("class", "axis").attr("transform", `translate(${m.left},0)`)
    .call(d3.axisLeft(y).tickValues(yTicks).tickSize(0).tickPadding(8).tickFormat((d) => `${d} s`))
    .call((g) => g.select(".domain").remove());
  svg.append("text").attr("class", "axis-label").attr("x", (m.left + W - m.right) / 2).attr("y", H - 8)
    .attr("text-anchor", "middle").text("cut this long after start-up (dead timeout 6 s)");

  noData(svg, x, m.top, H - m.bottom, fresh);
  const LINES = [
    { key: "q50", label: "half of cuts", cls: "line" },
    { key: "q90", label: "9 in 10", cls: "line q90" },
    { key: "q99", label: "99 in 100", cls: "line q99" },
  ];
  for (const l of LINES) {
    svg.append("path").attr("class", l.cls).attr("d", d3.line().x((d) => x(d.t)).y((d) => y(d[l.key]))(pts));
    // Label each line at the right edge, past the window, where they are
    // furthest apart.
    const p = pts[pts.length - 1];
    // The median sits just under the 90% line here: label it below.
    svg.append("text").attr("class", "value-label").attr("x", x(p.t)).attr("y", y(p[l.key]) + (l.key === "q50" ? 15 : -6))
      .attr("text-anchor", "end").text(l.label);
  }

  const cross = svg.append("line").attr("class", "ref").style("display", "none")
    .attr("y1", m.top).attr("y2", H - m.bottom);
  svg.append("rect").attr("class", "hit").attr("x", m.left).attr("y", m.top)
    .attr("width", W - m.left - m.right).attr("height", H - m.top - m.bottom)
    .on("pointermove", (event) => {
      const t = x.invert(d3.pointer(event)[0]);
      const p = pts[d3.bisector((d) => d.t).center(pts, t)];
      cross.style("display", null).attr("x1", x(p.t)).attr("x2", x(p.t));
      tip.show(event, [
        [`cut ${p.t} s after start-up`],
        [`half back within ${fmt(p.q50)} s`],
        [`9 in 10 within ${fmt(p.q90)} s · 99 in 100 within ${fmt(p.q99, 0)} s`],
        [`average ${fmt(p.mean)} s`, "muted"],
      ]);
    })
    .on("pointerleave", () => { cross.style("display", "none"); tip.hide(); });

  const rowsAt = [15, 60, 90, 150, 240, 290, 310, 400];
  table(el.closest("figure"), ["cut after start-up", "half within", "9 in 10 within", "99 in 100 within", "average"],
    rowsAt.map((t) => {
      const p = pts[d3.bisector((d) => d.t).center(pts, t)];
      return [`${p.t} s`, `${fmt(p.q50)} s`, `${fmt(p.q90)} s`, `${fmt(p.q99, 0)} s`, `${fmt(p.mean)} s`];
    }));
  return pts;
}

// ── Chart 4: the earlier fit's prediction against the cuts that tested it ─

function predictionChart(el, pred, fresh) {
  const cuts = fresh.filter((r) => r.long_wait && r.t > 120).sort((a, b) => a.t - b.t);
  if (!cuts.length) return empty(el, "No test cuts yet.");
  const W = 700, rowH = 34;
  const m = { top: 8, right: 140, bottom: 46, left: 96 };
  const H = m.top + rowH * cuts.length + m.bottom;
  const x = d3.scaleLinear().domain([280, 320]).range([m.left, W - m.right]);
  const svg = d3.select(el).append("svg").attr("viewBox", `0 0 ${W} ${H}`)
    .attr("role", "img").attr("aria-label", "Predicted and observed return times of the test cuts");
  const tip = tooltip(el);

  svg.append("g").attr("class", "grid").selectAll("line").data(x.ticks(8)).join("line")
    .attr("x1", x).attr("x2", x).attr("y1", m.top).attr("y2", H - m.bottom);
  svg.append("g").attr("class", "axis").attr("transform", `translate(0,${H - m.bottom})`)
    .call(d3.axisBottom(x).ticks(8).tickSizeOuter(0).tickFormat((d) => `${d} s`));
  svg.append("text").attr("class", "axis-label").attr("x", (m.left + W - m.right) / 2).attr("y", H - 8)
    .attr("text-anchor", "middle").text("a reaches b again, seconds after start-up");

  const near = (t) => pred.stuck.reduce((a, b) => (Math.abs(b.t - t) < Math.abs(a.t - t) ? b : a));
  cuts.forEach((r, i) => {
    const cy = m.top + rowH * i + rowH / 2;
    const back = r.t + r.y;
    const p = near(r.t);
    const g = svg.append("g");
    g.append("rect").attr("class", "band outer").attr("x", x(pred.back.q5)).attr("width", x(pred.back.q95) - x(pred.back.q5))
      .attr("y", cy - 9).attr("height", 18).attr("rx", 4);
    g.append("line").attr("class", "median").attr("x1", x(pred.back.q50)).attr("x2", x(pred.back.q50)).attr("y1", cy - 9).attr("y2", cy + 9);
    g.append("circle").attr("class", "obs").attr("cx", x(back)).attr("cy", cy).attr("r", 5);
    g.append("text").attr("class", "row-label").attr("x", m.left - 10).attr("y", cy + 4).attr("text-anchor", "end")
      .text(`cut at ${fmt(r.t, 0)} s`);
    g.append("text").attr("class", "row-label").attr("x", W - m.right + 10).attr("y", cy + 4)
      .text(`${pct(p.q50)} predicted · ${back > 285 ? "stuck" : "rerouted"}`);
    g.append("rect").attr("class", "hit").attr("x", m.left).attr("width", W - m.left - m.right).attr("y", cy - rowH / 2).attr("height", rowH)
      .on("pointermove", (event) => tip.show(event, [
        [`back ${fmt(back)} s after start-up`],
        [`cut at ${fmt(r.t)} s, stuck for ${fmt(r.y)} s`],
        [`predicted: back at ${fmt(pred.back.q5, 0)}–${fmt(pred.back.q95, 0)} s, ${pct(p.q50)} chance stuck`, "muted"],
      ]))
      .on("pointerleave", tip.hide);
  });

  table(el.closest("figure"), ["cut after start-up", "predicted stuck", "observed", "predicted back (90%)", "observed back"],
    cuts.map((r) => {
      const p = near(r.t);
      return [`${fmt(r.t)} s`, `${pct(p.q50)} (${pct(p.q5)}–${pct(p.q95)})`, r.t + r.y > 285 ? "stuck" : "rerouted",
        `${fmt(pred.back.q5)}–${fmt(pred.back.q95)} s`, `${fmt(r.t + r.y)} s`];
    }));
}

function empty(el, text) {
  d3.select(el).append("p").attr("class", "dim").text(text);
}

// ── Shared ─────────────────────────────────────────────────────────────

function table(figure, headers, rows) {
  const details = d3.select(figure).append("details");
  details.append("summary").text("data table");
  const t = details.append("table");
  t.append("thead").append("tr").selectAll("th").data(headers).join("th").text((d) => d);
  t.append("tbody").selectAll("tr").data(rows).join("tr")
    .selectAll("td").data((r) => r).join("td").text((d) => d);
}

function fill(name, value) {
  document.querySelectorAll(`[data-fill="${name}"]`).forEach((el) => { el.textContent = value; });
}

async function main() {
  const [post, pred] = await Promise.all(["posterior.json", "prediction.json"].map(async (f) => (await fetch(f)).json()));
  const { fresh, warm } = post.data;
  const fit = post.fits["when-back"];
  const s = fit.summary;

  fill("n-fresh", fresh.length);
  fill("n-warm", warm.length);
  fill("n-total", fresh.length + warm.length);

  const rows = windowChart(document.getElementById("chart-window"), post.fits);
  for (const r of rows) {
    fill(`${r.key}-lo`, fmt(r.lo, 0));
    fill(`${r.key}-hi`, fmt(r.hi, 0));
    fill(`${r.key}-median`, fmt(r.median, 0));
  }
  fill("w-mean", fmt(s.W.mean));
  fill("w-sd", fmt(s.W.sd));
  fill("back-sd", fmt(s.sd_back.mean));

  const stuck = stuckChart(document.getElementById("chart-stuck"), fit, fresh);
  const at = (pts, t) => pts[d3.bisector((d) => d.t).center(pts, t)];
  for (const t of [16, 90, 200, 290]) {
    const p = at(stuck, t);
    fill(`stuck-${t}`, pct(p.q50));
    fill(`stuck-${t}-lo`, pct(p.q5));
    fill(`stuck-${t}-hi`, pct(p.q95));
  }

  const outage = outageChart(document.getElementById("chart-outage"), fit, fresh);
  for (const t of [16, 90, 200, 310]) {
    const p = at(outage, t);
    fill(`out-${t}-q50`, fmt(p.q50));
    fill(`out-${t}-q90`, fmt(p.q90, 0));
    fill(`out-${t}-q99`, fmt(p.q99, 0));
    fill(`out-${t}-mean`, fmt(p.mean, 0));
  }

  predictionChart(document.getElementById("chart-prediction"), pred, fresh);
  const tests = fresh.filter((r) => r.long_wait && r.t > 120);
  fill("test-n", tests.length);
  fill("test-stuck", tests.filter((r) => r.t + r.y > 285).length);
  fill("test-back-lo", fmt(d3.min(tests, (r) => r.t + r.y)));
  fill("test-back-hi", fmt(d3.max(tests, (r) => r.t + r.y)));
  fill("pred-back-lo", fmt(pred.back.q5, 0));
  fill("pred-back-hi", fmt(pred.back.q95, 0));
  fill("pred-commit", pred.commit);
  for (const p of pred.stuck) {
    fill(`pred-${p.t}`, pct(p.q50));
    fill(`pred-${p.t}-lo`, pct(p.q5));
  }
  const watched = fresh.filter((r) => r.long_wait);
  fill("watched-n", watched.length);
  fill("watched-back-lo", fmt(d3.min(watched, (r) => r.t + r.y), 0));
  fill("watched-back-hi", fmt(d3.max(watched, (r) => r.t + r.y), 0));

  const bt = fit.by_timeout;
  for (const T of [6, 10, 30]) {
    const i = bt.T.indexOf(T);
    fill(`T${T}-q50`, fmt(bt.q50[i]));
    fill(`T${T}-q95`, fmt(bt.q95[i]));
    fill(`T${T}-q99`, fmt(bt.q99[i]));
  }
  fill("fast-median", fmt(Math.exp(s.mu_f.mean)));
  fill("slow-median", fmt(Math.exp(s.mu_s.mean), 0));
  fill("slow-win", pct(s.slow_win.mean));
  fill("slow-win-lo", pct(s.slow_win["hdi_3%"]));
  fill("slow-win-hi", pct(s.slow_win["hdi_97%"]));
  fill("slow-warm", pct(s.slow_warm.mean));
  fill("slow-warm-hi", pct(s.slow_warm["hdi_97%"]));
  fill("rhat", fmt(Math.max(...Object.values(post.fits).map((f) => f.rhat_max)), 3));
  fill("ess", fmt(Math.min(...Object.values(post.fits).map((f) => f.ess_min)), 0));
  fill("div", Object.values(post.fits).reduce((n, f) => n + f.divergences, 0));
}

document.getElementById("theme").addEventListener("click", () => {
  const root = document.documentElement;
  const dark = root.dataset.theme
    ? root.dataset.theme === "dark"
    : matchMedia("(prefers-color-scheme: dark)").matches;
  root.dataset.theme = dark ? "light" : "dark";
});

main();
