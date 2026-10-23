#!/bin/sh
# Experiment 6 (write-up 02): fill the 91-300 s gap in the stuck-risk curve.
# Cuts 150, 200 and 250 s after a fresh start, twice each, in shuffled order;
# about 5 minutes per cut. No new cut starts after BUDGET seconds, so the run
# stays near half an hour.
set -e
cd "$(dirname "$0")/.."
SETTLES="${SETTLES:-150 200 250}"
REPEAT="${REPEAT:-2}"
BUDGET="${BUDGET:-1500}"
start=$(date +%s)
order=$(for s in $SETTLES; do for i in $(seq "$REPEAT"); do echo "$s"; done; done | shuf)
for settle in $order; do
    if [ $(( $(date +%s) - start )) -gt "$BUDGET" ]; then
        echo "== time budget reached, stopping"
        break
    fi
    echo "== fresh start, settle ${settle} s"
    python3 lab/scenario.py window-probe --fresh --settle "$settle" \
        --fips node.heartbeat_interval_secs=2 \
        --fips node.link_dead_timeout_secs=6
done
