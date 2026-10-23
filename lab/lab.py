#!/usr/bin/env python3
"""FIPS Lab: build and drive a Docker mesh of FIPS nodes from a topology file.

    lab.py gen       <topology>            write node configs and a compose file
    lab.py up        <topology>            gen, start the containers, apply default netem
    lab.py down      <topology>            stop and remove the containers
    lab.py ping      <topology>            ping every ordered pair of nodes over the mesh
    lab.py status    <topology>            spanning tree and per-link quality

    lab.py netem     <topology> <a> <b> [delay=10ms] [jitter=2ms] [loss=1%] [rate=1mbit]
    lab.py netem     <topology> <a> <b> default   back to the topology's default impairment
    lab.py cut       <topology> <a> <b>    take the link down (both ends)
    lab.py heal      <topology> <a> <b>    bring it back up
    lab.py kill      <topology> <node>     kill the node's container (a crash, not a shutdown)
    lab.py start     <topology> <node>     start it again, restoring its links' cuts and netem
    lab.py partition <topology> <node>...  cut every link between these nodes and the rest
    lab.py reset     <topology>            start all nodes, heal all links, default netem

Topologies are topologies/<name>.toml; see lab/topology.py for the format.
Every link is its own Docker network, so impairments and cuts act on exactly
one link. Identities are generated once per topology and reused, so a node
keeps its npub (and mesh address) across runs.

The fault functions are shared with the collector (browser controls) and the
scenario runner, and raise LabError instead of exiting.
"""

import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from itertools import permutations
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import topology  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
IMAGE = "fips-lab-node:v0.5.3"
COLLECTOR_IMAGE = "fips-lab-collector"
COLLECTOR_PORT = 8080
UDP_PORT = 2121
NETEM_KEYS = ("delay", "jitter", "loss", "rate")
# tc takes these as separate argv entries, but they also arrive from the browser.
NETEM_VALUE = re.compile(r"^\d+(\.\d+)?(us|ms|s|%|bit|kbit|mbit|gbit)?$")


class LabError(Exception):
    pass


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


def identities(name, nodes, keys_from=None):
    """Load the topology's keys, generating any that are missing.

    A variant topology starts from its base topology's keys, so the same
    nodes keep the same identities across settings.
    """
    path = out_dir(name) / "keys.json"
    if not path.exists() and keys_from:
        identities(keys_from, nodes)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text((out_dir(keys_from) / "keys.json").read_text())
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
            # FIPS_LOG takes a tracing filter, e.g. FIPS_LOG=info,fips::node=debug.
            "environment": [f"RUST_LOG={os.environ.get('FIPS_LOG', 'info')}"],
            "volumes": [
                f"./nodes/{node}.yaml:/etc/fips/fips.yaml:ro",
                "./hosts:/etc/fips/hosts:ro",
                # Exposes the control socket to the collector.
                f"./run/{node}:/run/fips",
            ],
            "networks": {link.network: {"ipv4_address": link.ip(node)} for link in topo.links_of(node)},
        }
        for node in topo.nodes
    }
    # The collector reads the nodes' control sockets and, for the browser
    # controls, runs this module's fault functions against the Docker socket.
    # The repo is mounted at its host path so those functions find the same
    # files. It joins no link network: it never touches mesh traffic.
    services["collector"] = {
        "build": str(ROOT / "collector"),
        "image": COLLECTOR_IMAGE,
        "container_name": f"{project(topo.name)}-collector",
        "command": ["python3", "-u", str(ROOT / "collector" / "collector.py")],
        # A variant has no file of its own; the collector loads the base file.
        "environment": [f"TOPOLOGY={topo.name}", f"TOPOLOGY_BASE={topo.keys_from or topo.name}"],
        "ports": [f"127.0.0.1:{COLLECTOR_PORT}:8080"],
        "volumes": [
            f"{ROOT}:{ROOT}",
            "/var/run/docker.sock:/var/run/docker.sock",
        ],
    }
    networks = {
        link.network: {"driver": "bridge", "internal": True, "ipam": {"config": [{"subnet": link.subnet}]}}
        for link in topo.links
    }
    return {"name": project(topo.name), "services": services, "networks": networks}


def gen(topo):
    keys = identities(topo.name, topo.nodes, topo.keys_from)
    out = out_dir(topo.name)
    (out / "nodes").mkdir(parents=True, exist_ok=True)
    for node in topo.nodes:
        (out / "run" / node).mkdir(parents=True, exist_ok=True)
        path = out / "nodes" / f"{node}.yaml"
        path.write_text(json.dumps(node_config(topo, node, keys), indent=2))
        path.chmod(0o644)  # read by root inside the container; holds an nsec, but only lab keys
    # Every node can reach every other as <node>.fips.
    (out / "hosts").write_text("".join(f"{node} {keys[node]['npub']}\n" for node in topo.nodes))
    (out / "compose.yaml").write_text(json.dumps(compose_file(topo), indent=2))
    public = {node: {k: keys[node][k] for k in ("npub", "ipv6")} for node in topo.nodes}
    (out / "nodes.json").write_text(json.dumps(public, indent=2))
    links = [{"id": link.id, "a": link.a, "b": link.b} for link in topo.links]
    (out / "links.json").write_text(json.dumps(links, indent=2))
    # Both the host CLI and the collector (root, in a container) take this lock.
    lock = out / "labstate.lock"
    lock.touch()
    lock.chmod(0o666)
    print(f"generated {len(topo.nodes)} nodes, {len(topo.links)} links in {out.relative_to(ROOT)}")


def compose(topo, *args):
    subprocess.run(["docker", "compose", "-f", str(out_dir(topo.name) / "compose.yaml"), *args], check=True)


def stop_others(topo):
    """Take down any other lab topology: they all publish the collector port."""
    listing = run("docker", "compose", "ls", "--all", "--format", "json").stdout
    for entry in json.loads(listing or "[]"):
        name = entry["Name"]
        if name.startswith("fipslab-") and name != project(topo.name):
            print(f"stopping {name}")
            subprocess.run(["docker", "compose", "-p", name, "down"], check=True, capture_output=True)


def up(topo):
    stop_others(topo)
    gen(topo)
    with labstate(topo) as state:
        state.update(fresh_state())  # fresh containers: everything up, defaults
    compose(topo, "up", "-d", "--build")
    if topo.netem:
        parallel(lambda link: apply_netem(topo, link, topo.netem), topo.links)
        print(f"netem on all links: {' '.join(netem_args(topo.netem))}")
    print(f"visualization: http://localhost:{COLLECTOR_PORT}")


# ── Lab state ───────────────────────────────────────────────────────────
#
# What the lab has done to the mesh, as opposed to what FIPS believes:
#   cut      link ids taken down
#   netem    link id -> explicit impairment (absent: the topology default)
#   stopped  nodes killed
# The visualization shows both views; the difference is the interesting part.


def fresh_state():
    return {"cut": [], "netem": {}, "stopped": []}


@contextmanager
def labstate(topo):
    """Read-modify-write the lab state under a lock, written atomically."""
    out = out_dir(topo.name)
    path = out / "labstate.json"
    with open(out / "labstate.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = fresh_state()
        if path.exists():
            state.update(json.loads(path.read_text()))
        yield state
        tmp = path.with_name(f".labstate.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(state))
        tmp.chmod(0o644)
        os.replace(tmp, path)


def read_labstate(topo):
    path = out_dir(topo.name) / "labstate.json"
    state = fresh_state()
    if path.exists():
        state.update(json.loads(path.read_text()))
    return state


# ── Faults ──────────────────────────────────────────────────────────────


def iface(name, node, ip):
    """The container interface that holds a link's address."""
    for line in exec_in(name, node, "ip", "-o", "-4", "addr", "show").stdout.splitlines():
        fields = line.split()
        if fields[3].split("/")[0] == ip:
            return fields[1]
    raise LabError(f"{node} has no interface with {ip}")


def link_ends(topo, link):
    """(node, interface) for each end of a link whose node is running."""
    stopped = set(read_labstate(topo)["stopped"])
    return [(node, iface(topo.name, node, link.ip(node))) for node in (link.a, link.b) if node not in stopped]


def netem_args(params):
    args = []
    if "delay" in params:
        args += ["delay", params["delay"]] + ([params["jitter"]] if "jitter" in params else [])
    if "loss" in params:
        args += ["loss", params["loss"]]
    if "rate" in params:
        args += ["rate", params["rate"]]
    return args


def check_netem(params):
    unknown = set(params) - set(NETEM_KEYS)
    if unknown:
        raise LabError(f"unknown netem settings: {', '.join(sorted(unknown))}")
    for key, value in params.items():
        if not NETEM_VALUE.match(str(value)):
            raise LabError(f"bad netem value {key}={value}")
    return params


def apply_netem(topo, link, params, ends=None):
    """Impair both directions of a link alike; empty params remove impairment."""
    for node, dev in ends if ends is not None else link_ends(topo, link):
        if params:
            exec_in(topo.name, node, "tc", "qdisc", "replace", "dev", dev, "root", "netem", *netem_args(params))
        else:
            exec_in(topo.name, node, "tc", "qdisc", "del", "dev", dev, "root", check=False)


def set_netem(topo, link, params):
    """An explicit impairment for one link; None returns it to the topology default."""
    if params is not None:
        check_netem(params)
    apply_netem(topo, link, topo.netem if params is None else params)
    with labstate(topo) as state:
        if params is None:
            state["netem"].pop(str(link.id), None)
        else:
            state["netem"][str(link.id)] = params


def link_netem(topo, state, link):
    return state["netem"].get(str(link.id), topo.netem)


def cut(topo, link):
    for node, dev in link_ends(topo, link):
        exec_in(topo.name, node, "ip", "link", "set", "dev", dev, "down")
    with labstate(topo) as state:
        state["cut"] = sorted(set(state["cut"]) | {link.id})


def heal(topo, link):
    for node, dev in link_ends(topo, link):
        exec_in(topo.name, node, "ip", "link", "set", "dev", dev, "up")
    with labstate(topo) as state:
        state["cut"] = sorted(set(state["cut"]) - {link.id})


def partition(topo, group):
    """Cut every link with exactly one end in group; returns the links cut."""
    group = set(group)
    unknown = group - set(topo.nodes)
    if unknown:
        raise LabError(f"unknown nodes: {', '.join(sorted(unknown))}")
    if not group or group == set(topo.nodes):
        raise LabError("a partition needs nodes on both sides")
    crossing = [link for link in topo.links if (link.a in group) != (link.b in group)]
    parallel(lambda link: cut(topo, link), crossing)
    return crossing


def check_node(topo, node):
    if node not in topo.nodes:
        raise LabError(f"unknown node: {node}")


def kill(topo, node):
    check_node(topo, node)
    run("docker", "kill", container(topo.name, node))
    with labstate(topo) as state:
        state["stopped"] = sorted(set(state["stopped"]) | {node})


def start(topo, node):
    """Start a killed node and put its link interfaces back as the lab left them.

    A restarted container gets fresh interfaces: no qdisc, link up. So reapply
    each link's impairment and take its end down again if the link is cut.
    """
    check_node(topo, node)
    run("docker", "start", container(topo.name, node))
    with labstate(topo) as state:
        state["stopped"] = sorted(set(state["stopped"]) - {node})
        snapshot = dict(state)
    for link in topo.links_of(node):
        dev = iface(topo.name, node, link.ip(node))
        apply_netem(topo, link, link_netem(topo, snapshot, link), ends=[(node, dev)])
        if link.id in snapshot["cut"]:
            exec_in(topo.name, node, "ip", "link", "set", "dev", dev, "down")


def reset(topo):
    """Every node running, every link up, every link at the topology default."""
    state = read_labstate(topo)
    for node in state["stopped"]:
        run("docker", "start", container(topo.name, node))
    with labstate(topo) as fresh:
        fresh.update(fresh_state())

    def restore(link):
        ends = link_ends(topo, link)
        for node, dev in ends:
            exec_in(topo.name, node, "ip", "link", "set", "dev", dev, "up")
        apply_netem(topo, link, topo.netem, ends=ends)

    parallel(restore, topo.links)


# ── Observation ─────────────────────────────────────────────────────────


def ping_once(name, src, dst, count=3, wait=2):
    """Average RTT in ms, or None if no reply.

    By name, not mesh address: the DNS lookup is what gives the source node
    the destination's npub, which it needs before it can discover a route.
    """
    result = exec_in(
        name, src, "ping", "-6", "-c", str(count), "-i", "0.2", "-W", str(wait), f"{dst}.fips", check=False
    )
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
    start_time = time.monotonic()
    while pending and time.monotonic() - start_time < timeout:
        rtts = parallel(lambda pair: ping_once(topo.name, *pair), pending)
        now = time.monotonic() - start_time
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
    stopped = set(read_labstate(topo)["stopped"])

    def query(node):
        if node in stopped:
            return node, None, None
        return node, fipsctl_json(topo.name, node, "show", "tree"), fipsctl_json(topo.name, node, "show", "peers")

    state = {node: (tree, peers and peers["peers"]) for node, tree, peers in parallel(query, topo.nodes)}

    print("node  tree position")
    for node, (tree, _) in state.items():
        if tree is None:
            where = "stopped"
        elif tree["is_root"]:
            where = "root"
        else:
            where = f"depth {tree['depth']}, parent {tree['parent_display_name']}"
        print(f"  {node:<4}{where}")

    print("\nlink      state   srtt ms   loss    etx   tree edge")
    for link in topo.links:
        peers = state[link.a][1] or state[link.b][1] or []
        other = link.b if state[link.a][1] is not None else link.a
        peer = next((p for p in peers if p["display_name"] == other), None)
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


def parse_netem(pairs):
    if pairs in (["default"], ["clear"]):
        return None
    if not pairs or any("=" not in pair for pair in pairs):
        raise LabError(f"netem takes {', '.join(f'{k}=...' for k in NETEM_KEYS)} or 'default'")
    return dict(pair.split("=", 1) for pair in pairs)


COMMANDS = ["gen", "up", "down", "ping", "status", "netem", "cut", "heal", "kill", "start", "partition", "reset"]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=COMMANDS)
    parser.add_argument("topology")
    parser.add_argument("args", nargs="*")
    opts = parser.parse_args()
    topo = topology.load(ROOT, opts.topology)
    cmd, args = opts.command, opts.args

    try:
        if cmd in ("netem", "cut", "heal"):
            if len(args) < 2:
                parser.error(f"{cmd} needs two node names")
            link = topo.link(*args[:2])
        if cmd in ("kill", "start") and len(args) != 1:
            parser.error(f"{cmd} needs one node name")

        if cmd == "gen":
            gen(topo)
        elif cmd == "up":
            up(topo)
        elif cmd == "down":
            compose(topo, "down")
        elif cmd == "ping":
            sys.exit(0 if ping(topo) else 1)
        elif cmd == "status":
            status(topo)
        elif cmd == "netem":
            params = parse_netem(args[2:])
            set_netem(topo, link, params)
            shown = netem_args(topo.netem if params is None else params)
            print(f"{link.a}-{link.b}: {' '.join(shown) or 'no impairment'}")
        elif cmd == "cut":
            cut(topo, link)
            print(f"{link.a}-{link.b}: down")
        elif cmd == "heal":
            heal(topo, link)
            print(f"{link.a}-{link.b}: up")
        elif cmd == "kill":
            kill(topo, args[0])
            print(f"{args[0]}: killed")
        elif cmd == "start":
            start(topo, args[0])
            print(f"{args[0]}: started")
        elif cmd == "partition":
            crossing = partition(topo, args)
            print(f"cut {len(crossing)} links: {', '.join(f'{l.a}-{l.b}' for l in crossing)}")
        elif cmd == "reset":
            reset(topo)
            print("all nodes running, all links up, default netem")
    except (LabError, ValueError) as err:
        sys.exit(f"error: {err}")
    except subprocess.CalledProcessError as err:
        sys.exit(f"error: {' '.join(err.cmd)}: {err.stderr.strip()}")


if __name__ == "__main__":
    main()
