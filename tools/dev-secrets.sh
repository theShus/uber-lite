#!/usr/bin/env bash
# dev-secrets.sh: database passwords for DEV ONLY (never committed). Topic 17 replaces this with External Secrets.
set -euo pipefail
declare -A app=([rides]=rider-api [trips]=trip-service [payments]=payment-service)
for db in rides trips payments; do
  if ! kubectl -n data get secret "pg-$db" >/dev/null 2>&1; then
    kubectl -n data create secret generic "pg-$db" --type=kubernetes.io/basic-auth \
      --from-literal=username="$db" --from-literal=password="$(openssl rand -hex 16)"
  fi
  pw=$(kubectl -n data get secret "pg-$db" -o jsonpath='{.data.password}' | base64 -d)
  kubectl -n uber-lite create secret generic "${app[$db]}-db" \
    --from-literal=url="postgresql://$db:$pw@postgres-rw.data:5432/$db" \
    --dry-run=client -o yaml | kubectl apply -f -
  echo "pg-$db (data) and ${app[$db]}-db (uber-lite) ready"
done
