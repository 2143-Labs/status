#!/bin/sh
# Fetch the provider credentials from OpenBao into this job's environment.
# Requires id-token: write, plus the jwt-github role and ci-status-production policy.
set -eu
: "${ACTIONS_ID_TOKEN_REQUEST_URL:?run this step with id-token: write}"
: "${ACTIONS_ID_TOKEN_REQUEST_TOKEN:?run this step with id-token: write}"
: "${GITHUB_ENV:?run this step inside GitHub Actions}"

addr=${BAO_ADDR:-https://openbao.ts.2143.me}
role=${BAO_ROLE:-status-production}
secret=${BAO_SECRET_PATH:-consumers/digitalocean-status/ci}
mount=${secret%%/*}
sub=${secret#*/}
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

aud=$(printf '%s' "$addr" | od -An -tx1 | tr -d ' \n' | sed 's/../%&/g')
jwt=$(curl -sS --max-time 10 -H "Authorization: bearer $ACTIONS_ID_TOKEN_REQUEST_TOKEN" \
  "${ACTIONS_ID_TOKEN_REQUEST_URL}&audience=$aud" | jq -r '.value // empty')
[ -n "$jwt" ] || { echo "$0: GitHub issued no OIDC token; is id-token: write set?" >&2; exit 1; }

printf '{"jwt":"%s","role":"%s"}' "$jwt" "$role" > "$tmp/login.json"
login=$(curl -sS --max-time 10 -X POST --data-binary "@$tmp/login.json" "$addr/v1/auth/jwt-github/login")
tok=$(printf '%s' "$login" | jq -r '.auth.client_token // empty')
[ -n "$tok" ] || { echo "$0: login rejected: $(printf '%s' "$login" | jq -r '.errors[0]? // "no error text"')" >&2; exit 1; }

# KV v2 answers with .data.data, v1 with .data. Exactly one of the two paths exists.
body=
for p in "$mount/data/$sub" "$mount/$sub"; do
  out=$(curl -sS --max-time 10 -H "X-Vault-Token: $tok" "$addr/v1/$p")
  if printf '%s' "$out" | jq -e '.data.data | type == "object"' >/dev/null 2>&1; then body=$out; break; fi
  if printf '%s' "$out" | jq -e '.data | type == "object"' >/dev/null 2>&1; then
    body=$(printf '%s' "$out" | jq -c '{data:{data:.data}}'); break
  fi
done
[ -n "$body" ] || { echo "$0: cannot read $secret (both KV paths refused)" >&2; exit 1; }

printf '%s' "$body" | jq -r '.data.data | to_entries[] | "\(.key)=\(.value)"' > "$tmp/kv"
seen=
count=0
while IFS= read -r line; do
  [ -n "$line" ] || continue
  name=${line%%=*}
  value=${line#*=}
  case $name in
    DIGITALOCEAN_TOKEN|DESEC_API_TOKEN|AWS_ACCESS_KEY_ID|AWS_SECRET_ACCESS_KEY) ;;
    *) echo "$0: unexpected field '$name' in $secret; expected the four provider names" >&2; exit 1 ;;
  esac
  [ -n "$value" ] || { echo "$0: field $name is empty in $secret" >&2; exit 1; }
  printf '::add-mask::%s\n' "$value"
  printf '%s=%s\n' "$name" "$value" >> "$GITHUB_ENV"
  seen="$seen $name "   # trailing space so the " $want " test matches the last field too
  count=$((count + 1))
done < "$tmp/kv"

for want in DIGITALOCEAN_TOKEN DESEC_API_TOKEN AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY; do
  case $seen in
    *" $want "*) ;;
    *) echo "$0: $secret is missing $want (found:$seen)" >&2; exit 1 ;;
  esac
done

curl -sS --max-time 10 -X POST -H "X-Vault-Token: $tok" "$addr/v1/auth/token/revoke-self" >/dev/null 2>&1 || true
printf 'fetched %s fields from openbao\n' "$count"
