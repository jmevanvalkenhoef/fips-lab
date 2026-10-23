#!/usr/bin/env python3
"""Poll every lab node's control socket and stream mesh snapshots to browsers.

Runs in its own container next to the nodes, with the repo mounted at its
host path. Each node's /run/fips is bind-mounted from generated/<topology>/run,
so the collector talks to the control sockets directly instead of exec'ing
into containers.

    GET  /           the visualization (static files from web/)
    GET  /snapshot   the latest snapshot as JSON
    GET  /events     a Server-Sent Events stream of snapshots, one per poll
    POST /action     run a fault: {"action": "cut", "a": "a", "b": "b"}, ...

Actions go through lab/lab.py, the same functions the CLI uses, and act on
Docker through the mounted socket. That is root-equivalent on the host, so the
port is bound to localhost and /action only takes JSON from this page's own
origin; a JSON content type forces a CORS preflight, which is never answered.

Standard library only; the image adds just the Docker CLI.
"""

import json
import os
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lab"))
import lab  # noqa: E402
import topology  # noqa: E402

TOPOLOGY = os.environ["TOPOLOGY"]
# A variant (same mesh, other FIPS settings) runs under its own name; the
# collector only needs its nodes, links and default netem, which it shares
# with its base topology.
TOPO = replace(topology.load(ROOT, os.environ.get("TOPOLOGY_BASE", TOPOLOGY)), name=TOPOLOGY)
LAB = ROOT / "generated" / TOPOLOGY
SOCKETS = LAB / "run"
WEB = ROOT / "web"
PORT = int(os.environ.get("PORT", "8080"))
INTERVAL = float(os.environ.get("POLL_INTERVAL", "1.0"))


def query(node, command):
    """One control-socket request; the daemon answers one line and closes."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(2)
        sock.connect(str(SOCKETS / node / "control.sock"))
        sock.sendall(json.dumps({"command": command}).encode() + b"\n")
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = sock.recv(65536)
            if not chunk:
                break
            buf += chunk
    reply = json.loads(buf)
    if reply.get("status") != "ok":
        raise RuntimeError(reply.get("message", "control socket error"))
    return reply["data"]


def poll_node(node):
    try:
        return node, query(node, "show_tree"), query(node, "show_peers")["peers"]
    except (OSError, ValueError, RuntimeError):
        return node, None, None  # container stopped, or daemon not up yet


def read_json(name, default):
    try:
        return json.loads((LAB / name).read_text())
    except (OSError, ValueError):
        return default


def snapshot(pool):
    meta = read_json("nodes.json", {})
    links = read_json("links.json", [])
    lab_state = lab.read_labstate(TOPO)
    cut = set(lab_state["cut"])
    stopped = set(lab_state["stopped"])
    state = {node: (tree, peers) for node, tree, peers in pool.map(poll_node, meta)}

    nodes = []
    for node, info in meta.items():
        tree, peers = state[node]
        nodes.append({
            "id": node,
            "npub": info["npub"],
            "ipv6": info["ipv6"],
            "up": tree is not None,
            "stopped": node in stopped,  # killed by the lab
            "is_root": bool(tree and tree["is_root"]),
            "depth": tree["depth"] if tree else None,
            "parent": tree["parent_display_name"] if tree else None,
            "peer_count": len(peers) if peers else 0,
        })

    out_links = []
    for link in links:
        a, b = link["a"], link["b"]
        # Read the link from whichever end is up; both see the same link.
        view = None
        for here, there in ((a, b), (b, a)):
            peers = state[here][1]
            if peers is not None:
                view = next((p for p in peers if p["display_name"] == there), None)
                if view is not None:
                    break
        mmp = (view or {}).get("mmp") or {}
        stats = (view or {}).get("stats") or {}
        out_links.append({
            "id": link["id"],
            "a": a,
            "b": b,
            "cut": link["id"] in cut,      # what the lab did to the wire
            "netem": lab_state["netem"].get(str(link["id"])),  # explicit impairment, or None for default
            "fips_up": view is not None,   # what FIPS currently believes
            "tree": bool(view and (view["is_parent"] or view["is_child"])),
            "srtt_ms": mmp.get("srtt_ms"),
            "loss": mmp.get("loss_rate"),
            "etx": mmp.get("etx"),
            "bytes": stats.get("bytes_sent", 0) + stats.get("bytes_recv", 0),
        })

    return {
        "topology": TOPOLOGY,
        "t": time.time(),
        "default_netem": TOPO.netem,
        "nodes": nodes,
        "links": out_links,
    }


def link_of(body):
    return TOPO.link(str(body.get("a")), str(body.get("b")))


def run_action(body):
    """Run one fault; returns a short description of what was done."""
    action = body.get("action")
    if action == "cut":
        link = link_of(body)
        lab.cut(TOPO, link)
        return f"cut {link.a}—{link.b}"
    if action == "heal":
        link = link_of(body)
        lab.heal(TOPO, link)
        return f"healed {link.a}—{link.b}"
    if action == "netem":
        link = link_of(body)
        params = body.get("params")
        if params is not None and not isinstance(params, dict):
            raise lab.LabError("params must be an object or null")
        lab.set_netem(TOPO, link, params)
        shown = lab.netem_args(TOPO.netem if params is None else params)
        return f"{link.a}—{link.b}: {' '.join(shown) or 'no impairment'}"
    if action == "kill":
        lab.kill(TOPO, str(body.get("node")))
        return f"killed {body['node']}"
    if action == "start":
        lab.start(TOPO, str(body.get("node")))
        return f"started {body['node']}"
    if action == "partition":
        nodes = body.get("nodes")
        if not isinstance(nodes, list):
            raise lab.LabError("partition needs a list of nodes")
        crossing = lab.partition(TOPO, [str(n) for n in nodes])
        return f"partitioned {{{', '.join(nodes)}}}: cut {len(crossing)} links"
    if action == "reset":
        lab.reset(TOPO)
        return "reset: all nodes running, all links up"
    raise lab.LabError(f"unknown action: {action}")


class Feed:
    """The latest snapshot, plus a condition for streams waiting on the next."""

    def __init__(self):
        self.latest = None
        self.changed = threading.Condition()

    def run(self):
        with ThreadPoolExecutor(max_workers=32) as pool:
            while True:
                started = time.monotonic()
                snap = snapshot(pool)
                with self.changed:
                    self.latest = snap
                    self.changed.notify_all()
                time.sleep(max(0, INTERVAL - (time.monotonic() - started)))

    def next(self, after):
        with self.changed:
            self.changed.wait_for(lambda: self.latest is not None and self.latest["t"] > after, timeout=30)
            return self.latest


FEED = Feed()


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB), **kwargs)

    def do_GET(self):
        if self.path == "/snapshot":
            body = json.dumps(FEED.next(0)).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            last = 0
            try:
                while True:
                    snap = FEED.next(last)
                    if snap is None or snap["t"] <= last:
                        self.wfile.write(b": keepalive\n\n")
                    else:
                        last = snap["t"]
                        self.wfile.write(b"data: " + json.dumps(snap).encode() + b"\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
        else:
            super().do_GET()

    def do_POST(self):
        if self.path != "/action":
            return self.reply(404, {"ok": False, "error": "not found"})
        origin = self.headers.get("Origin")
        if origin is not None and origin != f"http://{self.headers.get('Host')}":
            return self.reply(403, {"ok": False, "error": "cross-origin request refused"})
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            return self.reply(415, {"ok": False, "error": "send application/json"})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            if not isinstance(body, dict):
                raise ValueError("expected a JSON object")
            message = run_action(body)
        except (lab.LabError, ValueError) as err:
            return self.reply(400, {"ok": False, "error": str(err)})
        except subprocess.CalledProcessError as err:
            return self.reply(500, {"ok": False, "error": (err.stderr or str(err)).strip()})
        print(f"action: {message}", flush=True)
        self.reply(200, {"ok": True, "message": message})

    def reply(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass  # one SSE stream per open tab would flood the log


def main():
    threading.Thread(target=FEED.run, daemon=True).start()
    print(f"collector on :{PORT}, polling every {INTERVAL}s", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
