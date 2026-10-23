#!/bin/sh
# Experiment 1: how reroute time after a link cut depends on dead-link
# detection. Runs cut-reroute on ring6 for each link_dead_timeout_secs,
# with the heartbeat at a third of the timeout (the FIPS default ratio, 10/30).
# All variants share ring6's identities, so the spanning tree is the same.
set -e
cd "$(dirname "$0")/.."
REPEAT=${REPEAT:-6}
for pair in 6:2 10:3 15:5 20:7 30:10; do
    dead=${pair%:*}
    heartbeat=${pair#*:}
    echo "== link_dead_timeout_secs=$dead heartbeat_interval_secs=$heartbeat"
    python3 lab/scenario.py cut-reroute --repeat "$REPEAT" \
        --fips node.link_dead_timeout_secs="$dead" \
        --fips node.heartbeat_interval_secs="$heartbeat"
done
