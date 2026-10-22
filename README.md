# FIPS Lab

A visual chaos lab for [FIPS](https://github.com/jmcorgan/fips), the self-organizing mesh network that uses Nostr keypairs as node identities.

Spin up a mesh of FIPS nodes in Docker, break it (cut links, kill nodes, partition the network, degrade links), and watch it heal live in the browser. Every experiment is measured and reproducible.

> Status: early development.

## Quickstart

Needs Docker with the compose plugin and Python 3.11+.

```sh
docker build -t fips-lab-node:v0.5.3 docker/   # ~8 min the first time
python3 lab/lab.py up ring6                     # six nodes in a ring, 5 ms links
open http://localhost:8080                      # live view; press m for per-link RTT/loss
python3 lab/lab.py ping ring6                   # every ordered pair over the mesh
python3 lab/lab.py status ring6                 # spanning tree + per-link RTT/loss
python3 lab/lab.py netem ring6 a b delay=80ms loss=5%
python3 lab/lab.py cut ring6 a b                # ...and watch traffic reroute
python3 lab/lab.py heal ring6 a b
python3 lab/lab.py down ring6
```

Topologies live in `topologies/*.toml`: explicit node and link lists, or generated (chain, ring, grid, random), with optional default netem and FIPS config overrides. See `lab/topology.py`. Each link is its own Docker network, so impairments and cuts hit exactly one link. Generated configs and keys go to `generated/` (git-ignored). The control sockets under `generated/<topology>/run/` are created by root inside the containers, so removing `generated/` needs `sudo`.

## Live view

`up` also starts a collector container that reads every node's control socket once a second and streams snapshots to the browser (Server-Sent Events, Python standard library only). The page shows:

- the spanning tree (bright edges, root in amber, depth per node) and the remaining mesh links (dim)
- link quality as colour, from teal to amber to red as RTT and loss rise
- traffic as particles, their rate following each link's byte counters
- a cut link as red dashes with a timer while FIPS still believes it is up, then dotted once FIPS declares it dead
- an event log: cuts, heals, links declared dead (with the time since the cut), parent and root changes

D3 is vendored in `web/vendor/`, so the lab runs fully offline.

## Layout

```
docker/       FIPS node image
lab/          Topology → configs + compose; up/down/ping
topologies/   Mesh definitions
collector/    Reads each node's control socket, streams state to the browser
web/          Live visualization
scenarios/    Scripted fault-injection scenarios
experiments/  Measurements, notebooks, write-ups
docs/         Plan and design notes
```

## License

MIT
