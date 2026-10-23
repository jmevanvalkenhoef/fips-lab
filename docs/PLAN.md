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

Item 1 done differently (2026-10-23): write-up 02 infers the verification window from the outside (censored mixture model, PyMC) instead of a timeout regression. Possible next step: use the posterior to pick the next cut times (the 91–300 s gap), a short run. Together they turn "I built a testbed" into "I built a testbed and used rigorous statistics to tune a real protocol".

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

## Fault injection findings (2026-10-22, milestone 4)

- **Faults:** cut / heal / netem per link, kill / start per node, partition (cuts every link crossing a node set), reset. Lab state (`labstate.json`: cut links, netem overrides, stopped nodes) is shared by CLI, collector and scenario runner under a file lock, written atomically so the host user and the root collector can both replace it.
- **A restarted container gets fresh interfaces** (no qdisc, link up), so `start` reapplies each link's netem and re-cuts its end if the link is cut.
- **cut-reroute on ring6, defaults** (3 reps): reroute 30.7 / 31.9 / 31.8 s, declared dead 31.4 / 31.9 / 31.9 s, relink after heal 4.5 / 3.3 / 3.5 s. Rerouting happens the moment the link is declared dead, not before.
- **Stuck reroute: resolved in milestone 5**, see below. (In the manual retry a's session to b *looked* `established`; the dedicated captures later showed `initiating`.)
- **Relink after a long cut: resolved in milestone 5**, exponential reconnect backoff confirmed.
- Waits in a scenario run in sequence, and `record` measures from the last *action*: a wait that follows a timed-out wait inherits that delay (`declared_dead_s` ≈ 90 s in the stuck reps). Read measurements after a censored one with that in mind.

## Experiment findings (2026-10-23, milestone 5)

Write-up: `experiments/writeups/01-reroute/` ("Thirty seconds of silence"). Data: `data.json` from `experiments/analyze.py`; the evidence of one stuck cut is committed in `evidence/`.

- **Reroute ≈ dead timeout + ~1 s** over `link_dead_timeout_secs` 6/10/15/20/30 (heartbeat = timeout ÷ 3), 6 reps each. Medians 8.0 / 12.3 / 16.8 / 22.0 / 32.3 s.
- **Stuck reroutes are a start-up effect.** Warm mesh: 0 of 38 stuck. Fresh start, cut 15 s later: slow (15–40 s) in half. Cut 90 s later: 6 of 9 stuck (> 45 s).
- **Cause: a stale *verified* coordinate entry on a transit node.** A destination a node looked up itself (proof checked) refuses coordinate hints from passing packets for `VERIFIED_TTL_MS` = 300 s (`src/cache/entry.rs`, `coord_cache.rs` → `HintOutcome::Rejected`; an anti-poisoning measure). The lab's convergence check makes every node look up every other, so for 5 min after start every entry is pinned. When a cut then moves a in the tree, e still holds a at `a>b>c` and routes up to d, d (correct) routes down to e: b's SessionAck loops d↔e until TTL exhaustion (`SessionDatagram TTL exhausted` on d, e's `coord_hint_rejected` 589). a's handshake stays `initiating`, b's `awaiting_msg3`; the handshake times out every 30 s and starts over.
- **Why 90 s is worse than 15 s:** idle sessions are removed after 90 s ("no application data"), so a late cut needs a new handshake, which has to cross the loop.
- **Confirmed** (`experiments/verified-window.sh`): cut 310 s after a fresh start, 0/6 stuck (median 7.9 s, same as warm). Stuck cuts left alone (4/4) recovered on their own 294–303 s after start-up, not after the cut. Also seen: after a heal moves the tree back, some pairs involving f stayed unreachable for 90 s, so heals can trigger it too.
- **Upstream candidates:** a verified entry that contradicts the tree (packet TTL-exhausted, or a TreeAnnounce placing the destination elsewhere) could lose its pin, or a transit node could re-verify instead of looping; worth an issue with the evidence bundle. Separately: a healed link waits for the next scheduled retry.
- **Relink = next reconnect attempt.** Attempts at 5, 15, 35, 75, 155 s after the dead verdict (`retry.base_interval_secs` 5, doubling, no ceiling seen up to 80 s gaps). Every heal lands on that schedule; the 75 s relink after a 90 s cut was the fifth attempt.
- **Tooling:** `FIPS_LOG` sets the nodes' `RUST_LOG`; `--fresh` restarts the mesh before every rep (since 2026-10-23 including rep 1; `analyze.py` marks `fresh` per rep from the events); every rep saves the capture nodes' logs.
- **Lesson:** run data is not in git and a careless glob deleted three runs (rerun the same day). Curated evidence now goes into the write-up folder.

## Decisions

- Containers instead of hardware for the main testbed (reproducible, scalable).
- Collector in Python, standard library only, streaming over Server-Sent Events (one-way data needs no WebSocket); runs in its own container with each node's `/run/fips` bind-mounted, which also solves the root-owned socket. Replaces the earlier FastAPI + WebSocket idea.
- Frontend: plain JavaScript + D3 7.9.0 (vendored), 2D force layout; tree edges get shorter link distances so the layout reshapes when the tree does. TypeScript and a build step can come later if the frontend grows.
- `lab.py cut/heal` record the wire state in `labstate.json`, so the view can show "cut, FIPS unaware" during the dead-link timeout.
- Browser controls run inside the collector (Docker CLI + mounted Docker socket, repo mounted at its host path so `lab.py` works unchanged). Root-equivalent, so: localhost-only port, `/action` requires `Content-Type: application/json` (forces a CORS preflight that is never answered) and refuses a foreign `Origin`; netem values are checked against a strict pattern.
- Scenario timeouts are recorded as censored observations, ready for survival-style / Bayesian models later.
- Run data (`experiments/runs/`) stays out of git for now; a curated dataset can be committed with the first write-up.

## Visualization notes (milestone 3)

- Headless Chromium `--screenshot` runs on virtual time, so it captures steady states only (verified: healthy ring, link down after reroute). Recording the transitions for the demo video (milestone 6) needs real-time capture, e.g. CDP `Page.startScreencast` or a screen recorder.
- Not yet checked in a real browser: the "cut, FIPS unaware" countdown and the event log during a live cut.
