#!/usr/bin/env python3
"""Poll every lab node's control socket and stream mesh snapshots to browsers.

Runs in its own container next to the nodes. Each node's /run/fips is
bind-mounted at /sockets/<node>, so the collector talks to the control
sockets directly instead of exec'ing into containers.

    GET /           the visualization (static files from /web)
    GET /snapshot   the latest snapshot as JSON
    GET /events     a Server-Sent Events stream of snapshots, one per poll

Standard library only, so the image needs nothing installed.
"""

import json
import os
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

LAB = Path(os.environ.get("LAB_DIR", "/lab"))
SOCKETS = Path(os.environ.get("SOCKETS_DIR", "/sockets"))
WEB = Path(os.environ.get("WEB_DIR", "/web"))
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
    cut = set(read_json("linkstate.json", {}).get("cut", []))
    state = {node: (tree, peers) for node, tree, peers in pool.map(poll_node, meta)}

    nodes = []
    for node, info in meta.items():
        tree, peers = state[node]
        nodes.append({
            "id": node,
            "npub": info["npub"],
            "ipv6": info["ipv6"],
            "up": tree is not None,
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
            "fips_up": view is not None,   # what FIPS currently believes
            "tree": bool(view and (view["is_parent"] or view["is_child"])),
            "srtt_ms": mmp.get("srtt_ms"),
            "loss": mmp.get("loss_rate"),
            "etx": mmp.get("etx"),
            "bytes": stats.get("bytes_sent", 0) + stats.get("bytes_recv", 0),
        })

    return {"topology": os.environ.get("TOPOLOGY", ""), "t": time.time(), "nodes": nodes, "links": out_links}


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

    def log_message(self, format, *args):
        pass  # one SSE stream per open tab would flood the log


def main():
    threading.Thread(target=FEED.run, daemon=True).start()
    print(f"collector on :{PORT}, polling every {INTERVAL}s", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
