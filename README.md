# FIPS Lab

A visual chaos lab for [FIPS](https://github.com/jmcorgan/fips), the self-organizing mesh network that uses Nostr keypairs as node identities.

Spin up a mesh of FIPS nodes in Docker, break it (cut links, kill nodes, partition the network, degrade links), and watch it heal live in the browser. Every experiment is measured and reproducible.

> Status: early development. First experiment write-up: [Thirty seconds of silence](experiments/writeups/01-reroute/).

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
python3 lab/lab.py kill ring6 e                 # crash a node; start brings it back
python3 lab/lab.py partition ring6 a b          # cut every link between {a, b} and the rest
python3 lab/lab.py reset ring6                  # everything back to the topology's defaults
python3 lab/lab.py down ring6
```

Only one topology runs at a time (the collector owns port 8080).

Topologies live in `topologies/*.toml`: explicit node and link lists, or generated (chain, ring, grid, random), with optional default netem and FIPS config overrides. See `lab/topology.py`. Each link is its own Docker network, so impairments and cuts hit exactly one link. Generated configs and keys go to `generated/` (git-ignored). The control sockets under `generated/<topology>/run/` are created by root inside the containers, so removing `generated/` needs `sudo`.

## Live view

`up` also starts a collector container that reads every node's control socket once a second and streams snapshots to the browser (Server-Sent Events, Python standard library only). The page shows:

- the spanning tree (bright edges, root in amber, depth per node) and the remaining mesh links (dim)
- link quality as colour, from teal to amber to red as RTT and loss rise
- traffic as particles, their rate following each link's byte counters
- a cut link as red dashes with a timer while FIPS still believes it is up, then dotted once FIPS declares it dead
- an event log: cuts, heals, links declared dead (with the time since the cut), parent and root changes

D3 is vendored in `web/vendor/`, so the lab runs fully offline.

The same faults are available in the browser: click a link to cut, heal or impair it (slow, lossy, flaky, narrow), click a node to kill or start it, shift-click nodes and press *partition* to split the mesh, *reset lab* to undo everything. The collector runs them through `lab/lab.py` with the host's Docker socket mounted, which is root-equivalent on the host: its port is bound to localhost, and `/action` only accepts JSON from the page's own origin.

## Scenarios

A scenario is a scripted fault sequence with measurements, repeated N times:

```sh
python3 lab/scenario.py cut-reroute                        # scenarios/cut-reroute.toml on ring6
python3 lab/scenario.py cut-reroute --topology ring6-fast  # same steps, other topology
```

Steps are actions (`cut`, `heal`, `netem`, `kill`, `start`, `partition`, `reset`, `sleep`) and waits (`wait_reachable`, `wait_unreachable`, `wait_link_dead`, `wait_link_up`); a wait with `record` stores the seconds since the last action. A wait that times out is kept as a censored measurement, not dropped. Each run writes `experiments/runs/<time>-<scenario>-<topology>/` with the scenario, metadata (topology, image, git commit), an event log, `measurements.csv` and the collector's per-second snapshots.

Options: `--repeat N`, `--settle S` (quiet seconds before each repetition), `--fresh` (new containers before every repetition), `--fips KEY=VALUE` (a variant of the topology with extra FIPS settings, same node identities). A wait with `capture = [nodes]` saves those nodes' full state when it times out, and every repetition keeps their logs; `FIPS_LOG=info,fips=debug` raises the nodes' log level.

## Write-ups

**[01 · Thirty seconds of silence](experiments/writeups/01-reroute/)**: how fast a FIPS mesh routes around a cut link. Short answer: the dead-link timeout plus about a second. The longer answer found two things worth taking upstream:

- In the first five minutes after nodes look each other up, a cut can strand traffic in a forwarding loop. A transit node keeps a destination's *verified* coordinates for 300 s and refuses the corrections in passing packets (an anti-poisoning rule), so replies bounce between two nodes until their hop limit runs out.
- A healed link is not used until the next scheduled reconnect attempt, and those back off exponentially: up to 76 s of a working link sitting idle in these runs.

**[02 · A timer through the wall](experiments/writeups/02-window/)**: a Bayesian model (PyMC) of every cut from 01, censored waits included, that infers the hidden verification window from the measurements alone: 300.1 ± 3.2 s, against 300 s in the source. It shows what each kind of evidence contributes and turns the posterior into outage predictions by time since start-up.

Serve the repo root (`python3 -m http.server`) and open `experiments/writeups/01-reroute/` or `02-window/`. To regenerate: the scripts in `experiments/`, then `python3 experiments/analyze.py`, then `uv run experiments/bayes/fit.py`.

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
