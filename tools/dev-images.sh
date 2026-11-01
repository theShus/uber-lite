#!/usr/bin/env bash
# dev-images.sh [service...]: build images for dev and load them into the kind cluster (all services by default).
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
services=("$@"); [ ${#services[@]} -gt 0 ] || services=($(ls apps))
for svc in "${services[@]}"; do
  docker build -q -t "$svc:dev" "apps/$svc" >/dev/null
  kind load docker-image "$svc:dev" --name dev >/dev/null
  echo "$svc:dev built and loaded"
done
