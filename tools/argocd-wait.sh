#!/usr/bin/env bash
# argocd-wait.sh <context> [timeout minutes]: wait until every Argo CD Application is Synced and
# Healthy, and none has a sync still running or a last sync that failed (a failed hook leaves
# its Application Synced and Healthy). Prints what isn't ready every 20 seconds.
set -uo pipefail
CTX=${1:?usage: argocd-wait.sh <context> [timeout minutes, default 30]}
TIMEOUT_MIN=${2:-30}
k() { kubectl --context "$CTX" -n argocd "$@"; }
indent() { while IFS= read -r line; do printf '   %s\n' "$line"; done; }

deadline=$(($(date +%s) + TIMEOUT_MIN * 60))
while :; do
  apps=$(k get applications.argoproj.io -o json 2>/dev/null) || apps='{"items":[]}'
  total=$(jq '.items | length' <<<"$apps")
  pending=$(jq -r '.items[] | select(.status.sync.status != "Synced" or .status.health.status != "Healthy"
      or ((.status.operationState.phase // "none") | IN("Running", "Failed", "Error")))
    | "\(.metadata.name)\t\(.status.sync.status // "?")\t\(.status.health.status // "?")\t\(.status.operationState.phase // "-")"' <<<"$apps")
  if [ "$total" -gt 0 ] && [ -z "$pending" ]; then
    echo "== all $total Applications Synced and Healthy"
    exit 0
  fi
  if [ "$(date +%s)" -ge "$deadline" ]; then
    echo "✗ not ready after $TIMEOUT_MIN minutes (name, sync, health, last operation):"
    indent <<<"$pending"
    exit 1
  fi
  echo "… $(date +%T) $(grep -c . <<<"$pending") of $total not ready yet:"
  indent <<<"$pending" | head -8
  sleep 20
done
