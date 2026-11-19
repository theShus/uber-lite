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

echo "== cert-manager"
h upgrade --install cert-manager cert-manager --repo https://charts.jetstack.io --version v1.21.2 \
  -n cert-manager --create-namespace -f gitops/platform/cert-manager/values.yaml --wait
k apply -f gitops/platform/cert-manager/clusterissuers.yaml

echo "== the gateway's certificate: restored if we have one (before the Gateway asks for it)"
tools/tls-restore.sh "$ENV"

echo "== partner API key (never in Git)"
if ! k -n uber-lite get secret partner-app-key >/dev/null 2>&1; then
  k -n uber-lite create secret generic partner-app-key --from-literal=key="$(openssl rand -hex 16)"
  k -n uber-lite label secret partner-app-key konghq.com/credential=key-auth
fi

echo "== gateway, plugins, consumers"
k apply -k "gitops/platform/gateway/$ENV"

echo "== external-dns (records from HTTPRoutes/GRPCRoutes; owner ul-$ENV)"
h upgrade --install external-dns external-dns --repo https://kubernetes-sigs.github.io/external-dns/ --version 1.23.0 \
  -n external-dns --create-namespace \
  -f gitops/platform/external-dns/values.yaml --set txtOwnerId="ul-$ENV" --wait

echo "== cloudflared (the tunnel ul-$ENV is the permanent 'tunnel' layer; here only its connectors)"
if [ "$ENV" = prod ]; then
  k apply -k gitops/platform/namespaces/cloudflared
  k -n cloudflared create secret generic cloudflared-token \
    --from-file=token=<(terraform -chdir=infra/terraform/tunnel output -raw tunnel_token) \
    --dry-run=client -o yaml | k apply -f -
  k apply -k gitops/platform/cloudflared
  k -n cloudflared rollout status deployment/cloudflared --timeout=3m
fi

echo "== data operators: Strimzi 1.3.0 (watches only ns data) + CloudNativePG 0.29.1"
h upgrade --install strimzi oci://quay.io/strimzi-helm/strimzi-kafka-operator --version 1.3.0 \
  -n strimzi --create-namespace -f gitops/platform/operators/strimzi/values.yaml --wait
h upgrade --install cnpg cloudnative-pg --repo https://cloudnative-pg.github.io/charts --version 0.29.1 \
  -n cnpg-system --create-namespace -f gitops/platform/operators/cnpg/values.yaml --wait

echo "== database secrets (before Postgres: the Cluster references pg-rides/pg-trips/pg-payments)"
tools/dev-secrets.sh "$CTX"

echo "== data: Kafka, Postgres, Valkey (PVCs land on the default gp3 StorageClass)"
k apply -k gitops/platform/data/dev
k -n data wait kafka/kafka --for=condition=Ready --timeout=10m
k -n data wait cluster.postgresql.cnpg.io/postgres --for=condition=Ready --timeout=10m
k -n data rollout status statefulset/valkey --timeout=5m
