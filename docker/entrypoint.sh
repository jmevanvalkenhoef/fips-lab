#!/bin/sh
# Route .fips names to the daemon's DNS responder and everything else to
# Docker's embedded DNS (which resolves the node-<x> peer hostnames).
#
# Resolving a .fips name is what tells a node the npub behind a mesh address;
# without it, traffic to a non-neighbour has no key to discover a route for.
set -e

cat > /etc/dnsmasq.conf <<'EOF'
port=53
listen-address=127.0.0.1
bind-interfaces
no-resolv
server=/fips/::1#5354
server=127.0.0.11
EOF
dnsmasq
echo "nameserver 127.0.0.1" > /etc/resolv.conf

exec fips --config /etc/fips/fips.yaml
