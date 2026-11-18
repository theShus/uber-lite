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

echo "== gateway certificate: self-signed placeholder (cert-manager replaces this in 16.4)"
tools/selfsigned-tls.sh "$ENV"
