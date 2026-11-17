#!/usr/bin/env bash
# platform-up.sh <env>: install the platform components on a fresh cluster, in dependency order.
# Each topic adds its block. Topic 18 replaces this whole script with Argo CD.
set -euo pipefail
ENV=${1:?usage: platform-up.sh <qa|prod>}
CTX=$ENV CLUSTER=ul-$ENV REGION=eu-central-1
cd "$(dirname "$0")/.."
h() { helm --kube-context "$CTX" "$@"; }
k() { kubectl --context "$CTX" "$@"; }

echo "== storage"
k apply -f gitops/platform/storage/

echo "== aws-load-balancer-controller"
h upgrade --install aws-load-balancer-controller aws-load-balancer-controller \
  --repo https://aws.github.io/eks-charts --version 3.6.0 -n kube-system \
  -f gitops/platform/aws-load-balancer-controller/values.yaml \
  --set clusterName="$CLUSTER" --set region="$REGION" \
  --set vpcId="$(terraform -chdir=infra/terraform/network output -raw vpc_id)" --wait