#!/usr/bin/env python3
"""FIPS Lab: build and drive a Docker mesh of FIPS nodes from a topology file.

    lab.py gen    <topology>               write node configs and a compose file
    lab.py up     <topology>               gen, start the containers, apply default netem
    lab.py down   <topology>               stop and remove the containers
    lab.py ping   <topology>               ping every ordered pair of nodes over the mesh
    lab.py status <topology>               spanning tree and per-link quality
    lab.py netem  <topology> <a> <b> [delay=10ms] [jitter=2ms] [loss=1%] [rate=1mbit]
    lab.py netem  <topology> <a> <b> clear
    lab.py cut    <topology> <a> <b>       take the link down (both ends)
    lab.py heal   <topology> <a> <b>       bring it back up

Topologies are topologies/<name>.toml; see lab/topology.py for the format.
Every link is its own Docker network, so impairments and cuts act on exactly
one link. Identities are generated once per topology and reused, so a node
keeps its npub (and mesh address) across runs.
"""

import argparse
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from itertools import permutations
from pathlib import Path

import topology

ROOT = Path(__file__).resolve().parent.parent
IMAGE = "fips-lab-node:v0.5.3"
UDP_PORT = 2121
NETEM_KEYS = ("delay", "jitter", "loss", "rate")


def out_dir(name):
    return ROOT / "generated" / name


def project(name):
    return f"fipslab-{name}"


def container(name, node):
    return f"{project(name)}-{node}"


def run(*cmd, check=True):
    return subprocess.run(cmd, check=check, capture_output=True, text=True)


def exec_in(name, node, *cmd, check=True):
    return run("docker", "exec", container(name, node), *cmd, check=check)


def parallel(fn, items):
    with ThreadPoolExecutor(max_workers=16) as pool:
        return list(pool.map(fn, items))


def fipsctl_local(*args):
    """Run fipsctl in a throwaway container; for commands that need no daemon."""
    return run("docker", "run", "--rm", "--entrypoint", "fipsctl", IMAGE, *args).stdout.split()


def fipsctl_json(name, node, *args):
    return json.loads(exec_in(name, node, "fipsctl", *args).stdout)


# ── Generation ──────────────────────────────────────────────────────────


def identities(name, nodes):
    """Load the topology's keys, generating any that are missing."""
    path = out_dir(name) / "keys.json"
    keys = json.loads(path.read_text()) if path.exists() else {}

    def make(node):
        nsec, npub = fipsctl_local("keygen", "--stdout")
        (ipv6,) = fipsctl_local("address", npub)
        return node, {"nsec": nsec, "npub": npub, "ipv6": ipv6}

    keys.update(parallel(make, [n for n in nodes if n not in keys]))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(keys, indent=2))
    path.chmod(0o600)
    return keys


def merge(base, extra):
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            merge(base[key], value)
        else:
            base[key] = value
    return base


def node_config(topo, node, keys):
    # JSON is valid YAML, so configs are written without a YAML library.
    config = {
        "node": {"identity": {"nsec": keys[node]["nsec"]}},
        "tun": {"enabled": True, "name": "fips0", "mtu": 1280},
        "dns": {"enabled": True},
        "transports": {"udp": {"bind_addr": f"0.0.0.0:{UDP_PORT}", "mtu": 1472}},
        "peers": [
            {
                "npub": keys[link.other(node)]["npub"],
                "alias": link.other(node),
                "addresses": [{"transport": "udp", "addr": f"{link.ip(link.other(node))}:{UDP_PORT}"}],
                "connect_policy": "auto_connect",
            }
            for link in topo.links_of(node)
        ],
    }
    return merge(config, topo.fips)


def compose_file(topo):
    services = {
        f"node-{node}": {
            "image": IMAGE,
            "container_name": container(topo.name, node),
            "hostname": f"node-{node}",
            "cap_add": ["NET_ADMIN"],
            "devices": ["/dev/net/tun:/dev/net/tun"],
            "sysctls": ["net.ipv6.conf.all.disable_ipv6=0"],
            "environment": ["RUST_LOG=info"],
            "volumes": [
                f"./nodes/{node}.yaml:/etc/fips/fips.yaml:ro",
                "./hosts:/etc/fips/hosts:ro",
            ],
            "networks": {link.network: {"ipv4_address": link.ip(node)} for link in topo.links_of(node)},
        }
        for node in topo.nodes
    }
    networks = {
        link.network: {"driver": "bridge", "internal": True, "ipam": {"config": [{"subnet": link.subnet}]}}
        for link in topo.links
    }
    return {"name": project(topo.name), "services": services, "networks": networks}


def gen(topo):
    keys = identities(topo.name, topo.nodes)
    out = out_dir(topo.name)
    (out / "nodes").mkdir(parents=True, exist_ok=True)
    for node in topo.nodes:
        path = out / "nodes" / f"{node}.yaml"
        path.write_text(json.dumps(node_config(topo, node, keys), indent=2))
        path.chmod(0o644)  # read by root inside the container; holds an nsec, but only lab keys
    # Every node can reach every other as <node>.fips.
    (out / "hosts").write_text("".join(f"{node} {keys[node]['npub']}\n" for node in topo.nodes))
    (out / "compose.yaml").write_text(json.dumps(compose_file(topo), indent=2))
    public = {node: {k: keys[node][k] for k in ("npub", "ipv6")} for node in topo.nodes}
    (out / "nodes.json").write_text(json.dumps(public, indent=2))
    print(f"generated {len(topo.nodes)} nodes, {len(topo.links)} links in {out.relative_to(ROOT)}")


def compose(topo, *args):
    subprocess.run(["docker", "compose", "-f", str(out_dir(topo.name) / "compose.yaml"), *args], check=True)


# ── Links ───────────────────────────────────────────────────────────────


def iface(name, node, ip):
    """The container interface that holds a link's address."""
    for line in exec_in(name, node, "ip", "-o", "-4", "addr", "show").stdout.splitlines():
        fields = line.split()
        if fields[3].split("/")[0] == ip:
            return fields[1]
    sys.exit(f"{node} has no interface with {ip}")


def link_ends(topo, link):
    return [(node, iface(topo.name, node, link.ip(node))) for node in (link.a, link.b)]


def netem_args(params):
    args = []
    if "delay" in params:
        args += ["delay", params["delay"]] + ([params["jitter"]] if "jitter" in params else [])
    if "loss" in params:
        args += ["loss", params["loss"]]
    if "rate" in params:
        args += ["rate", params["rate"]]
    return args


def set_netem(topo, link, params):
    """Impair both directions of a link alike; empty params clear it."""
    for node, dev in link_ends(topo, link):
        if params:
            exec_in(topo.name, node, "tc", "qdisc", "replace", "dev", dev, "root", "netem", *netem_args(params))
        else:
            exec_in(topo.name, node, "tc", "qdisc", "del", "dev", dev, "root", check=False)


def set_link_state(topo, link, state):
    for node, dev in link_ends(topo, link):
        exec_in(topo.name, node, "ip", "link", "set", "dev", dev, state)


def parse_netem(pairs):
    if pairs == ["clear"]:
        return {}
    params = dict(pair.split("=", 1) for pair in pairs)
    unknown = set(params) - set(NETEM_KEYS)
    if unknown or not params:
        sys.exit(f"netem takes {', '.join(f'{k}=...' for k in NETEM_KEYS)} or 'clear'")
    return params


# ── Observation ─────────────────────────────────────────────────────────


def ping_once(name, src, dst):
    # By name, not mesh address: the DNS lookup is what gives the source node
    # the destination's npub, which it needs before it can discover a route.
    result = exec_in(name, src, "ping", "-6", "-c", "3", "-i", "0.2", "-W", "2", f"{dst}.fips", check=False)
    if result.returncode != 0:
        return None
    # Last line: rtt min/avg/max/mdev = 0.1/0.2/0.3/0.0 ms
    return float(result.stdout.strip().splitlines()[-1].split("=")[1].split("/")[1])


def ping(topo, timeout=90):
    """Ping all ordered pairs, retrying until each succeeds or the timeout passes.

    Retrying matters: a multi-hop destination is only reachable once discovery
    has found it, which takes a moment after the containers start.
    """
    pending = list(permutations(topo.nodes, 2))
    results = {}
    start = time.monotonic()
    while pending and time.monotonic() - start < timeout:
        rtts = parallel(lambda pair: ping_once(topo.name, *pair), pending)
        now = time.monotonic() - start
        for pair, rtt in zip(list(pending), rtts):
            if rtt is not None:
                results[pair] = (rtt, now)
                pending.remove(pair)
        if pending:
            time.sleep(1)

    for (src, dst), (rtt, at) in sorted(results.items()):
        print(f"  ok    {src:>3} -> {dst:<3}  rtt {rtt:7.3f} ms   reachable after {at:4.1f} s")
    for src, dst in pending:
        print(f"  FAIL  {src:>3} -> {dst:<3}")
    print(f"{len(results)}/{len(results) + len(pending)} pairs reachable")
    return not pending


def status(topo):
    def query(node):
        return node, fipsctl_json(topo.name, node, "show", "tree"), fipsctl_json(topo.name, node, "show", "peers")

    state = {node: (tree, peers["peers"]) for node, tree, peers in parallel(query, topo.nodes)}

    print("node  tree position")
    for node, (tree, _) in state.items():
        where = "root" if tree["is_root"] else f"depth {tree['depth']}, parent {tree['parent_display_name']}"
        print(f"  {node:<4}{where}")

    print("\nlink      state   srtt ms   loss    etx   tree edge")
    for link in topo.links:
        peer = next((p for p in state[link.a][1] if p["display_name"] == link.b), None)
        if peer is None:
            print(f"  {link.a}-{link.b:<6} down")
            continue
        mmp = peer.get("mmp") or {}
        tree_edge = "yes" if peer["is_parent"] or peer["is_child"] else ""
        print(
            f"  {link.a}-{link.b:<6} up    {mmp.get('srtt_ms', 0):8.2f}  {mmp.get('loss_rate', 0):5.1%}"
            f"  {mmp.get('etx', 0):5.2f}   {tree_edge}"
        )


# ── CLI ─────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["gen", "up", "down", "ping", "status", "netem", "cut", "heal"])
    parser.add_argument("topology")
    parser.add_argument("args", nargs="*")
    opts = parser.parse_args()
    topo = topology.load(ROOT, opts.topology)

    if opts.command in ("netem", "cut", "heal"):
        if len(opts.args) < 2:
            parser.error(f"{opts.command} needs two node names")
        link = topo.link(*opts.args[:2])

    if opts.command == "gen":
        gen(topo)
    elif opts.command == "up":
        gen(topo)
        compose(topo, "up", "-d")
        if topo.netem:
            parallel(lambda link: set_netem(topo, link, topo.netem), topo.links)
            print(f"netem on all links: {' '.join(netem_args(topo.netem))}")
    elif opts.command == "down":
        compose(topo, "down")
    elif opts.command == "ping":
        sys.exit(0 if ping(topo) else 1)
    elif opts.command == "status":
        status(topo)
    elif opts.command == "netem":
        params = parse_netem(opts.args[2:])
        set_netem(topo, link, params)
        print(f"{link.a}-{link.b}: {' '.join(netem_args(params)) or 'cleared'}")
    elif opts.command == "cut":
        set_link_state(topo, link, "down")
        print(f"{link.a}-{link.b}: down")
    elif opts.command == "heal":
        set_link_state(topo, link, "up")
        print(f"{link.a}-{link.b}: up")


if __name__ == "__main__":
    main()
