#!/bin/sh
# Experiment 3: is the slow or stuck reroute a property of the first cut after
# the mesh starts? Every repetition restarts the whole mesh, then waits
# SETTLE seconds before cutting a-b. Fast timeouts, ring6 identities.
set -e
cd "$(dirname "$0")/.."
for settle in ${SETTLES:-15 90}; do
    echo "== fresh start, settle ${settle} s"
    python3 lab/scenario.py stuck-reroute --fresh --repeat "${REPEAT:-10}" --settle "$settle" \
        --fips node.heartbeat_interval_secs=2 \
        --fips node.link_dead_timeout_secs=6
done
