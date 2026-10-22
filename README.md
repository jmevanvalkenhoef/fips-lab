# FIPS Lab

A visual chaos lab for [FIPS](https://github.com/jmcorgan/fips), the self-organizing mesh network that uses Nostr keypairs as node identities.

Spin up a mesh of FIPS nodes in Docker, break it (cut links, kill nodes, partition the network, degrade links), and watch it heal live in the browser. Every experiment is measured and reproducible.

> Status: early development.

## Layout

```
docker/       FIPS node image and compose topologies
collector/    Reads each node's control socket, streams state to the browser
web/          Live visualization
scenarios/    Scripted fault-injection scenarios
experiments/  Measurements, notebooks, write-ups
docs/         Plan and design notes
```

## License

MIT
