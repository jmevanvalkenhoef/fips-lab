#!/bin/sh
# Experiment 2: the intermittent stuck reroute with fast dead-link detection.
set -e
cd "$(dirname "$0")/.."
python3 lab/scenario.py stuck-reroute --repeat "${REPEAT:-20}" \
    --fips node.heartbeat_interval_secs=2 \
    --fips node.link_dead_timeout_secs=6
