#!/bin/sh
# Install Prometheus node_exporter for status-1 host metrics. Idempotent.
# Called from bootstrap.sh (new droplets) and pull.sh (every 60 s, so an existing
# droplet picks it up without an SSH session). $1 = repo checkout.
set -eu
src=${1:-/opt/status/src}
bin=/usr/local/bin/node_exporter
unit=/etc/systemd/system/node-exporter.service
if [ -x "$bin" ] && [ -f "$unit" ]; then exit 0; fi
version=1.12.1
sha256=b51d8a76aa2a9156a55d501aca6276fae09e262259a5e4e831d2c2222f084e63
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
curl -fsSL -o "$tmp/node_exporter.tgz" \
  "https://github.com/prometheus/node_exporter/releases/download/v${version}/node_exporter-${version}.linux-amd64.tar.gz"
printf '%s  %s\n' "$sha256" "$tmp/node_exporter.tgz" | sha256sum -c - >/dev/null
tar -xzf "$tmp/node_exporter.tgz" -C "$tmp"
install -m 0755 "$tmp/node_exporter-${version}.linux-amd64/node_exporter" "$bin"
install -m 0644 "$src/deploy/systemd/node-exporter.service" "$unit"
systemctl daemon-reload
systemctl enable --now node-exporter.service
