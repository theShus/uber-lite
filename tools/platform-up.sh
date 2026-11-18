#!/usr/bin/env bash
# platform-up.sh <env>: install the platform components on a fresh cluster, in dependency order.
# Each topic adds its block. Topic 18 replaces this whole script with Argo CD.

# Redosled je bitan:
# LBC pre Konga. Kad se pojavi Kongov Service type: LoadBalancer, LBC i njegov webhook već moraju da postoje.
# CRD-ovi pre Konga. KIC traži Gateway API CRD-ove pri startu.
# Namespace-ovi pre Konga, sa labelom za readiness gate.
# TLS Secret i API key pre apply -k. Gateway i consumer ih referenciraju.

set -euo pipefail
ENV=${1:?usage: platform-up.sh <qa|prod>}
CTX=$ENV CLUSTER=ul-$ENV REGION=eu-central-1
DOMAIN="shus.world"
cd "$(dirname "$0")/.."
h() { helm --kube-context "$CTX" "$@"; }
k() { kubectl --context "$CTX" "$@"; }

echo "== storage"
k apply -f gitops/platform/storage/

echo "== aws-load-balancer-controller"
h upgrade --install aws-load-balancer-controller aws-load-balancer-controller \
  --repo https://aws.github.io/eks-charts --version 3.6.3 -n kube-system \
  -f gitops/platform/aws-load-balancer-controller/values.yaml \
  --set clusterName="$CLUSTER" --set region="$REGION" \
  --set vpcId="$(terraform -chdir=infra/terraform/network output -raw vpc_id)" --wait

echo "== Gateway API CRDs v1.3.0 (what KIC 3.5 is built against)"
k apply --server-side -f https://github.com/kubernetes-sigs/gateway-api/releases/download/v1.3.0/standard-install.yaml

echo "== namespaces"
for ns in kong uber-lite data; do k apply -k "gitops/platform/namespaces/$ns"; done

echo "== kong (KIC + proxy pods; on EKS the proxy Service becomes an NLB via LBC)"
h upgrade --install kong ingress --repo https://charts.konghq.com --version 0.24.0 -n kong \
  -f gitops/platform/operators/kong/values.yaml \
  -f "gitops/platform/operators/kong/values-$ENV.yaml" --wait

echo "== gateway certificate: self-signed placeholder (cert-manager replaces this in Stage 6)"
if ! k -n kong get secret aws-wildcard-tls >/dev/null 2>&1; then
  tmp=$(mktemp -d)
  openssl req -x509 -newkey rsa:2048 -nodes -days 30 -subj "/CN=*.aws.$DOMAIN" \
    -addext "subjectAltName=DNS:*.aws.$DOMAIN" -keyout "$tmp/tls.key" -out "$tmp/tls.crt" 2>/dev/null
  k -n kong create secret tls aws-wildcard-tls --cert "$tmp/tls.crt" --key "$tmp/tls.key"
  rm -rf "$tmp"
fi

echo "== partner API key (never in Git)"
if ! k -n uber-lite get secret partner-app-key >/dev/null 2>&1; then
  k -n uber-lite create secret generic partner-app-key --from-literal=key="$(openssl rand -hex 16)"
  k -n uber-lite label secret partner-app-key konghq.com/credential=key-auth
fi

echo "== gateway, plugins, consumers"
k apply -k "gitops/platform/gateway/$ENV"
