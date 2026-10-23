#!/bin/sh
# Experiment 4: the stuck reroute is a coordinate-cache entry that its node
# verified during the start-up convergence check and that refuses updates
# for VERIFIED_TTL_MS (300 s). Two predictions:
#   1. a cut made more than 300 s after start never gets stuck;
#   2. a stuck reroute left alone clears ~300 s after that node's discovery.
set -e
cd "$(dirname "$0")/.."
FAST="--fips node.heartbeat_interval_secs=2 --fips node.link_dead_timeout_secs=6"
echo "== fresh start, settle 310 s"
python3 lab/scenario.py stuck-reroute --fresh --repeat "${REPEAT:-6}" --settle 310 $FAST
echo "== fresh start, settle 90 s, stuck cuts left alone"
FIPS_LOG=info,fips=debug python3 lab/scenario.py stuck-unlock --fresh --repeat "${UNLOCK_REPEAT:-4}" $FAST
