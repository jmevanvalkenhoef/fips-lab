# FIPS Lab

A visual chaos lab for [FIPS](https://github.com/jmcorgan/fips), the self-organizing mesh network that uses Nostr keypairs as node identities.

Spin up a mesh of FIPS nodes in Docker, break it (cut links, kill nodes, partition the network, degrade links), and watch it heal live in the browser. Every experiment is measured and reproducible.

> Status: early development.

## Quickstart

Needs Docker with the compose plugin and Python 3.11+.

```sh
docker build -t fips-lab-node:v0.5.3 docker/   # ~8 min the first time
python3 lab/lab.py up ring6                     # six nodes in a ring, 5 ms links
python3 lab/lab.py ping ring6                   # every ordered pair over the mesh
python3 lab/lab.py status ring6                 # spanning tree + per-link RTT/loss
python3 lab/lab.py netem ring6 a b delay=80ms loss=5%
python3 lab/lab.py cut ring6 a b                # ...and watch traffic reroute
python3 lab/lab.py heal ring6 a b
python3 lab/lab.py down ring6
```

Topologies live in `topologies/*.toml`: explicit node and link lists, or generated (chain, ring, grid, random), with optional default netem and FIPS config overrides. See `lab/topology.py`. Each link is its own Docker network, so impairments and cuts hit exactly one link. Generated configs and keys go to `generated/` (git-ignored).

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
