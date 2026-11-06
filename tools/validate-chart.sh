#!/usr/bin/env bash
# validate-chart.sh [ENV]: unit-test the chart, then lint, render and validate every service for ENV.
set -euo pipefail
env=${1:-dev}
cd "$(git rev-parse --show-toplevel)"
out=$(mktemp -d)
trap 'rm -rf "$out"' EXIT
# CRD schemas (HTTPRoute in Stage 6, ServiceMonitor in topic 19): community catalog, pinned to a commit
crds='https://raw.githubusercontent.com/datreeio/CRDs-catalog/63669a570e231d4f1f8396d229a1de512bcf0a34/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json'

helm unittest charts/microservice
for dir in gitops/apps/*/; do
  svc=$(basename "$dir")
  files=(-f "$dir/values.yaml")
  [ -f "$dir/values-$env.yaml" ] && files+=(-f "$dir/values-$env.yaml")
  echo "== $svc ($env)"
  helm lint --strict charts/microservice "${files[@]}" >/dev/null
  helm template "$svc" charts/microservice "${files[@]}" > "$out/$svc.yaml"
done
kubeconform -strict -summary -schema-location default -schema-location "$crds" "$out"
kube-linter lint "$out"
echo "== all services render cleanly for $env"
