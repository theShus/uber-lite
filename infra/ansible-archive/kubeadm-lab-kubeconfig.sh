#!/usr/bin/env bash
# Merge the kubeadm lab's admin kubeconfig into ~/.kube/config as the context "kubeadm-lab".
set -euo pipefail
NEW=~/.kube/kubeadm-lab.yaml                      # fetched by k8s-cluster.yml
CP=$(terraform -chdir="$HOME/uber-lite/infra/terraform/k8s-lab" output -raw cp1_public_ip)
chmod 600 "$NEW"
kubectl --kubeconfig "$NEW" config set-cluster kubeadm-lab --server="https://${CP}:6443" >/dev/null
kubectl --kubeconfig "$NEW" config rename-context kubernetes-admin@kubeadm-lab kubeadm-lab >/dev/null
# Remove yesterday's entries first: when kubeconfigs merge, the FIRST file wins on equal names.
kubectl config delete-context kubeadm-lab >/dev/null 2>&1 || true
kubectl config delete-cluster kubeadm-lab >/dev/null 2>&1 || true
kubectl config delete-user kubernetes-admin >/dev/null 2>&1 || true
KUBECONFIG=~/.kube/config:"$NEW" kubectl config view --flatten > /tmp/kubeconfig.merged
mv /tmp/kubeconfig.merged ~/.kube/config && chmod 600 ~/.kube/config && rm "$NEW"
kubectl --context kubeadm-lab get nodes -o wide
