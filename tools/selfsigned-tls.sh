#!/usr/bin/env bash
# selfsigned-tls.sh <env>: self-signed wildcard cert for the Gateway's https listener (TLS Secret in ns kong).
# Placeholder until cert-manager (16.4). Idempotent: an existing Secret is kept.
set -euo pipefail
ENV=${1:?usage: selfsigned-tls.sh <dev|qa|prod>}
case "$ENV" in
  dev)     ZONE=dev.shus.world SECRET=dev-wildcard-tls ;;
  qa|prod) ZONE=aws.shus.world SECRET=aws-wildcard-tls ;;
  *) echo "unknown env '$ENV'"; exit 1 ;;
esac
k() { kubectl --context "$ENV" "$@"; }

if k -n kong get secret "$SECRET" >/dev/null 2>&1; then
  echo "== $SECRET already exists in kong: kept"; exit 0
fi
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT        # the private key never outlives the script
openssl req -x509 -newkey rsa:2048 -nodes -days 30 -subj "/CN=*.$ZONE" \
  -addext "subjectAltName=DNS:*.$ZONE" -keyout "$tmp/tls.key" -out "$tmp/tls.crt" 2>/dev/null
openssl x509 -in "$tmp/tls.crt" -noout -subject -enddate
k -n kong create secret tls "$SECRET" --cert "$tmp/tls.crt" --key "$tmp/tls.key"
