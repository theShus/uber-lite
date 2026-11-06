#!/usr/bin/env bash
# install-k8s-tools.sh: the Kubernetes validators CI needs, at the hub's versions.
set -euo pipefail
KUBECONFORM_VERSION=v0.8.0
KUBELINTER_VERSION=v0.8.3
HELM_UNITTEST_VERSION=v1.2.1

bin="${RUNNER_TEMP:-/tmp}/bin"
mkdir -p "$bin"
curl -fsSL "https://github.com/yannh/kubeconform/releases/download/${KUBECONFORM_VERSION}/kubeconform-linux-amd64.tar.gz" | tar xz -C "$bin" kubeconform
curl -fsSL "https://github.com/stackrox/kube-linter/releases/download/${KUBELINTER_VERSION}/kube-linter-linux.tar.gz" | tar xz -C "$bin" kube-linter
# unsigned plugin from Git: a conscious trust decision
helm plugin install https://github.com/helm-unittest/helm-unittest --version "$HELM_UNITTEST_VERSION" --verify=false >/dev/null
echo "$bin" >> "${GITHUB_PATH:-/dev/null}"
"$bin/kubeconform" -v && "$bin/kube-linter" version
