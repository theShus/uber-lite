#!/usr/bin/env bash
# validate-k8s.sh PATH...  Runs the same checks as pre-commit and CI, cheapest first.
set -euo pipefail
[ $# -gt 0 ] || { echo "usage: $0 <file-or-dir>..."; exit 2; }
root=$(git rev-parse --show-toplevel)

echo "== yamllint";        yamllint -c "$root/.yamllint" "$@"
echo "== kubeconform";     kubeconform -strict -summary "$@"
echo "== kube-linter";     kube-linter lint "$@"
echo "== server dry run"
args=(); for p in "$@"; do args+=(-f "$p"); done

kubectl apply --dry-run=server -R "${args[@]}"
echo "== all checks passed"
