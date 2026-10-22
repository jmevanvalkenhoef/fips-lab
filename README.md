# FIPS Lab

A visual chaos lab for [FIPS](https://github.com/jmcorgan/fips), the self-organizing mesh network that uses Nostr keypairs as node identities.

Spin up a mesh of FIPS nodes in Docker, break it (cut links, kill nodes, partition the network, degrade links), and watch it heal live in the browser. Every experiment is measured and reproducible.

> Status: early development.

## Quickstart

Needs Docker with the compose plugin and Python 3.11+.

```sh
docker build -t fips-lab-node:v0.5.3 docker/   # ~8 min the first time
python3 lab/lab.py up chain3                    # a — b — c
python3 lab/lab.py ping chain3                  # every pair, incl. a ↔ c via b
python3 lab/lab.py down chain3
```

Topologies live in `topologies/*.toml`. Generated configs and keys go to `generated/` (git-ignored).

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
