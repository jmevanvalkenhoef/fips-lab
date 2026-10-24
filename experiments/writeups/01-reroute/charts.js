// Charts for write-up 01, drawn from data.json (produced by experiments/analyze.py).
// Numbers quoted in the text are filled in from the same data, so prose and
// charts cannot disagree.

const fmt = (x, digits = 1) => (x == null ? "–" : x.toFixed(digits));
const median = (xs) => (xs.length ? d3.median(xs) : null);

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

// ── Chart 1: reroute time against the dead-link timeout ─────────────────

function empty(el, text) {
  d3.select(el).append("p").attr("class", "dim").text(text);
}

function sweepChart(el, sweep) {
  if (!sweep.length) return empty(el, "No sweep runs yet.");
  const settings = [...new Set(sweep.map((r) => r.dead_timeout_s))].sort((a, b) => a - b);
  const stuck = sweep.filter((r) => r.censored);
  const ok = sweep.filter((r) => !r.censored);

  const W = 700, H = 360;
  const m = { top: stuck.length ? 46 : 16, right: 24, bottom: 46, left: 52 };
  const xMax = d3.max(settings) + 4;
  const yMax = Math.max(xMax, d3.max(ok, (r) => r.reroute_s) + 4);
  const x = d3.scaleLinear().domain([0, xMax]).range([m.left, W - m.right]);
  const y = d3.scaleLinear().domain([0, yMax]).nice().range([H - m.bottom, m.top]);

  const svg = d3.select(el).append("svg").attr("viewBox", `0 0 ${W} ${H}`)
    .attr("role", "img").attr("aria-label", "Reroute time per dead-link timeout setting");
  const tip = tooltip(el);

  svg.append("g").attr("class", "grid").selectAll("line").data(y.ticks(6)).join("line")
    .attr("x1", m.left).attr("x2", W - m.right).attr("y1", y).attr("y2", y);
  svg.append("g").attr("class", "axis").attr("transform", `translate(0,${H - m.bottom})`)
    .call(d3.axisBottom(x).tickValues(settings).tickSizeOuter(0).tickFormat((d) => `${d} s`));
  svg.append("g").attr("class", "axis").attr("transform", `translate(${m.left},0)`)
    .call(d3.axisLeft(y).ticks(6).tickSize(0).tickPadding(8).tickFormat((d) => `${d} s`))
    .call((g) => g.select(".domain").remove());
  svg.append("text").attr("class", "axis-label").attr("x", (m.left + W - m.right) / 2).attr("y", H - 8)
    .attr("text-anchor", "middle").text("link_dead_timeout_secs (heartbeat = timeout ÷ 3)");

  // Reference: rerouting no earlier than the link is declared dead.
  svg.append("line").attr("class", "ref")
    .attr("x1", x(0)).attr("y1", y(0)).attr("x2", x(xMax)).attr("y2", y(xMax));
  svg.append("text").attr("class", "ref-label")
    .attr("x", x(xMax) - 4).attr("y", y(xMax) - 6).attr("text-anchor", "end").text("y = timeout");

  // Deterministic jitter so repeated values stay visible.
  const jitter = (r) => ((r.rep * 37) % 11 - 5) * 0.16;

  const medians = settings.map((s) => ({ s, v: median(ok.filter((r) => r.dead_timeout_s === s).map((r) => r.reroute_s)) }));
  svg.append("g").selectAll("line").data(medians.filter((d) => d.v != null)).join("line").attr("class", "median")
    .attr("x1", (d) => x(d.s) - 14).attr("x2", (d) => x(d.s) + 14).attr("y1", (d) => y(d.v)).attr("y2", (d) => y(d.v));

  const point = (sel, cls, cy) => {
    const g = sel.append("g");
    g.append("circle").attr("class", "hit").attr("r", 12);
    g.append("circle").attr("class", cls).attr("r", 4.5);
    g.attr("transform", (r) => `translate(${x(r.dead_timeout_s + jitter(r))},${cy(r)})`);
    return g;
  };

  svg.append("g").selectAll("g").data(ok).join((enter) => point(enter, "dot", (r) => y(r.reroute_s)))
    .on("pointermove", (event, r) => tip.show(event, [
      [`${fmt(r.reroute_s)} s to reroute`],
      [`timeout ${r.dead_timeout_s} s · declared dead after ${fmt(r.dead_s, 0)} s`],
      [`repetition ${r.rep}`, "muted"],
    ]))
    .on("pointerleave", tip.hide);

  if (stuck.length) {
    const top = m.top - 22;
    svg.append("text").attr("class", "ref-label").attr("x", m.left).attr("y", top - 12)
      .text(`no route within the ${d3.max(stuck, (r) => Math.round(r.reroute_s))} s timeout`);
    svg.append("line").attr("class", "grid").attr("x1", m.left).attr("x2", W - m.right)
      .attr("y1", m.top - 6).attr("y2", m.top - 6).style("stroke", "var(--grid)");
    svg.append("g").selectAll("g").data(stuck).join((enter) => point(enter, "dot stuck", () => top))
      .on("pointermove", (event, r) => tip.show(event, [
        [`> ${fmt(r.reroute_s, 0)} s, stuck`],
        [`timeout ${r.dead_timeout_s} s · declared dead after ${fmt(r.dead_s, 0)} s`],
        [`repetition ${r.rep}`, "muted"],
      ]))
      .on("pointerleave", tip.hide);
  }

  table(el.closest("figure"), ["timeout", "rep", "reroute (s)", "declared dead (s)"],
    sweep.map((r) => [`${r.dead_timeout_s} s`, r.rep, r.censored ? `> ${fmt(r.reroute_s)}` : fmt(r.reroute_s), fmt(r.dead_s, 0)]));
}

// ── Chart 2: reroute time per condition, warm mesh against fresh starts ──

// A cut in a mesh that has run for a while, or a cut some time after a
// fresh start. Rep 1 of a run without --fresh is left out of "warm": the
// run may have started that mesh itself moments before.
function conditions(stuck) {
  const rows = [{ key: "warm", label: "warm mesh", sub: "later cuts, reset between", reps: [] }];
  for (const r of stuck) {
    if (!r.fresh) {
      if (r.rep > 1) rows[0].reps.push(r);
      continue;
    }
    const key = `fresh-${r.settle_s}`;
    let row = rows.find((d) => d.key === key);
    if (!row) {
      row = { key, settle: r.settle_s, label: "fresh start", sub: `cut ${r.settle_s} s after start-up`, reps: [] };
      rows.push(row);
    }
    row.reps.push(r);
  }
  return [rows[0], ...rows.slice(1).sort((a, b) => a.settle - b.settle)].filter((d) => d.reps.length);
}

function conditionChart(el, stuck) {
  const rows = conditions(stuck);
  if (!rows.length) return empty(el, "No runs yet.");
  // Censored values overshoot the wait by up to a probe; label the wait itself.
  const timeout = d3.max(stuck, (r) => (r.censored ? 5 * Math.floor(r.reroute_s / 5) : 0)) || 45;
  const rowH = 58, W = 700;
  const m = { top: 24, right: 76, bottom: 40, left: 150 };
  const H = m.top + m.bottom + rows.length * rowH;
  const x = d3.scaleLinear().domain([0, timeout]).range([m.left, W - m.right - 30]);
  const xStuck = W - m.right + 6;
  const y = (i) => m.top + i * rowH + rowH / 2;

  const svg = d3.select(el).append("svg").attr("viewBox", `0 0 ${W} ${H}`)
    .attr("role", "img").attr("aria-label", "Reroute time after the cut, per condition");
  const tip = tooltip(el);

  svg.append("g").attr("class", "grid").selectAll("line").data(x.ticks(8)).join("line")
    .attr("x1", x).attr("x2", x).attr("y1", m.top).attr("y2", H - m.bottom);
  svg.append("g").attr("class", "axis").attr("transform", `translate(0,${H - m.bottom})`)
    .call(d3.axisBottom(x).ticks(8).tickSizeOuter(0).tickFormat((d) => `${d} s`));
  svg.append("text").attr("class", "axis-label").attr("x", (m.left + x.range()[1]) / 2).attr("y", H - 6)
    .attr("text-anchor", "middle").text("seconds from the cut until a reaches b");
  svg.append("text").attr("class", "ref-label").attr("x", xStuck).attr("y", m.top - 8).attr("text-anchor", "middle")
    .text(`> ${timeout} s`);
  svg.append("line").attr("class", "grid").style("stroke", "var(--grid)")
    .attr("x1", xStuck - 22).attr("x2", xStuck - 22).attr("y1", m.top).attr("y2", H - m.bottom);

  const dead = median(stuck.map((r) => r.dead_s).filter((v) => v != null));
  svg.append("line").attr("class", "ref").attr("x1", x(dead)).attr("x2", x(dead)).attr("y1", m.top).attr("y2", H - m.bottom);
  svg.append("text").attr("class", "ref-label").attr("x", x(dead) + 4).attr("y", m.top - 8).text("link declared dead");

  const g = svg.append("g").selectAll("g").data(rows).join("g").attr("transform", (d, i) => `translate(0,${y(i)})`);
  g.append("text").attr("class", "ref-label").attr("x", m.left - 14).attr("y", -6).attr("text-anchor", "end")
    .style("font-weight", 600).text((d) => d.label);
  g.append("text").attr("class", "row-label").attr("x", m.left - 14).attr("y", 9).attr("text-anchor", "end")
    .text((d) => d.sub);
  g.append("text").attr("class", "value-label").attr("x", W - 4).attr("dy", "0.35em").attr("text-anchor", "end")
    .text((d) => `${d.reps.filter((r) => r.censored).length}/${d.reps.length} stuck`);

  // Deterministic vertical jitter, so cuts with the same time stay visible.
  const jitter = (r, i) => (((i * 7 + r.rep * 13) % 9) - 4) * 4;
  g.each(function (d) {
    d3.select(this).selectAll("g").data(d.reps).join((enter) => {
      const p = enter.append("g").attr("transform", (r, i) => `translate(${r.censored ? xStuck + ((i * 5) % 11) - 5 : x(r.reroute_s)},${jitter(r, i)})`);
      p.append("circle").attr("class", "hit").attr("r", 9);
      p.append("circle").attr("class", (r) => (r.censored ? "dot stuck" : "dot")).attr("r", 4.5);
      return p;
    })
      .on("pointermove", (event, r) => tip.show(event, [
        [r.censored ? `> ${fmt(r.reroute_s, 0)} s, stuck` : `${fmt(r.reroute_s)} s to reroute`],
        [`${d.label}, ${d.sub} · declared dead after ${fmt(r.dead_s, 0)} s`],
        [`${r.identities} identities · repetition ${r.rep}`, "muted"],
      ]))
      .on("pointerleave", tip.hide);
  });

  table(el.closest("figure"), ["condition", "identities", "rep", "reroute (s)", "declared dead (s)"],
    rows.flatMap((d) => d.reps.map((r) => [`${d.label}, ${d.sub}`, r.identities, r.rep,
      r.censored ? `> ${fmt(r.reroute_s)}` : fmt(r.reroute_s), fmt(r.dead_s, 0)])));
}

// ── Chart 3: when a healed link comes back, on the retry schedule ────────

// Reconnect attempts after a link is declared dead: node.retry.base_interval_secs
// (5 s), doubling each time, so at 5, 15, 35, 75, 155 s after the verdict.
const RETRIES = [5, 15, 35, 75, 155];

function relinkChart(el, reps) {
  const data = reps.filter((r) => r.relink_s != null && r.cut_duration_s != null && r.dead_s != null)
    .map((r) => {
      const healed = Math.max(0, r.cut_duration_s - r.dead_s);
      return { ...r, healed, back: healed + r.relink_s };
    });
  if (!data.length) return empty(el, "No runs yet.");
  const W = 700, H = 380;
  const m = { top: 16, right: 24, bottom: 46, left: 56 };
  const ticks = [0, 1, 2, 5, 10, 20, 50, 100, 200];
  // symlog: linear near zero, logarithmic beyond, so 0.5 s and 80 s heals both read.
  const x = d3.scaleSymlog().constant(2).domain([0, 100]).range([m.left, W - m.right]);
  const y = d3.scaleSymlog().constant(2).domain([0, 200]).range([H - m.bottom, m.top]);

  const svg = d3.select(el).append("svg").attr("viewBox", `0 0 ${W} ${H}`)
    .attr("role", "img").attr("aria-label", "When a healed link was listed again, against when it was healed, both relative to the link being declared dead");
  const tip = tooltip(el);

  svg.append("g").attr("class", "grid").selectAll("line").data(ticks.filter((t) => t <= 200)).join("line")
    .attr("x1", m.left).attr("x2", W - m.right).attr("y1", y).attr("y2", y);
  svg.append("g").attr("class", "axis").attr("transform", `translate(0,${H - m.bottom})`)
    .call(d3.axisBottom(x).tickValues(ticks.filter((t) => t <= 100)).tickSizeOuter(0).tickFormat((d) => `${d} s`));
  svg.append("g").attr("class", "axis").attr("transform", `translate(${m.left},0)`)
    .call(d3.axisLeft(y).tickValues(ticks).tickSize(0).tickPadding(8).tickFormat((d) => `${d} s`))
    .call((g) => g.select(".domain").remove());
  svg.append("text").attr("class", "axis-label").attr("x", (m.left + W - m.right) / 2).attr("y", H - 8)
    .attr("text-anchor", "middle").text("link healed, seconds after it was declared dead");
  svg.append("text").attr("class", "axis-label").attr("transform", `translate(14,${(m.top + H - m.bottom) / 2}) rotate(-90)`)
    .attr("text-anchor", "middle").text("link back, seconds after declared dead");

  // Lower bound: a link cannot come back before it is healed.
  const diag = d3.range(0, 100.5, 0.5);
  svg.append("path").attr("class", "ref").attr("fill", "none").style("stroke-dasharray", "3 4")
    .attr("d", d3.line().x(x).y(y)(diag));
  svg.append("text").attr("class", "ref-label").attr("x", x(60)).attr("y", y(60) + 16).attr("text-anchor", "middle")
    .text("back the moment it heals");

  // Prediction: back at the first scheduled attempt after the heal, a staircase.
  const steps = RETRIES.map((r, i) => ({ r, from: i ? RETRIES[i - 1] : 0, n: i + 1 })).filter((d) => d.from < 100);
  svg.append("g").selectAll("line").data(steps).join("line").attr("class", "ref")
    .attr("x1", (d) => x(d.from)).attr("x2", (d) => x(Math.min(d.r, 100))).attr("y1", (d) => y(d.r)).attr("y2", (d) => y(d.r));
  // Labels go where the plot is empty: right of a step's end (under the
  // diagonal), or left of its start once the right edge gets close.
  const right = (d) => d.r < 30;
  svg.append("g").selectAll("text").data(steps).join("text").attr("class", "ref-label")
    .attr("x", (d) => (right(d) ? x(d.r) + 8 : x(d.from) - 8)).attr("y", (d) => y(d.r)).attr("dy", "0.35em")
    .attr("text-anchor", (d) => (right(d) ? "start" : "end"))
    .text((d) => (d.n === 1 ? `attempt 1 · ${d.r} s` : `${d.n} · ${d.r} s`));

  svg.append("g").selectAll("g").data(data).join((enter) => {
    const g = enter.append("g").attr("transform", (r) => `translate(${x(r.healed)},${y(r.back)})`);
    g.append("circle").attr("class", "hit").attr("r", 12);
    g.append("circle").attr("class", "dot").attr("r", 4.5);
    return g;
  })
    .on("pointermove", (event, r) => tip.show(event, [
      [`${fmt(r.relink_s)} s from heal to link`],
      [`healed ${fmt(r.healed)} s after declared dead · back after ${fmt(r.back)} s`],
      [`${r.source} · cut for ${fmt(r.cut_duration_s)} s`, "muted"],
    ]))
    .on("pointerleave", tip.hide);

  table(el.closest("figure"), ["experiment", "cut for (s)", "healed after dead (s)", "relink (s)", "back after dead (s)"],
    data.map((r) => [r.source, fmt(r.cut_duration_s), fmt(r.healed), fmt(r.relink_s), fmt(r.back)]));
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
  const data = await (await fetch("data.json")).json();
  const { sweep, stuck } = data;
  data.early = data.early || [];

  // Numbers for the prose.
  const ok = sweep.filter((r) => !r.censored);
  const over = ok.filter((r) => r.dead_s != null).map((r) => r.reroute_s - r.dead_timeout_s);
  fill("sweep-n", sweep.length);
  fill("sweep-settings", [...new Set(sweep.map((r) => r.dead_timeout_s))].length);
  fill("over-median", fmt(median(over)));
  fill("over-max", fmt(d3.max(over)));
  fill("sweep-stuck", sweep.filter((r) => r.censored).length);
  for (const row of conditions(stuck)) {
    const prefix = row.key === "warm" ? "warm" : row.key;
    fill(`${prefix}-n`, row.reps.length);
    fill(`${prefix}-k`, row.reps.filter((r) => r.censored).length);
    fill(`${prefix}-median`, fmt(median(row.reps.filter((r) => !r.censored).map((r) => r.reroute_s))));
  }
  const stuckN = stuck.filter((r) => r.censored).length;
  fill("stuck-n", stuck.length);
  fill("stuck-k", stuckN);
  fill("stuck-pct", stuck.length ? `${Math.round((100 * stuckN) / stuck.length)}%` : "–");
  fill("stuck-ok-median", fmt(median(stuck.filter((r) => !r.censored).map((r) => r.reroute_s))));
  fill("stuck-dead-median", fmt(median(stuck.map((r) => r.dead_s).filter((v) => v != null)), 0));
  fill("stuck-timeout", fmt(d3.max(stuck, (r) => r.reroute_s), 0));
  const relinks = [...sweep, ...stuck, ...data.early].filter((r) => r.relink_s != null && r.dead_s != null && r.cut_duration_s != null);
  fill("relink-n", relinks.length);
  fill("relink-median", fmt(median(relinks.map((r) => r.relink_s))));
  fill("relink-first", relinks.filter((r) => r.cut_duration_s - r.dead_s < RETRIES[0]).length);
  fill("relink-max", fmt(d3.max(relinks, (r) => r.relink_s), 0));
  const unlock = data.unlock || [];
  fill("unlock-n", unlock.length);
  fill("unlock-min", fmt(d3.min(unlock, (r) => r.back_since_start_s), 0));
  fill("unlock-max", fmt(d3.max(unlock, (r) => r.back_since_start_s), 0));
  fill("image", data.image);
  fill("git", data.git);

  sweepChart(document.getElementById("chart-sweep"), sweep);
  conditionChart(document.getElementById("chart-conditions"), stuck);
  const relink = [
    ...sweep.map((r) => ({ ...r, source: `sweep, timeout ${r.dead_timeout_s} s` })),
    ...stuck.map((r) => ({ ...r, source: `stuck-reroute, ${r.identities}` })),
    ...data.early.map((r) => ({ ...r, source: `first runs, ${r.identities}` })),
  ];
  relinkChart(document.getElementById("chart-relink"), relink);
}

document.getElementById("theme").addEventListener("click", () => {
  const root = document.documentElement;
  const dark = root.dataset.theme
    ? root.dataset.theme === "dark"
    : matchMedia("(prefers-color-scheme: dark)").matches;
  root.dataset.theme = dark ? "light" : "dark";
});

main();
