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

## Spike findings (2026-10-22, FIPS v0.5.3)

Result: 3-node chain a — b — c, all 6 directed pairs ping over the mesh, a ↔ c routed via b (reachable ~4–6 s after start).

- **Containers work.** Needs `NET_ADMIN`, `/dev/net/tun` and IPv6 enabled; peers address each other by Docker hostname. Image builds from the upstream tag in ~8 min (cached afterwards), 179 MB.
- **Reach peers by name, not by mesh address.** An `fd..` address is a hash of the key, so a node cannot route to a non-neighbour until a `.fips` DNS lookup has told it the npub. The image runs dnsmasq to forward `.fips` to the daemon; a generated `/etc/fips/hosts` maps `a`, `b`, `c` → npub so nodes ping `c.fips`.
- **Control socket has everything the visualization needs.** `show_tree` gives root, depth and parent per node; `show_peers` gives each link's quality (`mmp`: `srtt_ms`, `loss_rate`, `etx`, `goodput_bps`); `show_routing` / `show_metrics` give discovery and forwarding counters. One request per connection, line-delimited JSON. The socket is root-owned inside the container, so the collector should either run `docker exec` or sit in the containers' namespace; to decide in milestone 3.
- **Resources:** ~117 MB RAM and <0.5 % CPU per idle node. 4+4 crypto worker threads per node; `FIPS_ENCRYPT_WORKERS` / `FIPS_DECRYPT_WORKERS` may cut memory for larger meshes. Rough ceiling on this machine: 15–20 nodes.
- **Reuse:** upstream's chaos harness is tied to its CI layout; our own small generator was simpler. Its scenario ideas (netem, link flaps, churn) are worth borrowing in milestone 4.
- **Public discovery is off by default** (`node.rendezvous.nostr` / `lan`), so the lab mesh stays isolated.

## Decisions

- Containers instead of hardware for the main testbed (reproducible, scalable).
- Collector in Python (FastAPI + WebSocket); frontend in TypeScript.
