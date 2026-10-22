# Plan

## Goal

The missing layer on top of FIPS's own `testing/` harness: live visualization, partition faults, and measured, readable experiment reports. Complement upstream rather than duplicate it, and contribute back what fits.

## Milestones

1. **Spike** — build FIPS (v0.5.2) in Docker, 3 nodes pinging over the mesh, read their control sockets. Answers the open questions below.
2. **Testbed** — topology file → N nodes (chain, ring, random), tc/netem per link.
3. **Collector + live visualization v1** — control sockets → WebSocket → browser graph.
4. **Fault injection** — link cut, node kill, partition, degrade; from UI and from scenario files.
5. **Experiments** — recovery time vs. network size, throughput vs. loss; first write-up.
6. **Polish** — portfolio site, demo video, upstream PRs. Optional: one physical device joining the Docker mesh over LAN.

## Later: Bayesian layer (parked 2026-10-22)

Not started; pick up after milestones 4–5. Together they turn "I built a testbed" into "I built a testbed and used rigorous statistics to tune a real protocol".

1. **Bayesian analysis of recovery time** (fits milestone 5). Model recovery = dead timeout + reroute delay, fit with MCMC (PyMC or NumPyro) over a sweep of `link_dead_timeout_secs`, topologies and loss levels; topology as a group-level effect (hierarchical). Output claims like "with timeout 6 s, 95 % of recoveries finish within X s". Visual: posterior / predictive ridgelines or violins that sharpen as runs come in.
2. **Bayesian optimisation of FIPS settings** (needs milestone 4: scriptable fault injection). Black-box search over heartbeat interval, dead timeout, tree hysteresis, trading off recovery time, heartbeat overhead and false "dead" verdicts on lossy links. GP surrogate picks the next experiment. Visual: GP mean/uncertainty surface updating live with the next point highlighted.
3. **Standalone MCMC sampler explainer** (Metropolis / HMC / NUTS on banana, funnel, multimodal targets). Lowest priority: good ones exist (Chi Feng's demo); only worth it with a twist such as using posteriors from the lab's own data.

Order: 1, then 2; 3 only if there is a clear angle.

## Spike findings (2026-10-22, FIPS v0.5.3)

Result: 3-node chain a — b — c, all 6 directed pairs ping over the mesh, a ↔ c routed via b (reachable ~4–6 s after start).

- **Containers work.** Needs `NET_ADMIN`, `/dev/net/tun` and IPv6 enabled; peers address each other by Docker hostname. Image builds from the upstream tag in ~8 min (cached afterwards), 179 MB.
- **Reach peers by name, not by mesh address.** An `fd..` address is a hash of the key, so a node cannot route to a non-neighbour until a `.fips` DNS lookup has told it the npub. The image runs dnsmasq to forward `.fips` to the daemon; a generated `/etc/fips/hosts` maps `a`, `b`, `c` → npub so nodes ping `c.fips`.
- **Control socket has everything the visualization needs.** `show_tree` gives root, depth and parent per node; `show_peers` gives each link's quality (`mmp`: `srtt_ms`, `loss_rate`, `etx`, `goodput_bps`); `show_routing` / `show_metrics` give discovery and forwarding counters. One request per connection, line-delimited JSON. The socket is root-owned inside the container, so the collector should either run `docker exec` or sit in the containers' namespace; to decide in milestone 3.
- **Resources:** ~117 MB RAM and <0.5 % CPU per idle node. 4+4 crypto worker threads per node; `FIPS_ENCRYPT_WORKERS` / `FIPS_DECRYPT_WORKERS` may cut memory for larger meshes. Rough ceiling on this machine: 15–20 nodes.
- **Reuse:** upstream's chaos harness is tied to its CI layout; our own small generator was simpler. Its scenario ideas (netem, link flaps, churn) are worth borrowing in milestone 4.
- **Public discovery is off by default** (`node.rendezvous.nostr` / `lan`), so the lab mesh stays isolated.

## Testbed findings (2026-10-22, milestone 2)

- **One Docker network per link** (a /29 in `10.99.<link>.0`, `internal: true`). netem and `ip link set down` on that interface then affect exactly one link. Up to 254 links.
- **Reroute after a cut is dominated by dead-link detection.** ring6, cut a–b, time until a pings b again the long way round (3 runs each):
  - defaults (`heartbeat_interval_secs` 10, `link_dead_timeout_secs` 30): 34.2, 32.1, 31.3 s
  - heartbeat 2, dead timeout 6: 7.6, 8.7, 6.2 s

  So recovery ≈ dead timeout + 0–3 s. Candidate experiment: sweep the timeout against heartbeat overhead and false "dead" verdicts on lossy links.
- **Degradation is visible fast.** a–b at 80 ms / 5 % loss shows in `show_peers` mmp within 10 s (srtt 8 → 101 ms). The tree parent did not switch within that window (`tree.reeval_interval_secs` is 60).
- **random10** (15 links, 15 ms ± 3 ms, 0.5 % loss): 90/90 pairs reachable within 9 s. Node e sat at depth 4 via g despite a direct link to root a; worth looking at with the visualization.
- **Generator bias:** `random_mesh` builds its spanning tree by attaching each node to a random earlier one, which makes early nodes hubs (a had degree 8 of 9). Fine for now; revisit with a degree-balanced or geometric generator before drawing conclusions from random meshes.
- **Memory:** 10 nodes ≈ 1.2 GB. Ceiling of 15–20 nodes on this machine stands.
- A host reboot stops the lab (containers have no restart policy); `lab.py up` brings it back with the same identities.

## Decisions

- Containers instead of hardware for the main testbed (reproducible, scalable).
- Collector in Python, standard library only, streaming over Server-Sent Events (one-way data needs no WebSocket); runs in its own container with each node's `/run/fips` bind-mounted, which also solves the root-owned socket. Replaces the earlier FastAPI + WebSocket idea.
- Frontend: plain JavaScript + D3 7.9.0 (vendored), 2D force layout; tree edges get shorter link distances so the layout reshapes when the tree does. TypeScript and a build step can come later if the frontend grows.
- `lab.py cut/heal` record the wire state in `linkstate.json`, so the view can show "cut, FIPS unaware" during the dead-link timeout.

## Visualization notes (milestone 3)

- Headless Chromium `--screenshot` runs on virtual time, so it captures steady states only (verified: healthy ring, link down after reroute). Recording the transitions for the demo video (milestone 6) needs real-time capture, e.g. CDP `Page.startScreencast` or a screen recorder.
- Not yet checked in a real browser: the "cut, FIPS unaware" countdown and the event log during a live cut.
