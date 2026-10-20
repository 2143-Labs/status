#!/bin/sh
# Installed as /usr/local/sbin/statusd-apply. Usage: statusd-apply <old-sha> <new-sha>
set -eu
old=$1; new=$2; src=/opt/status/src

install_tree() {
  install -m 0644 "$src/deploy/Caddyfile" /etc/caddy/Caddyfile
  install -m 0644 "$src"/deploy/systemd/* /etc/systemd/system/
  install -m 0755 "$src/deploy/statusd-backup" /usr/local/sbin/statusd-backup
  install -m 0644 "$src"/config/*.toml /etc/status/
  printf 'STATUS_BUILD_SHA=%s\n' "$1" > /etc/status/build.env
  systemctl daemon-reload
  systemctl enable statusd.service status-deploy.timer statusd-backup.timer
  systemctl restart statusd.service
  systemctl reload-or-restart caddy
}

healthy() {  # $1 = expected build sha; up to 30 s
  for _ in $(seq 30); do
    got=$(curl -fsS --max-time 2 http://127.0.0.1:8080/healthz 2>/dev/null \
      | python3 -c 'import json,sys; print(json.load(sys.stdin)["build_sha"])' 2>/dev/null || true)
    [ "$got" = "$1" ] && return 0
    sleep 1
  done
  return 1
}

fail() {  # $1 = reason
  git -C "$src" reset --quiet --hard "$old"
  install -m 0755 "$src/deploy/apply.sh" /usr/local/sbin/statusd-apply   # never keep a broken new copy
  install_tree "$old"
  { echo "$new"; echo "$1"; journalctl -u statusd -n 40 --no-pager; } > /var/lib/status/deploy-failed
  logger -t statusd-apply "deploy of $new failed ($1); rolled back to $old"
  exit 1
}

git -C "$src" reset --quiet --hard "$new"
install -m 0755 "$src/deploy/apply.sh" /usr/local/sbin/statusd-apply   # used from the next deploy on
caddy validate --config "$src/deploy/Caddyfile" --adapter caddyfile >/dev/null 2>&1 || fail "invalid Caddyfile"
install_tree "$new"
healthy "$new" || fail "healthz did not report build $new within 30 s"
rm -f /var/lib/status/deploy-failed
logger -t statusd-apply "deployed $new"
