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

## Open questions (answer in the spike)

- Does a FIPS node run cleanly in a container (TUN device, `NET_ADMIN`, IPv6)?
- What does the JSON control socket expose: peers, routes / spanning tree, per-link stats? Can it be polled cheaply?
- Can upstream's `testing/` topology generator or chaos harness be reused instead of rebuilt?
- How many nodes fit on the dev machine (4 cores, 8 GB)?

## Decisions

- Containers instead of hardware for the main testbed (reproducible, scalable).
- Collector in Python (FastAPI + WebSocket); frontend in TypeScript.
