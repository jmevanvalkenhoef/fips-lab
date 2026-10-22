# FIPS node image for the lab.
#
# Builds the daemon and its tools from an upstream release tag, then copies
# them into a slim runtime image with the network tools experiments need.

ARG FIPS_REF=v0.5.3

FROM rust:1.94.1-slim-trixie AS build
ARG FIPS_REF
RUN apt-get update && \
    apt-get install -y --no-install-recommends git ca-certificates libclang-dev pkg-config libdbus-1-dev && \
    rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 --branch "${FIPS_REF}" https://github.com/jmcorgan/fips.git /src
WORKDIR /src
RUN cargo build --release --bins && \
    mkdir /out && \
    cp target/release/fips target/release/fipsctl target/release/fipstop /out/

FROM debian:trixie-slim
ARG FIPS_REF
LABEL org.opencontainers.image.title="fips-lab-node" \
      org.opencontainers.image.version="${FIPS_REF}"
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        iproute2 iputils-ping iperf3 netcat-openbsd tcpdump dnsmasq dnsutils libdbus-1-3 && \
    rm -rf /var/lib/apt/lists/*
COPY --from=build /out/ /usr/local/bin/
# Matches the systemd RuntimeDirectory, so the control socket lands at
# /run/fips/control.sock where fipsctl and the collector expect it.
RUN mkdir -p /run/fips /etc/fips
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
