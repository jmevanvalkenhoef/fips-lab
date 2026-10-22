#!/usr/bin/env python3
"""FIPS Lab: build and drive a Docker mesh of FIPS nodes from a topology file.

Usage:
    lab.py gen  <topology>   write node configs and a compose file
    lab.py up   <topology>   gen, then start the containers
    lab.py down <topology>   stop and remove the containers
    lab.py ping <topology>   ping every ordered pair of nodes over the mesh

A topology is topologies/<name>.toml with a `nodes` list and a `links` list
of node pairs. Identities are generated once per topology and reused, so a
node keeps its npub (and mesh address) across runs.
"""

import json
import subprocess
import sys
import time
import tomllib
from itertools import permutations
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IMAGE = "fips-lab-node:v0.5.3"
UDP_PORT = 2121


def load_topology(name):
    topo = tomllib.loads((ROOT / "topologies" / f"{name}.toml").read_text())
    nodes = topo["nodes"]
    for a, b in topo["links"]:
        if a not in nodes or b not in nodes:
            sys.exit(f"link {a}-{b} names an unknown node")
    return topo


def out_dir(name):
    return ROOT / "generated" / name


def project(name):
    return f"fipslab-{name}"


def container(name, node):
    return f"{project(name)}-{node}"


def fipsctl(*args):
    """Run fipsctl in a throwaway container; for commands that need no daemon."""
    result = subprocess.run(
        ["docker", "run", "--rm", "--entrypoint", "fipsctl", IMAGE, *args],
        check=True, capture_output=True, text=True,
    )
    return result.stdout.split()


def identities(name, nodes):
    """Load the topology's keys, generating any that are missing."""
    path = out_dir(name) / "keys.json"
    keys = json.loads(path.read_text()) if path.exists() else {}
    for node in nodes:
        if node not in keys:
            nsec, npub = fipsctl("keygen", "--stdout")
            (ipv6,) = fipsctl("address", npub)
            keys[node] = {"nsec": nsec, "npub": npub, "ipv6": ipv6}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(keys, indent=2))
    path.chmod(0o600)
    return keys


def node_config(node, peers, keys):
    # JSON is valid YAML, so configs are written without a YAML library.
    return {
        "node": {"identity": {"nsec": keys[node]["nsec"]}},
        "tun": {"enabled": True, "name": "fips0", "mtu": 1280},
        "dns": {"enabled": True},
        "transports": {"udp": {"bind_addr": f"0.0.0.0:{UDP_PORT}", "mtu": 1472}},
        "peers": [
            {
                "npub": keys[peer]["npub"],
                "alias": peer,
                "addresses": [{"transport": "udp", "addr": f"node-{peer}:{UDP_PORT}"}],
                "connect_policy": "auto_connect",
            }
            for peer in peers
        ],
    }


def compose_file(name, nodes):
    services = {
        f"node-{node}": {
            "image": IMAGE,
            "container_name": container(name, node),
            "hostname": f"node-{node}",
            "cap_add": ["NET_ADMIN"],
            "devices": ["/dev/net/tun:/dev/net/tun"],
            "sysctls": ["net.ipv6.conf.all.disable_ipv6=0"],
            "environment": ["RUST_LOG=info"],
            "volumes": [
                f"./nodes/{node}.yaml:/etc/fips/fips.yaml:ro",
                "./hosts:/etc/fips/hosts:ro",
            ],
            "networks": ["lab"],
        }
        for node in nodes
    }
    return {"name": project(name), "services": services, "networks": {"lab": {"driver": "bridge"}}}


def gen(name):
    topo = load_topology(name)
    nodes = topo["nodes"]
    keys = identities(name, nodes)
    peers = {node: [] for node in nodes}
    for a, b in topo["links"]:
        peers[a].append(b)
        peers[b].append(a)

    out = out_dir(name)
    (out / "nodes").mkdir(parents=True, exist_ok=True)
    for node in nodes:
        path = out / "nodes" / f"{node}.yaml"
        path.write_text(json.dumps(node_config(node, peers[node], keys), indent=2))
        path.chmod(0o644)  # read by root inside the container; holds an nsec, but only lab keys
    # Every node can reach every other as <node>.fips.
    (out / "hosts").write_text("".join(f"{node} {keys[node]['npub']}\n" for node in nodes))
    (out / "compose.yaml").write_text(json.dumps(compose_file(name, nodes), indent=2))
    public = {node: {k: keys[node][k] for k in ("npub", "ipv6")} for node in nodes}
    (out / "nodes.json").write_text(json.dumps(public, indent=2))
    print(f"generated {len(nodes)} nodes in {out.relative_to(ROOT)}")


def compose(name, *args):
    subprocess.run(["docker", "compose", "-f", str(out_dir(name) / "compose.yaml"), *args], check=True)


def ping_once(name, src, dst):
    # By name, not mesh address: the DNS lookup is what gives the source node
    # the destination's npub, which it needs before it can discover a route.
    result = subprocess.run(
        ["docker", "exec", container(name, src), "ping", "-6", "-c", "3", "-i", "0.2", "-W", "2", f"{dst}.fips"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return None
    # Last line: rtt min/avg/max/mdev = 0.1/0.2/0.3/0.0 ms
    return float(result.stdout.strip().splitlines()[-1].split("=")[1].split("/")[1])


def ping(name, timeout=60):
    """Ping all ordered pairs, retrying until each succeeds or the timeout passes.

    Retrying matters: a multi-hop destination is only reachable once discovery
    has found it, which takes a moment after the containers start.
    """
    topo = load_topology(name)
    pending = list(permutations(topo["nodes"], 2))
    results = {}
    start = time.monotonic()
    while pending and time.monotonic() - start < timeout:
        for src, dst in list(pending):
            rtt = ping_once(name, src, dst)
            if rtt is not None:
                results[(src, dst)] = (rtt, time.monotonic() - start)
                pending.remove((src, dst))
        if pending:
            time.sleep(1)

    for (src, dst), (rtt, at) in sorted(results.items()):
        print(f"  ok    {src} -> {dst}   rtt {rtt:6.3f} ms   reachable after {at:4.1f} s")
    for src, dst in pending:
        print(f"  FAIL  {src} -> {dst}")
    total = len(results) + len(pending)
    print(f"{len(results)}/{total} pairs reachable")
    return not pending


def main():
    if len(sys.argv) != 3 or sys.argv[1] not in ("gen", "up", "down", "ping"):
        sys.exit(__doc__)
    cmd, name = sys.argv[1:]
    if cmd == "gen":
        gen(name)
    elif cmd == "up":
        gen(name)
        compose(name, "up", "-d")
    elif cmd == "down":
        compose(name, "down")
    elif cmd == "ping":
        sys.exit(0 if ping(name) else 1)


if __name__ == "__main__":
    main()
