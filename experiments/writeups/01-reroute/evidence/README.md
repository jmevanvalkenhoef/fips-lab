# Evidence: one stuck reroute, every node's view

Repetition 1 of `scenarios/stuck-diagnose.toml` (fresh start, 90 s settle,
heartbeat 2 s, dead timeout 6 s, `FIPS_LOG=info,fips=debug`): cut a—b at
17:04:56.6 UTC, a never reached b within the 45 s wait.

- `capture/`: every `fipsctl show` of all six nodes at the timeout, plus a
  `fipsctl probe` from a to b. `e-cache.json` holds a's stale verified entry
  (`a > b > c`); every other node has `a > f > e > d > c`.
- `logs/`: each node's debug log for the repetition. Merge them by timestamp
  to follow the SessionAck from b: c → d → e → d → … until
  `SessionDatagram TTL exhausted` on d. `e.log` has the discovery at 17:03:16
  that made e's entry verified.
- `events.jsonl`, `meta.json`, `scenario.toml`: the run as recorded.

Node identities are the lab's throwaway test keys; logs contain public keys only.
