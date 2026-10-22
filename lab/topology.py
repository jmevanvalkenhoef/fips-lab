"""Topology files: load them, generate standard shapes, and plan link networks.

A topology is either explicit:

    nodes = ["a", "b", "c"]
    links = [["a", "b"], ["b", "c"]]

or generated:

    [generate]
    kind = "random"     # chain | ring | grid | random
    nodes = 10          # chain, ring, random
    degree = 3          # random: target average degree
    seed = 1            # random
    width = 4           # grid
    height = 3          # grid

Either form may set a default impairment applied to every link at startup:

    [netem]
    delay = "10ms"
    jitter = "2ms"
    loss = "0.5%"

and FIPS settings merged into every node's config:

    [fips.node]
    link_dead_timeout_secs = 10
"""

import random
import string
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

# Each link gets its own /29 so netem and link cuts act on exactly one link.
LINK_NET = "10.99.{}.0/29"
LINK_IP = "10.99.{}.{}"
MAX_LINKS = 254


@dataclass
class Link:
    id: int
    a: str
    b: str

    @property
    def network(self):
        return f"l{self.id}"

    @property
    def subnet(self):
        return LINK_NET.format(self.id)

    def ip(self, node):
        # .1 is the Docker gateway; the endpoints take .2 and .3.
        return LINK_IP.format(self.id, 2 if node == self.a else 3)

    def other(self, node):
        return self.b if node == self.a else self.a


@dataclass
class Topology:
    name: str
    nodes: list
    links: list
    netem: dict
    fips: dict

    def link(self, a, b):
        for link in self.links:
            if {link.a, link.b} == {a, b}:
                return link
        sys.exit(f"no link between {a} and {b}")

    def links_of(self, node):
        return [link for link in self.links if node in (link.a, link.b)]


def names(n):
    if n <= 26:
        return list(string.ascii_lowercase[:n])
    return [f"n{i:02d}" for i in range(1, n + 1)]


def chain(n):
    nodes = names(n)
    return nodes, list(zip(nodes, nodes[1:]))


def ring(n):
    nodes, edges = chain(n)
    return nodes, edges + [(nodes[-1], nodes[0])]


def grid(width, height):
    nodes = names(width * height)
    at = lambda x, y: nodes[y * width + x]
    edges = [(at(x, y), at(x + 1, y)) for y in range(height) for x in range(width - 1)]
    edges += [(at(x, y), at(x, y + 1)) for y in range(height - 1) for x in range(width)]
    return nodes, edges


def random_mesh(n, degree, seed):
    """A connected random graph: a random spanning tree plus extra random edges."""
    rng = random.Random(seed)
    nodes = names(n)
    edges = {tuple(sorted((nodes[i], nodes[rng.randrange(i)]))) for i in range(1, n)}
    target = min(round(n * degree / 2), n * (n - 1) // 2)
    while len(edges) < target:
        a, b = rng.sample(nodes, 2)
        edges.add(tuple(sorted((a, b))))
    return nodes, sorted(edges)


def load(root: Path, name):
    spec = tomllib.loads((root / "topologies" / f"{name}.toml").read_text())
    if "generate" in spec:
        g = spec["generate"]
        kind = g["kind"]
        if kind == "chain":
            nodes, edges = chain(g["nodes"])
        elif kind == "ring":
            nodes, edges = ring(g["nodes"])
        elif kind == "grid":
            nodes, edges = grid(g["width"], g["height"])
        elif kind == "random":
            nodes, edges = random_mesh(g["nodes"], g.get("degree", 3), g.get("seed", 1))
        else:
            sys.exit(f"unknown topology kind: {kind}")
    else:
        nodes, edges = spec["nodes"], spec["links"]

    for a, b in edges:
        if a not in nodes or b not in nodes:
            sys.exit(f"link {a}-{b} names an unknown node")
    if len(edges) > MAX_LINKS:
        sys.exit(f"{len(edges)} links; at most {MAX_LINKS} supported")
    links = [Link(i, a, b) for i, (a, b) in enumerate(edges, start=1)]
    return Topology(name, nodes, links, spec.get("netem", {}), spec.get("fips", {}))
