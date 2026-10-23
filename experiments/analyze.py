#!/usr/bin/env python3
"""Turn scenario runs into the curated dataset behind write-up 01.

    analyze.py [--out experiments/writeups/01-reroute/data.json]

Reads experiments/runs/*/ and, per repetition of a cut, derives:

    reroute_s   seconds from the cut until a reaches b again (runner measurement)
    censored    the reroute wait timed out; the true value is longer
    dead_s      seconds from the cut until FIPS no longer lists the link,
                from the collector snapshots (1 s resolution). Taken from the
                snapshots rather than the runner's wait, because that wait only
                starts after the reroute wait ends.
    relink_s    seconds from the heal until FIPS lists the link again

Runs of cut-reroute with a link_dead_timeout_secs override form the sweep;
stuck-reroute runs form the stuck-reroute investigation, stuck-unlock runs
(stuck cuts left alone) `unlock`; the remaining
cut-reroute runs (the first ones, with a long reroute timeout and therefore
long cuts) form `early`. Standard library only.
"""

import argparse
import csv
import json
import re
import statistics
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "experiments" / "runs"
CUT_LINK = {"a", "b"}


def load_jsonl(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def first_snapshot(snapshots, after, until, test):
    """Seconds after `after` of the first snapshot in (after, until] passing test."""
    for snap in snapshots:
        if after < snap["t"] <= until and test(snap):
            return round(snap["t"] - after, 1)
    return None


def link_up(snap):
    link = next(l for l in snap["links"] if {l["a"], l["b"]} == CUT_LINK)
    return link["fips_up"]


def tree_before(snapshots, cut):
    """Root and a's parent in the last snapshot before the cut."""
    before = [s for s in snapshots if s["t"] <= cut]
    if not before:
        return None, None
    nodes = {n["id"]: n for n in before[-1]["nodes"]}
    root = next((n for n, info in nodes.items() if info["is_root"]), None)
    return root, nodes.get("a", {}).get("parent")


def since_start(rep_events, events, cut):
    """Seconds from the mesh's start to the cut, if this run started the mesh."""
    fresh = [e["t"] for e in rep_events if e.get("step") == "fresh-start"]
    if fresh:
        return round(cut - fresh[0], 1)
    return None


def repetitions(run_dir):
    events = load_jsonl(run_dir / "events.jsonl")
    snapshots = load_jsonl(run_dir / "snapshots.jsonl")
    with open(run_dir / "measurements.csv") as f:
        measured = defaultdict(dict)
        for row in csv.DictReader(f):
            measured[int(row["rep"])][row["measurement"]] = (float(row["seconds"]), row["censored"] == "True")

    by_rep = defaultdict(list)
    for event in events:
        if isinstance(event.get("rep"), int):
            by_rep[event["rep"]].append(event)

    reps = []
    for rep, evs in sorted(by_rep.items()):
        cut = next((e["t"] for e in evs if e.get("action") == "cut"), None)
        heal = next((e["t"] for e in evs if e.get("action") == "heal"), None)
        if cut is None or "reroute_s" not in measured[rep]:
            continue  # interrupted repetition
        end = heal or cut + 600
        reroute, censored = measured[rep]["reroute_s"]
        relink = measured[rep].get("relink_s")
        root, a_parent = tree_before(snapshots, cut)
        dead_s = first_snapshot(snapshots, cut, end, lambda s: not link_up(s))
        if dead_s is None and "declared_dead_s" in measured[rep] and not censored:
            # Healed within one snapshot interval of being declared dead; the
            # runner's own wait (which followed a successful reroute) has it.
            seconds, dead_censored = measured[rep]["declared_dead_s"]
            dead_s = None if dead_censored else round(seconds, 1)
        reps.append({
            # Per repetition: in runs before --fresh covered rep 1, it ran on
            # the mesh as it was.
            "fresh": any(e.get("step") == "fresh-start" for e in evs),
            "since_start_s": since_start(evs, events, cut),
            "root": root,
            "a_parent": a_parent,
            "rep": rep,
            "reroute_s": reroute,
            "censored": censored,
            "dead_s": dead_s,
            "cut_duration_s": round(heal - cut, 1) if heal else None,
            "relink_s": relink[0] if relink and not relink[1] else None,
        })
    return reps


PROBE_STAGE = re.compile(r"^\s{2}(bloom|discovery|path|session|rtt)\s+(\w+)\s+[\d.]+s\s+(.*)$")


def probe_summary(capture_dir):
    """Per-stage verdicts from a captured `fipsctl probe`, plus the route line."""
    probes = list(capture_dir.glob("probe-*.txt"))
    if not probes:
        return None
    text = probes[0].read_text()
    stages = {m.group(1): f"{m.group(2)}: {m.group(3).strip()}"
              for line in text.splitlines() if (m := PROBE_STAGE.match(line))}
    route = next((line.strip() for line in text.splitlines() if line.strip().startswith("route")), None)
    return {"stages": stages, "route": route}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default=str(ROOT / "experiments" / "writeups" / "01-reroute" / "data.json"))
    opts = parser.parse_args()

    sweep, stuck, early, unlock, sources = [], [], [], [], []
    for run_dir in sorted(RUNS.iterdir()):
        meta_path = run_dir / "meta.json"
        if not meta_path.exists() or not (run_dir / "measurements.csv").exists():
            continue
        meta = json.loads(meta_path.read_text())
        overrides = meta.get("overrides", {})
        dead = overrides.get("node.link_dead_timeout_secs")
        heartbeat = overrides.get("node.heartbeat_interval_secs")
        if meta["scenario"] == "cut-reroute" and dead is not None and meta.get("base_topology") == "ring6":
            for r in repetitions(run_dir):
                sweep.append({"dead_timeout_s": dead, "heartbeat_s": heartbeat, **r})
            sources.append(run_dir.name)
        elif meta["scenario"] == "cut-reroute" and dead is None:
            settings = meta.get("fips_overrides", {}).get("node", {})
            for r in repetitions(run_dir):
                early.append({
                    "identities": meta.get("base_topology", meta["topology"]),
                    "dead_timeout_s": settings.get("link_dead_timeout_secs", 30),
                    "heartbeat_s": settings.get("heartbeat_interval_secs", 10),
                    **r,
                })
            sources.append(run_dir.name)
        elif meta["scenario"] == "stuck-unlock":
            # Stuck cuts left alone: when does a reach b again, counted from
            # the fresh start (just after the convergence check's lookups)?
            for r in repetitions(run_dir):
                unlock.append({**r, "back_since_start_s":
                               round(r["since_start_s"] + r["reroute_s"], 1) if r["since_start_s"] is not None else None})
            sources.append(run_dir.name)
        elif meta["scenario"] == "stuck-reroute":
            # Fast timeouts come either from --fips overrides on ring6 or from
            # the ring6-fast topology file, which also has its own identities.
            settings = meta.get("fips_overrides", {}).get("node", {})
            for r in repetitions(run_dir):
                capture = run_dir / "captures" / f"rep{r['rep']:02d}-step2"
                stuck.append({
                    "identities": meta.get("base_topology", meta["topology"]),
                    "dead_timeout_s": settings.get("link_dead_timeout_secs"),
                    "heartbeat_s": settings.get("heartbeat_interval_secs"),
                    "settle_s": meta.get("settle_s"),
                    **r,
                    "probe": probe_summary(capture) if capture.exists() else None,
                })
            sources.append(run_dir.name)

    image = git = None
    if sources:
        meta = json.loads((RUNS / sources[-1] / "meta.json").read_text())
        image, git = meta["image"], meta["git"]

    out = Path(opts.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "image": image,
        "git": git,
        "topology": "ring6 (6 nodes in a ring, 5 ms ± 1 ms per link)",
        "cut": "a—b",
        "sources": sources,
        "sweep": sweep,
        "stuck": stuck,
        "early": early,
        "unlock": unlock,
    }, indent=1))
    print(f"wrote {out}: {len(sweep)} sweep reps, {len(stuck)} stuck-reroute reps, {len(early)} early reps")

    by_setting = defaultdict(list)
    for r in sweep:
        by_setting[r["dead_timeout_s"]].append(r)
    for dead, reps in sorted(by_setting.items()):
        ok = [r["reroute_s"] for r in reps if not r["censored"]]
        deads = [r["dead_s"] for r in reps if r["dead_s"] is not None]
        print(f"  dead timeout {dead:>2} s: n={len(reps)}  reroute median "
              f"{statistics.median(ok) if ok else float('nan'):5.1f} s  "
              f"declared dead median {statistics.median(deads) if deads else float('nan'):5.1f} s  "
              f"stuck {sum(r['censored'] for r in reps)}")
    for r in unlock:
        print(f"  unlock rep {r['rep']}: cut {r['since_start_s']} s after start, "
              f"{'still stuck after' if r['censored'] else 'back after'} {r['reroute_s']:.1f} s "
              f"= {r['back_since_start_s']} s after start")
    groups = defaultdict(list)
    for r in stuck:
        groups[(r["identities"], r["fresh"], r["settle_s"])].append(r)
    for (identities, fresh, settle), reps in sorted(groups.items(), key=lambda g: str(g[0])):
        ok = [r["reroute_s"] for r in reps if not r["censored"]]
        how = f"fresh start, settle {settle:g} s" if fresh else f"reset, settle {settle:g} s"
        print(f"  stuck-reroute, {identities} identities, {how}: {sum(r['censored'] for r in reps)}/{len(reps)} stuck, "
              f"reroute median {statistics.median(ok) if ok else float('nan'):.1f} s, "
              f"reps over 10 s: {[r['rep'] for r in reps if r['reroute_s'] > 10]}")


if __name__ == "__main__":
    main()
