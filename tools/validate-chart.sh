#!/usr/bin/env bash
# validate-chart.sh [ENV]  Unit-test the chart, then render every service for ENV and validate the output.
set -euo pipefail
env=${1:-dev}
root=$(git rev-parse --show-toplevel); cd "$root"
out=$(mktemp -d); trap 'rm -rf "$out"' EXIT
# Schemas for custom resources (HTTPRoute from Stage 6, ServiceMonitor from topic 19): a community catalog, pinned to a commit
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
