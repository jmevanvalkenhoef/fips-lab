#!/usr/bin/env python3
"""Run a fault scenario against a lab topology and record what happened.

    scenario.py <scenario> [--topology NAME] [--repeat N] [--fips KEY=VALUE ...]

--fips runs a variant of the topology with extra FIPS settings, e.g.
--fips node.link_dead_timeout_secs=10. Variants keep the base topology's node
identities, so the spanning tree is the same across settings.

A scenario is scenarios/<name>.toml:

    topology = "ring6"
    repeat = 5          # repetitions
    settle = 15         # seconds of quiet after the reset before each repetition

    [[steps]]
    do = "cut"
    link = ["a", "b"]

    [[steps]]
    do = "wait_reachable"
    from = "a"
    to = "b"
    timeout = 90
    record = "reroute_s"

Actions: cut, heal (link), netem (link, params table or "default"),
kill, start (node), partition (nodes), reset, sleep (seconds).
Waits: wait_reachable, wait_unreachable (from, to), wait_link_dead,
wait_link_up (link). A wait with `record` stores the seconds since the most
recent action; a wait that times out is stored as censored (the real value is
longer than the timeout), not dropped. A wait with `capture = ["a", "b"]`
saves those nodes' full state when it times out (every `fipsctl show`, recent
logs, and for a reachability wait a `fipsctl probe` of the target), for
diagnosing what got stuck.

Each run writes experiments/runs/<timestamp>-<scenario>/:
    scenario.toml      the scenario as run
    meta.json          topology, FIPS image, git commit, start time
    events.jsonl       every action and wait, with timestamps
    measurements.csv   repetition, measurement, seconds, censored
    snapshots.jsonl    the collector's snapshot every second, for replay
    captures/          node state at each timed-out wait with `capture`
    logs/              every repetition's FIPS logs of the nodes named in a `capture`
"""

import argparse
import csv
import json
import shutil
import statistics
import subprocess
import sys
import threading
import time
import tomllib
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lab  # noqa: E402
import topology  # noqa: E402

ROOT = lab.ROOT
POLL = 0.25
SNAPSHOT_URL = f"http://localhost:{lab.COLLECTOR_PORT}/snapshot"
ACTIONS = {"cut", "heal", "netem", "kill", "start", "partition", "reset", "sleep"}
WAITS = {"wait_reachable", "wait_unreachable", "wait_link_dead", "wait_link_up"}
SHOW = ["status", "tree", "peers", "links", "sessions", "routing", "cache", "identity-cache", "mmp"]


def running(topo):
    result = lab.run("docker", "inspect", "-f", "{{.State.Running}}", lab.container(topo.name, topo.nodes[0]), check=False)
    return result.stdout.strip() == "true"


def link_up(topo, link):
    """Whether FIPS on either running end currently lists the other as a peer."""
    stopped = set(lab.read_labstate(topo)["stopped"])
    for here, there in ((link.a, link.b), (link.b, link.a)):
        if here in stopped:
            continue
        result = lab.exec_in(topo.name, here, "fipsctl", "show", "peers", check=False)
        if result.returncode == 0:
            return any(p["display_name"] == there for p in json.loads(result.stdout)["peers"])
    return False


def reachable(topo, src, dst):
    return lab.ping_once(topo.name, src, dst, count=1, wait=1) is not None


class Recorder:
    """Appends events, and in the background the collector's snapshots."""

    def __init__(self, run_dir):
        self.events = open(run_dir / "events.jsonl", "a")
        self.snapshots = open(run_dir / "snapshots.jsonl", "a")
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.record_snapshots, daemon=True)
        self.thread.start()

    def event(self, **fields):
        self.events.write(json.dumps({"t": time.time(), **fields}) + "\n")
        self.events.flush()

    def record_snapshots(self):
        while not self.stop.wait(1):
            try:
                with urllib.request.urlopen(SNAPSHOT_URL, timeout=3) as res:
                    self.snapshots.write(res.read().decode() + "\n")
                    self.snapshots.flush()
            except OSError:
                pass  # collector restarting; the events still carry the run

    def close(self):
        self.stop.set()
        self.thread.join()
        self.events.close()
        self.snapshots.close()


def do_action(topo, step):
    kind = step["do"]
    if kind in ("cut", "heal", "netem"):
        link = topo.link(*step["link"])
        if kind == "cut":
            lab.cut(topo, link)
        elif kind == "heal":
            lab.heal(topo, link)
        else:
            params = step["params"]
            lab.set_netem(topo, link, None if params == "default" else params)
    elif kind == "kill":
        lab.kill(topo, step["node"])
    elif kind == "start":
        lab.start(topo, step["node"])
    elif kind == "partition":
        lab.partition(topo, step["nodes"])
    elif kind == "reset":
        lab.reset(topo)
    elif kind == "sleep":
        time.sleep(step["seconds"])


def condition(topo, step):
    kind = step["do"]
    if kind == "wait_reachable":
        return lambda: reachable(topo, step["from"], step["to"])
    if kind == "wait_unreachable":
        return lambda: not reachable(topo, step["from"], step["to"])
    link = topo.link(*step["link"])
    if kind == "wait_link_dead":
        return lambda: not link_up(topo, link)
    return lambda: link_up(topo, link)


def wait(check, timeout):
    """Seconds until check() holds, or None on timeout."""
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        if check():
            return time.monotonic() - start
        time.sleep(POLL)
    return None


def capture(topo, step, out):
    """Save everything the named nodes can tell about themselves, plus a probe."""
    out.mkdir(parents=True, exist_ok=True)
    for node in step["capture"]:
        for what in SHOW:
            result = lab.exec_in(topo.name, node, "fipsctl", "show", what, check=False)
            (out / f"{node}-{what}.json").write_text(result.stdout or result.stderr)
        logs = lab.run("docker", "logs", "--since", "180s", lab.container(topo.name, node), check=False)
        (out / f"{node}.log").write_text(logs.stdout + logs.stderr)
    if "from" in step and "to" in step:
        try:
            result = subprocess.run(
                ["docker", "exec", lab.container(topo.name, step["from"]), "fipsctl", "probe", step["to"]],
                capture_output=True, text=True, timeout=60,
            )
            text = result.stdout + result.stderr
        except subprocess.TimeoutExpired:
            text = "probe did not finish within 60 s"
        (out / f"probe-{step['from']}-{step['to']}.txt").write_text(text)


def save_logs(topo, nodes, since, out):
    """Save the nodes' logs since a Unix time, before a fresh start discards them."""
    out.mkdir(parents=True, exist_ok=True)
    for node in nodes:
        logs = lab.run("docker", "logs", "--since", f"{since:.3f}", lab.container(topo.name, node), check=False)
        (out / f"{node}.log").write_text(logs.stdout + logs.stderr)


def validate(topo, steps):
    for i, step in enumerate(steps, 1):
        kind = step.get("do")
        if kind not in ACTIONS | WAITS:
            sys.exit(f"step {i}: unknown step '{kind}'")
        if "link" in step:
            topo.link(*step["link"])  # raises on an unknown link
        for key in ("node", "from", "to"):
            if key in step and step[key] not in topo.nodes:
                sys.exit(f"step {i}: unknown node '{step[key]}'")
        for node in step.get("capture", []):
            if node not in topo.nodes:
                sys.exit(f"step {i}: unknown node '{node}' in capture")


def git_commit():
    result = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"], capture_output=True, text=True)
    dirty = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain"], capture_output=True, text=True).stdout
    return result.stdout.strip() + ("-dirty" if dirty.strip() else "")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("scenario")
    parser.add_argument("--topology", help="run against this topology instead of the scenario's")
    parser.add_argument("--repeat", type=int, help="override the number of repetitions")
    parser.add_argument("--fresh", action="store_true",
                        help="restart the whole mesh before every repetition instead of resetting it")
    parser.add_argument("--settle", type=float, help="override the scenario's settle time")
    parser.add_argument("--fips", action="append", default=[], metavar="KEY=VALUE",
                        help="extra FIPS setting, e.g. node.link_dead_timeout_secs=10 (repeatable)")
    opts = parser.parse_args()

    path = ROOT / "scenarios" / f"{opts.scenario}.toml"
    spec = tomllib.loads(path.read_text())
    overrides = dict(topology.parse_override(o) for o in opts.fips)
    base = topology.load(ROOT, opts.topology or spec["topology"])
    topo = topology.variant(base, overrides)
    steps = spec["steps"]
    repeat = opts.repeat or spec.get("repeat", 1)
    settle = opts.settle if opts.settle is not None else spec.get("settle", 15)
    validate(topo, steps)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = ROOT / "experiments" / "runs" / f"{stamp}-{opts.scenario}-{topo.name}"
    run_dir.mkdir(parents=True)
    shutil.copy(path, run_dir / "scenario.toml")
    (run_dir / "meta.json").write_text(json.dumps({
        "scenario": opts.scenario,
        "topology": topo.name,
        "base_topology": base.name,
        "overrides": overrides,
        "nodes": topo.nodes,
        "links": [[l.a, l.b] for l in topo.links],
        "default_netem": topo.netem,
        "fips_overrides": topo.fips,
        "image": lab.IMAGE,
        "git": git_commit(),
        "repeat": repeat,
        "settle_s": settle,
        "fresh": opts.fresh,
        "started": datetime.now().isoformat(timespec="seconds"),
    }, indent=2))

    if not opts.fresh:  # with --fresh, every repetition starts and checks its own mesh
        if not running(topo):
            print(f"starting {topo.name}")
            lab.up(topo)
        print(f"waiting for {topo.name} to converge")
        if not lab.ping(topo):
            sys.exit("topology did not converge; not running the scenario")

    recorder = Recorder(run_dir)
    measurements = []
    log_nodes = list(dict.fromkeys(n for step in steps for n in step.get("capture", [])))
    try:
        for rep in range(1, repeat + 1):
            # Before a fresh start, so the logs include the convergence check.
            rep_start = time.time()
            if opts.fresh:
                # New containers: every node starts with empty caches and sessions.
                lab.compose(topo, "down")
                lab.up(topo)
                if not lab.ping(topo):
                    sys.exit(f"rep {rep}: topology did not converge after restart")
                recorder.event(rep=rep, step="fresh-start")
            lab.reset(topo)
            recorder.event(rep=rep, step="reset+settle", seconds=settle)
            time.sleep(settle)
            last_action = time.monotonic()
            for i, step in enumerate(steps, 1):
                kind = step["do"]
                if kind in ACTIONS:
                    do_action(topo, step)
                    last_action = time.monotonic()
                    recorder.event(rep=rep, step=i, action=kind, **{k: v for k, v in step.items() if k != "do"})
                    continue
                timeout = step.get("timeout", 120)
                took = wait(condition(topo, step), timeout)
                since_action = time.monotonic() - last_action
                recorder.event(rep=rep, step=i, wait=kind, waited_s=took, since_action_s=since_action,
                               timed_out=took is None)
                if took is None and step.get("capture"):
                    capture(topo, step, run_dir / "captures" / f"rep{rep:02d}-step{i}")
                    recorder.event(rep=rep, step=i, captured=step["capture"])
                if "record" in step:
                    censored = took is None
                    measurements.append((rep, step["record"], round(since_action, 3), censored))
                    shown = f">{since_action:.1f} s (timeout)" if censored else f"{since_action:.1f} s"
                    print(f"  rep {rep}/{repeat}  {step['record']:<16} {shown}", flush=True)
            if log_nodes:
                save_logs(topo, log_nodes, rep_start, run_dir / "logs" / f"rep{rep:02d}")
    finally:
        recorder.close()
        lab.reset(topo)
        with open(run_dir / "measurements.csv", "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["rep", "measurement", "seconds", "censored"])
            writer.writerows(measurements)

    print(f"\n{run_dir.relative_to(ROOT)}")
    for name in dict.fromkeys(m[1] for m in measurements):
        values = [m[2] for m in measurements if m[1] == name and not m[3]]
        censored = sum(1 for m in measurements if m[1] == name and m[3])
        if values:
            print(f"  {name:<16} n={len(values)}  median {statistics.median(values):.1f} s"
                  f"  range {min(values):.1f}–{max(values):.1f} s" + (f"  ({censored} timed out)" if censored else ""))
        else:
            print(f"  {name:<16} all {censored} timed out")


if __name__ == "__main__":
    main()
