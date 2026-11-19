#!/usr/bin/env bash
# dev-secrets.sh <context>: database passwords for dev and the lab prod (never committed).
# Topic 17 replaces this with External Secrets.
set -euo pipefail
CTX=${1:?usage: dev-secrets.sh <dev|qa|prod>}
k() { kubectl --context "$CTX" "$@"; }
declare -A app=([rides]=rider-api [trips]=trip-service [payments]=payment-service)
for db in rides trips payments; do
  if ! k -n data get secret "pg-$db" >/dev/null 2>&1; then
    k -n data create secret generic "pg-$db" --type=kubernetes.io/basic-auth \
      --from-literal=username="$db" --from-literal=password="$(openssl rand -hex 16)"
  fi
  pw=$(k -n data get secret "pg-$db" -o jsonpath='{.data.password}' | base64 -d)
  k -n uber-lite create secret generic "${app[$db]}-db" \
    --from-literal=url="postgresql://$db:$pw@postgres-rw.data:5432/$db" \
    --dry-run=client -o yaml | k apply -f -
  echo "pg-$db (data) and ${app[$db]}-db (uber-lite) ready"
done
