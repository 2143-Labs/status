#!/bin/sh
# Run once by cloud-init as root. Idempotent.
set -eu
src=/opt/status/src
id statusd >/dev/null 2>&1 || useradd --system --home-dir /var/lib/status --shell /usr/sbin/nologin statusd
install -d -o statusd -g statusd -m 0755 /var/lib/status /var/lib/status/www /var/lib/status/snapshots
install -d -m 0755 /etc/status
install -m 0755 "$src/deploy/apply.sh" /usr/local/sbin/statusd-apply
install -m 0644 "$src/deploy/Caddyfile" /etc/caddy/Caddyfile
install -m 0644 "$src"/deploy/systemd/* /etc/systemd/system/
sh "$src/deploy/install-node-exporter.sh" "$src"
install -m 0755 "$src/deploy/statusd-backup" /usr/local/sbin/statusd-backup
install -m 0644 "$src"/config/*.toml /etc/status/
printf 'STATUS_BUILD_SHA=%s\n' "$(git -C "$src" rev-parse HEAD)" > /etc/status/build.env
systemctl daemon-reload
systemctl enable --now statusd.service status-deploy.timer statusd-backup.timer
systemctl reload-or-restart caddy
