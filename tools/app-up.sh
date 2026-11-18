#!/usr/bin/env bash
# app-up.sh <env>: install every service from its newest sha- image in ECR, by digest.
# 12.6 Step 5 with real images. Topic 18 replaces this script with Argo CD.
set -euo pipefail
ENV=${1:?usage: app-up.sh <qa|prod>}
cd "$(dirname "$0")/.."
REGISTRY=$(terraform -chdir=infra/terraform/ci output -raw registry)

latest_digest() { # newest image of repository uber-lite/$1 that carries a sha- tag
  aws ecr describe-images --repository-name "uber-lite/$1" --output json |
    jq -r '[.imageDetails[] | select(any(.imageTags[]?; startswith("sha-")))] | sort_by(.imagePushedAt) | last | .imageDigest // empty'
}

for dir in gitops/apps/*/; do
  svc=$(basename "$dir")
  digest=$(latest_digest "$svc")
  [ -n "$digest" ] || { echo "X no sha- image for $svc in ECR. Has main built it? (13.4)"; exit 1; }
  echo "== $svc @ ${digest:7:12}"
  helm --kube-context "$ENV" upgrade --install "$svc" charts/microservice -n uber-lite \
    -f "$dir/values.yaml" -f "$dir/values-$ENV.yaml" \
    --set image.repository="$REGISTRY/uber-lite/$svc" --set image.digest="$digest" \
    --wait --timeout 5m
done