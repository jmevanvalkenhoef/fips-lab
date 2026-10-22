// FIPS Lab live view: renders collector snapshots as a force-directed mesh.
//
// The collector streams one snapshot per second over Server-Sent Events. Node
// and link objects persist across snapshots so the layout stays put; only
// changes to the spanning tree nudge the simulation. Traffic rates come from
// the difference in each link's byte counters between snapshots.

const css = getComputedStyle(document.documentElement);
const token = (name) => css.getPropertyValue(`--${name}`).trim();
const OK = token("ok"), WARN = token("warn"), BAD = token("bad");

const svg = d3.select("#graph");
const canvas = document.getElementById("particles");
const ctx = canvas.getContext("2d");
const linkLayer = svg.append("g");
const labelLayer = svg.append("g");
const nodeLayer = svg.append("g");

const nodes = new Map();   // id -> node object (keeps x, y between snapshots)
const links = new Map();   // id -> link object
const cutSeen = new Map(); // link id -> { at, exact } when the cut was first seen
const healSeen = new Map();
let prev = null;
let selected = null;
let lastMessage = 0;

// 0 = healthy, 1 = bad. RTT and loss on a scale that suits WAN-ish lab links.
const clamp01 = (x) => Math.max(0, Math.min(1, x));
const badness = (l) => Math.max(clamp01(((l.srtt_ms ?? 0) - 20) / 180), clamp01((l.loss ?? 0) / 0.1));
const quality = d3.scaleLinear().domain([0, 0.5, 1]).range([OK, WARN, BAD]).interpolate(d3.interpolateRgb);
const linkState = (l) => (!l.fips_up ? "dead" : l.cut ? "pending" : l.tree ? "tree" : "mesh");

// ── Layout ─────────────────────────────────────────────────────────────

let width = 0, height = 0;

const sim = d3.forceSimulation()
  .force("link", d3.forceLink().id((d) => d.id)
    // Tree edges pull tighter, so the layout reshapes when the tree does.
    .distance((l) => (l.tree ? 120 : 165))
    .strength((l) => (l.tree ? 0.7 : 0.35)))
  .force("charge", d3.forceManyBody().strength(-1100))
  .force("collide", d3.forceCollide(34))
  .force("x", d3.forceX().strength(0.04))
  .force("y", d3.forceY().strength(0.05))
  .on("tick", positionGraph);

function resize() {
  width = window.innerWidth;
  height = window.innerHeight;
  const dpr = window.devicePixelRatio || 1;
  canvas.width = width * dpr;
  canvas.height = height * dpr;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  // Leave room for the side panel on wide screens.
  const cx = width > 760 ? (width - 360) / 2 : width / 2;
  const cy = width > 760 ? height / 2 : height * 0.36;
  sim.force("x").x(cx);
  sim.force("y").y(cy);
  sim.alpha(0.3).restart();
}
window.addEventListener("resize", resize);

// ── Snapshots ──────────────────────────────────────────────────────────

function apply(snap) {
  const structure = (s) => s.nodes.map((n) => n.id).join() + "|" + s.links.map((l) => l.id).join();
  const rebuilt = !prev || structure(prev) !== structure(snap);
  const treeChanged = prev && snap.links.some((l) => links.get(l.id)?.tree !== l.tree);
  const dt = prev ? snap.t - prev.t : 0;

  for (const n of snap.nodes) {
    // New nodes start near the centre rather than at the layout's origin.
    const fresh = () => ({
      id: n.id,
      x: sim.force("x").x()() + (Math.random() - 0.5) * 80,
      y: sim.force("y").y()() + (Math.random() - 0.5) * 80,
    });
    nodes.set(n.id, Object.assign(nodes.get(n.id) || fresh(), n));
  }
  for (const n of nodes.keys()) if (!snap.nodes.some((s) => s.id === n)) nodes.delete(n);

  for (const l of snap.links) {
    const old = links.get(l.id);
    // A re-established link starts its counters at zero; never show a negative rate.
    const instant = old && dt > 0 ? Math.max(0, (l.bytes - old.bytes) / dt) : 0;
    const rate = old ? old.rate * 0.5 + instant * 0.5 : 0;
    // source/target are set once: the force layout swaps them for node objects.
    links.set(l.id, Object.assign(old || { spawn: 0, source: l.a, target: l.b }, l, { rate }));
  }

  logChanges(prev, snap);

  if (rebuilt) {
    sim.nodes([...nodes.values()]);
    sim.force("link").links([...links.values()]);
    if (prev) {
      sim.alpha(0.5).restart();
    } else {
      // Settle the first layout before it is shown, so the page opens calm.
      sim.alpha(1).tick(400);
      sim.alpha(0.05).restart();
    }
  } else if (treeChanged) {
    sim.force("link").links([...links.values()]); // re-reads distance and strength
    sim.alpha(0.35).restart();
  }

  prev = snap;
  render();
  renderStats(snap);
  renderDetail();
}

// ── Rendering ──────────────────────────────────────────────────────────

let linkSel, labelSel, metricSel, nodeSel;

function render() {
  const data = [...links.values()];

  linkSel = linkLayer.selectAll("g.l").data(data, (d) => d.id).join((enter) => {
    const g = enter.append("g").attr("class", "l");
    g.append("line").attr("class", "link");
    g.append("line").attr("class", "link-hit").append("title");
    return g;
  });
  linkSel.select(".link")
    .attr("class", (d) => `link ${linkState(d)}`)
    .attr("stroke", (d) => quality(badness(d)));
  linkSel.select(".link-hit title").text(linkTitle);

  labelSel = labelLayer.selectAll("text.link-label")
    .data(data.filter((d) => d.cut || !d.fips_up), (d) => d.id)
    .join("text").attr("class", "link-label");
  metricSel = labelLayer.selectAll("text.link-metric")
    .data(data.filter((d) => d.fips_up), (d) => d.id)
    .join("text").attr("class", "link-metric")
    .text((d) => `${(d.srtt_ms ?? 0).toFixed(1)} ms · ${((d.loss ?? 0) * 100).toFixed(1)}%`);

  nodeSel = nodeLayer.selectAll("g.node").data([...nodes.values()], (d) => d.id).join((enter) => {
    const g = enter.append("g").attr("class", "node").call(drag);
    g.append("circle").attr("class", "halo").attr("r", 26);
    g.append("circle").attr("class", "crown").attr("r", 18);
    g.append("circle").attr("class", "core").attr("r", 11);
    g.append("text").attr("class", "name");
    g.append("text").attr("class", "npub").attr("y", 32);
    g.append("text").attr("class", "depth").attr("x", 15).attr("y", -13);
    g.on("click", (event, d) => {
      event.stopPropagation();
      selected = selected === d.id ? null : d.id;
      renderDetail();
    });
    return g;
  });
  nodeSel
    .classed("root", (d) => d.is_root)
    .classed("down", (d) => !d.up)
    .classed("selected", (d) => d.id === selected);
  nodeSel.select(".name").text((d) => d.id);
  nodeSel.select(".npub").text((d) => shortNpub(d.npub));
  nodeSel.select(".depth").text((d) => (d.up && !d.is_root && d.depth != null ? `d${d.depth}` : ""));

  positionGraph();
}

function positionGraph() {
  if (!linkSel) return;
  linkSel.selectAll("line")
    .attr("x1", (d) => d.source.x).attr("y1", (d) => d.source.y)
    .attr("x2", (d) => d.target.x).attr("y2", (d) => d.target.y);
  const mid = (sel, dy) => sel
    .attr("x", (d) => (d.source.x + d.target.x) / 2)
    .attr("y", (d) => (d.source.y + d.target.y) / 2 + dy);
  mid(labelSel, -6);
  mid(metricSel, 12);
  nodeSel.attr("transform", (d) => `translate(${d.x},${d.y})`);
}

function linkTitle(d) {
  if (!d.fips_up) return `${d.a}—${d.b}: down`;
  return `${d.a}—${d.b}${d.tree ? " (tree edge)" : ""}\nrtt ${(d.srtt_ms ?? 0).toFixed(2)} ms · loss ${((d.loss ?? 0) * 100).toFixed(1)}% · etx ${(d.etx ?? 0).toFixed(2)}\n${formatRate(d.rate)}`;
}

function cutLabel(d) {
  if (!d.fips_up) {
    const heal = healSeen.get(d.id);
    return !d.cut && heal ? `reconnecting ${Math.round(Date.now() / 1000 - heal)}s` : "down";
  }
  const seen = cutSeen.get(d.id);
  if (!seen) return "cut";
  const secs = Math.round(Date.now() / 1000 - seen.at);
  return `cut ${seen.exact ? "" : "≥"}${secs}s · FIPS unaware`;
}

// ── Particles ──────────────────────────────────────────────────────────

const particles = [];

function frame(now) {
  const dt = Math.min(0.1, (now - (frame.last ?? now)) / 1000);
  frame.last = now;

  for (const l of links.values()) {
    if (!l.fips_up || l.cut || l.source.x == null) continue;
    // Particle rate grows with the square root of bytes/s: heartbeat chatter
    // stays a trickle, a bulk transfer becomes a stream.
    l.spawn += Math.min(40, Math.sqrt(l.rate) / 12) * dt;
    while (l.spawn >= 1) {
      l.spawn -= 1;
      particles.push({
        link: l,
        t: 0,
        forward: Math.random() < 0.5,
        // Slower links carry slower particles.
        duration: Math.min(3, 0.9 + (l.srtt_ms ?? 0) * 0.012),
      });
    }
  }

  ctx.clearRect(0, 0, width, height);
  ctx.shadowBlur = 8;
  for (let i = particles.length - 1; i >= 0; i--) {
    const p = particles[i];
    p.t += dt / p.duration;
    const l = p.link;
    if (p.t >= 1 || !l.fips_up || l.cut) {
      particles.splice(i, 1);
      continue;
    }
    const [from, to] = p.forward ? [l.source, l.target] : [l.target, l.source];
    const x = from.x + (to.x - from.x) * p.t;
    const y = from.y + (to.y - from.y) * p.t;
    const c = quality(badness(l));
    ctx.fillStyle = c;
    ctx.shadowColor = c;
    ctx.globalAlpha = Math.sin(Math.PI * p.t) * 0.9;
    ctx.beginPath();
    ctx.arc(x, y, l.tree ? 1.9 : 1.4, 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.globalAlpha = 1;

  if (labelSel) labelSel.text(cutLabel);
  updateLive();
  requestAnimationFrame(frame);
}

// ── Events ─────────────────────────────────────────────────────────────

function logChanges(before, after) {
  const t = after.t;
  if (!before) {
    const cut = after.links.filter((l) => l.cut);
    cut.forEach((l) => cutSeen.set(l.id, { at: t, exact: false }));
    log(t, `watching <b>${after.topology}</b> · ${after.nodes.length} nodes, ${after.links.length} links`);
    return;
  }
  const was = (list, id) => list.find((x) => x.id === id);

  for (const n of after.nodes) {
    const o = was(before.nodes, n.id);
    if (!o) continue;
    if (o.up && !n.up) log(t, `<b>${n.id}</b> node down`, "bad");
    if (!o.up && n.up) log(t, `<b>${n.id}</b> node up`, "ok");
    if (n.up && n.is_root && !o.is_root) log(t, `<b>${n.id}</b> is now the tree root`, "warn");
    if (o.up && n.up && !n.is_root && o.parent && n.parent && o.parent !== n.parent) {
      log(t, `<b>${n.id}</b> new parent ${n.parent} <span class="dim">(was ${o.parent})</span>`);
    }
  }

  for (const l of after.links) {
    const o = was(before.links, l.id);
    if (!o) continue;
    const name = `<b>${l.a}—${l.b}</b>`;
    if (!o.cut && l.cut) {
      cutSeen.set(l.id, { at: t, exact: true });
      log(t, `${name} cut by lab`, "bad");
    }
    if (o.fips_up && !l.fips_up) {
      const seen = cutSeen.get(l.id);
      const after = seen ? `, ${seen.exact ? "" : "≥"}${(t - seen.at).toFixed(1)} s after the cut` : "";
      log(t, `${name} FIPS declared the link dead${after}`, "bad");
    }
    if (o.cut && !l.cut) {
      cutSeen.delete(l.id);
      healSeen.set(l.id, t);
      log(t, `${name} healed by lab`, "ok");
    }
    if (!o.fips_up && l.fips_up) {
      const heal = healSeen.get(l.id);
      log(t, `${name} link up${heal ? `, ${(t - heal).toFixed(1)} s after heal` : ""}`, "ok");
      healSeen.delete(l.id);
    }
  }
}

function log(t, html, kind = "") {
  const li = document.createElement("li");
  li.className = "fresh";
  li.innerHTML = `<time>${clock(t)}</time><span class="${kind}">${html}</span>`;
  const list = document.getElementById("log");
  list.prepend(li);
  while (list.children.length > 200) list.lastChild.remove();
}

// ── HUD ────────────────────────────────────────────────────────────────

function renderStats(snap) {
  const up = snap.nodes.filter((n) => n.up).length;
  const linksUp = snap.links.filter((l) => l.fips_up).length;
  const root = snap.nodes.find((n) => n.is_root);
  const traffic = [...links.values()].reduce((sum, l) => sum + (l.rate || 0), 0);
  document.getElementById("topology").textContent = snap.topology;
  document.getElementById("stat-nodes").textContent = `${up}/${snap.nodes.length}`;
  document.getElementById("stat-links").textContent = `${linksUp}/${snap.links.length}`;
  document.getElementById("stat-root").textContent = root ? root.id : "–";
  document.getElementById("stat-traffic").textContent = formatRate(traffic);
}

function renderDetail() {
  const panel = document.getElementById("detail");
  const n = selected && nodes.get(selected);
  nodeSel?.classed("selected", (d) => d.id === selected);
  if (!n) {
    panel.hidden = true;
    return;
  }
  panel.hidden = false;
  document.getElementById("detail-title").textContent = `node ${n.id}`;
  const peers = [...links.values()]
    .filter((l) => l.fips_up && (l.a === n.id || l.b === n.id))
    .map((l) => (l.a === n.id ? l.b : l.a));
  const rows = [
    ["state", n.up ? "running" : "down"],
    ["tree", n.is_root ? "root" : n.up ? `depth ${n.depth}, parent ${n.parent}` : "–"],
    ["peers", peers.join(", ") || "–"],
    ["npub", n.npub],
    ["address", n.ipv6],
  ];
  document.getElementById("detail-body").innerHTML =
    rows.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("");
}

function updateLive() {
  const el = document.getElementById("live");
  const age = Date.now() / 1000 - lastMessage;
  const state = !lastMessage ? "connecting" : age < 4 ? "live" : "stale";
  if (el.dataset.state === state) return;
  el.dataset.state = state;
  el.className = `live ${state === "live" ? "on" : state === "stale" ? "off" : ""}`;
  el.querySelector("span").textContent = state;
}

// ── Helpers ────────────────────────────────────────────────────────────

const shortNpub = (npub) => (npub ? `${npub.slice(0, 9)}…${npub.slice(-4)}` : "");
const clock = (t) => new Date(t * 1000).toLocaleTimeString([], { hour12: false });

function formatRate(bytesPerSec) {
  const bits = (bytesPerSec || 0) * 8;
  if (bits >= 1e6) return `${(bits / 1e6).toFixed(1)} Mb/s`;
  if (bits >= 1e3) return `${(bits / 1e3).toFixed(1)} kb/s`;
  return `${bits.toFixed(0)} b/s`;
}

const drag = d3.drag()
  .on("start", (event, d) => {
    if (!event.active) sim.alphaTarget(0.2).restart();
    d.fx = d.x;
    d.fy = d.y;
  })
  .on("drag", (event, d) => {
    d.fx = event.x;
    d.fy = event.y;
  })
  .on("end", (event, d) => {
    if (!event.active) sim.alphaTarget(0);
    d.fx = null;
    d.fy = null;
  });

svg.on("click", () => {
  selected = null;
  renderDetail();
});

// "m" toggles RTT/loss labels on every link.
window.addEventListener("keydown", (event) => {
  if (event.key === "m") document.getElementById("graph").classList.toggle("metrics");
});

// ── Start ──────────────────────────────────────────────────────────────

resize();
requestAnimationFrame(frame);
const events = new EventSource("events");
events.onmessage = (event) => {
  lastMessage = Date.now() / 1000;
  apply(JSON.parse(event.data));
};
