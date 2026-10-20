#!/bin/sh
# Every 60 s as root. Deploys the main commit named in branch deploy's DEPLOY_SHA.
set -eu
cd /opt/status/src
git fetch --quiet origin +refs/heads/main:refs/remotes/origin/main +refs/heads/deploy:refs/remotes/origin/deploy
new=$(git show refs/remotes/origin/deploy:DEPLOY_SHA | tr -d '[:space:]')
git cat-file -e "$new^{commit}"
old=$(git rev-parse HEAD)
[ "$new" = "$old" ] && exit 0
# A SHA that already failed is not retried; the next promotion is.
if [ -f /var/lib/status/deploy-failed ] && [ "$(head -n1 /var/lib/status/deploy-failed)" = "$new" ]; then exit 0; fi
exec /usr/local/sbin/statusd-apply "$old" "$new"
